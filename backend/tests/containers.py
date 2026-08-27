"""Resolving the two real services a slice of this suite needs: PostgreSQL and S3.

Both are gated by an environment variable today, which means a contributor
running `./scripts/test.sh full` locally gets a green result with **21 tests
silently skipped** — and they are not incidental ones. They are the
dialect-sensitive SQL, the migration path a self-hoster upgrades through, and the
S3 storage and backup destinations. A local run that skips those is not the suite
CI runs, so "it passed for me" and "it passed in CI" stop meaning the same thing.

So the resolution order here is: **the environment variable if it is set, else a
throwaway container, else skip.**

*Environment variable first* because CI already provides one. GitHub's
`services:` block starts PostgreSQL in parallel with checkout from the runner's
image cache; starting it from inside the step instead would add wall clock to
every run and gain nothing. The SeaweedFS command is pinned to a digest and given
a development-sized volume limit, which is worth keeping explicit in the workflow
where an operator can read it.

*A container second* so the local run matches CI without anybody having to
remember two `docker run` invocations and two exports.

*Skip last, and only when Docker is genuinely absent.* The skip reason says so,
rather than naming an environment variable the contributor has no reason to know
about.

Containers are started lazily — on the first test that needs one, not at
collection — and torn down once at session end. A run that touches neither
resource starts nothing.
"""

from __future__ import annotations

import os
from typing import Any, Callable

# SeaweedFS in `mini` mode: master, volume and S3 gateway in one process. Pinned by
# digest and given the same development-sized volume limit as CI, so a failure here
# and a failure there are the same failure.
SEAWEEDFS_IMAGE = (
    "chrislusf/seaweedfs:4.41"
    "@sha256:43b768cd62b00d132439cda881b93fd1adebf1b315e996e794087743821d771d"
)
SEAWEEDFS_S3_PORT = 8333
POSTGRES_IMAGE = "postgres:16-alpine"

S3_ACCESS_KEY = os.environ.get("PRINTSTASH_TEST_S3_ACCESS_KEY", "printstash")
S3_SECRET_KEY = os.environ.get("PRINTSTASH_TEST_S3_SECRET_KEY", "printstash-secret")

_started: list[Any] = []
_resolved: dict[str, str | None] = {}


def docker_available() -> bool:
    """Whether a Docker daemon is reachable, without raising if it is not.

    Checked rather than assumed because the alternative is turning a clean skip
    into an import error on every machine without Docker — including the CI jobs
    that deliberately run without one.
    """
    try:
        from testcontainers.core.docker_client import DockerClient
    except ImportError:
        return False
    try:
        DockerClient().client.ping()
    except Exception:
        return False
    return True


def _resolve(key: str, env_var: str, start: Callable[[], str]) -> str | None:
    """Return the configured URL for *key*, starting a container if we must."""
    if key in _resolved:
        return _resolved[key]
    configured = os.environ.get(env_var)
    if configured:
        _resolved[key] = configured
        return configured
    if not docker_available():
        _resolved[key] = None
        return None
    url = start()
    # Exported so any code still reading the variable directly agrees with the
    # fixtures, and so a subprocess the tests spawn inherits the same endpoint.
    os.environ[env_var] = url
    _resolved[key] = url
    return url


def _start_postgres() -> str:
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer(
        POSTGRES_IMAGE,
        username="printstash",
        password="printstash",
        dbname="printstash",
    )
    container.start()
    _started.append(container)
    # `psycopg2` is the driver testcontainers advertises; the app normalises the
    # scheme itself, so hand on the plain `postgresql://` form it understands.
    return container.get_connection_url(driver=None)


def _start_seaweedfs() -> str:
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = (
        DockerContainer(SEAWEEDFS_IMAGE)
        .with_env("AWS_ACCESS_KEY_ID", S3_ACCESS_KEY)
        .with_env("AWS_SECRET_ACCESS_KEY", S3_SECRET_KEY)
        .with_exposed_ports(SEAWEEDFS_S3_PORT)
        .with_command(
            "mini -dir=/data -master.volumeSizeLimitMB=64 -master.telemetry=false"
        )
    )
    container.start()
    _started.append(container)
    # The S3 gateway logs this once it is accepting requests. Waiting on the log
    # rather than on the port avoids the race where the port is bound before the
    # gateway can answer, which presents as a connection reset on the first call.
    wait_for_logs(container, "Start Seaweed S3 API", timeout=60)
    host = container.get_container_host_ip()
    port = container.get_exposed_port(SEAWEEDFS_S3_PORT)
    return f"http://{host}:{port}"


def postgres_url() -> str | None:
    """A real PostgreSQL URL, or `None` when there is no way to get one."""
    return _resolve("postgres", "PRINTSTASH_TEST_POSTGRES_URL", _start_postgres)


def s3_endpoint() -> str | None:
    """A real S3-compatible endpoint URL, or `None` when there is none."""
    return _resolve("s3", "PRINTSTASH_TEST_S3_ENDPOINT", _start_seaweedfs)


def unavailable_reason(env_var: str, resource: str) -> str:
    """Why *resource* is being skipped, in terms the reader can act on."""
    if docker_available():
        return f"could not start {resource}; set {env_var} to point at your own"
    return f"needs {resource}: start Docker, or set {env_var}"


def shutdown_containers() -> None:
    """Stop whatever was started, once, at the end of the session."""
    while _started:
        container = _started.pop()
        try:
            container.stop()
        except Exception:
            # A container that already died takes nothing with it, and raising
            # here would turn a clean run into a session-teardown error.
            pass
