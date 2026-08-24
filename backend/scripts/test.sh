#!/usr/bin/env bash
set -euo pipefail

lane="${1:-fast}"
if (( $# > 0 )); then
  shift
fi

pytest_args=("$@")
has_target=false
for arg in "${pytest_args[@]}"; do
  if [[ "$arg" == *::* || -e "$arg" ]]; then
    has_target=true
    break
  fi
done
if [[ "$has_target" == false ]]; then
  pytest_args=(tests "${pytest_args[@]}")
fi

parallel=(-n auto --dist worksteal)

case "$lane" in
  fast)
    # The normal feature loop: in-process tests, excluding service/schema
    # boundaries, real files, and the real-app E2E layer.
    exec uv run pytest "${parallel[@]}" -m "not integration and not e2e" "${pytest_args[@]}"
    ;;
  affected)
    # First use seeds .testmondata; later runs select tests whose executed
    # Python lines changed. Never use this lane as the only pre-merge gate.
    exec uv run pytest "${parallel[@]}" --testmon "${pytest_args[@]}"
    ;;
  integration)
    exec uv run pytest "${parallel[@]}" -m integration "${pytest_args[@]}"
    ;;
  e2e)
    exec uv run pytest "${parallel[@]}" -m e2e "${pytest_args[@]}"
    ;;
  full)
    exec uv run pytest "${parallel[@]}" "${pytest_args[@]}"
    ;;
  serial)
    exec uv run pytest "${pytest_args[@]}"
    ;;
  *)
    echo "usage: $0 {fast|affected|integration|e2e|full|serial} [pytest arguments...]" >&2
    exit 2
    ;;
esac
