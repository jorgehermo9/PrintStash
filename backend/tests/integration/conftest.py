"""``tests/integration/`` — the real app in this process, no egress.

The default tier. SQLite with the production pragmas, real routers, real services, real
storage backend, real RBAC, real fixture files. The only things stood in for are the
outbound boundaries, and only by injection or by patching ``get_http_client`` where it is
used. The socket guard below makes that structural: a real connection fails the test, so a
test that needs one is a contract test and belongs in ``tests/contract/``.
"""

from __future__ import annotations

from tests._guards import block_real_network  # noqa: F401 — autouse
