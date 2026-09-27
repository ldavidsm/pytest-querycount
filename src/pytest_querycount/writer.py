"""Writing budget markers into test files.

Adopting a query budget on a suite that already has five hundred tests is the
hard part. Nobody is going to read five hundred failures and type five hundred
numbers, so in practice the plugin gets installed, switched on once, and turned
off again. This module removes that excuse: run the suite once with
``--querycount-write-budgets`` and the markers are written for you, each holding
the count that test actually ran.

The numbers are exact on purpose. A budget with slack in it is not a ratchet --
the point is that the next query added to that code path turns a test red.

Source editing is done through ``ast`` rather than regular expressions, because
"the line above the def" is not a thing a regular expression can find reliably
once decorators, classes, async definitions and multi-line signatures are in
play.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

MARKER = "max_queries"


@dataclass
class Insertion:
    """One marker to be written above one function."""

    lineno: int
    """1-based line of the ``def``, i.e. where the marker goes above."""

    indent: str
    count: int
    qualname: str

    def render(self, newline: str) -> str:
        return f"{self.indent}@pytest.mark.{MARKER}({self.count}){newline}"


@dataclass
class FileResult:
    """What happened to one file."""

    path: Path
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    """Functions that already carried a budget."""

    missing: list[str] = field(default_factory=list)
    """Functions the observed counts named but the source does not contain."""

    import_added: bool = False
    error: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.written)


def _qualnames(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Map every function's dotted qualname to its node.

    Matching on qualname rather than bare name is what keeps two ``test_create``
    methods in different classes from being confused for each other.
    """
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                found[name] = child
                visit(child, f"{name}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")

    visit(tree, "")
    return found


def _already_budgeted(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the function already carries a max_queries marker.

    Deliberately loose: any mention of the name in a decorator counts. A false
    positive leaves a hand-written budget alone, which is the safe direction to
    be wrong in.
    """
    for decorator in node.decorator_list:
        for inner in ast.walk(decorator):
            if isinstance(inner, ast.Attribute) and inner.attr == MARKER:
                return True
            if isinstance(inner, ast.Name) and inner.id == MARKER:
                return True
    return False


def _imports_pytest(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "pytest" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest":
            return True
    return False


def _import_line(tree: ast.Module) -> int:
    """1-based line to insert ``import pytest`` at.

    Before the first existing import rather than after the last. Appending would
    leave a plain ``import pytest`` below a local ``from conftest import ...``,
    which every import sorter then wants to move -- so the file would come back
    from the tool already failing the project's own lint.

    Getting this exactly right in every case needs an import sorter's knowledge
    of which package belongs to which group, which is not this tool's job. The
    first-import position is correct for the common test file, and
    :func:`summarise` says to run the formatter when it had to add the line.
    """
    after_docstring = 1
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            after_docstring = (node.end_lineno or node.lineno) + 1
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # __future__ imports must stay first of all.
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                after_docstring = (node.end_lineno or node.lineno) + 1
                continue
            return node.lineno
        break
    return after_docstring


def rewrite(source: str, counts: dict[str, int]) -> tuple[str, FileResult]:
    """Return ``source`` with markers added, and a record of what was done.

    ``counts`` maps a function's qualname to the number of queries it ran.
    """
    result = FileResult(path=Path("<memory>"))
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        result.error = f"could not parse: {error}"
        return source, result

    nodes = _qualnames(tree)
    insertions: list[Insertion] = []
    for qualname, count in sorted(counts.items()):
        node = nodes.get(qualname)
        if node is None:
            result.missing.append(qualname)
            continue
        if _already_budgeted(node):
            result.skipped.append(qualname)
            continue
        insertions.append(
            Insertion(
                lineno=node.lineno,
                indent=" " * node.col_offset,
                count=count,
                qualname=qualname,
            )
        )

    if not insertions:
        return source, result

    lines = source.splitlines(keepends=True)
    newline = _newline(lines)

    # Bottom upwards, so earlier insertions cannot shift later line numbers.
    for insertion in sorted(insertions, key=lambda item: item.lineno, reverse=True):
        lines.insert(insertion.lineno - 1, insertion.render(newline))
        result.written.append(insertion.qualname)
    result.written.reverse()

    if not _imports_pytest(tree):
        lines.insert(_import_line(tree) - 1, f"import pytest{newline}")
        result.import_added = True

    return "".join(lines), result


def _newline(lines: list[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\n"


def rewrite_file(path: Path, counts: dict[str, int]) -> FileResult:
    """Apply :func:`rewrite` to a file on disk."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        result = FileResult(path=path)
        result.error = f"could not read: {error}"
        return result

    updated, result = rewrite(source, counts)
    result.path = path
    if result.error or not result.changed:
        return result

    try:
        path.write_text(updated, encoding="utf-8")
    except OSError as error:  # pragma: no cover - permissions
        result.error = f"could not write: {error}"
        result.written.clear()
    return result


def summarise(results: list[FileResult]) -> list[str]:
    """Terminal lines describing what was written."""
    lines: list[str] = []
    total = sum(len(result.written) for result in results)
    for result in sorted(results, key=lambda item: str(item.path)):
        if result.error:
            lines.append(f"  {result.path}: {result.error}")
            continue
        if result.written:
            lines.append(f"  {result.path}: {len(result.written)} marker(s) added")
        for qualname in result.missing:
            lines.append(f"  {result.path}: could not find {qualname} in the source")

    skipped = sum(len(result.skipped) for result in results)
    if skipped:
        lines.append(f"  {skipped} test(s) already had a budget and were left alone")

    if total:
        lines += [
            "",
            f"{total} budget marker(s) written. Review the diff before committing: "
            "these numbers describe what your code does today, not what it should do.",
        ]
        if any(result.import_added for result in results):
            lines.append(
                "An 'import pytest' was added to some files. Run your formatter "
                "(ruff format, isort) if the project sorts imports."
            )
    elif not lines:
        lines.append("  nothing to write: every test with queries already has a budget")
    return lines
