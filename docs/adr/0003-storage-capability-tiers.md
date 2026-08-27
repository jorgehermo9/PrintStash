# ADR-0003: Storage capability tiers, and OpenDAL as an additive adapter

- **Status**: Proposed
- **Date**: 2026-08-27
- **Deciders**: maintainers
- **Tracking issue**: [#90](https://github.com/xiao-villamor/PrintStash/issues/90)

## Context

`StorageBackend` (`backend/app/services/storage_backend.py`) is not a
portability shim. Roughly a quarter of its surface implements one safety
protocol: **create-only writes carrying positive proof of which exact object
was created**, so that a rollback can never destroy bytes it did not write.
`CreationReceipt` is that proof, and each adapter earns it natively:

- **Local** stages through `mkstemp`, `fsync`s, publishes with
  `os.link(..., follow_symlinks=False)` (an atomic no-replace publication),
  `fsync`s the directory, and fingerprints the result as
  `(st_dev, st_ino, st_ctime_ns, st_size)`.
- **S3** issues `PutObject` with `IfNoneMatch="*"`, stamps a per-operation
  token into object metadata, and records the returned `VersionId`.

Self-hosters keep asking for storage PrintStash does not speak — Nextcloud over
WebDAV, a NAS over SFTP, occasionally a consumer drive. Writing an adapter per
protocol is not tractable for a project this size.

[Apache OpenDAL](https://opendal.apache.org/) is a Rust data-access layer with
Python bindings that speaks ~60 storage services behind one interface. The
question this ADR settles is not "is OpenDAL good" — it is **which part of the
storage layer may be expressed through a uniform interface, and which part must
not be.**

### What we verified

`write_with_if_not_exists` is declared by exactly **fs, s3, gcs, azblob, azdls,
oss, cos, ghac**. Since v0.52 write APIs return `Metadata` (etag, version,
last-modified) rather than `()`, though per-service population is still being
filled in under RFC-5556. `WriteOptions` carries `if_not_exists`, `if_match`,
`if_none_match`, `user_metadata`, `chunk`, and `concurrent`.

Sorted against what PrintStash would actually gain:

| | Backends |
|---|---|
| Conditional create **and** user metadata | `fs`, `s3` *(both already implemented by hand)*, `gcs`, `azblob`, `azdls`, `oss`, `cos` |
| Neither | `webdav`, `sftp`, `ftp`, `onedrive`, `googledrive`, `dropbox`, `pcloud`, `koofr`, `yandexdisk`, … |

The backends that arrive with full semantics are the three hyperscaler object
stores. The backends people actually ask for are in the second row. Any plan
that supports only the first row buys almost nothing.

## Decision

### 1. Union, not intersection: OpenDAL never becomes the only path to storage

**OpenDAL's API is the intersection of ~60 backends.** It models
path-addressed objects because that is what all of them share. PrintStash's
local guarantee is *stronger* than the object-store guarantee, so routing local
through a uniform interface would silently downgrade it. We want the **union**,
where each adapter contributes its strongest available primitive, and the tier
system (decision 2) makes the difference legible instead of hiding it.

The concrete loss, if local were migrated:

> *POSIX has no unlink-if-inode-still-matches primitive. A check followed by
> unlink has a TOCTOU window that could remove a newly mounted or concurrently
> replaced path.* — `LocalStorageBackend._quarantine_owned`

`_quarantine_owned` exists because **a local file cannot be safely deleted by
path**. It `os.replace`s the file into a random same-directory quarantine name,
re-verifies the fingerprint on the *moved* inode, and only then unlinks — so the
only inode ever destroyed is one proven to be ours. OpenDAL's `fs` service
offers `delete(path)`: path-addressed, no inode identity. `rollback_create`
would become a bare unlink with an open race, and if a self-hoster's Syncthing,
rsync, or backup tool replaced that path in the meantime, we would delete their
file.

The same applies to `adopt_existing` (`O_NOFOLLOW` plus matching `fstat`
snapshots around the hash — an object API has no descriptor to hold open),
`_fsync_directory`, `_assert_no_managed_escape`, and
`verify_destructive_access` (an `mkstemp` probe per parent directory, because
nested ACLs and read-only submounts differ beneath one configured root).

This matters **more** for local than for remote, not less: a self-hoster's
`/data/files` is a shared, multi-writer directory. An S3 prefix usually is not.

**Local is never migrated to OpenDAL.** Not in this change, not later.

### 2. Capability axes, with tiers derived from them

Not a `AtomicStorageBackend` / `BestEffortStorageBackend` hierarchy. Two
reasons:

1. `StorageBackend`'s own contract forbids what a hierarchy invites — *"Callers
   must never branch on the concrete backend type."* Named subclasses are an
   engraved invitation to `isinstance` checks at call sites.
2. It is not one bit. A backend can hold any subset: `fs` has conditional
   create and a real path but no durable version identity; `webdav` has atomic
   rename but no conditional create; `s3` has every guarantee but no path.

```python
class ObjectIdentity(StrEnum):
    """How a receipt binds to the exact bytes it was issued for."""
    INODE = "inode"      # local: (st_dev, st_ino, st_ctime_ns, st_size)
    VERSION = "version"  # object store with versioning: version_id
    ETAG = "etag"        # entity tag only — detects change, cannot address it
    NONE = "none"


@dataclass(frozen=True)
class StorageCapabilities:
    """What one bound adapter can actually promise. Computed once, at setup."""

    conditional_create: bool      # create fails rather than clobbers
    object_identity: ObjectIdentity
    verified_delete: bool         # can delete *only* the proven object
    conditional_replace: bool     # replace only while proof still holds
    namespace_ownership: bool     # can prove a key sits inside our root
    direct_path: bool             # a real Path exists (performance, not safety)

    @property
    def tier(self) -> StorageTier:
        if not self.conditional_create:
            return StorageTier.UNGUARDED
        if self.verified_delete and self.conditional_replace:
            return StorageTier.VERIFIED
        return StorageTier.GUARDED
```

The three tiers, each named for what it **guarantees**, not for what it lacks:

| Tier | Guarantee | Backends |
|---|---|---|
| **Verified** | A create never clobbers; a rollback destroys only our own bytes | local, versioned S3, versioned GCS/azblob |
| **Guarded** | A create never clobbers; a failed operation leaks bytes we cannot positively reclaim | **unversioned S3 (shipping today)**, unversioned GCS/azblob |
| **Unguarded** | Best effort. Concurrent writes to one key can lose data | webdav, sftp, ftp, consumer drives |

**The middle tier already ships.** `S3StorageBackend.rollback_create` returns
`False` and logs *"S3 object has no immutable version identity"* whenever the
bucket is unversioned. This ADR does not introduce a taxonomy — it names one
PrintStash has had since S3 support landed, and the OpenDAL adapter then slots
into a structure that already has a home for it.

Consequence worth stating plainly: the tier machinery is testable and shippable
against unversioned MinIO **before any OpenDAL code exists**.

`object_identity` stays an enum rather than collapsing into a boolean because
`INODE` and `VERSION` both reach Verified by different mechanisms, and
`/health` and support threads need to say which.

### 3. A DB intent ledger, which supplies conditional-create where the backend cannot

PrintStash already built this once, for a single path. `CaptureUploadSlot`
carries `state` (`PENDING`), `storage_key` **UNIQUE**, `sha256`, `size_bytes`
and `receipt_json`. Generalise it:

```python
class StorageObject(SQLModel, table=True):
    """Intent ledger: every key PrintStash means to own, recorded before write.

    The unique constraint on `key` is the conditional-create primitive for
    backends that have none of their own.
    """
    __tablename__ = "storage_objects"

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(max_length=2048, unique=True)
    state: StorageObjectState = Field(default=StorageObjectState.PENDING, index=True)
    backend: str = Field(max_length=32)
    namespace: str = Field(max_length=512)
    size_bytes: Optional[int] = None
    sha256: Optional[str] = Field(default=None, max_length=64, index=True)
    receipt_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    committed_at: Optional[datetime] = Field(default=None, index=True)
```

Three things it buys:

**(a) Conditional create on any backend.** Insert the row *before* writing
bytes; the unique constraint serialises two concurrent writers. Instances share
a database, so this holds for multi-instance deployments too. It does not stop
an *external* writer (Nextcloud's own web UI dropping a file at that path) —
which is exactly the residual risk the Unguarded warning must name.

**(b) An orphan sweep that substitutes for rollback.** `PENDING` rows older
than a threshold are orphans; a sweeper reclaims them. This recovers leaked
bytes on **every** tier, including unversioned S3 today, with no OpenDAL
involved. It is a weaker delete than `rollback_create` — by key, not by proof —
so it is gated: only keys this ledger created and never committed, and
`size_bytes` (plus etag where available) re-verified immediately before
deleting.

**(c) Content proof as a last-resort `creation_matches`.** With `sha256` and
`size_bytes` recorded, identity can be proven by re-reading and hashing on a
backend with no metadata at all. Absurd for a 400 MB mesh; entirely reasonable
for thumbnails and cover images. Therefore a per-keyspace option, never a
global one.

**The one correctness rule.** The database and the store are two systems and
the write is not atomic across them. Therefore: **always insert-then-write,
never write-then-insert.** Under that ordering every failure mode is an orphan
(safe, sweepable) and none is a clobber (unsafe). That ordering *is* the
correctness argument and belongs in a comment at the seam.

### 4. Unverified backends are opt-in at boot, via environment only

`ensure_setup()` refuses to bind an Unguarded adapter unless
`VAULT_STORAGE_ALLOW_UNVERIFIED=true`.

```python
def ensure_setup(self) -> None:
    caps = self.capabilities()
    if caps.tier is StorageTier.UNGUARDED and not settings.storage_allow_unverified:
        raise RuntimeError(
            f"storage backend {self.backend_name!r} cannot create objects without "
            "replacement. Set VAULT_STORAGE_ALLOW_UNVERIFIED=true to accept the "
            "consequences listed at <docs link>."
        )
```

Environment-only, not a runtime-config overlay value, for three reasons:

1. **Circularity.** The flag gates `ensure_setup()`, which runs at boot. A value
   the web UI can toggle cannot gate the thing that must already have booted to
   serve that UI.
2. **Blast radius.** A safety acknowledgement flippable from a web session is
   one compromised session away from being flipped.
   `runtime_config.update_storage` may keep choosing among *permitted*
   backends; it must not choose what is permitted.
3. **Deliberateness is the point.** Editing a deploy file is the act we want a
   self-hoster to perform.

The UI's role is display and discovery: show the derived tier and its
consequences read-only in Settings and `/health`, and grey out unverified
backends in the storage form with *"set `VAULT_STORAGE_ALLOW_UNVERIFIED=true`
to enable"* — discoverable without being toggleable.

### 5. Warnings are derived per axis, not per tier

A tier label is too coarse to act on. The operator-facing text is assembled
from the axes that are false, so it names the failure the operator will
actually meet:

| Axis absent | What the operator is told |
|---|---|
| `conditional_create` | Two simultaneous uploads of the same revision can silently overwrite each other. |
| `object_identity = none` | PrintStash cannot verify a file is the one it wrote; failed uploads leave files behind that need manual cleanup. |
| `verified_delete` | Interrupted uploads leak files; the orphan sweep reclaims them. |
| `namespace_ownership` | PrintStash cannot confirm a file is inside its own folder before deleting it. |

Honesty about severity is part of the design, and PrintStash's own keyspace
makes the honest version narrower than "atomicity is unavailable":

- `blob_key(slug, version, filename)` — a lost update loses a revision. Real.
- `stl_cache_key(sha256)` — content-addressed; a collision writes identical
  bytes. Benign.
- `thumbnail_key(file_id)` — overwriting is the intended behaviour. Benign.

Because the table is keyed by axis, it is directly testable: one case per axis,
asserted against each adapter's declared capabilities.

### 6. Hard-delete stays gated above the flag

`verify_destructive_access` and `_owned_namespace` exist because a wrong answer
during hard delete is permanent. On an adapter with
`namespace_ownership = False`, `services/storage_deletion` operates on faith.
Purge therefore requires explicit confirmation on a non-Verified backend **even
after** `VAULT_STORAGE_ALLOW_UNVERIFIED` is set. The blanket acknowledgement
covers ingest; it does not cover irreversible deletion.

### 7. OpenDAL is configured through typed settings, never a forwarded URL

```python
storage_backend: str = "local"                     # local | s3 | opendal
opendal_scheme: str = ""                           # webdav, azblob, gcs, sftp, …
opendal_root: str = ""                             # MANDATORY
opendal_options: str = "{}"                        # JSON, non-secret
opendal_secret_options: SecretStr = SecretStr("{}")  # JSON, never logged
```

Not a DSN (`VAULT_STORAGE_DSN=s3://key:secret@host/bucket?...`), because:

1. **Storage config is already overridden field-by-field.**
   `runtime_config.update_storage` takes discrete optional parameters and
   layers them into the ADR-0002 overlay. One opaque string cannot be rendered
   as a form, and partial updates become string surgery.
2. **Secrets.** A DSN embeds the key in the value that gets logged, echoed by
   `/health`, and written to the database. Splitting secret from non-secret
   options means both can be echoed verbatim with no redaction pass —
   `create_backend` already logs the bucket name today, which under a DSN would
   become a credential leak.
3. **Upstream URI mapping is not a stability contract we control.** The real
   interface is a scheme plus an options mapping; per-scheme URI key names live
   upstream. Self-hosters' `.env` files *are* our compatibility contract.
4. **Boot-time validation.** `Settings` validators already reject impossible
   combinations before anything runs; an opaque bag fails at first write.

`opendal_root` is mandatory and validated non-empty, because `walk_keys("")`
and `usage("")` drive `services/storage_deletion`. With an empty root on a
shared container, a purge would enumerate and delete data that is not ours.
Carry `f"{scheme}/{root}"` into `CreationReceipt.namespace`, exactly as the S3
adapter carries `f"{bucket}/{prefix}"`.

Capabilities are probed once in `ensure_setup()` from
`operator.capability()` and cached — never consulted per call, and never
trusted blindly (see Consequences).

### 8. Retire the two bucket-administration writes

Independent of OpenDAL, and worth doing on its own merits:

- **`_apply_lifecycle_policy` — remove.** It writes an `Expiration` rule onto
  the user's bucket: the application configuring automatic deletion of the
  user's data. It directly contradicts `destructive_lifecycle_findings`, whose
  entire purpose is to *warn* about such rules — one method installs the hazard
  the other reports. It also forces `s3:PutLifecycleConfiguration` into the
  credential we ask self-hosters to create. Document the recommended rule
  instead; let operators apply it in their provider's console.
- **`_ensure_bucket` — remove the create half.** Auto-create requires
  `s3:CreateBucket`, over-privileging the credential, and *succeeds* against a
  mistyped endpoint or wrong region by creating a bucket the operator never
  intended. Replace with a probe that fails loudly.
- **`destructive_lifecycle_findings` — keep.** Read-only, needs only
  `s3:GetLifecycleConfiguration`, already degrades to `[]` on any error, and it
  is the one that protects users.

This shrinks the boto3 surface to a single optional read-only call, which is
what makes a later S3 data-plane migration a real option rather than a
dependency shuffle.

### 9. Sequencing: the S3 data plane migrates last, or not at all

OpenDAL's S3 service can express the whole data plane — `if_not_exists`,
`if_match`, `user_metadata`, `chunk`/`concurrent` for the multipart threshold,
`delete` with a version, presigned reads. Migration is therefore *viable* once
decision 8 lands. It is deliberately **not** part of this work:
`S3StorageBackend` is tested, in the field, and the most safety-critical code in
the repository. It migrates only after the OpenDAL adapter has a release of real
use behind it, and only if unification then looks worth the churn.

## Consequences

**Gained**

- WebDAV/Nextcloud, SFTP-to-NAS, Azure, GCS, and the consumer drives become
  reachable, each labelled with what it does and does not guarantee.
- Unversioned S3 stops being an unnamed middle case and gets honest reporting.
- The orphan sweep reclaims leaked bytes on backends we already support.
- One place decides tier and warnings, so `/health`, Settings, and the boot log
  cannot drift apart.

**Accepted costs**

- A Rust native extension enters the wheel set. Mitigated by `opendal` being an
  optional extra: a self-hoster on local or S3 never installs it.
- Three adapters to maintain instead of two.
- The Unguarded tier means PrintStash ships a configuration in which concurrent
  writes can lose data. That is the deliberate trade, bounded by the ledger, the
  boot gate, and the delete gate.

**Risks**

- **A declared capability can be wrong.** `write_with_if_not_exists` has shipped
  as declared-but-unenforced, reported against azblob *and* s3/MinIO. Therefore
  capabilities are never trusted on declaration alone: the `contract/` tier must
  prove conditional create against a real server per supported scheme, and the
  adapter pins an exact `opendal` version.
- **Write-returns-metadata coverage is still landing per service.** If a scheme
  does not populate `version_id`, it lands in Guarded rather than Verified —
  degradation by design, not breakage, but it means the tier of a given scheme
  can improve across upstream releases and must be reported at runtime rather
  than documented as a constant.
- **Ledger drift.** A ledger that disagrees with the store causes a sweep to
  target a live object. Mitigated by insert-then-write ordering, by
  re-verification immediately before any sweep delete, and by the sweep being
  restricted to never-committed rows.

## Alternatives considered

**Replace both adapters with OpenDAL.** Rejected: decision 1. It downgrades the
default backend's guarantees to the intersection of sixty services.

**Support only backends with full semantics.** Rejected: it yields Azure, GCS,
and azdls — none of which the people asking are asking for — while excluding
every backend they are. The support question is the whole value.

**Warn at runtime instead of gating at boot.** Rejected: a warning after the
fact is read by nobody, and the failure it warns about is silent.

**Model tiers as an `AtomicStorageBackend` / `BestEffort` class hierarchy.**
Rejected: decision 2. It invites the `isinstance` branching the base class
explicitly forbids, and two classes cannot express six independent axes.

**Forward an OpenDAL URI from configuration.** Rejected: decision 7.

## Testing

Docs-only change; no coverage matrix applies to this PR. The implementation
work it authorises carries these obligations:

- `unit/` — tier derivation: one case per axis combination, plus one per
  warning-table row.
- `integration/` — the ledger's insert-then-write ordering, the orphan sweep's
  refusal to touch committed rows, and the boot gate's refusal without the flag.
- `contract/` — each supported OpenDAL scheme against a real server over a
  loopback socket, proving conditional create actually rejects a second write.
  This is the mitigation for the declared-but-unenforced risk; it is not
  optional.
- `e2e/` — one upload through an OpenDAL-backed vault.
