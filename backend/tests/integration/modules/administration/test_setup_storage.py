"""Storage preparation for an installation's first owner.

Validation runs before anything is persisted, because a vault directory that already
holds someone's model library is not an empty blob store, and roots that overlap —
directly or through a symlink — would let one subsystem delete another's files.

An owner provisioned from ``VAULT_SETUP_ADMIN_*`` signs in before any storage has
been chosen, so the choice the browser wizard makes in one request happens here in a
second one. Two properties matter. The choice is checked before it is persisted, so a
mistyped remote setting is refused and the owner can try again. And once storage has
been chosen it cannot be chosen again through this path, which would otherwise be a
way to repoint a live vault at an empty directory.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

from app.core.config import _overlay
from app.core.errors import ErrorKind, OperationError
from app.db.models import SystemConfig
from app.modules.administration import setup_storage
from app.modules.storage.storage_backend.runtime import get_backend
from app.schemas.setup import SetupStorageRequest

CONFIGURED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def runtime_dirs(tmp_path: Path) -> Path:
    """Point every managed root at the test's own tmp dir."""
    _overlay["staging_dir"] = tmp_path / "staging"
    _overlay["backup_dir"] = tmp_path / "backups"
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    return tmp_path


@pytest.fixture
def provisioned_owner(make_user, make_system_config) -> SystemConfig:
    """What startup leaves behind for ``VAULT_SETUP_ADMIN_*``: an owner, no storage."""
    make_user("store-owner", superuser=True)
    return make_system_config(configured_at=CONFIGURED_AT, setup_storage_pending=True)


def _local_choice(root: Path) -> SetupStorageRequest:
    return SetupStorageRequest(
        storage_backend="local",
        data_dir=str(root / "chosen-files"),
        thumb_dir=str(root / "chosen-thumbs"),
    )


def _hostile_path(failing_call: str):
    """A ``Path`` stand-in whose *one* named call fails the way a bad mount does.

    ``pathlib.Path`` cannot be subclassed usefully on 3.11, and a real filesystem
    cannot be made to refuse ``mkdir`` or ``iterdir`` on demand, so this delegates
    everything except the call under test.
    """

    class _HostilePath:
        def __init__(self, *parts: Any) -> None:
            self._path = Path(*parts)

        def _wrap(self, path: Path) -> "_HostilePath":
            return _HostilePath(path)

        def resolve(self, *args: Any, **kwargs: Any) -> "_HostilePath":
            if failing_call == "resolve":
                raise OSError("cannot resolve")
            return self._wrap(self._path.resolve(*args, **kwargs))

        def expanduser(self) -> "_HostilePath":
            return self._wrap(self._path.expanduser())

        def mkdir(self, *args: Any, **kwargs: Any) -> None:
            if failing_call == "mkdir":
                raise OSError("read-only filesystem")
            self._path.mkdir(*args, **kwargs)

        def iterdir(self):
            if failing_call == "iterdir":
                raise OSError("cannot list")
            return self._path.iterdir()

        def unlink(self, *args: Any, **kwargs: Any) -> None:
            if failing_call == "unlink":
                raise OSError("cannot unlink")
            self._path.unlink(*args, **kwargs)

        def exists(self) -> bool:
            return self._path.exists()

        def is_dir(self) -> bool:
            return self._path.is_dir()

        def __truediv__(self, other: Any) -> "_HostilePath":
            return self._wrap(self._path / other)

        def __fspath__(self) -> str:
            return str(self._path)

        def __str__(self) -> str:
            return str(self._path)

    return _HostilePath


LOCAL = SetupStorageRequest(storage_backend="local")


class TestPrepare:
    def test_resolves_the_deployment_roots_for_a_blank_choice(
        self, db_session: Session, runtime_dirs: Path
    ) -> None:
        # The browser omits unchanged defaults, so the *effective* paths are
        # prepared — not only explicit overrides.
        prepared = setup_storage.prepare(LOCAL, db_session, provision=True)

        assert prepared.data_dir == str((runtime_dirs / "files").resolve())

    def test_refuses_a_populated_vault_directory(self, db_session: Session) -> None:
        existing = Path(_overlay["data_dir"]) / "Jonathan" / "part.stl"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"user-owned")

        with pytest.raises(OperationError, match="^data_dir_not_empty$"):
            setup_storage.prepare(LOCAL, db_session, provision=True)

    def test_leaves_a_populated_directory_untouched(self, db_session: Session) -> None:
        existing = Path(_overlay["data_dir"]) / "Jonathan" / "part.stl"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"user-owned")

        with pytest.raises(OperationError):
            setup_storage.prepare(LOCAL, db_session, provision=True)

        assert existing.read_bytes() == b"user-owned"

    def test_refuses_nested_storage_roots(
        self, db_session: Session, runtime_dirs: Path
    ) -> None:
        shared = runtime_dirs / "shared"
        nested = SetupStorageRequest(
            storage_backend="local",
            data_dir=str(shared),
            thumb_dir=str(shared / "thumbs"),
        )

        with pytest.raises(OperationError, match="^storage_paths_overlap$"):
            setup_storage.prepare(nested, db_session, provision=True)

    def test_refuses_roots_aliased_by_a_symlink(
        self, db_session: Session, runtime_dirs: Path
    ) -> None:
        shared = runtime_dirs / "shared"
        shared.mkdir()
        alias = runtime_dirs / "alias"
        alias.symlink_to(shared, target_is_directory=True)
        aliased = SetupStorageRequest(
            storage_backend="local", data_dir=str(shared), thumb_dir=str(alias)
        )

        with pytest.raises(OperationError, match="^storage_paths_overlap$"):
            setup_storage.prepare(aliased, db_session, provision=True)

    @pytest.mark.parametrize("managed_root", ["staging_dir", "backup_dir"], ids=str)
    def test_refuses_a_root_that_swallows_a_managed_scratch_root(
        self, db_session: Session, managed_root: str
    ) -> None:
        swallowing = SetupStorageRequest(
            storage_backend="local", data_dir=str(_overlay[managed_root])
        )

        with pytest.raises(OperationError, match="^storage_paths_overlap$"):
            setup_storage.prepare(swallowing, db_session, provision=True)

    def test_refuses_a_root_that_swallows_the_database_file(
        self, db_session: Session, runtime_dirs: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A vault root containing the SQLite file would put the database inside the
        # blob store the GC walks.
        monkeypatch.setitem(
            _overlay, "db_url", f"sqlite:///{runtime_dirs / 'files' / 'vault.sqlite'}"
        )

        with pytest.raises(OperationError, match="^storage_paths_overlap$"):
            setup_storage.prepare(LOCAL, db_session, provision=True)

    @pytest.mark.parametrize(
        ("failing_call", "detail"),
        [
            pytest.param("resolve", "invalid_data_dir_path", id="unresolvable"),
            pytest.param("mkdir", "data_dir_not_creatable", id="not-creatable"),
            pytest.param("iterdir", "data_dir_not_readable", id="not-readable"),
        ],
    )
    def test_reports_a_root_the_filesystem_refuses(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        failing_call: str,
        detail: str,
    ) -> None:
        # A real filesystem cannot be made to fail these on demand, so only the one
        # call under test is stood in for — bound in this module's namespace, so the
        # pathlib.Path every other module holds is untouched.
        monkeypatch.setattr(setup_storage, "Path", _hostile_path(failing_call))

        with pytest.raises(OperationError, match=f"^{detail}$"):
            setup_storage.prepare(LOCAL, db_session, provision=True)

    def test_accepts_a_root_whose_write_probe_cannot_be_removed(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch, runtime_dirs: Path
    ) -> None:
        # Probe cleanup is best-effort: a filesystem that refuses the unlink must not
        # fail an otherwise valid setup.
        monkeypatch.setattr(setup_storage, "Path", _hostile_path("unlink"))

        prepared = setup_storage.prepare(LOCAL, db_session, provision=True)

        assert prepared.storage_backend == "local"

    def test_refuses_a_read_only_root(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def read_only_mount(*_args: object, **_kwargs: object):
            raise PermissionError("read-only mount")

        monkeypatch.setattr(
            setup_storage.tempfile, "NamedTemporaryFile", read_only_mount
        )

        with pytest.raises(OperationError, match="^data_dir_not_writable$"):
            setup_storage.prepare(LOCAL, db_session, provision=True)


class TestChoiceRequired:
    def test_holds_for_an_owner_without_storage(
        self, provisioned_owner: SystemConfig
    ) -> None:
        assert setup_storage.choice_required(provisioned_owner) is True

    def test_does_not_hold_before_the_installation_has_an_owner(
        self, make_system_config
    ) -> None:
        config = make_system_config(setup_storage_pending=True)

        assert setup_storage.choice_required(config) is False

    def test_does_not_hold_without_a_configuration_row(self) -> None:
        assert setup_storage.choice_required(None) is False

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param({"storage_backend": "local"}, id="local-backend"),
            pytest.param({"storage_provider": "webdav"}, id="typed-provider"),
        ],
    )
    def test_does_not_hold_once_a_storage_source_is_persisted(
        self, make_system_config, source: dict[str, str]
    ) -> None:
        # Chosen but not yet activated is a retry, never a second choice.
        config = make_system_config(
            configured_at=CONFIGURED_AT, setup_storage_pending=True, **source
        )

        assert setup_storage.choice_required(config) is False

    def test_does_not_hold_once_storage_is_prepared(self, make_system_config) -> None:
        config = make_system_config(configured_at=CONFIGURED_AT)

        assert setup_storage.choice_required(config) is False


class TestChoose:
    def test_activates_the_chosen_local_storage(
        self, db_session: Session, provisioned_owner: SystemConfig, tmp_path: Path
    ) -> None:
        setup_storage.choose(db_session, _local_choice(tmp_path))

        backend = get_backend()
        destination = tmp_path / "chosen-files" / "first-upload.bin"
        backend.create_bytes(b"ready", str(destination))
        assert destination.read_bytes() == b"ready"

    def test_finishes_the_pending_setup(
        self, db_session: Session, provisioned_owner: SystemConfig, tmp_path: Path
    ) -> None:
        setup_storage.choose(db_session, _local_choice(tmp_path))

        assert db_session.get(SystemConfig, 1).setup_storage_pending is False

    def test_pins_the_chosen_roots(
        self, db_session: Session, provisioned_owner: SystemConfig, tmp_path: Path
    ) -> None:
        # An unpinned root would let a later environment change reinterpret the
        # stored rows against a different mount.
        setup_storage.choose(db_session, _local_choice(tmp_path))

        config = db_session.get(SystemConfig, 1)
        assert config.data_dir == str((tmp_path / "chosen-files").resolve())

    def test_refuses_a_second_choice(
        self, db_session: Session, provisioned_owner: SystemConfig, tmp_path: Path
    ) -> None:
        setup_storage.choose(db_session, _local_choice(tmp_path))

        with pytest.raises(
            OperationError, match="^setup_storage_already_chosen$"
        ) as exc:
            setup_storage.choose(db_session, _local_choice(tmp_path / "elsewhere"))

        assert exc.value.kind is ErrorKind.CONFLICT

    def test_refuses_a_populated_root_without_persisting_it(
        self, db_session: Session, provisioned_owner: SystemConfig, tmp_path: Path
    ) -> None:
        populated = tmp_path / "chosen-files"
        populated.mkdir()
        (populated / "someone-elses-model.stl").write_text("solid")

        with pytest.raises(OperationError, match="^data_dir_not_empty$"):
            setup_storage.choose(db_session, _local_choice(tmp_path))

        db_session.expire_all()
        assert setup_storage.choice_required(db_session.get(SystemConfig, 1)) is True

    def test_refuses_unreachable_remote_storage_without_persisting_it(
        self, db_session: Session, provisioned_owner: SystemConfig
    ) -> None:
        # The integration socket guard refuses the connection, as a mistyped
        # endpoint would.
        unreachable = SetupStorageRequest(
            storage_backend="s3",
            s3_bucket="typo-bucket",
            s3_endpoint_url="http://127.0.0.1:9",
        )

        with pytest.raises(OperationError, match="^setup_remote_storage_unavailable$"):
            setup_storage.choose(db_session, unreachable)

        db_session.expire_all()
        assert setup_storage.choice_required(db_session.get(SystemConfig, 1)) is True
