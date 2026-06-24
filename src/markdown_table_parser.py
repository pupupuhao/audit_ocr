from __future__ import annotations

import re


def _clean_cell(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.IGNORECASE)
    value = value.replace("\\|", "__PIPE__")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    return value.replace("__PIPE__", "|").strip()


def _looks_like_separator(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    stripped = stripped.strip("|").strip()
    if not stripped:
        return False
    parts = [part.strip() for part in stripped.split("|")]
    return bool(parts) and all(re.fullmatch(r":?-{2,}:?", part or "") for part in parts)


def _split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [_clean_cell(part) for part in re.split(r"(?<!\\)\|", stripped)]


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.count("|") >= 2


def _normalize_grid(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return []
    max_cols = max(len(row) for row in rows)
    return [row + [""] * (max_cols - len(row)) for row in rows]


def parse_markdown_tables(markdown: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        rows = []
        for line in current:
            if _looks_like_separator(line):
                continue
            row = _split_markdown_row(line)
            if any(cell.strip() for cell in row):
                rows.append(row)
        if len(rows) >= 2:
            tables.append(_normalize_grid(rows))
        current = []

    for line in (markdown or "").splitlines():
        if _is_table_line(line):
            current.append(line)
        else:
            flush()
    flush()
    return tables
