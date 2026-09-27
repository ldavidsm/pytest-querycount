"""The fingerprinter. Pure functions, so these are plain unit tests."""

from __future__ import annotations

import pytest

from pytest_querycount.normalize import fingerprint, statement_kind


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # Literal values must not distinguish two queries.
        ("SELECT * FROM t WHERE id = 42", "SELECT * FROM t WHERE id = 43"),
        ("SELECT * FROM t WHERE n = 'ana'", "SELECT * FROM t WHERE n = 'luis'"),
        ("SELECT * FROM t WHERE x = 1.5", "SELECT * FROM t WHERE x = 99.25"),
        ("SELECT * FROM t LIMIT 10", "SELECT * FROM t LIMIT 20"),
        # Nor should the bind parameter style, so the same query fingerprints
        # identically across drivers.
        ("SELECT * FROM t WHERE id = ?", "SELECT * FROM t WHERE id = :ident"),
        ("SELECT * FROM t WHERE id = %s", "SELECT * FROM t WHERE id = $1"),
        ("SELECT * FROM t WHERE id = %(ident)s", "SELECT * FROM t WHERE id = ?"),
        # Nor the length of a variable-length list.
        ("SELECT * FROM t WHERE id IN (1, 2, 3)", "SELECT * FROM t WHERE id IN (9)"),
        ("INSERT INTO t (a) VALUES (1), (2), (3)", "INSERT INTO t (a) VALUES (7)"),
        # Nor comments, casing, or whitespace.
        ("SELECT * FROM t /* hint */ WHERE id = 1", "select  *  from t where id = 2"),
        ("SELECT * FROM t WHERE id = 1 -- note", "SELECT * FROM t WHERE id = 2;"),
    ],
)
def test_same_shape(left: str, right: str) -> None:
    assert fingerprint(left) == fingerprint(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # Different columns, tables or predicates are genuinely different.
        ("SELECT * FROM t WHERE id = 1", "SELECT * FROM t WHERE email = 'a'"),
        ("SELECT * FROM users WHERE id = 1", "SELECT * FROM orders WHERE id = 1"),
        ("SELECT a FROM t", "SELECT b FROM t"),
        ("SELECT * FROM t WHERE a = 1", "SELECT * FROM t WHERE a = 1 AND b = 2"),
    ],
)
def test_different_shape(left: str, right: str) -> None:
    assert fingerprint(left) != fingerprint(right)


def test_keeps_identifiers_containing_digits() -> None:
    """A word boundary protects `table1`; without it every name would collapse."""
    assert "table1" in fingerprint("SELECT * FROM table1 WHERE x = 5")
    assert fingerprint("SELECT * FROM t1") != fingerprint("SELECT * FROM t2")


def test_keeps_postgres_cast() -> None:
    """`::int` must survive; only `:name` is a bind parameter."""
    assert fingerprint("SELECT x::int FROM t") == "select x::int from t"


def test_handles_escaped_quote() -> None:
    """`''` is an embedded quote, not the end of the string."""
    assert fingerprint("SELECT * FROM t WHERE s = 'it''s 42'") == "select * from t where s = ?"


def test_handles_dollar_quoting() -> None:
    body = "CREATE FUNCTION f() RETURNS int AS $$ SELECT 42; $$ LANGUAGE sql"
    assert "42" not in fingerprint(body)


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("SELECT 1", "select"),
        ("  insert into t values (1)", "insert"),
        ("UPDATE t SET a = 1", "update"),
        ("SAVEPOINT sa_1", "savepoint"),
        ("(SELECT 1) UNION (SELECT 2)", "select"),
        ("-- only a comment\nBEGIN", "begin"),
        ("", "other"),
    ],
)
def test_statement_kind(statement: str, expected: str) -> None:
    assert statement_kind(statement) == expected
