#!/usr/bin/env bash
#
# Test lanes. A lane is a directory (the tier) plus, at most, a marker that gates a
# subset needing a resource — never a filename heuristic. `./scripts/test.sh --help`
# prints the table below.
set -euo pipefail

usage() {
  cat <<'EOF'
usage: ./scripts/test.sh [lane] [pytest arguments...]

Lanes
  fast       tests/unit + tests/integration, minus `slow`.        (default)
             The feature loop: real SQLite, real routers, no sockets.
  contract   tests/contract — our clients against contract-enforcing fakes
             over a real loopback socket. Needs no external services;
             `s3`-marked cases skip themselves without an endpoint.
  e2e        tests/e2e — the whole app over ASGITransport plus the fakes.
  full       everything, including `slow`. What CI gates on.
  affected   `--testmon`: only tests whose executed lines changed. Seed it with
             one full run first, and never use it as the only pre-merge gate.
  serial     `full` without xdist. For debugging an ordering or state bug.

Anything after the lane goes to pytest, so a path or `-k` still works:
  ./scripts/test.sh fast tests/unit/services/test_gcode_parser.py
  ./scripts/test.sh full -k "trash and not slow" -x

The `postgres`- and `s3`-marked subsets run against a real PostgreSQL and a real
SeaweedFS, started as containers for the run (see backend/tests/containers.py).
`full` therefore needs Docker running and stops with a message if it is not —
skipping them would report a green run that verified none of them. `fast` needs
nothing.
EOF
}

lane="${1:-fast}"
if (( $# > 0 )); then
  shift
fi

case "$lane" in
  -h|--help|help)
    usage
    exit 0
    ;;
esac

pytest_args=("$@")
has_target=false
for arg in "${pytest_args[@]:-}"; do
  if [[ "$arg" == *::* || -e "$arg" ]]; then
    has_target=true
    break
  fi
done

parallel=(-n auto --dist worksteal)

# Prepend the lane's paths only when the caller did not name a target of their own.
lane_paths=()
add_paths() {
  if [[ "$has_target" == false ]]; then
    lane_paths=("$@")
  fi
}

case "$lane" in
  fast)
    add_paths tests/unit tests/integration
    exec uv run pytest "${parallel[@]}" -m "not slow" "${lane_paths[@]}" "${pytest_args[@]}"
    ;;
  contract)
    add_paths tests/contract
    exec uv run pytest "${parallel[@]}" "${lane_paths[@]}" "${pytest_args[@]}"
    ;;
  e2e)
    add_paths tests/e2e
    exec uv run pytest "${parallel[@]}" "${lane_paths[@]}" "${pytest_args[@]}"
    ;;
  full)
    add_paths tests
    exec uv run pytest "${parallel[@]}" "${lane_paths[@]}" "${pytest_args[@]}"
    ;;
  affected)
    add_paths tests
    exec uv run pytest "${parallel[@]}" --testmon "${lane_paths[@]}" "${pytest_args[@]}"
    ;;
  serial)
    add_paths tests
    exec uv run pytest "${lane_paths[@]}" "${pytest_args[@]}"
    ;;
  *)
    echo "unknown lane: $lane" >&2
    echo >&2
    usage >&2
    exit 2
    ;;
esac
