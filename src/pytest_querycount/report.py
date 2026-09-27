"""The end-of-session table.

This is the part people leave switched on. A budget tells you when you crossed a
line you drew; the report tells you where the lines should go.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TestStats:
    nodeid: str
    count: int
    duration: float
    worst_duplicate: int
    worst_sql: str | None = None


def build(stats: dict[str, TestStats], top: int = 10) -> list[str]:
    """Render the summary as terminal lines. Empty when nothing was recorded."""
    recorded = [entry for entry in stats.values() if entry.count]
    if not recorded:
        return []

    recorded.sort(key=lambda entry: (entry.count, entry.duration), reverse=True)
    shown = recorded[:top]

    id_width = max(len(entry.nodeid) for entry in shown)
    id_width = min(max(id_width, 4), 70)

    lines = [
        f"{'queries':>7}  {'time':>9}  {'dupes':>5}  test",
        f"{'-' * 7}  {'-' * 9}  {'-' * 5}  {'-' * id_width}",
    ]
    for entry in shown:
        dupes = str(entry.worst_duplicate) if entry.worst_duplicate > 1 else "-"
        flag = "!" if entry.worst_duplicate > 1 else " "
        lines.append(
            f"{entry.count:>7}  {entry.duration * 1000:>7.1f}ms  "
            f"{dupes:>4}{flag}  {_trim(entry.nodeid, id_width)}"
        )

    total_queries = sum(entry.count for entry in recorded)
    total_time = sum(entry.duration for entry in recorded)
    hidden = len(recorded) - len(shown)
    footer = f"{total_queries} queries in {total_time * 1000:.1f}ms across {len(recorded)} tests"
    if hidden:
        footer += f" ({hidden} more not shown, raise --querycount-top)"
    lines += ["", footer]

    flagged = [entry for entry in shown if entry.worst_duplicate > 1]
    if flagged:
        lines.append(
            "! marks a repeated query shape -- add @pytest.mark.no_n_plus_one to see the detail."
        )
    return lines


def _trim(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return "…" + text[-(width - 1) :]
