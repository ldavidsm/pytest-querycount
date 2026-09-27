"""Missing-index detection against a real PostgreSQL.

The claim under test: this works on a table of eight rows. If it needed a large
table it would be useless, because test databases are small -- which is exactly
why the obvious implementation of this check does not work. See
:mod:`pytest_querycount.explain` for the reasoning.
"""

from __future__ import annotations

import pytest

from pytest_querycount import explain


def test_indexed_column_passes(pg_app: pytest.Pytester) -> None:
    """An index exists, so the planner can avoid the scan when pushed to."""
    pg_app.makepyfile(
        """
        import pytest
        from conftest import by_email

        @pytest.mark.no_seq_scan
        def test_indexed(pg):
            assert len(by_email(pg)) == 1
        """
    )
    pg_app.runpytest_subprocess().assert_outcomes(passed=1)


def test_unindexed_column_fails(pg_app: pytest.Pytester) -> None:
    """No index exists, and eight rows is enough to prove it."""
    pg_app.makepyfile(
        """
        import pytest
        from conftest import by_city

        @pytest.mark.no_seq_scan
        def test_unindexed(pg):
            assert len(by_city(pg)) == 1
        """
    )
    result = pg_app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*no index could serve*",
            '*Seq Scan on "qc_customers"*city*',
            "*CREATE INDEX ON qc_customers (city);*",
        ]
    )


def test_ignore_excuses_a_table(pg_app: pytest.Pytester) -> None:
    """A small lookup table read whole is not a bug, and must be excludable."""
    pg_app.makepyfile(
        """
        import pytest
        from conftest import by_city

        @pytest.mark.no_seq_scan(ignore=("qc_customers",))
        def test_ignored(pg):
            assert len(by_city(pg)) == 1
        """
    )
    pg_app.runpytest_subprocess().assert_outcomes(passed=1)


def test_the_explain_does_not_disturb_the_test(pg_app: pytest.Pytester) -> None:
    """The plan probe must leave no trace: not in the query count, not in the
    session settings, and not in the transaction."""
    pg_app.makepyfile(
        """
        import pytest
        from sqlalchemy import text
        from conftest import Customer, by_email

        @pytest.mark.no_seq_scan(ignore=("qc_customers",))
        @pytest.mark.max_queries(4)   # 2 selects in the block, plus the SHOW and the INSERT
        def test_undisturbed(pg, querycount):
            with querycount(no_seq_scan=True, ignore=("qc_customers",)) as queries:
                by_email(pg)
                by_email(pg)

            # The EXPLAIN statements bypass the engine, so they are not counted.
            assert queries.count == 2
            assert queries.explained_count == 2

            # enable_seqscan was restored by the savepoint rollback.
            assert pg.execute(text("SHOW enable_seqscan")).scalar() == "on"

            # And the transaction still works, including writes.
            pg.add(Customer(email="new@example.com", city="nowhere"))
            pg.flush()
        """
    )
    pg_app.runpytest_subprocess().assert_outcomes(passed=1)


def test_unfiltered_scan_is_not_flagged(pg_app: pytest.Pytester) -> None:
    """`SELECT * FROM t` reads the whole table on purpose. No index would help."""
    pg_app.makepyfile(
        """
        import pytest
        from sqlalchemy import select
        from conftest import Customer

        @pytest.mark.no_seq_scan
        def test_full_read(pg):
            assert len(pg.scalars(select(Customer)).all()) == 8
        """
    )
    pg_app.runpytest_subprocess().assert_outcomes(passed=1)


def test_sqlite_says_so_instead_of_passing(app: pytest.Pytester) -> None:
    """On a backend with no usable plan the check must refuse, not pass.

    Note this uses the SQLite app, so it needs no PostgreSQL.
    """
    app.makepyfile(
        """
        import pytest
        from sqlalchemy import select
        from conftest import User

        @pytest.mark.no_seq_scan
        def test_on_sqlite(session):
            session.scalars(select(User)).all()
        """
    )
    result = app.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*could not obtain a query plan*", "*needs PostgreSQL*"])


# -- plan parsing, no database needed ------------------------------------


def test_parse_finds_nested_scans() -> None:
    document = [
        {
            "Plan": {
                "Node Type": "Hash Join",
                "Plans": [
                    {
                        "Node Type": "Seq Scan",
                        "Relation Name": "orders",
                        "Filter": "(status = 'open'::text)",
                    },
                    {"Node Type": "Index Scan", "Relation Name": "customers"},
                ],
            }
        }
    ]
    scans = explain.parse(document)
    assert [scan.relation for scan in scans] == ["orders"]
    assert scans[0].suggested_index() == "CREATE INDEX ON orders (status);"


def test_parse_accepts_a_json_string() -> None:
    """Drivers differ on whether they decode a json column for you."""
    raw = '[{"Plan": {"Node Type": "Seq Scan", "Relation Name": "t", "Filter": "(a = 1)"}}]'
    assert explain.parse(raw)[0].relation == "t"


def test_parse_ignores_unfiltered_scans() -> None:
    document = [{"Plan": {"Node Type": "Seq Scan", "Relation Name": "t"}}]
    assert explain.parse(document) == ()


def test_parse_survives_rubbish() -> None:
    assert explain.parse(None) == ()
    assert explain.parse("not json") == ()
    assert explain.parse([{"Plan": {}}]) == ()


def test_no_index_hint_when_the_filter_is_a_function_call() -> None:
    """Better to show the raw filter than to suggest a wrong index."""
    document = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "users",
                "Filter": "(lower(email) = 'a'::text)",
            }
        }
    ]
    scan = explain.parse(document)[0]
    assert scan.columns == ()
    assert scan.suggested_index() is None
    assert "lower(email)" in scan.describe()


def test_parse_handles_a_parenthesised_cast_column() -> None:
    """PostgreSQL writes a varchar filter as "((city)::text = 'x'::text)".

    Regression: the wrapper parentheses hid the column name, so the commonest
    case of all -- a text column -- produced no index suggestion.
    """
    document = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "qc_customers",
                "Filter": "((city)::text = 'city3'::text)",
            }
        }
    ]
    scan = explain.parse(document)[0]
    assert scan.columns == ("city",)
    assert scan.suggested_index() == "CREATE INDEX ON qc_customers (city);"


def test_global_flag_applies_the_check(pg_app: pytest.Pytester) -> None:
    """--querycount-no-seq-scan needs no marker, for finding out where to look."""
    pg_app.makepyfile(
        """
        from conftest import by_city, by_email

        def test_indexed(pg):
            by_email(pg)

        def test_unindexed(pg):
            by_city(pg)
        """
    )
    pg_app.runpytest_subprocess().assert_outcomes(passed=2)
    pg_app.runpytest_subprocess("--querycount-no-seq-scan").assert_outcomes(passed=1, failed=1)


def test_marker_ignore_still_applies_under_the_global_flag(pg_app: pytest.Pytester) -> None:
    pg_app.makepyfile(
        """
        import pytest
        from conftest import by_city

        @pytest.mark.no_seq_scan(ignore=("qc_customers",))
        def test_excused(pg):
            by_city(pg)
        """
    )
    pg_app.runpytest_subprocess("--querycount-no-seq-scan").assert_outcomes(passed=1)
