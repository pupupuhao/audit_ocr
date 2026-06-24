from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any


def clean_cell_text(value: str) -> str:
    value = unescape(value or "")
    value = re.sub(r"\r\n?", "\n", value)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


class HTMLTableGridParser(HTMLParser):
    """Parse HTML tables into rectangular grids while expanding rowspan/colspan."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._current_rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: dict[str, Any] | None = None
        self._pending_rowspans: dict[int, list[dict[str, Any]]] = {}
        self._cell_text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._current_rows = []
                self._pending_rowspans = {}
            return
        if self._table_depth != 1:
            return
        if tag == "tr":
            self._current_row = []
            self._apply_pending_rowspans()
        elif tag in {"td", "th"} and self._current_row is not None:
            attr_map = {name.lower(): value for name, value in attrs if value is not None}
            self._current_cell = {
                "rowspan": _safe_span(attr_map.get("rowspan")),
                "colspan": _safe_span(attr_map.get("colspan")),
            }
            self._cell_text_parts = []
        elif tag == "br" and self._current_cell is not None:
            self._cell_text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "table":
            if self._table_depth == 1:
                self._flush_pending_tail_rows()
                if self._current_rows:
                    self.tables.append(_rectangular(self._current_rows))
            self._table_depth = max(0, self._table_depth - 1)
            return
        if self._table_depth != 1:
            return
        if tag in {"td", "th"} and self._current_cell is not None and self._current_row is not None:
            text = clean_cell_text("".join(self._cell_text_parts))
            rowspan = int(self._current_cell["rowspan"])
            colspan = int(self._current_cell["colspan"])
            start_col = len(self._current_row)
            for _ in range(colspan):
                self._current_row.append(text)
            if rowspan > 1:
                for offset in range(1, rowspan):
                    row_index = len(self._current_rows) + offset
                    self._pending_rowspans.setdefault(row_index, []).append(
                        {"col": start_col, "colspan": colspan, "text": text}
                    )
            self._current_cell = None
            self._cell_text_parts = []
        elif tag == "tr" and self._current_row is not None:
            self._current_rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._cell_text_parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._current_cell is not None:
            self._cell_text_parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._current_cell is not None:
            self._cell_text_parts.append(f"&#{name};")

    def _apply_pending_rowspans(self) -> None:
        if self._current_row is None:
            return
        row_index = len(self._current_rows)
        for span in sorted(self._pending_rowspans.pop(row_index, []), key=lambda item: item["col"]):
            while len(self._current_row) < span["col"]:
                self._current_row.append("")
            for offset in range(span["colspan"]):
                insert_at = span["col"] + offset
                if insert_at <= len(self._current_row):
                    self._current_row.insert(insert_at, span["text"])
                else:
                    self._current_row.append(span["text"])

    def _flush_pending_tail_rows(self) -> None:
        while self._pending_rowspans:
            next_row = min(self._pending_rowspans)
            while len(self._current_rows) < next_row:
                self._current_rows.append([])
            row: list[str] = []
            for span in sorted(self._pending_rowspans.pop(next_row, []), key=lambda item: item["col"]):
                while len(row) < span["col"]:
                    row.append("")
                for _ in range(span["colspan"]):
                    row.append(span["text"])
            self._current_rows.append(row)


def _safe_span(value: str | None) -> int:
    try:
        return max(1, int(str(value or "1").strip()))
    except ValueError:
        return 1


def _rectangular(rows: list[list[str]]) -> list[list[str]]:
    max_cols = max((len(row) for row in rows), default=0)
    return [row + [""] * (max_cols - len(row)) for row in rows]


def parse_html_tables(html: str) -> list[list[list[str]]]:
    parser = HTMLTableGridParser()
    parser.feed(html)
    return parser.tables
