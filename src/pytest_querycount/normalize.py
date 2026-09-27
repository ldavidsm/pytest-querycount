"""Reduce a SQL statement to a *fingerprint*.

Two statements share a fingerprint when they differ only in their literal
values. That is what lets us say "this same query ran 47 times" instead of
"47 queries ran", which is the difference between a number and a diagnosis.

    >>> fingerprint("SELECT * FROM users WHERE id = 42")
    'select * from users where id = ?'
    >>> fingerprint("SELECT * FROM users WHERE id = 43")
    'select * from users where id = ?'
"""

from __future__ import annotations

import re
from functools import lru_cache

_PLACEHOLDER = "?"

# Comments go first: they can contain anything, including quotes that would
# otherwise unbalance the string matching below.
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")

# Postgres dollar quoting ($$body$$ or $tag$body$tag$) must be handled before
# numeric placeholders, or "$1" inside a body would be mangled.
_DOLLAR_QUOTED = re.compile(r"\$(\w*)\$.*?\$\1\$", re.DOTALL)

# Single-quoted strings, with '' as the escape for an embedded quote.
_STRING = re.compile(r"'(?:[^']|'')*'")

# Bind parameters in every style the DBAPIs use. Named styles come before the
# bare ones so ":name" is not left as a stray colon.
_PARAM_PYFORMAT = re.compile(r"%\(\w+\)s")
_PARAM_NAMED = re.compile(r"(?<![:\w]):\w+")  # ":name" but never "::cast"
_PARAM_NUMERIC = re.compile(r"\$\d+")
_PARAM_FORMAT = re.compile(r"%s")

# Numbers, once strings are gone so we never touch digits inside a literal.
# The word boundary keeps identifiers like "table1" intact.
_NUMBER = re.compile(r"\b\d+\.?\d*(?:[eE][+-]?\d+)?\b")

# Collapse variable-length lists so an IN clause of 3 ids fingerprints the same
# as one of 300 -- otherwise every batch size looks like a different query.
_IN_LIST = re.compile(r"\bin\s*\(\s*\?(?:\s*,\s*\?)*\s*\)")
_VALUES_TUPLE = re.compile(r"\(\s*\?(?:\s*,\s*\?)*\s*\)")
_VALUES_CLAUSE = re.compile(r"\bvalues\s*((?:\(\s*\?(?:\s*,\s*\?)*\s*\)\s*,?\s*)+)")

_WHITESPACE = re.compile(r"\s+")


def _collapse_values(match: re.Match[str]) -> str:
    """Rewrite a multi-row VALUES clause as a single tuple."""
    tuples = _VALUES_TUPLE.findall(match.group(1))
    if len(tuples) <= 1:
        return match.group(0)
    first = _VALUES_TUPLE.search(match.group(1))
    assert first is not None
    return f"values {first.group(0)}"


@lru_cache(maxsize=2048)
def fingerprint(statement: str) -> str:
    """Return a stable, literal-free form of ``statement``.

    Cached, because the whole point is that the same statements recur and this
    runs on every query the suite emits.
    """
    sql = _BLOCK_COMMENT.sub(" ", statement)
    sql = _LINE_COMMENT.sub(" ", sql)
    sql = _DOLLAR_QUOTED.sub(_PLACEHOLDER, sql)
    sql = _STRING.sub(_PLACEHOLDER, sql)
    sql = _PARAM_PYFORMAT.sub(_PLACEHOLDER, sql)
    sql = _PARAM_NAMED.sub(_PLACEHOLDER, sql)
    sql = _PARAM_NUMERIC.sub(_PLACEHOLDER, sql)
    sql = _PARAM_FORMAT.sub(_PLACEHOLDER, sql)
    sql = _NUMBER.sub(_PLACEHOLDER, sql)

    sql = sql.lower()
    sql = _IN_LIST.sub(f"in ({_PLACEHOLDER})", sql)
    sql = _VALUES_CLAUSE.sub(_collapse_values, sql)

    sql = _WHITESPACE.sub(" ", sql).strip()
    return sql.rstrip(";").strip()


def statement_kind(statement: str) -> str:
    """The leading keyword of ``statement``, lowercased.

    ``"select"``, ``"insert"``, ``"savepoint"``, ... or ``"other"`` when the
    statement does not start with a word (an empty string, say).
    """
    for token in _WHITESPACE.split(_LINE_COMMENT.sub(" ", statement).strip()):
        cleaned = token.strip("(").lower()
        if cleaned.isalpha():
            return cleaned
    return "other"
