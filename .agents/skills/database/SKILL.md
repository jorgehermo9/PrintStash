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
picks per batch (`recreate="auto"`, the default), so it is **not** a rebuild every
time:

| Batch contains | What runs |
| --- | --- |
| `add_column` only | `ALTER TABLE … ADD COLUMN`. No rebuild. |
| any constraint or type change | full rebuild |

So adding a column stays cheap forever; only constraint work pays. And the rebuild is
cheaper than it sounds — **10 ms for 10,000 rows, 93 ms for 100,000** on a
library-shaped `files` table. Table size is not a reason to avoid it at this
product's scale.

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

### Constraint names

Batch mode drops a constraint *by name* in order to recreate it, so an unnamed
constraint cannot be altered on SQLite at all. There is no `naming_convention` on
`SQLModel.metadata` today, which is why the schema comparison reports "different
unique constraints" for things that are otherwise equivalent — the two paths generate
different names.

Adding one is the enabling step for any future constraint migration. It affects
constraints generated *after* it, not existing DDL, so it is safe but it does move
what `create_all` emits — take it as its own change, with the parity counts in
`tests/integration/db/migrations/test_models_versus_chain.py` re-measured.

Until then: name every constraint explicitly in the models
(`sa.UniqueConstraint(..., name="uq_…")`) so batch mode can reach it. **13 of the 18
`UniqueConstraint` declarations in `app/db/models.py` are unnamed today**, which means
those constraints cannot be altered on SQLite at all. Naming one is a two-line change
and worth doing as you touch the table.

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
