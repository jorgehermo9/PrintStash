from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from printstash_core.printers.contracts import PrinterClient
from printstash_core.printers.models import OctoPrintConfig, PrinterSnapshot
from printstash_core.printers.octoprint import (
    OctoPrintClient,
    OctoPrintError,
    OctoPrintFactory,
)


def _client(handler: Any) -> OctoPrintClient:
    return OctoPrintClient(
        OctoPrintConfig("http://octopi.local/", "key-123"),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_status_wire_shape_and_neutral_snapshot_are_exact() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "key-123"
        if request.url.path == "/api/printer":
            return httpx.Response(
                200,
                json={
                    "state": {"text": "Printing", "flags": {"printing": True}},
                    "temperature": {
                        "bed": {"actual": 59.5, "target": 60},
                        "tool0": {"actual": 214, "target": 215},
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "job": {"file": {"name": "cube.gcode"}},
                "progress": {"completion": 25.0, "printTime": 120},
            },
        )

    client = _client(handler)
    legacy = await client.query_status()
    snapshot = await client.query_snapshot()

    assert legacy == {
        "result": {
            "status": {
                "print_stats": {
                    "state": "printing",
                    "filename": "cube.gcode",
                    "message": "Printing",
                    "print_duration": 120,
                },
                "virtual_sdcard": {"progress": 0.25},
                "heater_bed": {"temperature": 59.5, "target": 60},
                "extruder": {"temperature": 214, "target": 215},
            }
        }
    }
    assert snapshot == PrinterSnapshot.from_legacy_payload(legacy)


@pytest.mark.asyncio
async def test_file_operations_preserve_streaming_and_nested_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/files":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "name": "folder",
                            "type": "folder",
                            "children": [
                                {
                                    "name": "cube.gcode",
                                    "path": "folder/cube.gcode",
                                    "type": "machinecode",
                                }
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(200, json={})

    source = tmp_path / "cube.gcode"
    source.write_bytes(b"G28\n" * 100)

    def forbid_read_bytes(_path: Path) -> bytes:
        raise AssertionError("upload must stream from an open file")

    monkeypatch.setattr(Path, "read_bytes", forbid_read_bytes)
    client = _client(handler)
    assert [item["path"] for item in await client.list_files()] == ["folder/cube.gcode"]
    await client.upload(source, "sub/dir/cube.gcode")
    await client.start("sub/dir/cube.gcode")
    await client.delete_file("sub/dir/cube.gcode")
    await client.pause()
    await client.resume()
    await client.cancel()

    upload = next(item for item in seen if item[1] == "/api/files/local")
    assert b'name="path"' in upload[2]
    assert b"sub/dir" in upload[2]
    assert ("POST", "/api/files/local/sub/dir/cube.gcode") in {
        (method, path) for method, path, _body in seen
    }


@pytest.mark.asyncio
async def test_error_codes_and_legacy_payloads_remain_stable() -> None:
    forbidden = _client(lambda _request: httpx.Response(403))
    with pytest.raises(OctoPrintError) as auth_error:
        await forbidden.info()
    assert auth_error.value.code == "provider_authentication_failed"

    conflict = _client(lambda _request: httpx.Response(409))
    with pytest.raises(OctoPrintError) as job_error:
        await conflict.pause()
    assert job_error.value.code == "provider_no_active_job"

    no_content = _client(lambda _request: httpx.Response(204))
    assert await no_content.cancel() == {"ok": True}
    with pytest.raises(OctoPrintError) as unsupported:
        await no_content.run_gcode("G28")
    assert unsupported.value.code == "operation_not_supported_for_provider"


@pytest.mark.asyncio
async def test_legacy_subscription_adapts_to_snapshot_callback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/printer":
            return httpx.Response(200, json={"state": {"flags": {}}})
        return httpx.Response(200, json={"job": {}, "progress": {}})

    stop = asyncio.Event()
    stop.set()
    snapshots: list[PrinterSnapshot] = []

    async def receive(snapshot: PrinterSnapshot) -> None:
        snapshots.append(snapshot)

    await _client(handler).subscribe_snapshots(receive, stop_event=stop)
    assert snapshots[0].state == "standby"


def test_factory_builds_runtime_protocol_client() -> None:
    client = OctoPrintFactory().build(OctoPrintConfig("http://octoprint.local", "key"))
    assert isinstance(client, PrinterClient)


class TestRequestErrorMapping:
    """Every failure gets a stable code, because callers branch on it."""

    @pytest.mark.parametrize(
        ("status", "code"),
        [
            pytest.param(401, "provider_authentication_failed", id="unauthorized"),
            pytest.param(403, "provider_authentication_failed", id="forbidden"),
            pytest.param(404, "provider_endpoint_not_supported", id="not-found"),
            pytest.param(409, "provider_no_active_job", id="conflict"),
            pytest.param(500, "provider_transport_error", id="server-error"),
            pytest.param(418, "provider_transport_error", id="unexpected-4xx"),
        ],
    )
    @pytest.mark.asyncio
    async def test_maps_a_status_to_its_code(self, status: int, code: str) -> None:
        client = _client(lambda _request: httpx.Response(status))

        # `401`/`403` share a code because both mean "fix your credentials", and
        # that code is what triggers exactly one prompt rather than a retry loop.
        # `409` is deliberately *not* a fault: it means there is no active job.
        with pytest.raises(OctoPrintError) as caught:
            await client.info()

        assert caught.value.code == code

    @pytest.mark.asyncio
    async def test_reports_a_timeout_as_its_own_code(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("too slow")

        client = _client(handler)

        # A printer that is slow is a different problem from one that refuses,
        # and the UI says different things about them.
        with pytest.raises(OctoPrintError) as caught:
            await client.info()

        assert caught.value.code == "provider_timeout"

    @pytest.mark.asyncio
    async def test_reports_a_transport_failure(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        client = _client(handler)

        with pytest.raises(OctoPrintError):
            await client.info()

    @pytest.mark.asyncio
    async def test_treats_an_empty_body_as_success(self) -> None:
        client = _client(lambda _request: httpx.Response(204))

        # OctoPrint answers several actions with 204. That is a success, and
        # turning it into an error would fail every command that worked.
        assert await client.info() == {
            "result": {"provider": "octoprint", "version": {"ok": True}}
        }

    @pytest.mark.asyncio
    async def test_reports_a_body_that_is_not_json(self) -> None:
        client = _client(
            lambda _request: httpx.Response(200, text="<html>proxy</html>")
        )

        # A reverse proxy in front of the printer answers with HTML when it is
        # unhappy; that must not surface as a parser traceback.
        with pytest.raises(OctoPrintError) as caught:
            await client.info()

        assert caught.value.code == "provider_invalid_response"


class TestFilePath:
    @pytest.mark.parametrize(
        "remote_filename",
        [
            pytest.param("/absolute.gcode", id="absolute"),
            pytest.param("../escape.gcode", id="traversal"),
            pytest.param("a/../b.gcode", id="traversal-mid-path"),
            pytest.param("", id="empty"),
        ],
    )
    def test_refuses_a_name_that_could_escape_the_upload_root(
        self, remote_filename: str
    ) -> None:
        # The name reaches the printer as a URL path. A traversal here targets
        # somebody else's file on the printer's own storage.
        with pytest.raises(OctoPrintError) as caught:
            OctoPrintClient._file_path(remote_filename)

        assert caught.value.code == "provider_error"

    def test_encodes_each_segment_of_an_ordinary_name(self) -> None:
        # Per-segment: the slash stays a separator and the space does not turn
        # the filename into two directories.
        assert (
            OctoPrintClient._file_path("folder/my part.gcode")
            == "folder/my%20part.gcode"
        )

    @pytest.mark.parametrize(
        ("remote_filename", "expected"),
        [
            pytest.param("./here.gcode", "here.gcode", id="leading-dot-segment"),
            pytest.param("a//b.gcode", "a/b.gcode", id="doubled-separator"),
        ],
    )
    def test_normalises_a_redundant_segment_rather_than_refusing_it(
        self, remote_filename: str, expected: str
    ) -> None:
        # `PurePosixPath` collapses these before the guard sees them, so they are
        # accepted in their normalised form. That is the safe outcome — neither
        # can escape the root — and refusing them would reject filenames real
        # slicers and users produce.
        assert OctoPrintClient._file_path(remote_filename) == expected


class TestStatusStateMapping:
    @pytest.mark.parametrize(
        ("flags", "completion", "expected"),
        [
            pytest.param({"printing": True}, 12.0, "printing", id="printing"),
            pytest.param({"paused": True}, 40.0, "paused", id="paused"),
            pytest.param({"pausing": True}, 40.0, "paused", id="pausing"),
            pytest.param({"cancelling": True}, 40.0, "cancelled", id="cancelling"),
            pytest.param({"error": True}, 0.0, "error", id="error"),
            pytest.param({"closedOrError": True}, 0.0, "error", id="closed-or-error"),
            pytest.param({}, 100.0, "complete", id="complete-at-100"),
            pytest.param({}, 99.9, "complete", id="complete-at-boundary"),
            pytest.param({}, 99.8, "standby", id="below-boundary-is-standby"),
            pytest.param({}, None, "standby", id="no-completion"),
            pytest.param({"printing": True}, 100.0, "printing", id="finishing"),
        ],
    )
    @pytest.mark.asyncio
    async def test_derives_the_state_from_flags_and_completion(
        self, flags: dict, completion: float | None, expected: str
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/printer":
                return httpx.Response(200, json={"state": {"flags": flags}})
            return httpx.Response(
                200,
                json={
                    "job": {"file": {"name": "cube.gcode"}},
                    "progress": {"completion": completion},
                },
            )

        client = _client(handler)
        snapshot = await client.query_snapshot()

        # `finishing` is the case the flags exist for: 100% *with* `printing`
        # still set is a print about to end, not one that ended. Reading the
        # percentage alone closes the job record while the nozzle is moving.
        assert snapshot.state == expected

    @pytest.mark.asyncio
    async def test_reports_standby_for_a_printer_with_no_file(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/printer":
                return httpx.Response(200, json={"state": {"flags": {}}})
            return httpx.Response(200, json={"job": {}, "progress": {}})

        client = _client(handler)

        assert (await client.query_snapshot()).state == "standby"

    @pytest.mark.asyncio
    async def test_survives_a_response_whose_fields_are_the_wrong_type(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/printer":
                return httpx.Response(200, json={"state": "not-an-object"})
            return httpx.Response(200, json={"job": [], "progress": "nope"})

        client = _client(handler)

        # A provider that answers with the wrong shape must not take the poll
        # loop down; the snapshot degrades instead.
        assert (await client.query_snapshot()).state == "standby"
