"""Builders for the fleet: printers, their remote files, and print jobs.

`Printer` is the most-built row in the suite and the easiest to build *wrongly*.
One table carries the credentials for all five providers, and every field is
nullable, so a printer with `provider="bambu_lan"` and no `bambu_host` inserts
happily and then fails somewhere far away — inside a dispatch, or as a
`provider_config_mismatch` from a factory three layers down. That is a whole
class of confusing test failure, and it is what `build_printer(provider=...)`
exists to prevent: name the provider and the credential set that provider needs
is filled in.

The values are obviously-fake placeholders. A printer access code is a real
credential — nothing here may resemble one.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.db.models import (
    File,
    Printer,
    PrinterFile,
    PrinterProvider,
    PrinterStatus,
    PrintJob,
    PrintJobState,
)
from tests.factories._support import nth, reject_aliases, save

# Per-provider connection details. Keyed by the same enum the app dispatches on,
# so adding a provider to `PrinterProvider` without adding it here is a KeyError
# in the builder rather than a printer row that silently cannot connect.
_PROVIDER_FIELDS: dict[PrinterProvider, dict[str, Any]] = {
    PrinterProvider.MOONRAKER: {
        "moonraker_url": "http://printer.invalid:7125",
        "api_key": "not-a-real-api-key",
    },
    PrinterProvider.BAMBU_LAN: {
        "bambu_host": "printer.invalid",
        "bambu_serial": "FAKESERIAL0001",
        "bambu_access_code": "00000000",
    },
    PrinterProvider.PRUSALINK: {
        "prusalink_url": "http://printer.invalid",
        "prusalink_auth_mode": "digest",
        "prusalink_username": "maker",
        "prusalink_password": "not-a-real-password",
    },
    # The variant is part of the credential set here, not cosmetic: the client
    # factory only accepts a variant it has an implementation for, and the
    # second-generation one additionally requires an access code.
    PrinterProvider.ELEGOO_CENTAURI: {
        "elegoo_centauri_host": "printer.invalid",
        "provider_variant": "elegoo_centauri_carbon",
    },
    PrinterProvider.OCTOPRINT: {
        "octoprint_url": "http://printer.invalid",
        "octoprint_api_key": "not-a-real-api-key",
    },
}


def build_printer(
    session: Session,
    name: str | None = None,
    *,
    provider: PrinterProvider = PrinterProvider.MOONRAKER,
    status: PrinterStatus = PrinterStatus.READY,
    trashed: bool = False,
    **overrides: Any,
) -> Printer:
    """A configured printer of *provider*, ready to accept a job.

    The credentials that provider requires are filled in unless the test names
    them. To test a *mis*configured printer, pass the field explicitly as `None`
    — that reads as the deliberate omission it is:

        build_printer(session, provider=PrinterProvider.BAMBU_LAN,
                      bambu_access_code=None)

    `status` defaults to `READY` because an offline printer is skipped by
    dispatch, so a test that forgets it ends up asserting against a fleet with
    nothing in it.
    """
    reject_aliases(overrides, {"deleted_at": "trashed"} if trashed else {})
    fields = dict(_PROVIDER_FIELDS[provider])
    fields.update(overrides)
    if trashed:
        from app.core.time import utcnow

        fields.setdefault("deleted_at", utcnow())
    return save(
        session,
        Printer(
            name=name or f"Printer {nth('printer')}",
            provider=provider,
            status=status,
            **fields,
        ),
    )


def build_printer_file(
    session: Session,
    printer: Printer,
    *,
    file: File | None = None,
    remote_filename: str | None = None,
    **overrides: Any,
) -> PrinterFile:
    """A file the printer reports having on its own storage.

    `file` links it to a library artifact; leaving it `None` is the real and
    interesting case of a file somebody put on the printer by SD card, which the
    library knows about but does not own.
    """
    index = nth("printer_file")
    if file is not None:
        overrides.setdefault("file_id", file.id)
        overrides.setdefault("sha256", file.sha256)
        overrides.setdefault("matched_by", "sha256")
    return save(
        session,
        PrinterFile(
            printer_id=printer.id,
            remote_filename=remote_filename or f"remote-{index}.gcode",
            **overrides,
        ),
    )


def build_print_job(
    session: Session,
    file: File,
    *,
    printer: Printer | None = None,
    state: PrintJobState = PrintJobState.QUEUED,
    **overrides: Any,
) -> PrintJob:
    """A print job for *file*.

    `model_id` is derived from the artifact rather than asked for: a job whose
    model does not own its file is a state the app cannot produce, and a test that
    accidentally builds one gets confusing results from every read path that
    joins the two.
    """
    if printer is not None:
        overrides.setdefault("printer_id", printer.id)
        overrides.setdefault("printer_name", printer.name)
    overrides.setdefault("remote_filename", file.original_filename)
    return save(
        session,
        PrintJob(
            file_id=file.id,
            model_id=file.model_id,
            state=state,
            **overrides,
        ),
    )
