PrintStash is installed and running. Two steps are yours:

1. Open the app at `$CLOUDRON-APP-ORIGIN` and register the first administrator.
   Registration only works from this app's own domain, which the package names
   for you, so open it from the Cloudron dashboard rather than an IP address.
2. Choose where PrintStash stores files. The default, the app's own data
   directory, is the one Cloudron backs up. Pointing it at an existing folder
   indexes that folder in place instead of copying it.

Everything PrintStash keeps lives in one directory, so a Cloudron backup
captures the database, the library, thumbnails and local backups together, and
a restore brings all of them back.

Optional, with `cloudron env set` and a restart:

- `VAULT_SETUP_MODE=environment` with `VAULT_SETUP_ADMIN_USERNAME` and
  `VAULT_SETUP_ADMIN_PASSWORD` creates the administrator at first start instead,
  which leaves no window for anyone else to claim a fresh install.
- `VAULT_COMPUTE_MEMORY_MB` and the two budget fractions bound how much a GPU
  and the AI search models may use.
