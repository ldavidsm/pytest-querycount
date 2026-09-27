# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

## Unreleased

### Planned

- Raw psycopg backend, for projects using SQL without an ORM.
- `pytest-xdist` aware reporting.
- More plan checks: sorts spilling to disk, nested loops over large sets.

## [0.4.0] - 2026-09-27

### Added

- `--querycount-write-budgets` writes `@pytest.mark.max_queries` markers into
  your test files using the counts observed in that run, then exits without
  enforcing them. This is the answer to adopting a budget on a suite that
  already has five hundred tests: nobody was ever going to read five hundred
  failures and type five hundred numbers by hand.

  Source editing goes through `ast`, not regular expressions, because "the line
  above the def" is not something a regular expression finds reliably once
  decorators, classes, async definitions and multi-line signatures are involved.
  It matches on qualified names, so two `test_create` methods in different
  classes are never confused; it leaves a hand-written budget alone; it skips
  tests that failed, whose query count is whatever they reached before blowing
  up; and it refuses to touch a file it cannot parse.

- `asyncio` extra, `pytest-querycount[asyncio]`, which brings in
  `sqlalchemy[asyncio]` and therefore greenlet.

### Fixed

- **Caller attribution was silently lost under asyncio.** The budget still
  fired, but every failure came without the one piece of information that makes
  it actionable -- no "from your_file.py:39" line at all.

  SQLAlchemy runs its synchronous internals inside a greenlet spawned per
  operation, and that greenlet's stack begins at SQLAlchemy's own entry point,
  so following `f_back` reached only library frames. The awaiting frames live on
  the *parent* greenlet's stack, reachable through `gr_frame`, and the walk now
  crosses that boundary. Since the audience for this plugin is FastAPI and
  Litestar, where async is the default, this was the most important gap it had.

### Verified rather than assumed

The README claimed async engines were instrumented "sync or async" on the
strength of reasoning alone. They are, and there are now tests for it -- along
with tests proving that the three async tests fail without the greenlet fix, so
they guard something real. The plan check works under async PostgreSQL too: the
EXPLAIN on a raw DBAPI cursor survives being issued from inside the greenlet,
and the savepoint still restores `enable_seqscan` and keeps the transaction
usable there.

102 tests, up from 75.

## [0.3.0] - 2026-09-26

### Added

- `@pytest.mark.no_seq_scan(ignore=())` -- fail a test whose query scans a table
  sequentially because no index can serve its filter. PostgreSQL only.
- `--querycount-no-seq-scan`, the same check across a whole suite.
- `no_seq_scan=` and `ignore=` on the `querycount` fixture.
- Failure messages carry a suggested `CREATE INDEX`, derived from the columns in
  the plan's filter expression.

### How it works, and why not the obvious way

Failing on the presence of a `Seq Scan` does not work. On a test database of
twenty rows PostgreSQL picks a sequential scan *even when a perfect index
exists*, because reading twenty rows is cheaper than descending a B-tree. So the
plan alone cannot tell a missing index from a small table, and thresholding on
table size means the check never fires on test data at all.

Instead the plan is taken with `enable_seqscan` disabled. If PostgreSQL still
chooses a sequential scan, no index can serve that filter -- an answer that does
not depend on how much data the table holds. The tests for this feature run
against a table of eight rows to hold that claim honest.

### Notes

- The `EXPLAIN` runs on a raw DBAPI cursor, so it is invisible to SQLAlchemy's
  events: it is not counted among the test's queries and cannot recurse.
- It is wrapped in a savepoint, which both scopes the `enable_seqscan` change and
  absorbs a failed `EXPLAIN` that would otherwise abort the test's transaction.
- An unfiltered `Seq Scan` is never reported: reading a whole table is sometimes
  the point, and no index would improve it.
- On SQLite or MySQL the check raises rather than passing, for the same reason a
  missing backend does.

## [0.2.0] - 2026-09-26

First public release. Requires Python 3.10+, pytest 8+, and SQLAlchemy 2.x.

### Added

- `@pytest.mark.max_queries(n)` -- fail a test that runs more than `n` queries.
- `@pytest.mark.no_n_plus_one` -- fail a test that executes one query shape
  repeatedly, with `threshold` and `kinds` to tune it.
- The `querycount` fixture, for budgeting a block rather than a whole test.
- SQL fingerprinting, so `WHERE id = 42` and `WHERE id = 43` are recognised as
  the same query. Handles every bind-parameter style, comments, dollar quoting,
  and collapses `IN (...)` and multi-row `VALUES` lists.
- Caller attribution: failures name the line of your code that emitted the
  query, not a line inside SQLAlchemy.
- `--querycount-report`, a table of the tests that run the most queries.
- `--querycount-max=N` and the `querycount_max` ini option, for applying a
  budget across a whole suite.
- SQLAlchemy 2.x instrumentation, sync and async, with no configuration.

### Notes

- A missing backend raises rather than passing silently, on the grounds that a
  budget which cannot fail is worse than no budget.
- Only the call phase is measured, so fixture setup and teardown never consume a
  budget.
