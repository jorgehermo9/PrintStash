"""Giving a finished mesh's memory back without paying for a process start.

Every mesh worker reclaims after its geometry. Looking libc up by name runs
ldconfig in a subprocess, once per worker, so the reclaim uses the C library
the process already has loaded.
"""

import subprocess

from app.modules.media import mesh_policy


class TestReclaimMemory:
    def test_reclaims_without_starting_a_process(self, monkeypatch) -> None:
        def refuse(*_args, **_kwargs):
            raise AssertionError("reclaim started a process")

        monkeypatch.setattr(mesh_policy, "_LIBC", None)
        monkeypatch.setattr(subprocess.Popen, "__init__", refuse)

        mesh_policy.reclaim_memory()
