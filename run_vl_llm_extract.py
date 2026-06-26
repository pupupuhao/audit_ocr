#!/usr/bin/env python3
"""Run hybrid rule/Qwen extraction from existing PaddleOCR-VL OCR outputs.

This is an independent experiment path:
PaddleOCR-VL output directory -> rule page/table classification -> local Qwen field extraction -> business JSON.
It does not run OCR; use run_auto_vl_eval.py to generate VL/OCR inputs first.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from src.llm_extractor import (
    clean_construction_process_text,
    extract_table_data,
    normalize_data,
    set_debug_dir,
)
from src.html_table_parser import parse_html_tables
from src.markdown_table_parser import parse_markdown_tables
from src.document_info_extractor import extract_document_info
from src.field_extractor import extract_construction_processes_from_pages, extract_sub_item_project_rows
from src.business_extractor import classify_page as classify_page_by_rules
from src.models import AuditProject, SubProject
from src.sub_project_normalizer import clean_sub_project_name, normalize_sub_project_ids


RULE_TYPE_TO_LLM_TYPE = {
    "unit_project_fee_table": "unit_project_fee_table",
    "sub_item_project_table": "sub_item_project_table",
    "quantity_confirmation_form": "quantity_confirm_table",
    "specialty_fee_table": "specialty_fee_table",
    "quantity_resource_subtable_labor": "labor_table",
    "quantity_resource_subtable_material_equipment": "material_table",
    "quantity_resource_subtable_machine": "machine_table",
}

# Keep every classifier, prompt, and output schema in place.  These entries
# only control what the current VL -> Qwen run sends for field extraction.
# To restore a table type later, comment out its corresponding line here.
DISABLED_RULE_TABLE_TYPES = {
    "unit_project_fee_table",                 # 单位（专业）工程费用表 / 招标控制价费用表
    "specialty_fee_table",                    # 专业工程费用表 / 专业费用表
    "quantity_resource_subtable_labor",       # 主要工日一览表
    "quantity_resource_subtable_material_equipment",  # 主要材料和工程设备一览表
    "quantity_resource_subtable_machine",     # 主要机械台班一览表
    "quantity_confirmation_form",             # 工程量确认单
}

# Keep the construction-process parser and Qwen cleanup path available, but
# do not run it for the current export. Set this back to True when needed.
ENABLE_CONSTRUCTION_PROCESS_EXTRACTION = False

ROW_TYPE_MAP = {
    "unit_project_fee_table": "unit_project_fee_rows",
    "sub_item_project_table": "sub_item_project_rows",
}

SUB_TABLE_TYPES = {"labor_table", "material_table", "machine_table"}
MAX_CELL_CHARS = 500


def clean_amount_value(value: Any) -> str:
    return re.sub(r"[^\d.]", "", str(value or ""))


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def is_number_text(value: Any) -> bool:
    text = str(value or "").strip()
    text = re.sub(r"[￥¥,\s]", "", text)
    return bool(text and re.fullmatch(r"\d+(?:\.\d+)?", text))


def is_sub_item_summary_like_row(row: dict[str, Any]) -> bool:
    seq = str(row.get("seq", "") or "").strip()
    project_code = str(row.get("project_code", "") or "").strip()
    project_name = compact_text(row.get("project_name", ""))
    if re.fullmatch(r"\d+", seq) or project_code:
        if not project_code or not re.fullmatch(r"\d+(?:#|栋|幢|号楼)", project_code):
            return False
        return not any(
            str(row.get(field, "") or "").strip()
            for field in ("project_name", "project_description", "unit", "quantity", "remark")
        )
    if project_name in {"本页小计", "小计", "合计"}:
        return True
    if re.fullmatch(r"\d+(?:#|栋|幢|号楼)?", project_name):
        return True
    return False


def can_index_backfill_sub_item(row: dict[str, Any]) -> bool:
    seq = str(row.get("seq", "") or "").strip()
    project_code = str(row.get("project_code", "") or "").strip()
    project_name = str(row.get("project_name", "") or "").strip()
    return bool(re.fullmatch(r"\d+", seq) or project_code or (seq and project_name))


def find_table_column(grid: list[Any], keywords: list[str], max_rows: int = 8) -> int | None:
    by_col: dict[int, list[str]] = {}
    for row in grid[:max_rows]:
        if not isinstance(row, list):
            continue
        for col_index, cell in enumerate(row):
            by_col.setdefault(col_index, []).append(str(cell or ""))

    for col_index, values in by_col.items():
        combined = compact_text(" ".join(values))
        if all(compact_text(keyword) in combined for keyword in keywords):
            return col_index
    return None


def find_best_grid_row_for_sub_item(row: dict[str, Any], grid: list[Any]) -> list[str] | None:
    best_score = 0
    best_row: list[str] | None = None
    seq = str(row.get("seq", "") or "").strip()
    project_code = str(row.get("project_code", "") or "").strip()
    project_name = compact_text(row.get("project_name", ""))
    quantity = clean_amount_value(row.get("quantity"))
    total_price = clean_amount_value(row.get("total_price"))

    for raw_row in grid:
        if not isinstance(raw_row, list):
            continue
        cells = [str(cell or "").strip() for cell in raw_row]
        row_compact = compact_text(" ".join(cells))
        amount_cells = [clean_amount_value(cell) for cell in cells]
        score = 0
        if seq and any(cell == seq for cell in cells):
            score += 3
        if project_code and any(cell == project_code for cell in cells):
            score += 5
        if project_name and project_name in row_compact:
            score += 3
        if quantity and quantity in amount_cells:
            score += 2
        if total_price and total_price in amount_cells:
            score += 3
        if score > best_score:
            best_score = score
            best_row = cells

    return best_row if best_score >= 5 else None


def find_unit_price_in_grid_row(row: dict[str, Any], cells: list[str], unit_price_col: int | None, total_col: int | None) -> str:
    total_price = clean_amount_value(row.get("total_price"))
    quantity = clean_amount_value(row.get("quantity"))
    seq = str(row.get("seq", "") or "").strip()

    scan_from = None
    if total_price:
        for index, cell in enumerate(cells):
            if clean_amount_value(cell) == total_price:
                scan_from = index
                break

    if scan_from is not None:
        for index in range(scan_from - 1, -1, -1):
            value = clean_amount_value(cells[index]) if is_number_text(cells[index]) else ""
            if value and value not in {quantity, seq}:
                return value

    if unit_price_col is not None and unit_price_col < len(cells):
        value = clean_amount_value(cells[unit_price_col]) if is_number_text(cells[unit_price_col]) else ""
        if value and value not in {quantity, seq, total_price}:
            return value

    scan_from = total_col
    if scan_from is not None:
        for index in range(scan_from - 1, -1, -1):
            value = clean_amount_value(cells[index]) if is_number_text(cells[index]) else ""
            if value and value not in {quantity, seq, total_price}:
                return value

    if quantity:
        quantity_index = None
        for index, cell in enumerate(cells):
            if clean_amount_value(cell) == quantity:
                quantity_index = index
                break
        if quantity_index is not None:
            for cell in cells[quantity_index + 1:]:
                value = clean_amount_value(cell) if is_number_text(cell) else ""
                if value and value != total_price:
                    return value

    return ""


def backfill_sub_item_unit_price_directly(rows: list[dict[str, Any]], table: dict[str, Any]) -> list[dict[str, Any]]:
    grid = table.get("grid", [])
    if not isinstance(grid, list) or not grid:
        return rows

    unit_price_col = find_table_column(grid, ["综合单价"])
    total_col = find_table_column(grid, ["合价"])

    for row in rows:
        if clean_amount_value(row.get("unit_price")):
            continue
        cells = find_best_grid_row_for_sub_item(row, grid)
        if not cells:
            continue
        unit_price = find_unit_price_in_grid_row(row, cells, unit_price_col, total_col)
        if unit_price:
            row["unit_price"] = unit_price
    return rows


def backfill_sub_item_unit_price_from_grid(
    rows: list[dict[str, Any]],
    table: dict[str, Any],
    page_no: int,
) -> list[dict[str, Any]]:
    """Repair Qwen sub-item rows from the same VL table grid when possible."""
    if not rows:
        return rows

    rule_rows = [
        row.to_dict()
        for row in extract_sub_item_project_rows(table, page_no=page_no)
        if is_number_text(row.unit_price)
    ]
    by_project_code = {
        str(row.get("project_code", "") or "").strip(): row
        for row in rule_rows
        if str(row.get("project_code", "") or "").strip()
    }
    by_seq_name = {
        (
            str(row.get("seq", "") or "").strip(),
            str(row.get("project_name", "") or "").strip(),
        ): row
        for row in rule_rows
        if str(row.get("seq", "") or "").strip() or str(row.get("project_name", "") or "").strip()
    }

    filtered_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        project_code = str(row.get("project_code", "") or "").strip()
        seq = str(row.get("seq", "") or "").strip()
        if is_sub_item_summary_like_row(row):
            continue
        is_sub_project_summary = (
            bool(re.fullmatch(r"\d+\s*#", project_code))
            and not re.fullmatch(r"\d+", seq)
            and not any(
                str(row.get(field, "") or "").strip()
                for field in ("project_name", "project_description", "unit", "quantity")
            )
        )
        if is_sub_project_summary:
            # Rows such as "37#" contain only a sub-project subtotal. They are
            # not bill-of-quantities items, even when they occur at a table's top.
            continue

        seq_name_key = (
            seq,
            str(row.get("project_name", "") or "").strip(),
        )
        source_row = by_project_code.get(project_code) if project_code else None
        if source_row is None:
            source_row = by_seq_name.get(seq_name_key)
        if source_row is None and can_index_backfill_sub_item(row) and index < len(rule_rows):
            source_row = rule_rows[index]

        if not re.fullmatch(r"\d+", seq):
            source_seq = str(source_row.get("seq", "") or "").strip() if source_row else ""
            if re.fullmatch(r"\d+", source_seq):
                row["seq"] = source_seq
            else:
                cells = find_best_grid_row_for_sub_item(row, table.get("grid", []))
                grid_seq = str(cells[0] or "").strip() if cells else ""
                # Never keep a table header (for example "序号") as data.
                row["seq"] = grid_seq if re.fullmatch(r"\d+", grid_seq) else ""

        if not clean_amount_value(row.get("unit_price")) and source_row is not None:
            unit_price = clean_amount_value(source_row.get("unit_price")) if is_number_text(source_row.get("unit_price")) else ""
            if unit_price:
                row["unit_price"] = unit_price
        filtered_rows.append(row)

    return backfill_sub_item_unit_price_directly(filtered_rows, table)


def should_switch_sub_project(rule_table_types: list[str], current_sub_project: str) -> bool:
    return "sub_item_project_table" in rule_table_types


def page_no_from_path(path: Path) -> int:
    match = re.search(r"page[_-](\d+)", path.stem)
    return int(match.group(1)) if match else 0


def collect_block_content(obj: Any) -> list[str]:
    values: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in {"block_content", "markdown_texts", "text"} and isinstance(value, str):
                if value.strip():
                    values.append(value.strip())
            else:
                values.extend(collect_block_content(value))
    elif isinstance(obj, list):
        for item in obj:
            values.extend(collect_block_content(item))
    return values


def render_grid_for_qwen(grid: list[list[str]], source_name: str, table_index: int) -> str:
    lines = [f"HTML_TABLE source={source_name} table_index={table_index}"]
    for row_index, row in enumerate(grid, start=1):
        cells = []
        for cell in row:
            text = str(cell or "").strip().replace("\n", "\\n")
            if len(text) > MAX_CELL_CHARS:
                text = text[:MAX_CELL_CHARS] + "...[truncated]"
            cells.append(text)
        lines.append(f"row_{row_index:03d}: " + " | ".join(cells))
    return "\n".join(lines)


def read_html_text(page_json: Path, page_suffix: str = "vl") -> str:
    parts = []
    for html_path in html_paths_for_page(page_json, page_suffix):
        html = html_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not html:
            continue
        tables = parse_html_tables(html)
        if tables:
            for table_index, grid in enumerate(tables, start=1):
                parts.append(render_grid_for_qwen(grid, html_path.name, table_index))
        else:
            parts.append(f"HTML_SOURCE {html_path.name}\n{html}")
    return "\n\n".join(parts).strip()


def read_markdown_text(page_json: Path, page_suffix: str = "vl") -> str:
    page_no = page_no_from_path(page_json)
    md_path = page_json.with_name(f"page_{page_no:03d}_{page_suffix}.md")
    if md_path.exists():
        return md_path.read_text(encoding="utf-8", errors="ignore").strip()
    return ""


def read_json_text(page_json: Path) -> str:
    try:
        payload = json.loads(page_json.read_text(encoding="utf-8"))
    except Exception:
        return ""

    blocks = collect_block_content(payload)
    seen: set[str] = set()
    deduped: list[str] = []
    for block in blocks:
        if block not in seen:
            seen.add(block)
            deduped.append(block)
    return "\n\n".join(deduped).strip()


def read_vl_page_text(page_json: Path, page_suffix: str = "vl", prefer_source: str = "auto") -> str:
    html_text = ""
    md_text = ""
    json_text = ""

    if prefer_source in {"html", "all"}:
        html_text = read_html_text(page_json, page_suffix)
    if prefer_source in {"md", "all"}:
        md_text = read_markdown_text(page_json, page_suffix)
    if prefer_source in {"json", "all"}:
        json_text = read_json_text(page_json)

    if prefer_source == "html":
        return html_text or read_markdown_text(page_json, page_suffix) or read_json_text(page_json)
    if prefer_source == "md":
        return md_text or read_json_text(page_json) or read_html_text(page_json, page_suffix)
    if prefer_source == "json":
        return json_text or read_markdown_text(page_json, page_suffix) or read_html_text(page_json, page_suffix)
    if prefer_source == "all":
        parts = []
        if html_text:
            parts.append("以下是VL输出的HTML表格结构：\n" + html_text)
        if md_text:
            parts.append("以下是VL输出的Markdown/文本：\n" + md_text)
        if json_text:
            parts.append("以下是VL JSON中的文本块：\n" + json_text)
        return "\n\n".join(parts).strip()

    return read_markdown_text(page_json, page_suffix) or read_json_text(page_json) or read_html_text(page_json, page_suffix)


def html_paths_for_page(page_json: Path, page_suffix: str = "vl") -> list[Path]:
    page_no = page_no_from_path(page_json)
    paths = [page_json.with_name(f"page_{page_no:03d}_{page_suffix}.html")]
    paths.extend(sorted(page_json.parent.glob(f"page_{page_no:03d}_{page_suffix}_*.html")))
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path.exists() and path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def read_html_tables(page_json: Path, page_suffix: str = "vl") -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for html_path in html_paths_for_page(page_json, page_suffix):
        html = html_path.read_text(encoding="utf-8", errors="ignore")
        for table_index, grid in enumerate(parse_html_tables(html), start=1):
            tables.append(
                {
                    "grid": grid,
                    "source_type": "html",
                    "source_path": str(html_path),
                    "table_index": table_index,
                }
            )
    return tables


def read_markdown_tables(page_json: Path, page_suffix: str = "vl") -> list[dict[str, Any]]:
    md_text = read_markdown_text(page_json, page_suffix)
    if not md_text:
        return []
    return [
        {
            "grid": grid,
            "source_type": "md",
            "source_path": str(page_json.with_name(f"page_{page_no_from_path(page_json):03d}_{page_suffix}.md")),
            "table_index": table_index,
        }
        for table_index, grid in enumerate(parse_markdown_tables(md_text), start=1)
    ]


def read_vl_page_tables(page_json: Path, page_suffix: str = "vl", prefer_source: str = "auto") -> list[dict[str, Any]]:
    html_tables = read_html_tables(page_json, page_suffix)
    md_tables = read_markdown_tables(page_json, page_suffix)

    if prefer_source == "html":
        return html_tables or md_tables
    if prefer_source == "md":
        return md_tables or html_tables
    if prefer_source == "all":
        return html_tables + md_tables
    if prefer_source == "json":
        return md_tables or html_tables
    return html_tables or md_tables


def find_pdf_dirs(vl_root: Path, file_filter: str | None, source_subdir: str) -> list[Path]:
    vl_dir = vl_root / source_subdir
    if not vl_dir.exists():
        raise FileNotFoundError(f"VL directory not found: {vl_dir}")

    dirs = sorted([path for path in vl_dir.iterdir() if path.is_dir()])
    if file_filter:
        dirs = [path for path in dirs if file_filter in path.name]
    return dirs


def read_page_texts_from_source_root(source_root: Path, pdf_name: str) -> dict[int, dict[str, Any]]:
    text_dir = source_root / "page_texts" / pdf_name
    pages: dict[int, dict[str, Any]] = {}
    if not text_dir.exists():
        return pages
    for text_path in sorted(text_dir.glob("page_*_text.json")):
        try:
            payload = json.loads(text_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        page_no = int(payload.get("page_no") or page_no_from_path(text_path) or 0)
        if not page_no:
            continue
        pages[page_no] = {
            "page_no": page_no,
            "text": str(payload.get("text") or ""),
            "lines": payload.get("lines") or [],
            "text_path": str(text_path),
            "source": "page_texts",
        }
    return pages


def _parse_page_numbers(values: list[str] | None) -> list[int] | None:
    if not values:
        return None
    pages: list[int] = []
    for value in values:
        for part in str(value).replace("，", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                page_no = int(part)
            except ValueError as exc:
                raise ValueError(f"invalid --page value: {part}") from exc
            if page_no <= 0:
                raise ValueError(f"--page must be positive: {page_no}")
            pages.append(page_no)
    return sorted(dict.fromkeys(pages))


def _filter_page_numbers(
    page_numbers: list[int],
    start_page: int,
    end_page: int,
    max_pages: int,
    selected_pages: list[int] | None = None,
) -> list[int]:
    result = sorted(page_numbers)
    if selected_pages:
        selected_page_set = set(selected_pages)
        result = [page_no for page_no in result if page_no in selected_page_set]
    if start_page > 0:
        result = [page_no for page_no in result if page_no >= start_page]
    if end_page > 0:
        result = [page_no for page_no in result if page_no <= end_page]
    if max_pages > 0:
        result = result[:max_pages]
    return result


def is_partial_page_run(args: argparse.Namespace) -> bool:
    return bool(args.page_numbers) or args.start_page > 0 or args.end_page > 0 or args.max_pages > 0


def _row_page_no(row: Any) -> int:
    if not isinstance(row, dict):
        return 0
    try:
        return int(row.get("page_no") or 0)
    except (TypeError, ValueError):
        return 0


def _rows_not_in_pages(rows: Any, page_set: set[int]) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and _row_page_no(row) not in page_set]


def _merge_document_info(existing: Any, current: Any) -> dict[str, Any]:
    existing_info = dict(existing) if isinstance(existing, dict) else {}
    current_info = current if isinstance(current, dict) else {}
    if not current_info:
        return existing_info

    merged = dict(existing_info)
    for key, value in current_info.items():
        if isinstance(value, dict):
            nested_existing = merged.get(key) if isinstance(merged.get(key), dict) else {}
            nested_value = {
                nested_key: nested_val
                for nested_key, nested_val in value.items()
                if nested_val not in ("", None, [], {})
            }
            if nested_value:
                merged[key] = {**nested_existing, **nested_value}
        elif value not in ("", None, [], {}):
            merged[key] = value
    return merged


def merge_business_extract_by_pages(
    existing: dict[str, Any],
    current: dict[str, Any],
    page_numbers: list[int],
) -> dict[str, Any]:
    """Merge a partial-page extraction into an existing business JSON."""
    page_set = set(page_numbers)
    if not existing or not page_set:
        return current

    merged = dict(existing)
    for key, value in current.items():
        if key not in {
            "sub_projects",
            "specialty_fee_rows",
            "labor_rows",
            "material_rows",
            "machine_rows",
            "construction_processes",
            "pages",
            "document_info",
            "total_pages",
            "doc_id",
            "created_at",
        }:
            merged[key] = value

    merged["document_info"] = _merge_document_info(
        merged.get("document_info"),
        current.get("document_info"),
    )
    merged["total_pages"] = existing.get("total_pages") or current.get("total_pages", 0)

    for key in [
        "specialty_fee_rows",
        "quantity_confirm_rows",
        "labor_rows",
        "material_rows",
        "machine_rows",
        "construction_processes",
    ]:
        merged[key] = _rows_not_in_pages(existing.get(key), page_set) + [
            row for row in current.get(key, []) if isinstance(row, dict)
        ]

    existing_sub_projects = existing.get("sub_projects") if isinstance(existing.get("sub_projects"), list) else []
    current_sub_projects = current.get("sub_projects") if isinstance(current.get("sub_projects"), list) else []
    sub_project_map: dict[str, dict[str, Any]] = {}

    for sub_project in existing_sub_projects:
        if not isinstance(sub_project, dict):
            continue
        sp_id = str(sub_project.get("sub_project_id") or sub_project.get("sub_project_name") or "")
        item = dict(sub_project)
        legacy_quantity_rows = item.pop("quantity_confirm_rows", [])
        if isinstance(legacy_quantity_rows, list):
            merged.setdefault("quantity_confirm_rows", [])
            merged["quantity_confirm_rows"].extend(
                row for row in _rows_not_in_pages(legacy_quantity_rows, page_set) if isinstance(row, dict)
            )
        for row_key in ["unit_project_fee_rows", "sub_item_project_rows"]:
            item[row_key] = _rows_not_in_pages(item.get(row_key), page_set)
        sub_project_map[sp_id] = item

    for sub_project in current_sub_projects:
        if not isinstance(sub_project, dict):
            continue
        sp_id = str(sub_project.get("sub_project_id") or sub_project.get("sub_project_name") or "")
        if not sp_id:
            continue
        target = sub_project_map.setdefault(
            sp_id,
            {
                "sub_project_id": sp_id,
                "sub_project_name": sub_project.get("sub_project_name", sp_id),
                "parent_project": sub_project.get("parent_project", ""),
                "unit_project_fee_rows": [],
                "sub_item_project_rows": [],
            },
        )
        target["sub_project_name"] = sub_project.get("sub_project_name", target.get("sub_project_name", sp_id))
        target["parent_project"] = sub_project.get("parent_project", target.get("parent_project", ""))
        for row_key in ["unit_project_fee_rows", "sub_item_project_rows"]:
            rows = sub_project.get(row_key)
            if isinstance(rows, list):
                target.setdefault(row_key, [])
                target[row_key].extend(row for row in rows if isinstance(row, dict))
        legacy_quantity_rows = sub_project.get("quantity_confirm_rows")
        if isinstance(legacy_quantity_rows, list):
            merged.setdefault("quantity_confirm_rows", [])
            merged["quantity_confirm_rows"].extend(row for row in legacy_quantity_rows if isinstance(row, dict))

    merged["sub_projects"] = list(sub_project_map.values())

    existing_pages = existing.get("pages") if isinstance(existing.get("pages"), list) else []
    current_pages = current.get("pages") if isinstance(current.get("pages"), list) else []
    merged["pages"] = _rows_not_in_pages(existing_pages, page_set) + [
        page for page in current_pages if isinstance(page, dict)
    ]
    merged["pages"] = sorted(merged["pages"], key=lambda item: _row_page_no(item))
    return merged


def page_range_suffix(page_numbers: list[int]) -> str:
    if not page_numbers:
        return "pages_none"
    if len(page_numbers) == 1:
        return f"page_{page_numbers[0]:03d}"
    return f"pages_{min(page_numbers):03d}_{max(page_numbers):03d}"


def ensure_sub_project(
    sub_projects_map: dict[str, dict[str, Any]],
    sub_project_id: str,
    sub_project_name: str | None = None,
) -> None:
    if sub_project_id in sub_projects_map:
        return
    sub_projects_map[sub_project_id] = {
        "sub_project_id": sub_project_id,
        "sub_project_name": sub_project_name or sub_project_id,
        "unit_project_fee_rows": [],
        "sub_item_project_rows": [],
    }


def add_rows_to_project_state(
    *,
    table_type: str,
    normalized: list[dict[str, Any]],
    page_no: int,
    current_sub_project: str,
    sub_projects_map: dict[str, dict[str, Any]],
    specialty_fee_rows: list[dict[str, Any]],
    labor_rows: list[dict[str, Any]],
    material_rows: list[dict[str, Any]],
    machine_rows: list[dict[str, Any]],
    quantity_confirm_rows: list[dict[str, Any]],
) -> None:
    def valid_sub_item_row(row: dict[str, Any]) -> bool:
        project_code = str(row.get("project_code", "") or "").strip()
        has_project_code = bool(re.fullmatch(r"\d{9,15}", project_code))
        has_business_columns = any(
            str(row.get(field, "") or "").strip()
            for field in ["project_description", "unit", "quantity", "unit_price", "total_price"]
        )
        return not (project_code and not has_project_code and not has_business_columns)

    if table_type == "sub_item_project_table":
        fixed_rows = []
        for row in normalized:
            if not valid_sub_item_row(row):
                continue
            item = dict(row)
            labor_cost = str(item.get("labor_cost", "") or "").strip()
            provisional_estimate = str(item.get("provisional_estimate", "") or "").strip()
            if labor_cost and provisional_estimate and labor_cost == provisional_estimate:
                item["provisional_estimate"] = ""
            fixed_rows.append(item)
        normalized = fixed_rows

    for row in normalized:
        row["page_no"] = page_no

    if table_type == "unit_project_fee_table":
        seen = set()
        deduped = []
        for row in normalized:
            key = (row.get("seq", ""), row.get("fee_name", ""), row.get("amount", ""))
            if key not in seen:
                seen.add(key)
                deduped.append(row)
        normalized = deduped

    if table_type in ROW_TYPE_MAP:
        row_key = ROW_TYPE_MAP[table_type]
        sp = sub_projects_map[current_sub_project]
        for row in normalized:
            row["sub_project_id"] = current_sub_project
            sp[row_key].append(row)
    elif table_type == "specialty_fee_table":
        specialty_fee_rows.extend(normalized)
    elif table_type == "quantity_confirm_table":
        quantity_confirm_rows.extend(normalized)
    elif table_type in SUB_TABLE_TYPES:
        if table_type == "labor_table":
            labor_rows.extend(normalized)
        elif table_type == "material_table":
            material_rows.extend(normalized)
        elif table_type == "machine_table":
                machine_rows.extend(normalized)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hybrid rule/Qwen business extraction from existing PaddleOCR-VL outputs."
    )
    parser.add_argument("--vl-output", required=True, help="VL output root, e.g. output_vl or output_auto_vl.")
    parser.add_argument("--output", required=True, help="Output directory for VL+Qwen JSON.")
    parser.add_argument("--file", help="Specific PDF folder name to process, without requiring .pdf suffix.")
    parser.add_argument("--source-subdir", default="vl", help="Source subdirectory under --vl-output. Default: vl")
    parser.add_argument("--page-suffix", default="vl", help="Page file suffix before .json/.md. Default: vl")
    parser.add_argument(
        "--prefer-source",
        choices=["auto", "html", "md", "json", "all"],
        default="auto",
        help="Which VL artifact to read. auto uses HTML tables first, then MD tables; page text still falls back to MD/JSON/HTML.",
    )
    parser.add_argument("--page", action="append", help="Specific 1-based page number(s), comma-separated or repeated.")
    parser.add_argument("--start-page", type=int, default=0, help="First page number to process, 1-based.")
    parser.add_argument("--end-page", type=int, default=0, help="Last page number to process, inclusive.")
    parser.add_argument("--max-pages", type=int, default=0, help="Max pages to process per PDF after page filtering.")
    parser.add_argument("--debug-llm", action="store_true", help="Save raw Qwen prompts and responses.")
    args = parser.parse_args()
    try:
        args.page_numbers = _parse_page_numbers(args.page)
    except ValueError as exc:
        parser.error(str(exc))

    vl_root = Path(args.vl_output)
    output_root = Path(args.output)
    business_root = output_root / "business_json_vl_llm"
    report_root = output_root / "reports"
    debug_root = output_root / "debug_llm"
    business_root.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)

    try:
        pdf_dirs = find_pdf_dirs(vl_root, args.file, args.source_subdir)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    if not pdf_dirs:
        print("No matching VL output folders found.")
        sys.exit(1)

    all_summaries: list[dict[str, Any]] = []

    for pdf_dir in pdf_dirs:
        pdf_name = pdf_dir.name
        print(f"\nProcessing VL output: {pdf_name}")
        start = time.time()
        if args.debug_llm:
            set_debug_dir(debug_root / pdf_name)
        else:
            set_debug_dir(None)

        vl_page_files = {
            page_no_from_path(path): path
            for path in sorted(pdf_dir.glob(f"page_*_{args.page_suffix}.json"))
            if page_no_from_path(path)
        }
        page_texts_by_no = read_page_texts_from_source_root(vl_root, pdf_name)
        page_numbers = _filter_page_numbers(
            sorted(set(vl_page_files) | set(page_texts_by_no)),
            args.start_page,
            args.end_page,
            args.max_pages,
            args.page_numbers,
        )

        sub_projects_map: dict[str, dict[str, Any]] = {}
        current_sub_project = ""
        specialty_fee_rows: list[dict[str, Any]] = []
        quantity_confirm_rows: list[dict[str, Any]] = []
        labor_rows: list[dict[str, Any]] = []
        material_rows: list[dict[str, Any]] = []
        machine_rows: list[dict[str, Any]] = []
        construction_processes: list[dict[str, Any]] = []
        text_pages: list[dict[str, Any]] = []
        page_summaries: list[dict[str, Any]] = []
        document_info = extract_document_info(text_pages)

        for page_no in page_numbers:
            page_file = vl_page_files.get(page_no)
            base_text_page = page_texts_by_no.get(page_no, {})
            rapid_page_text = str(base_text_page.get("text") or "")
            page_text = rapid_page_text
            page_text_path = str(base_text_page.get("text_path") or "")
            page_tables: list[dict[str, Any]] = []
            if page_file:
                vl_page_text = read_vl_page_text(
                    page_file,
                    page_suffix=args.page_suffix,
                    prefer_source=args.prefer_source,
                )
                if vl_page_text:
                    page_text = vl_page_text
                    page_text_path = str(page_file)
                page_tables = read_vl_page_tables(
                    page_file,
                    page_suffix=args.page_suffix,
                    prefer_source=args.prefer_source,
                )
            if not page_text and not page_tables:
                print(f"  Page {page_no}: skip (empty text)")
                page_summaries.append({"page_no": page_no, "table_type": "empty", "rows": 0})
                continue
            rule_page_text = (
                f"{rapid_page_text}\n{page_text}"
                if rapid_page_text and rapid_page_text != page_text
                else page_text
            )
            text_pages.append({"page_no": page_no, "text": rule_page_text, "text_path": page_text_path})
            document_info = extract_document_info(text_pages)

            if not page_tables:
                classification = classify_page_by_rules(rule_page_text, None)
                print(
                    f"  Page {page_no}: rules only "
                    f"({','.join(classification.get('matched_keywords', [])) or 'unknown'})"
                )
                page_summaries.append(
                    {
                        "page_no": page_no,
                        "table_types": [],
                        "sub_project_name": current_sub_project,
                        "rows": 0,
                        "source": str(page_file) if page_file else page_text_path,
                        "prefer_source": args.prefer_source,
                        "input_chars": len(page_text),
                        "table_count": 0,
                        "extractor": "rules_pipeline_no_table",
                    }
                )
                continue

            page_row_count = 0
            page_table_types: list[str] = []
            print(f"  Page {page_no}: rules classify + Qwen fields...", end=" ", flush=True)
            for table_index, table in enumerate(page_tables, start=1):
                classification = classify_page_by_rules(rule_page_text, table)
                detected_rule_table_types = classification.get("table_types", [])
                disabled_rule_table_types = [
                    table_type
                    for table_type in detected_rule_table_types
                    if table_type in DISABLED_RULE_TABLE_TYPES
                ]
                rule_table_types = [
                    table_type
                    for table_type in detected_rule_table_types
                    if table_type not in DISABLED_RULE_TABLE_TYPES
                ]
                sub_project_name = classification.get("sub_project_name", "")
                cleaned_sub_project = clean_sub_project_name(
                    sub_project_name,
                    file_name=pdf_name,
                    document_info=document_info,
                ) if sub_project_name else ""
                if cleaned_sub_project and should_switch_sub_project(rule_table_types, current_sub_project):
                    current_sub_project = cleaned_sub_project

                for disabled_table_type in disabled_rule_table_types:
                    page_table_types.append(f"skip:{disabled_table_type}")
                    page_summaries.append(
                        {
                            "page_no": page_no,
                            "table_index": table_index,
                            "table_type": disabled_table_type,
                            "sub_project_name": current_sub_project,
                            "rows": 0,
                            "source": str(page_file) if page_file else page_text_path,
                            "table_source_type": table.get("source_type", ""),
                            "table_source_path": table.get("source_path", ""),
                            "prefer_source": args.prefer_source,
                            "input_chars": len(page_text),
                            "extractor": "rules_pipeline_disabled_by_config",
                            "skip_reason": "disabled_rule_table_type",
                        }
                    )

                for rule_table_type in rule_table_types:
                    llm_table_type = RULE_TYPE_TO_LLM_TYPE.get(rule_table_type)
                    if not llm_table_type:
                        continue

                    if llm_table_type in ROW_TYPE_MAP:
                        if not current_sub_project:
                            current_sub_project = "(root)"
                        ensure_sub_project(
                            sub_projects_map,
                            current_sub_project,
                            current_sub_project,
                        )

                    raw_rows = extract_table_data(table, llm_table_type)
                    normalized = normalize_data(raw_rows, llm_table_type)
                    if llm_table_type == "sub_item_project_table":
                        normalized = backfill_sub_item_unit_price_from_grid(
                            normalized,
                            table,
                            page_no,
                        )
                    if llm_table_type in ROW_TYPE_MAP and current_sub_project:
                        add_rows_to_project_state(
                            table_type=llm_table_type,
                            normalized=normalized,
                            page_no=page_no,
                            current_sub_project=current_sub_project,
                            sub_projects_map=sub_projects_map,
                            specialty_fee_rows=specialty_fee_rows,
                            labor_rows=labor_rows,
                            material_rows=material_rows,
                            machine_rows=machine_rows,
                            quantity_confirm_rows=quantity_confirm_rows,
                        )
                    elif llm_table_type in {"specialty_fee_table", "quantity_confirm_table", *SUB_TABLE_TYPES}:
                        add_rows_to_project_state(
                            table_type=llm_table_type,
                            normalized=normalized,
                            page_no=page_no,
                            current_sub_project=current_sub_project or "(root)",
                            sub_projects_map=sub_projects_map,
                            specialty_fee_rows=specialty_fee_rows,
                            labor_rows=labor_rows,
                            material_rows=material_rows,
                            machine_rows=machine_rows,
                            quantity_confirm_rows=quantity_confirm_rows,
                        )

                    page_row_count += len(normalized)
                    page_table_types.append(llm_table_type)
                    page_summaries.append(
                        {
                            "page_no": page_no,
                            "table_index": table_index,
                            "table_type": llm_table_type,
                            "rule_table_type": rule_table_type,
                            "sub_project_name": current_sub_project,
                            "rows": len(normalized),
                            "source": str(page_file) if page_file else page_text_path,
                            "table_source_type": table.get("source_type", ""),
                            "table_source_path": table.get("source_path", ""),
                            "prefer_source": args.prefer_source,
                            "input_chars": len(page_text),
                            "extractor": "rules_pipeline_qwen_fields",
                        }
                    )

                if not detected_rule_table_types:
                    page_summaries.append(
                        {
                            "page_no": page_no,
                            "table_index": table_index,
                            "table_type": "unknown",
                            "sub_project_name": current_sub_project,
                            "rows": 0,
                            "source": str(page_file) if page_file else page_text_path,
                            "table_source_type": table.get("source_type", ""),
                            "table_source_path": table.get("source_path", ""),
                            "prefer_source": args.prefer_source,
                            "input_chars": len(page_text),
                            "extractor": "rules_pipeline_unmatched_table",
                        }
                    )

            print(f"{','.join(page_table_types) or 'unknown'} ({page_row_count} rows)")

        if ENABLE_CONSTRUCTION_PROCESS_EXTRACTION:
            construction_processes = [
                item.to_dict()
                for item in extract_construction_processes_from_pages(text_pages)
            ]
            for index, section in enumerate(construction_processes, start=1):
                cleaned = clean_construction_process_text(
                    section.get("content", ""),
                    debug_label=f"construction_process_{index:02d}",
                )
                section["cleaned_content"] = cleaned.get("cleaned_content", "")
                section["structured_items"] = cleaned.get("structured_items", [])

        sub_projects = [
            SubProject(
                sub_project_id=sp_data["sub_project_id"],
                sub_project_name=sp_data["sub_project_name"],
                parent_project=document_info.get("consultation_project_name", ""),
                unit_project_fee_rows=sp_data["unit_project_fee_rows"],
                sub_item_project_rows=sp_data["sub_item_project_rows"],
            ).to_dict()
            for sp_data in sub_projects_map.values()
        ]

        project = AuditProject(
            file_name=pdf_name,
            total_pages=len(page_numbers),
            sub_projects=sub_projects,
            specialty_fee_rows=specialty_fee_rows,
            quantity_confirm_rows=quantity_confirm_rows,
            labor_rows=labor_rows,
            material_rows=material_rows,
            machine_rows=machine_rows,
            construction_processes=construction_processes,
            document_info=extract_document_info(text_pages),
        )

        out_path = business_root / pdf_name / "business_extract.json"
        current_payload = normalize_sub_project_ids(project.to_dict())
        if is_partial_page_run(args) and out_path.exists():
            try:
                existing_payload = json.loads(out_path.read_text(encoding="utf-8"))
            except Exception:
                existing_payload = {}
            merged_payload = normalize_sub_project_ids(
                merge_business_extract_by_pages(existing_payload, current_payload, page_numbers)
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(merged_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            output_payload = merged_payload
            print(f"  Partial update merged into existing business JSON: pages={page_numbers}")
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(current_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            output_payload = current_payload

        summary = {
            "file_name": pdf_name,
            "processed_pages": len(page_numbers),
            "partial_update": is_partial_page_run(args),
            "vl_page_count": len(vl_page_files),
            "page_text_count": len(page_texts_by_no),
            "sub_projects": len(output_payload.get("sub_projects", [])),
            "specialty_fee_rows": len(output_payload.get("specialty_fee_rows", [])),
            "quantity_confirm_rows": len(output_payload.get("quantity_confirm_rows", [])),
            "labor_rows": len(output_payload.get("labor_rows", [])),
            "material_rows": len(output_payload.get("material_rows", [])),
            "machine_rows": len(output_payload.get("machine_rows", [])),
            "construction_processes": len(output_payload.get("construction_processes", [])),
            "document_info": output_payload.get("document_info", {}),
            "elapsed_seconds": round(time.time() - start, 2),
            "pages": page_summaries,
        }
        if is_partial_page_run(args):
            report_path = report_root / f"{pdf_name}_vl_llm_extract_{page_range_suffix(page_numbers)}_partial_summary.json"
        else:
            report_path = report_root / f"{pdf_name}_vl_llm_extract_summary.json"
        report_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        all_summaries.append(summary)

        print(f"  Done: {out_path}")

    all_summary_name = (
        "all_vl_llm_extract_partial_summary.json"
        if is_partial_page_run(args)
        else "all_vl_llm_extract_summary.json"
    )
    (report_root / all_summary_name).write_text(
        json.dumps(all_summaries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nDone. Reports written to {report_root}")


if __name__ == "__main__":
    main()
