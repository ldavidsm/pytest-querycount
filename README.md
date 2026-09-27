# pytest-querycount

[![CI](https://github.com/ldavidsm/pytest-querycount/actions/workflows/ci.yml/badge.svg)](https://github.com/ldavidsm/pytest-querycount/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pytest-querycount.svg)](https://pypi.org/project/pytest-querycount/)
[![Python versions](https://img.shields.io/pypi/pyversions/pytest-querycount.svg)](https://pypi.org/project/pytest-querycount/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Fail your tests when they run too many SQL queries.

An endpoint that quietly grows from 2 queries to 200 does not break any test. It
just gets slower, until one day it is slow enough to notice. This plugin turns
that drift into a red test.

```python
@pytest.mark.max_queries(2)
def test_billing_summary(db):
    assert len(serialise(db)) == 12
```

Add a lazy-loaded relationship to that serialiser and the test fails on the next
CI run, naming the repeated query and the line of your code that caused it.

## Install

```bash
pip install "pytest-querycount[sqlalchemy]"
```

Requires Python 3.10+, pytest 8+, and SQLAlchemy 2.x. There is nothing to
configure: the plugin instruments every engine your suite creates, sync or async.

## What it gives you

### A query budget

```python
@pytest.mark.max_queries(2)
def test_billing_summary(db):
    assert len(serialise(db)) == 12
```

When the serialiser loops, you get this -- real output, not an illustration:

```
_____________________________ test_billing_summary _____________________________

E   TooManyQueriesError: test_billing_summary ran 13 queries, budget is 2 (11 over).

    Repeated query shapes -- most likely where the extra queries come from:
      12x  SELECT invoices.id, invoices.customer_id, invoices.cents FROM invoices WHERE ? = invoices.custome…
          from test_billing.py:10 (12x)

    13 queries in 0.1ms
            1. [   0.02ms] SELECT customers.id, customers.email FROM customers
                            from test_billing.py:11
         2-13. [   0.06ms] 12x SELECT invoices.id, invoices.customer_id, invoices.cents FROM invoices WHERE …
                            from test_billing.py:10
```

Three things to notice, because they are the reason this is a package and not a
snippet:

- **The diagnosis comes first.** `12x` on one query shape, before any listing.
  In a CI log you get the culprit in the first three lines.
- **`test_billing.py:10` is your code**, not a line inside SQLAlchemy. The
  plugin walks up the stack past the ORM to find the line that actually caused
  the query.
- **The twelve identical queries print as one line.** Twelve copies of the same
  SELECT tell you nothing that `12x` does not, and they would push the useful
  lines off the screen.

### N+1 detection without picking a number

Sometimes you do not want to choose a budget, you just want to assert that
nothing is looping.

```python
@pytest.mark.no_n_plus_one
def test_billing_summary(db):
    assert len(serialise(db)) == 12
```

This groups queries by *shape* -- `WHERE id = 42` and `WHERE id = 43` are the
same shape -- and fails when one shape repeats. By default it looks only at
SELECTs, since repeated `SAVEPOINT`s are just how transactions work.

```python
@pytest.mark.no_n_plus_one(threshold=5)             # tolerate up to 4 repeats
@pytest.mark.no_n_plus_one(kinds=("select", "insert"))
@pytest.mark.no_n_plus_one(kinds=None)              # consider every statement
```

### Missing indexes (PostgreSQL)

```python
@pytest.mark.no_seq_scan
def test_customer_search(db):
    assert len(find_by_city(db, "city3")) == 1
```

```
_____________________________ test_customer_search _____________________________

E   SeqScanError: test_customer_search ran 1 query that no index could serve.

    PostgreSQL still chose a sequential scan with enable_seqscan disabled, which
    means no index covers the filtered columns.

      Seq Scan on "demo_customers"  Filter: ((city)::text = 'city3'::text)
          try:  CREATE INDEX ON demo_customers (city);
          from test_search.py:7
          SELECT demo_customers.id, ... FROM demo_customers WHERE demo_customers.…
```

That table holds **eight rows**, and that matters more than it looks.

The obvious way to write this check does not work. Failing on the presence of a
`Seq Scan` is useless, because on a test database of twenty rows PostgreSQL picks
a sequential scan *even when a perfect index exists* -- reading twenty rows is
cheaper than descending a B-tree. So the plan alone cannot distinguish a missing
index from a small table. And thresholding on table size, the usual next idea,
means the check never fires on test data at all. Either way you get a check that
lies to you.

So this asks a different question: not "did it scan?" but "**could** it have used
an index?". The plan is taken with `enable_seqscan` disabled, which makes the
planner treat sequential scans as enormously expensive. If it still picks one, no
index can serve that filter -- and that answer does not depend on data volume.
The test suite for this feature runs against eight rows on purpose, to keep that
claim honest.

Excluding a table you read whole deliberately:

```python
@pytest.mark.no_seq_scan(ignore=("countries", "settings"))
```

Three things this is careful about:

- The `EXPLAIN` runs on a raw DBAPI cursor, invisible to SQLAlchemy's events, so
  it is never counted among your test's queries and cannot recurse.
- It is wrapped in a savepoint, which both scopes the `enable_seqscan` change and
  absorbs a failed `EXPLAIN` that would otherwise abort your test's transaction.
- An unfiltered `Seq Scan` is never reported. Reading a whole table is sometimes
  exactly what you meant.

Needs `pip install "pytest-querycount[postgresql]"`. On SQLite or MySQL the check
raises rather than passing, because a check that cannot fail is not a check.

### A fixture, for when a marker is too coarse

A marker covers the whole test. When you only care about one block:

```python
def test_detail(db, querycount):
    with querycount(max_queries=2, no_seq_scan=True) as queries:
        serialise(db)

    assert queries.count == 2
    assert queries.duplicates() == []
    print(queries.report())
```

Call it with no arguments to observe without asserting -- that is how you find
out what the budget should be before committing to one. The object from the
`with` is the recorder, and it keeps its records after the block ends.

### The report you leave switched on

```bash
pytest --querycount-report
```

```
============================== querycount summary ==============================
queries       time  dupes  test
-------  ---------  -----  -------------------------------------------
     13      0.1ms    12!  test_billing.py::test_billing_summary
      2      0.0ms     -   test_billing.py::test_billing_summary_fixed

15 queries in 0.1ms across 2 tests
! marks a repeated query shape -- add @pytest.mark.no_n_plus_one to see the detail.
```

A budget tells you when you crossed a line you drew. This tells you where the
lines should go, and it is the honest way to adopt the plugin on an existing
suite: run it once, look at the rows with a `!`, write budgets for those.

## Options

| Flag | Effect |
|---|---|
| `--querycount-report` | Print the summary table |
| `--querycount-top=N` | How many tests the table lists (default 10) |
| `--querycount-max=N` | Apply a budget of N to every test without an explicit one |
| `--querycount-no-seq-scan` | Apply the missing-index check to every test |

`--querycount-max` is how you ratchet: set it just above your current worst
test, then lower it as you fix things.

In `pyproject.toml`:

```toml
[tool.pytest.ini_options]
querycount_max = "20"
querycount_duplicate_threshold = "3"
```

## Design decisions worth knowing

- **Only the call phase is measured.** Fixture setup and teardown run outside it,
  so creating a schema and seeding it never consume a budget.
- **A failing test is never second-guessed.** If your assertion fails, you see
  that failure, not a complaint about query counts on top of it.
- **`executemany` counts as one query**, because it is one round trip. That is
  the point of it, and the fix for a loop of inserts.
- **No backend is an error, not a pass.** If nothing is instrumented, every
  count is zero and every budget is trivially satisfied. The plugin raises
  instead, because a budget that cannot fail is worse than no budget.

## Why not just count them yourself

You can, in about twenty lines of `event.listen`. What you will not have is the
fingerprinting that tells `WHERE id = 42` and `WHERE id = 43` apart from each
other but together against `WHERE email = ?`; the walk up the stack to find the
line in *your* code rather than in SQLAlchemy's; the collapsing of `IN (?,?,?)`
so that batch size does not change the shape; or the failure message that names
the culprit before it shows the evidence. That is what this package is.

## Prior art

[`nplusone`](https://pypi.org/project/nplusone/) did the detection half of this
for Django and SQLAlchemy, but its last release was in **May 2018** and it does
not support SQLAlchemy 2.x. Django users have `assertNumQueries` built in, which
covers budgets but not shapes. This plugin is aimed at the SQLAlchemy side --
FastAPI, Flask, Litestar -- where nothing is currently maintained.

## Limitations

- SQLAlchemy 2.x only for now. Django and raw psycopg are on the roadmap.
- Under `pytest-xdist` the summary table is per worker, so it will be partial.
  Budgets and N+1 detection are unaffected.
- `no_seq_scan` is PostgreSQL only, and costs one `EXPLAIN` per SELECT while
  enabled. Budgets and N+1 detection work on any SQLAlchemy backend.
- The suggested `CREATE INDEX` is a starting point, not advice. Which columns to
  index, in what order, and whether the index earns its write cost need the whole
  query pattern, not one plan node. A filter over a function call
  (`lower(email) = ...`) yields no suggestion at all rather than a wrong one.

## Contributing

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# The plan tests need PostgreSQL; without one they skip.
docker run -d --name qc-pg -e POSTGRES_PASSWORD=querycount \
    -e POSTGRES_DB=querycount -p 55432:5432 postgres:17-alpine

pytest && ruff check src tests && mypy
```

Set `QUERYCOUNT_REQUIRE_PG=1` to turn those skips into failures, as CI does, and
`QUERYCOUNT_TEST_PG_URL` to point elsewhere.

The suite runs pytest inside pytest via `pytester`: it writes a throwaway test,
runs it in a subprocess, and asserts on the outcome. That is the only honest way
to test a plugin whose job is to make tests fail.

## Releasing

See [RELEASING.md](RELEASING.md). Publication goes through PyPI Trusted
Publishing, so there is no API token in this repository or its secrets.

## Licence

MIT
