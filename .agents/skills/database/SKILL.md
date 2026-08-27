# Database

Invoke before any schema change, migration, or query that touches soft-deleted rows.
SQLite is the default installation and PostgreSQL is optional, so every rule here
has to hold on both.

## Migrations

### Never hand-write one

Generate it:

```bash
cd backend && uv run alembic revision --autogenerate -m "add durable capture slots"
```

Then read the result, delete what it got wrong, and keep the rest. Hand-writing is
how this repo ended up with two different schemas at the same `head`.

`alembic/env.py` passes `render_as_batch=True` for SQLite, which makes autogenerate
wrap operations in `with op.batch_alter_table(...)`. That is a **rendering** flag: it
shapes what `alembic revision --autogenerate` writes into the file and does nothing
for a migration you type yourself. A hand-written `op.create_foreign_key` is a plain
`ALTER TABLE … ADD CONSTRAINT` whatever `env.py` says — and SQLite has no such
statement, so it fails with `near "FOREIGN": syntax error`.

That is exactly what happened in `69b6a6d8a1d1`, and the author resolved it with
`if not is_sqlite:` around the constraint. The column landed on every dialect and the
constraint on none of the SQLite ones. Eighteen foreign keys the models declare do
not exist on any installation that upgraded through the chain, and nobody noticed for
three months. `tests/repo/test_migration_patterns.py` now fails on that shape.

### The four rules

1. **Autogenerate, then edit.** Never author DDL operations from scratch.
2. **Never edit, delete or re-parent a merged migration.** Self-hosters have already
   run it. Fix it forward with a new one. (AGENTS.md hard rule 1.)
3. **Never guard a constraint operation on the dialect.** `if not is_sqlite:` around
   `op.create_foreign_key` produces a schema that differs by installation, silently.
   Branching per dialect is fine; *skipping the operation* is not.
4. **Every migration ships with a test** that runs it. There are five such files for
   66 migrations, so this is a rule for new work rather than a description of the
   chain: the ones that have tests are the ones that repaired data or rebuilt a
   table, which is the right priority. A migration that only adds a nullable column
   is covered by the parity tests below.

### SQLite cannot ALTER a constraint. Batch mode rebuilds the table

`ALTER TABLE` on SQLite supports four things: rename table, rename column, add
column, drop column. Everything else — adding or dropping a foreign key, a unique
constraint, a check constraint, changing a column type — needs the table rebuilt.

`op.batch_alter_table` does that: create `_alembic_tmp_<table>` with the full target
definition, `INSERT … SELECT` the rows across, drop the original, rename. Alembic
picks per batch (`recreate="auto"`, the default), and the line falls in a place worth
knowing exactly, because **a new column that is a foreign key is on the expensive
side**:

| What the migration does | What runs |
| --- | --- |
| `add_column`, no constraint | `ALTER TABLE … ADD COLUMN`. No rebuild. |
| `op.add_column` with an inline `ForeignKey`, no batch | refuses: `NotImplementedError: No support for ALTER of constraints in SQLite dialect` |
| `batch.add_column` with an inline `ForeignKey` | `ValueError: Constraint must have a name` unless a `naming_convention` is in play — then **rebuild** |
| `batch.add_column` then `batch.create_foreign_key` | **rebuild** |
| any constraint or column-type change | **rebuild** |

So a plain column is cheap forever and *every* new foreign-key column costs one
rebuild. There is no way around that through Alembic: it classifies a foreign key as
a constraint operation, and its SQLite dialect does not implement ALTER of
constraints. Note that this is Alembic being conservative rather than SQLite
refusing — raw SQLite accepts
`ALTER TABLE things ADD COLUMN owner_id INTEGER REFERENCES users(id)` quite happily.
Reaching for `op.execute` to exploit that is not worth it: it is hand-written DDL
(rule 1), it skips the model Alembic reasons about, and it saves 93 ms.

Which is the other half of the answer — the rebuild is cheap. Measured on a
library-shaped `files` table: **10 ms for 10,000 rows, 93 ms for 100,000.** Table
size is not a reason to avoid a foreign key at this product's scale, and "we will add
the constraint later" is how the schemas diverged in the first place.

### Rebuilding needs foreign keys off, and a check afterwards

The rebuild drops the original table. If another table references it —
`print_jobs.file_id → files.id` — that `DROP TABLE` fails with
`IntegrityError: FOREIGN KEY constraint failed` while enforcement is on. Verified
both ways: with `PRAGMA foreign_keys=OFF` the rebuild succeeds, the other table's
foreign key still points at the right table, rows are intact and
`PRAGMA foreign_key_check` comes back empty.

This is SQLite's own prescribed procedure for altering a table, and its first and
last steps are the pragmas:

```python
def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        # Outside any transaction: SQLite ignores this pragma while one is open, so
        # setting it inside `op.get_bind().begin()` silently does nothing.
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table("files", copy_from=_files_table()) as batch:
            batch.create_foreign_key("fk_files_deleted_by_users", "users",
                                     ["deleted_by"], ["id"])
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys=ON")
            orphans = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if orphans:
                raise RuntimeError(f"rebuild left orphaned rows: {orphans}")
```

Two details that bite:

- **`copy_from=`**, not reflection, for new migrations. Batch mode reconstructs the
  table from what it can see, and anything reflection misses is silently dropped on
  the floor. Pass a `Table` defined literally in the migration file, so it is pinned
  to that revision rather than following the models as they move on.

  All 78 existing `batch_alter_table` calls in this chain rely on reflection, and
  none is going to be edited — they have already run on real installations
  (rule 2). The rule is forward-looking: it is cheap in a new migration and it is
  the difference between a rebuild that preserves the table and one that quietly
  simplifies it.
- **The pragma must be outside a transaction.** SQLite ignores
  `PRAGMA foreign_keys` while one is open. This is the same trap that left the test
  suite with enforcement off for months (`tests/conftest.py::_truncate_all`).

### Constraint names: the naming convention is load-bearing

`SQLModel.metadata` carries a `naming_convention` (declared in `app/db/models.py`),
and it is not cosmetic. Batch mode alters a constraint by dropping it **by name** and
recreating it, so an anonymous constraint cannot be altered on SQLite at all —
`batch_alter_table` fails with `ValueError: Constraint must have a name`. The
convention is what makes a schema migratable on the database this product ships with.

It also makes the two schemas comparable: without it, `create_all` and the chain
generate different names for the same constraint, and the parity test cannot tell
that apart from real divergence.

Declared constraints may still name themselves, and five in `app/db/models.py` do.
The convention only fills in the rest.

### `copy_from` versus reflection — they do different things

Batch mode without `copy_from` **reflects** the table: it rebuilds it in the shape the
database currently has, and applies only the operations the migration names. With
`copy_from=<Table>`, the table instead *becomes* the definition you pass.

That is a difference in behaviour, not just in safety. Measured on a table whose
database shape was missing a foreign key the models declare:

| | Result |
| --- | --- |
| reflection + `create_foreign_key("fk_files_deleted_by_users")` | 2 foreign keys — the missing one stayed missing |
| `copy_from=<literal Table with all three>` | 3 foreign keys — the table converged to the definition |

So pick by intent:

- **Reflection** when you are changing one thing and want everything else preserved
  exactly as it is. Smallest blast radius. This is what
  `eb8435c9400e_add_missing_audit_foreign_keys` uses: it adds eighteen foreign keys
  and deliberately does *not* sweep up the enum-representation and server-default
  differences autogenerate also offered.
- **`copy_from`** when you want the table to *become* a known shape — converging a
  divergence, or when reflection cannot see something (an odd server default, a check
  constraint an older SQLAlchemy misses). Define the `Table` **literally in the
  migration file**, never imported from the models: a migration is pinned to a
  revision, and the models will move on without it.

`copy_from` is a per-call argument. There is no Alembic setting that turns it on
globally, and the 78 existing `batch_alter_table` calls in this chain all use
reflection. They will not be changed — they have already run on real installations
(rule 2) — so this is a rule for new work.

### Repair before you constrain

Adding a constraint to rows that already violate it leaves a database that cannot be
written to. SQLite adds the constraint without validating existing rows, because
enforcement is off for the rebuild, so the violation sits there until something
touches the row and then fails at the worst possible moment.

So a constraint migration has three parts, in this order:

1. **Repair.** Null the references that point at rows which are not there. Every
   column `eb8435c9400e` touches is nullable, which is what makes this free: an audit
   pointer (`created_by`, `updated_by`, `deleted_by`) to a user id that does not exist
   carries nothing that nulling destroys.
2. **Constrain**, in batch mode.
3. **Verify**, and *scope the verification to what you added*.
   `PRAGMA foreign_key_check` is the obvious tool and the wrong one: with no argument
   it walks the whole database, and even given a table it reports every violation of
   every constraint on it. The migration test data alone has `print_jobs` rows pointing
   at models that do not exist — violations that predate the migration and are none of
   its business. Reporting them would turn an unrelated inconsistency into a failed
   upgrade. Ask instead the same question the repair asked, per constraint you added,
   and expect no answer.

### The whole thing, end to end, on SQLite

What actually happens when you add a foreign key, from writing it to a self-hoster
upgrading:

1. You declare it in `app/db/models.py`. `create_all` will now emit it, so **new
   installations get it immediately** — that path builds from the models and stamps
   head, and never replays the chain.
2. You autogenerate a migration. Alembic compares the models against a database and
   emits `with op.batch_alter_table(...)` blocks, because `render_as_batch=True` is
   set for SQLite.
3. You trim it. Autogenerate offers everything it noticed — 890 lines and 242
   operations, the first time this was run — and you keep only what the change is
   about.
4. Repair, constrain, verify (above).
5. On upgrade, each affected table is rebuilt: `CREATE TABLE _alembic_tmp_x` with the
   constraint inline, `INSERT … SELECT` the rows, `DROP TABLE x`, rename. Foreign keys
   must be off for that `DROP`, and Alembic's engine leaves them off. **10 ms per
   10,000 rows, 93 ms per 100,000.**
6. `PRAGMA foreign_keys=ON` is restored per connection by `app/db/session.py`, so the
   constraint is enforced from the next request onwards.
7. The parity test proves the two schemas now agree on it.

The recurring temptation is to skip step 4–5 for one dialect because it is awkward.
That is the whole bug: `if not is_sqlite:` is cheap to write and produces two
products.

## Two schemas exist. Know which one you are looking at

`run_migrations` has three branches:

| State | What runs | Result |
| --- | --- | --- |
| No tables | `create_all` from the models, then `stamp head` | every constraint the models declare |
| Has `alembic_version` | `upgrade head` — pending migrations only | whatever the chain built, minus what SQLite could not ALTER |
| Tables, no version | adopt only if the schema matches the models exactly | fails closed otherwise |

A fresh installation therefore **never replays the chain**, and an upgraded one never
runs `create_all`. The two disagree — 136 structural differences on SQLite today —
and `tests/integration/db/migrations/test_models_versus_chain.py` pins the gap so it
cannot widen unnoticed. Its consequence for support: the orphan-rescue branch can
only ever adopt a `create_all` database, never a genuinely upgraded one.

PostgreSQL is unaffected. The chain's baseline cannot bootstrap a Postgres database
at all, which is why the fresh path is `create_all`, so every Postgres installation
has the full set.

## Deleting a row means knowing its children

`foreign_keys=ON` is a production pragma and most foreign keys here have no
`ondelete`, which means `RESTRICT`. A parent delete with a child still pointing at it
does not dangle — it **fails**.

Before writing a delete path, list what references the row:

```python
uv run python -c "
from sqlmodel import SQLModel
import app.db.models  # noqa
for t in SQLModel.metadata.sorted_tables:
    for fk in t.foreign_key_constraints:
        for e in fk.elements:
            if e.column.table.name == 'files':
                print(f'{t.name}.{e.parent.name} ondelete={fk.ondelete}')
"
```

Then handle every one: delete it, or null it if the column is nullable, or let
`ondelete=CASCADE` do it. `hard_delete_file` cleaned up three of five and the two it
missed made purging a file in a print batch fail; `purge_library_index` left
`files.external_library_id` pointing at the library it then deleted. Both were 500s on
a fresh installation and dangling rows on an upgraded one.

**When the database cascades, let it — but tell the ORM.** A DB-level
`ON DELETE CASCADE` removes the row without SQLAlchemy knowing, so a caller holding
the session keeps reading an object that no longer exists. Delete the child through
the session, flush, then delete the parent: the identity map stays honest and the
cascade has no row left to race for.

## Queries

- **Soft-deleted rows go through `app.db.scopes`.** `live(Model)` and
  `trashed(Model)`, never a hand-written `deleted_at.is_(None)` — the scopes are the
  single place the rule lives, and a query that spells it out by hand is a query that
  will not follow when the rule changes.
- **Sessions come from `get_session_factory()`**, never a module-level engine. That
  is the seam the test suite overrides and the cloud deployment replaces
  (AGENTS.md hard rule 5).
- **Writes from worker threads use their own session.** `asyncio.to_thread` work must
  not share a session with the request that started it, and a write that lands after
  the caller has read is how a terminal state gets overwritten by a stale snapshot.

## Verifying a schema change

```bash
cd backend
uv run alembic upgrade head                       # forwards
uv run alembic downgrade -1 && uv run alembic upgrade head   # and back
./scripts/test.sh coverage                        # includes the parity tests below
```

Three tests are the schema's own guard rails:

- `tests/repo/test_migration_patterns.py` — no constraint operation is skipped for a
  dialect.
- `tests/integration/db/migrations/test_models_versus_chain.py` — the migrated schema
  differs from the models only as recorded, foreign key by foreign key and category
  by category.
- `tests/repo/test_db_parity.py` — the test database enforces what production's does.

A migration that changes the schema and leaves all three green has changed both
supported installations the same way. One that turns any of them red has not.
