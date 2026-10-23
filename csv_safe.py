"""CSV helpers that mitigate spreadsheet formula injection."""

from __future__ import annotations

from typing import Any, Iterable

import defusedcsv


def csv_safe(value: Any) -> str:
    """Prefix formula-like cells so Excel/Sheets do not execute them."""
    if value is None:
        return ""
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def write_csv(buf, headers: Iterable[Any], rows: Iterable[Iterable[Any]]) -> None:
    """Write headers + rows via defusedcsv with formula-safe cells."""
    writer = defusedcsv.writer(buf)
    writer.writerow([csv_safe(h) for h in headers])
    for row in rows:
        writer.writerow([csv_safe(c) for c in row])
