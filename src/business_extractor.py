from __future__ import annotations

from typing import Any

from .field_extractor import (
    extract_construction_process_section,
    detect_sub_project,
    _compact,
    _row_text,
)

TARGET_TABLE_KEYWORDS = [
    "分部分项工程清单与计价表",
    "分部分项工程表",
    "工程量确认单",
    "单位（专业）工程招标控制价费用表",
    "单位工程费用表",
    "专业工程费用表",
    "专业费用表",
    "主要工日一览表",
    "主要材料和工程设备一览表",
    "主要机械台班一览表",
    "主要施工机械台班一览表",
]

def _table_has_text(table: dict[str, Any], needle: str) -> bool:
    target = _compact(needle)
    for row in table.get("grid", []):
        if target in _compact(_row_text(row)):
            return True
    return False


def _table_has_headers(table: dict[str, Any], headers: list[str]) -> bool:
    grid = table.get("grid", [])
    if not grid:
        return False
    for row in grid[:5]:
        row_compact = _compact(_row_text(row))
        if all(_compact(h) in row_compact for h in headers):
            return True
    for i in range(min(4, len(grid) - 1)):
        combined = _compact(_row_text(grid[i]) + " " + _row_text(grid[i + 1]))
        if all(_compact(h) in combined for h in headers):
            return True
    return False


def classify_page(page_text: str, table: dict[str, Any] | None = None) -> dict[str, Any]:
    compact = _compact(page_text)
    if table:
        compact += _compact("\n".join(_row_text(row) for row in table.get("grid", [])))

    table_types: list[str] = []

    is_labor_table = bool(table) and (
        _table_has_text(table, "主要工日一览表")
        or _table_has_headers(table, ["工日", "名称", "单价"])
        or _table_has_headers(table, ["工日", "名称", "合价"])
    )
    is_material_table = bool(table) and (
        _table_has_text(table, "主要材料和工程设备一览表")
        or _table_has_text(table, "主要材料")
        or _table_has_text(table, "工程设备一览表")
        or ("主要材料" in compact and "一览表" in compact)
        or ("工程设备" in compact and "一览表" in compact)
    )
    is_machine_table = bool(table) and (
        _table_has_text(table, "主要机械台班一览表")
        or _table_has_text(table, "主要施工机械台班一览表")
        or _table_has_text(table, "机械台班一览表")
        or ("机械" in compact and "台班" in compact and "一览表" in compact)
    )
    is_resource_table = is_labor_table or is_material_table or is_machine_table

    if is_labor_table:
        table_types.append("quantity_resource_subtable_labor")
    if is_material_table:
        table_types.append("quantity_resource_subtable_material_equipment")
    if is_machine_table:
        table_types.append("quantity_resource_subtable_machine")

    if table and (
        _table_has_text(table, "分部分项工程清单与计价表")
        or _table_has_text(table, "分部分项工程表")
        or _table_has_headers(table, ["项目编码", "项目名称", "综合单价"])
        or _table_has_headers(table, ["项目编码", "项目名称", "合价"])
    ) and not is_resource_table:
        table_types.append("sub_item_project_table")

    is_quantity_confirm_table = bool(table) and (
        _table_has_text(table, "工程量确认单")
        or (
            "工程量确认单" in compact
            and (
                _table_has_headers(table, ["名称", "维修内容"])
                or _table_has_headers(table, ["名称", "工程量"])
                or ("维修内容" in compact and ("预估工程量" in compact or "工程量" in compact))
            )
        )
    )
    if is_quantity_confirm_table:
        table_types.append("quantity_confirmation_form")

    if table and (
        _table_has_text(table, "单位（专业）工程招标控制价费用表")
        or _table_has_text(table, "单位工程费用表")
    ) or (table and _table_has_headers(table, ["费用名称", "计算公式"])
          and not _table_has_text(table, "专业工程费用表")
          and not _table_has_text(table, "专业费用表")):
        table_types.append("unit_project_fee_table")

    if table and (
        _table_has_text(table, "专业工程费用表")
        or _table_has_text(table, "专业费用表")
        or _table_has_headers(table, ["工程名称", "金额", "暂估价"])
        or _table_has_headers(table, ["工程名称", "金额", "安全文明"])
    ) and not _table_has_text(table, "单位（专业）工程招标控制价费用表"):
        table_types.append("specialty_fee_table")

    sub_project = ""
    sub_project_tables = {"sub_item_project_table"}
    if table and any(tt in sub_project_tables for tt in table_types):
        sub_project = detect_sub_project(table, page_text)
    elif page_text:
        for tt in table_types:
            if tt in sub_project_tables:
                sub_project = detect_sub_project(None, page_text)
                break

    has_construction_process = bool(extract_construction_process_section(page_text))

    matched_keywords = [
        kw for kw in TARGET_TABLE_KEYWORDS
        if _compact(kw) in compact
    ]
    if has_construction_process:
        matched_keywords.append("施工工艺")

    return {
        "table_types": table_types,
        "has_construction_process": has_construction_process,
        "matched_keywords": matched_keywords,
        "sub_project_name": sub_project,
    }
