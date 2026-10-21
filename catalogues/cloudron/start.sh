#!/bin/bash
# Cloudron start script.
#
# This runs as the image's unprivileged runtime user, because the image's own
# entrypoint has already repaired ownership of /app/data and applied migrations
# before handing over. Nothing here needs root.
#
# What Cloudron needs from a package and the image cannot know on its own is the
# app's own domain: the first-run host check accepts private addresses and local
# names, and every Cloudron app is served from a public FQDN behind the platform
# proxy. Naming that domain is what lets the owner register the first
# administrator in the browser.
set -Eeuo pipefail

export VAULT_SETUP_MODE="${VAULT_SETUP_MODE:-trusted_network}"
if [[ -z "${VAULT_SETUP_ALLOWED_HOSTS:-}" ]]; then
  VAULT_SETUP_ALLOWED_HOSTS="${CLOUDRON_APP_DOMAIN:-}"
  export VAULT_SETUP_ALLOWED_HOSTS
fi

# Caches and temporary files never touch the read-only layer.
mkdir -p "${HOME}" "${XDG_CACHE_HOME}" "${XDG_CONFIG_HOME}"

echo "PrintStash: starting on port 3000 (data root ${VAULT_DATA_ROOT}, setup mode ${VAULT_SETUP_MODE})"

# The supervised pair: uvicorn on 127.0.0.1:8000 behind the bundled Nginx on 3000.
# exec keeps SIGTERM reaching the supervisor, so Cloudron stops both processes.
exec /app/unified-run.sh
