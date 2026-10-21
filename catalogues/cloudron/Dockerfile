# Cloudron package for PrintStash.
#
# Cloudron builds this on the server (or with `cloudron builder`) and runs it with
# a read-only filesystem, so the image is the published one and nothing else: the
# release image already contains the web UI, the API, Nginx and the entrypoint
# that repairs data ownership and applies migrations. This Dockerfile only adds
# the pieces Cloudron needs.
#
# Pinned to the release tag; the catalogue tests read this line to keep every
# listing on the version the release commit bumps.
FROM ghcr.io/xiao-villamor/printstash:0.14.0

# One data root, and it is the only path Cloudron keeps and backs up. The image's
# entrypoint creates and repairs ownership of every app-owned child directory
# (db, files, thumbs, staging, backups, artifact-cache, ai-models) here.
ENV VAULT_DATA_ROOT=/app/data

# First-run settings. Cloudron has no install form and injects no credentials, so
# the owner registers in the browser, the same door CasaOS and Unraid use. The
# host check that protects that door only accepts private addresses and local
# names, so the app's own Cloudron domain has to be named explicitly; start.sh
# fills it from CLOUDRON_APP_DOMAIN. Set these with `cloudron env set` to create
# the administrator from the environment instead.
ENV VAULT_SETUP_MODE=trusted_network \
    VAULT_SETUP_ADMIN_USERNAME="" \
    VAULT_SETUP_ADMIN_PASSWORD="" \
    VAULT_SETUP_ADMIN_EMAIL=""

# A Settings restart is a graceful exit; Cloudron's restart policy relaunches the
# container, which re-runs migrations and both supervised processes.
ENV VAULT_RESTART_ENABLED=true

# Compute budgets (docs/compute.md). A GPU is used when Cloudron gives the
# container one; otherwise the CPU does the work.
ENV VAULT_COMPUTE_MODE=auto \
    VAULT_COMPUTE_MEMORY_MB=1024 \
    VAULT_MESH_MEMORY_BUDGET_FRACTION=0.5 \
    VAULT_EMBEDDING_MEMORY_BUDGET_FRACTION=0.25

# Caches and homes belong on the writable paths, never on the read-only layer.
ENV HOME=/tmp/printstash-home \
    XDG_CACHE_HOME=/tmp/printstash-cache \
    XDG_CONFIG_HOME=/tmp/printstash-config \
    TMPDIR=/tmp

RUN mkdir -p /app/code

COPY --chmod=0755 start.sh /app/code/start.sh

CMD [ "/app/code/start.sh" ]
