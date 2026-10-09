# Data checklist (on demand — schema, migrations, queries, Prisma; distilled from vendor/ecc: database-reviewer, database-migrations, prisma-patterns)

Load when the diff touches schema/migration files, SQL or ORM query code. Architect: apply "Migrations" while planning.
Never run a migration or seed against a shared/production database from a run — that is a deploy step the user owns.
`prisma migrate dev` may reset the database on drift: local/dev DB only.

## Migrations — blocker
- One-step breaking change on a table that already has data: drop/rename of a column or table, type narrowing,
  NOT NULL without a default. Use expand → backfill → contract (rename = add new, dual-write, switch reads, drop later).
- Schema change (DDL) and data backfill in the same migration; a large backfill in one unbatched statement.
- Editing a migration that was already applied anywhere (Prisma: checksum mismatch) — add a new migration instead.
- Index added to a large live table without the concurrent form (Postgres `CREATE INDEX CONCURRENTLY`).
- Irreversible step without a rollback note in the plan's "Riskler ve geri alma".

## Queries — major
- N+1: a query or API call inside a loop / per-row relation load → batch, `include`/join, `IN (…)`.
- New WHERE / JOIN / ORDER BY column or foreign key without an index; composite index order (equality first, then range).
- Unbounded read on a growing table (no `take`/LIMIT; `SELECT *` / no `select`); OFFSET pagination on large tables → cursor.
- Multi-step write without a transaction; a transaction kept open across an external API call; inconsistent lock order.
- Check-then-write race → unique constraint + upsert, or a row lock.
- Money stored as float; timestamps without time zone; a missing FK / NOT NULL / CHECK for an invariant the code assumes.

## Prisma traps — major
- `updateMany`/`deleteMany` return `{ count }`, not rows; with an empty `where` they hit every row.
- `undefined` in a `where` field means "no filter" — a missing id silently widens the query. Validate before querying.
- Interactive `$transaction` times out after 5 s by default — no network calls inside it.
- Soft delete: `findUnique*` ignores `deletedAt`; filter explicitly (`findFirst*` with `deletedAt: null`).
- Returning ORM entities straight from an API/loader leaks internal fields → `select` or map to a DTO.
- Serverless: unpooled connections exhaust the database → pooler / low `connection_limit`.

## Output
Rows in your review table: severity · file:line · scenario · concrete fix.
