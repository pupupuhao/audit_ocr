from __future__ import annotations

import re
from typing import Any

from .models import (
    UnitProjectFeeRow,
    SubItemProjectRow,
    SpecialtyFeeRow,
    LaborRow,
    MaterialRow,
    MachineRow,
    QuantityConfirmRow,
    ConstructionProcess,
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _row_text(row: list) -> str:
    return " ".join(str(c).strip() for c in row if str(c).strip())


def _cells(row: list) -> list[str]:
    return [str(c).strip() for c in row]


def _non_empty(row: list) -> list[str]:
    return [str(c).strip() for c in row if str(c).strip()]


def _fix_empty_cell(mapped: dict[str, str], col_map: dict[str, int], cells: list[str], field: str, search_range: int = 3) -> str:
    val = mapped.get(field, "")
    if val.strip():
        return val
    if field not in col_map:
        return val
    idx = col_map[field]
    for offset in range(1, search_range + 1):
        for direction in [1, -1]:
            check = idx + offset * direction
            if 0 <= check < len(cells):
                cell_val = cells[check].strip()
                if cell_val:
                    return cell_val
    return val


def _find_header_row(grid: list[list], keywords: list[str]) -> int:
    for i, row in enumerate(grid):
        compact = _compact(_row_text(row))
        if all(_compact(kw) in compact for kw in keywords):
            return i
    for i in range(len(grid) - 1):
        combined = _compact(_row_text(grid[i]) + " " + _row_text(grid[i + 1]))
        if all(_compact(kw) in combined for kw in keywords):
            return i
    for i in range(min(4, len(grid) - 2)):
        combined = _compact(" ".join(_row_text(grid[j]) for j in range(i, min(i + 3, len(grid)))))
        if all(_compact(kw) in combined for kw in keywords):
            return i
    return -1


def _find_header_row_any(grid: list[list], keyword_sets: list[list[str]]) -> int:
    for keywords in keyword_sets:
        idx = _find_header_row(grid, keywords)
        if idx >= 0:
            return idx
    return -1


def _detect_column_positions(grid: list[list], header_row_idx: int, column_keywords: list[tuple[str, list[str]]]) -> dict[str, int]:
    if header_row_idx < 0 or header_row_idx >= len(grid):
        return {}
    row = grid[header_row_idx]
    row_cells = _cells(row)
    col_map: dict[str, int] = {}
    used_positions: set[int] = set()

    for col_name, keywords in column_keywords:
        best_pos = -1
        best_score = 0
        for pos, cell in enumerate(row_cells):
            if pos in used_positions:
                continue
            compact_cell = _compact(cell)
            score = 0
            for kw in keywords:
                if _compact(kw) in compact_cell:
                    score += 1
            if score > best_score:
                best_score = score
                best_pos = pos
        if best_pos >= 0 and best_score > 0:
            col_map[col_name] = best_pos
            used_positions.add(best_pos)

    return col_map


def _find_multi_row_header(grid: list[list], column_keywords: list[tuple[str, list[str]]], max_rows: int = 5) -> dict[str, int]:
    col_map: dict[str, int] = {}
    if not grid:
        return col_map
    num_rows_to_check = min(max_rows, len(grid))

    all_cells_by_col: dict[int, list[str]] = {}
    for row_idx in range(num_rows_to_check):
        row_cells = _cells(grid[row_idx])
        for pos, cell in enumerate(row_cells):
            if pos not in all_cells_by_col:
                all_cells_by_col[pos] = []
            all_cells_by_col[pos].append(cell)

    used_positions: set[int] = set()
    for col_name, keywords in column_keywords:
        best_pos = -1
        best_score = 0
        for pos, cell_list in all_cells_by_col.items():
            if pos in used_positions:
                continue
            combined = _compact(" ".join(cell_list))
            score = 0
            for kw in keywords:
                if _compact(kw) in combined:
                    score += 1
            if score > best_score:
                best_score = score
                best_pos = pos
        if best_pos >= 0 and best_score > 0:
            col_map[col_name] = best_pos
            used_positions.add(best_pos)

    secondary_map: dict[str, int] = {}
    for col_name, keywords in column_keywords:
        if col_name in col_map:
            continue
        best_pos = -1
        best_score = 0
        for pos, cell_list in all_cells_by_col.items():
            combined = _compact(" ".join(cell_list))
            score = 0
            for kw in keywords:
                if _compact(kw) in combined:
                    score += 1
            if score > best_score:
                best_score = score
                best_pos = pos
        if best_pos >= 0 and best_score > 0:
            secondary_map[col_name] = best_pos

    for col_name, pos in secondary_map.items():
        if col_name not in col_map:
            col_map[col_name] = pos

    return col_map


def _get_cell(cells: list[str], idx: int) -> str:
    if 0 <= idx < len(cells):
        return cells[idx].strip()
    return ""


def _extract_rows_from_grid(
    grid: list[list],
    header_row_idx: int,
    col_map: dict[str, int],
    row_filter=None,
) -> list[dict[str, str]]:
    rows = []
    start = header_row_idx + 1
    for row_idx, row in enumerate(grid[start:], start=1):
        cells = _cells(row)
        if not any(c.strip() for c in cells):
            continue
        non_empty = _non_empty(row)
        if not non_empty:
            continue
        mapped = {col_name: _get_cell(cells, pos) for col_name, pos in col_map.items()}
        mapped["_row_index"] = row_idx
        mapped["_raw_text"] = _row_text(row)
        mapped["_cells"] = cells
        if row_filter and not row_filter(mapped, cells, non_empty):
            continue
        rows.append(mapped)
    return rows


def _parse_top_level_seq(text: str) -> str:
    text = str(text).strip()
    if not text:
        return ""
    first = text[0]
    if first not in "1234567":
        return ""
    if len(text) == 1:
        return first
    after_first = text[1:]
    if after_first[0] == '.':
        return ""
    if after_first[0].isdigit():
        return ""
    return first


def _split_amount_remark(text: str) -> tuple[str, str]:
    text = str(text).strip()
    amount_re = re.compile(r"([\d,]+\.?\d*)")
    m = amount_re.search(text.replace(",", ""))
    if not m:
        return "", text
    amount = m.group(1)
    start, end = m.span()
    full_start = m.start()
    full_end = m.end()
    original = text.replace(",", "")
    remark = (original[:full_start] + original[full_end:]).strip()
    return amount, remark


def extract_unit_project_fee_rows(
    table: dict[str, Any],
    page_no: int,
    sub_project_id: str = "",
) -> list[UnitProjectFeeRow]:
    grid = table.get("grid", [])
    header_idx = _find_header_row(grid, ["费用名称", "计算公式"])
    if header_idx < 0:
        header_idx = _find_header_row(grid, ["费用名称", "金额"])
    if header_idx < 0:
        return []

    column_keywords: list[tuple[str, list[str]]] = [
        ("seq", ["序号"]),
        ("fee_name", ["费用名称"]),
        ("formula", ["计算公式"]),
        ("amount", ["金额"]),
        ("remark", ["备注"]),
    ]
    col_map = _detect_column_positions(grid, header_idx, column_keywords)

    def filter_top_level_rows(mapped: dict, cells: list[str], non_empty: list[str]) -> bool:
        seq = mapped.get("seq", "")
        if not seq.strip():
            seq = non_empty[0] if non_empty else ""
        top_seq = _parse_top_level_seq(seq)
        return top_seq != ""

    raw_rows = _extract_rows_from_grid(grid, header_idx, col_map, row_filter=filter_top_level_rows)

    results = []
    for mapped in raw_rows:
        seq = mapped.get("seq", "")
        top_seq = _parse_top_level_seq(seq)
        fee_name = mapped.get("fee_name", "").strip()
        if not fee_name:
            seq_stripped = seq.strip()
            if len(seq_stripped) > 1 and seq_stripped[0] in "1234567":
                rest = seq_stripped[1:].strip()
                for prefix in ["其中"]:
                    if rest.startswith(prefix):
                        rest = rest[len(prefix):].strip()
                        break
                if rest:
                    fee_name = rest
        amount_raw = mapped.get("amount", "")
        remark_raw = mapped.get("remark", "")
        if not fee_name.strip():
            ne = _non_empty(mapped.get("_cells", []))
            if len(ne) > 1:
                fee_name = ne[1]
        amount, remark_from_amount = _split_amount_remark(amount_raw)
        remark_parts = []
        if remark_raw:
            remark_parts.append(remark_raw)
        if remark_from_amount and remark_from_amount != "":
            remark_parts.append(remark_from_amount)
        remark = " ".join(p for p in remark_parts if p) if remark_parts else ""

        results.append(UnitProjectFeeRow(
            seq=top_seq,
            fee_name=fee_name,
            formula=mapped.get("formula", ""),
            amount=amount if amount else amount_raw,
            remark=remark,
            page_no=page_no,
            row_index=mapped.get("_row_index", 0),
            raw_text=mapped.get("_raw_text", ""),
            sub_project_id=sub_project_id,
        ))
    return results


def extract_sub_item_project_rows(
    table: dict[str, Any],
    page_no: int,
    sub_project_id: str = "",
) -> list[SubItemProjectRow]:
    grid = table.get("grid", [])
    keyword_sets = [
        ["项目编码", "项目名称", "综合单价"],
        ["项目编码", "项目名称", "合价"],
        ["项目名称", "综合单价"],
        ["项目编码", "项目名称"],
        ["序号", "项目名称"],
    ]
    header_idx = _find_header_row_any(grid, keyword_sets)
    if header_idx < 0:
        return []

    column_keywords: list[tuple[str, list[str]]] = [
        ("seq", ["序号"]),
        ("project_code", ["项目编码"]),
        ("project_name", ["项目名称"]),
        ("project_description", ["项目特征"]),
        ("unit", ["计量单位", "单位"]),
        ("quantity", ["工程量"]),
        ("unit_price", ["综合单价"]),
        ("total_price", ["合价"]),
        ("provisional_estimate", ["暂估价", "暂估"]),
        ("labor_cost", ["人工费"]),
        ("machinery_cost", ["机械费"]),
        ("remark", ["备注"]),
    ]
    col_map = _find_multi_row_header(grid, column_keywords, max_rows=5)

    if "unit_price" not in col_map and "total_price" not in col_map:
        for i in range(min(5, len(grid))):
            row_cells = _cells(grid[i])
            row_compact = _compact(_row_text(grid[i]))
            if "综合单价" in row_compact:
                for pos, cell in enumerate(row_cells):
                    cc = _compact(cell)
                    if "综合" in cc and "单价" in cc:
                        col_map.setdefault("unit_price", pos)
                    elif "合价" in cc:
                        col_map.setdefault("total_price", pos)
            if "人工费" in row_compact:
                for pos, cell in enumerate(row_cells):
                    cc = _compact(cell)
                    if "人工费" in cc:
                        col_map.setdefault("labor_cost", pos)
                    elif "机械费" in cc:
                        col_map.setdefault("machinery_cost", pos)
                    elif "暂估" in cc:
                        col_map.setdefault("provisional_estimate", pos)

    data_start = header_idx + 1
    for i in range(data_start, min(data_start + 3, len(grid))):
        cells = _cells(grid[i])
        if not any(c.strip() and re.match(r"^\d", c.strip()) for c in cells):
            continue
        if "project_code" not in col_map and len(cells) > 1:
            for pos, cell in enumerate(cells):
                cc = _compact(cell)
                if re.match(r"^\d{2,}-\d+-\d+$", cc) or re.match(r"^\d{9,}", cc):
                    col_map.setdefault("project_code", pos)
                    if "project_name" not in col_map and pos + 1 < len(cells) and cells[pos + 1].strip():
                        col_map.setdefault("project_name", pos + 1)

    raw_rows = _extract_rows_from_grid(grid, header_idx, col_map)

    results = []
    for mapped in raw_rows:
        seq = mapped.get("seq", "")
        if not seq.strip():
            ne = _non_empty(mapped.get("_cells", []))
            if ne and not re.match(r"^\d", ne[0]):
                continue
        results.append(SubItemProjectRow(
            seq=mapped.get("seq", ""),
            project_code=mapped.get("project_code", ""),
            project_name=mapped.get("project_name", ""),
            project_description=mapped.get("project_description", ""),
            unit=mapped.get("unit", ""),
            quantity=mapped.get("quantity", ""),
            unit_price=mapped.get("unit_price", ""),
            total_price=mapped.get("total_price", ""),
            provisional_estimate=mapped.get("provisional_estimate", ""),
            labor_cost=mapped.get("labor_cost", ""),
            machinery_cost=mapped.get("machinery_cost", ""),
            remark=mapped.get("remark", ""),
            page_no=page_no,
            row_index=mapped.get("_row_index", 0),
            raw_text=mapped.get("_raw_text", ""),
            sub_project_id=sub_project_id,
        ))
    return results


def extract_specialty_fee_rows(
    table: dict[str, Any],
    page_no: int,
) -> list[SpecialtyFeeRow]:
    grid = table.get("grid", [])
    keyword_sets = [
        ["工程名称", "金额", "暂估价"],
        ["工程名称", "金额", "安全文明"],
        ["工程名称", "金额"],
    ]
    header_idx = _find_header_row_any(grid, keyword_sets)
    if header_idx < 0:
        return []

    column_keywords: list[tuple[str, list[str]]] = [
        ("seq", ["序号"]),
        ("project_name", ["工程名称"]),
        ("amount", ["金额"]),
        ("provisional_estimate", ["暂估价", "暂估"]),
        ("safety_civilization_fee", ["安全文明"]),
        ("regulatory_fee", ["规费"]),
        ("tax", ["税金"]),
        ("remark", ["备注"]),
    ]
    col_map = _detect_column_positions(grid, header_idx, column_keywords)

    raw_rows = _extract_rows_from_grid(grid, header_idx, col_map)

    results = []
    for mapped in raw_rows:
        results.append(SpecialtyFeeRow(
            seq=mapped.get("seq", ""),
            project_name=mapped.get("project_name", ""),
            amount=mapped.get("amount", ""),
            provisional_estimate=mapped.get("provisional_estimate", ""),
            safety_civilization_fee=mapped.get("safety_civilization_fee", ""),
            regulatory_fee=mapped.get("regulatory_fee", ""),
            tax=mapped.get("tax", ""),
            remark=mapped.get("remark", ""),
            page_no=page_no,
            row_index=mapped.get("_row_index", 0),
            raw_text=mapped.get("_raw_text", ""),
        ))
    return results


def extract_labor_rows(
    table: dict[str, Any],
    page_no: int,
) -> list[LaborRow]:
    grid = table.get("grid", [])
    keyword_sets = [
        ["工日", "名称", "合价"],
        ["工日", "名称", "单价"],
        ["工日", "名称"],
    ]
    header_idx = _find_header_row_any(grid, keyword_sets)
    if header_idx < 0:
        return []

    column_keywords: list[tuple[str, list[str]]] = [
        ("seq", ["序号"]),
        ("name", ["名称", "类别", "工日名称"]),
        ("unit", ["单位"]),
        ("quantity", ["数量"]),
        ("unit_price", ["单价"]),
        ("total_price", ["合价"]),
        ("remark", ["备注"]),
    ]
    col_map = _detect_column_positions(grid, header_idx, column_keywords)

    raw_rows = _extract_rows_from_grid(grid, header_idx, col_map)

    results = []
    for mapped in raw_rows:
        seq = mapped.get("seq", "")
        name = mapped.get("name", "")
        if not name.strip():
            cells = mapped.get("_cells", [])
            if col_map.get("name") is not None:
                name_idx = col_map["name"]
                for offset in range(1, 3):
                    for direction in [1, -1]:
                        check = name_idx + offset * direction
                        if 0 <= check < len(cells) and cells[check].strip():
                            name = cells[check].strip()
                            break
                    if name.strip():
                        break
        results.append(LaborRow(
            seq=seq,
            name=name,
            unit=mapped.get("unit", ""),
            quantity=mapped.get("quantity", ""),
            unit_price=mapped.get("unit_price", ""),
            total_price=mapped.get("total_price", ""),
            remark=mapped.get("remark", ""),
            page_no=page_no,
            row_index=mapped.get("_row_index", 0),
            raw_text=mapped.get("_raw_text", ""),
        ))
    return results


def extract_material_rows(
    table: dict[str, Any],
    page_no: int,
) -> list[MaterialRow]:
    grid = table.get("grid", [])
    keyword_sets = [
        ["材料", "名称", "合价"],
        ["材料", "名称", "单价"],
        ["材料", "规格", "合价"],
        ["材料", "名称"],
        ["名称", "规格", "型号", "单位"],
        ["名称", "规格", "合价"],
    ]
    header_idx = _find_header_row_any(grid, keyword_sets)
    if header_idx < 0:
        for i, row in enumerate(grid[:5]):
            compact = _compact(_row_text(row))
            if "序号" in compact and "名称" in compact and "合价" in compact:
                header_idx = i
                break
    if header_idx < 0:
        return []

    column_keywords: list[tuple[str, list[str]]] = [
        ("seq", ["序号"]),
        ("name_spec", ["名称", "规格", "型号"]),
        ("unit", ["单位"]),
        ("quantity", ["数量"]),
        ("unit_price", ["单价"]),
        ("total_price", ["合价"]),
        ("remark", ["备注"]),
    ]
    col_map = _detect_column_positions(grid, header_idx, column_keywords)

    raw_rows = _extract_rows_from_grid(grid, header_idx, col_map)

    results = []
    for mapped in raw_rows:
        seq = mapped.get("seq", "")
        name_spec = mapped.get("name_spec", "")
        if not name_spec.strip():
            cells = mapped.get("_cells", [])
            if col_map.get("name_spec") is not None:
                name_idx = col_map["name_spec"]
                for offset in range(1, 3):
                    for direction in [1, -1]:
                        check = name_idx + offset * direction
                        if 0 <= check < len(cells) and cells[check].strip():
                            name_spec = cells[check].strip()
                            break
                    if name_spec.strip():
                        break
        unit_price = mapped.get("unit_price", "")
        if not unit_price.strip():
            cells = mapped.get("_cells", [])
            if col_map.get("unit_price") is not None:
                up_idx = col_map["unit_price"]
                for offset in range(1, 3):
                    check = up_idx + offset
                    if 0 <= check < len(cells) and cells[check].strip():
                        try:
                            float(cells[check].strip().replace(",", ""))
                            unit_price = cells[check].strip()
                            break
                        except ValueError:
                            pass
        results.append(MaterialRow(
            seq=seq,
            name_spec=name_spec,
            unit=mapped.get("unit", ""),
            quantity=mapped.get("quantity", ""),
            unit_price=unit_price,
            total_price=mapped.get("total_price", ""),
            remark=mapped.get("remark", ""),
            page_no=page_no,
            row_index=mapped.get("_row_index", 0),
            raw_text=mapped.get("_raw_text", ""),
        ))
    return results


def extract_machine_rows(
    table: dict[str, Any],
    page_no: int,
) -> list[MachineRow]:
    grid = table.get("grid", [])
    keyword_sets = [
        ["机械", "名称", "合价"],
        ["机械", "名称", "单价"],
        ["台班", "名称", "合价"],
        ["台班", "名称", "单价"],
        ["机械", "名称"],
        ["台班", "名称"],
    ]
    header_idx = _find_header_row_any(grid, keyword_sets)
    if header_idx < 0:
        return []

    column_keywords: list[tuple[str, list[str]]] = [
        ("seq", ["序号"]),
        ("name_spec", ["名称", "规格", "型号", "机械"]),
        ("unit", ["单位"]),
        ("quantity", ["数量"]),
        ("unit_price", ["单价"]),
        ("total_price", ["合价"]),
        ("remark", ["备注"]),
    ]
    col_map = _detect_column_positions(grid, header_idx, column_keywords)

    raw_rows = _extract_rows_from_grid(grid, header_idx, col_map)

    results = []
    for mapped in raw_rows:
        results.append(MachineRow(
            seq=mapped.get("seq", ""),
            name_spec=mapped.get("name_spec", ""),
            unit=mapped.get("unit", ""),
            quantity=mapped.get("quantity", ""),
            unit_price=mapped.get("unit_price", ""),
            total_price=mapped.get("total_price", ""),
            remark=mapped.get("remark", ""),
            page_no=page_no,
            row_index=mapped.get("_row_index", 0),
            raw_text=mapped.get("_raw_text", ""),
        ))
    return results


def extract_quantity_confirm_rows(
    table: dict[str, Any],
    page_no: int,
    sub_project_id: str = "",
) -> list[QuantityConfirmRow]:
    grid = table.get("grid", [])
    keyword_sets = [
        ["工程量", "确认"],
        ["名称", "维修内容", "工程量"],
        ["名称", "工程量"],
        ["序号", "名称", "单位"],
    ]
    header_idx = _find_header_row_any(grid, keyword_sets)
    if header_idx < 0:
        return []

    for i in range(header_idx + 1, min(header_idx + 3, len(grid))):
        compact = _compact(_row_text(grid[i]))
        if "序号" in compact:
            header_idx = i
            break

    column_keywords: list[tuple[str, list[str]]] = [
        ("seq", ["序号"]),
        ("name", ["名称", "项目名称"]),
        ("repair_content", ["维修内容"]),
        ("unit", ["单位"]),
        ("formula", ["计算式", "计算公式"]),
        ("quantity", ["工程量"]),
        ("remark", ["备注"]),
    ]
    col_map = _detect_column_positions(grid, header_idx, column_keywords)

    def filter_numbered_rows(mapped: dict, cells: list[str], non_empty: list[str]) -> bool:
        seq = mapped.get("seq", "")
        if not seq.strip():
            seq = non_empty[0] if non_empty else ""
        compact = _compact(seq)
        return bool(re.match(r"^\d", compact))

    raw_rows = _extract_rows_from_grid(grid, header_idx, col_map, row_filter=filter_numbered_rows)

    results = []
    for mapped in raw_rows:
        results.append(QuantityConfirmRow(
            seq=mapped.get("seq", ""),
            name=mapped.get("name", ""),
            repair_content=mapped.get("repair_content", ""),
            unit=mapped.get("unit", ""),
            formula=mapped.get("formula", ""),
            quantity=mapped.get("quantity", ""),
            remark=mapped.get("remark", ""),
            page_no=page_no,
            row_index=mapped.get("_row_index", 0),
            raw_text=mapped.get("_raw_text", ""),
            sub_project_id=sub_project_id,
        ))
    return results


def extract_construction_processes(
    page_text: str,
    page_no: int,
) -> list[ConstructionProcess]:
    return extract_construction_processes_from_pages([{"page_no": page_no, "text": page_text}])


def extract_construction_process_section(page_text: str) -> str:
    sections = extract_construction_processes(page_text, 0)
    return sections[0].content if sections else ""


def extract_construction_processes_from_pages(pages: list[dict[str, Any]]) -> list[ConstructionProcess]:
    heading_re = re.compile(
        r"^\s*(?:#{1,6}\s*)?(?:[一二三四五六七八九十]+[、.．]\s*|\d+(?:\.\d+)*[、.．]?\s*)?施工工艺\s*[：:]?\s*$"
    )
    known_non_process_titles = {
        "咨询报告书目录",
        "工程造价审定单",
        "专业工程费用表",
        "专业费用表",
        "工程结算审核造价汇总表",
        "现场踏勘记录表",
        "工程现场勘察签到单",
        "施工方案",
        "单位（专业）工程招标控制价费用表",
        "分部分项工程清单与计价表",
        "主要工日一览表",
        "主要材料和工程设备一览表",
        "主要机械台班一览表",
        "工程量确认单",
    }
    known_non_process_title_keys = {_compact(title) for title in known_non_process_titles}
    stop_prefixes = ["建设单位", "施工单位", "经办人", "电话", "日期", "项目主审", "复核人员", "抄送", "共印"]

    def strip_markdown_heading(line: str) -> str:
        return re.sub(r"^\s*#{1,6}\s*", "", line).strip()

    def image_ref_from_line(line: str, line_no: int, page_no: int) -> dict[str, Any] | None:
        text = line.strip()
        if not text:
            return None
        md_match = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", text)
        if md_match:
            return {
                "type": "markdown_image",
                "alt": md_match.group(1),
                "target": md_match.group(2),
                "page_no": page_no,
                "line_no": line_no,
                "raw": text,
            }
        if re.search(r"<img\b[^>]*>", text, flags=re.IGNORECASE):
            src_match = re.search(r"\bsrc=[\"']?([^\"'\s>]+)", text, flags=re.IGNORECASE)
            return {
                "type": "html_image",
                "target": src_match.group(1) if src_match else "",
                "page_no": page_no,
                "line_no": line_no,
                "raw": text,
            }
        if re.search(r"data:image/[a-zA-Z0-9.+-]+;base64,", text):
            return {
                "type": "embedded_image",
                "target": "",
                "page_no": page_no,
                "line_no": line_no,
                "raw": "<embedded image omitted>",
            }
        return None

    def is_stop_line(line: str) -> bool:
        compact = _compact(line)
        if not compact:
            return False
        if compact in known_non_process_title_keys:
            return True
        return any(compact.startswith(prefix) for prefix in stop_prefixes)

    def is_likely_toc_page(lines: list[str]) -> bool:
        compact_lines = [_compact(line) for line in lines if _compact(line)]
        if not compact_lines:
            return False
        if any(line in {"目录", "咨询报告书目录"} for line in compact_lines[:5]):
            return True
        title_hits = sum(1 for line in compact_lines if line in known_non_process_title_keys)
        return title_hits >= 3

    numbered_step_re = re.compile(r"^\s*(?:\d+|[一二三四五六七八九十]+)[、.．)]")
    numbered_heading_re = re.compile(r"^\s*(\d+)(?:\.\d+)*[、.．]\s*(.+?)\s*$")
    section_title_re = re.compile(r"^[^：:]{1,30}[：:]$")
    plain_heading_re = re.compile(r"^[^\d\s]{2,35}(?:（[^）]{1,40}）)?$")
    page_marker_re = re.compile(r"第\s*\d+\s*页|共\s*\d+\s*页")

    def meaningful_lines(lines: list[str]) -> list[str]:
        result = []
        for line in lines:
            if not _compact(line):
                continue
            if image_ref_from_line(line, 0, 0):
                continue
            result.append(strip_markdown_heading(line))
        return result

    def numbered_heading(line: str) -> tuple[int, str] | None:
        match = numbered_heading_re.match(strip_markdown_heading(line))
        if not match:
            return None
        return int(match.group(1)), _compact(match.group(2))

    def looks_like_toc_heading(lines: list[str], index: int) -> bool:
        current = numbered_heading(lines[index])
        if not current:
            return False
        current_number, current_title = current
        if current_title != "施工工艺":
            return False

        next_number = current_number + 1
        hits = 0
        for line in lines[index + 1:index + 5]:
            item = numbered_heading(line)
            if not item:
                continue
            number, title = item
            if number == next_number:
                hits += 1
                next_number += 1
                if title in known_non_process_title_keys or title in {"施工方案", "现场踏勘记录表"}:
                    return True
        return hits >= 2

    def previous_line_allows_continuation() -> bool:
        for line in reversed(collected):
            text = line.strip()
            if not text:
                continue
            return bool(re.search(r"[，,、：（(【；;]$", text) or not re.search(r"[。.!！?？；;]$", text))
        return False

    def is_continuation_line(line: str) -> bool:
        compact = _compact(line)
        if not compact or is_stop_line(line):
            return False
        if numbered_step_re.match(line):
            return True
        if section_title_re.match(line):
            return True
        if re.search(r"\d+\s*(?:m2|m²|㎡|m|米|台班|遍)", line, flags=re.IGNORECASE):
            return True
        return False

    def looks_like_new_document_section(line: str) -> bool:
        compact = _compact(line)
        if not compact:
            return False
        if compact in known_non_process_title_keys:
            return True
        if page_marker_re.search(line):
            return True
        if re.match(r"^[一二三四五六七八九十]+[、.．](?!施工工艺)", line):
            return True
        if re.match(r"^[（(][一二三四五六七八九十\d]+[）)]", line):
            return True
        return False

    def should_continue_to_page(lines: list[str]) -> bool:
        candidates = meaningful_lines(lines)[:5]
        if not candidates:
            return True
        if is_likely_toc_page(lines):
            return False
        first = candidates[0]
        if is_stop_line(first):
            return False
        if looks_like_new_document_section(first):
            return False
        if heading_re.match(first):
            return True
        if any(is_continuation_line(line) for line in candidates):
            return True
        if previous_line_allows_continuation():
            return True
        if len(candidates) >= 2 and plain_heading_re.match(first) and any(is_continuation_line(line) for line in candidates[1:]):
            return True
        return False

    def build_result() -> list[ConstructionProcess]:
        content_lines = [line for line in collected[1:] if _compact(line)]
        if not content_lines:
            return []
        if not any(len(_compact(line)) >= 6 for line in content_lines):
            return []
        return [
            ConstructionProcess(
                process_type="施工工艺",
                content="\n".join(collected).strip(),
                page_no=start_page,
                section_index=1,
                image_refs=image_refs,
            )
        ]

    started = False
    start_page = 0
    last_content_page = 0
    collected: list[str] = []
    image_refs: list[dict[str, Any]] = []

    for page in sorted(pages, key=lambda item: int(item.get("page_no") or 0)):
        page_no = int(page.get("page_no") or 0)
        lines = [line.strip() for line in str(page.get("text") or "").replace("\r", "\n").splitlines()]
        page_is_toc = is_likely_toc_page(lines)
        if started and page_no != last_content_page and not should_continue_to_page(lines):
            return build_result()
        for line_no, line in enumerate(lines, start=1):
            if not started:
                if heading_re.match(line) and not page_is_toc and not looks_like_toc_heading(lines, line_no - 1):
                    started = True
                    start_page = page_no
                    last_content_page = page_no
                    collected.append(strip_markdown_heading(line))
                continue

            if is_stop_line(line):
                return build_result()

            image_ref = image_ref_from_line(line, line_no, page_no)
            if image_ref:
                image_refs.append(image_ref)
                continue
            if line:
                collected.append(strip_markdown_heading(line))
                last_content_page = page_no

    return build_result()


def extract_construction_process_payload(page_text: str) -> tuple[str, list[dict[str, Any]]]:
    """Return only the section whose heading is explicitly 施工工艺."""
    lines = [line.strip() for line in (page_text or "").replace("\r", "\n").splitlines()]
    heading_re = re.compile(
        r"^\s*(?:#{1,6}\s*)?(?:[一二三四五六七八九十]+[、.．]\s*|\d+(?:\.\d+)*[、.．]?\s*)?施工工艺\s*[：:]?\s*$"
    )
    next_heading_re = re.compile(
        r"^\s*(?:#{1,6}\s+|[一二三四五六七八九十]+[、.．]\s*|[（(]\s*[一二三四五六七八九十\d]+\s*[）)]\s*|\d+(?:\.\d+)*[、.．]\s*)"
    )
    known_non_process_titles = {
        "咨询报告书目录",
        "工程造价审定单",
        "专业工程费用表",
        "专业费用表",
        "工程结算审核造价汇总表",
        "现场踏勘记录表",
        "工程现场勘察签到单",
        "施工方案",
        "单位（专业）工程招标控制价费用表",
        "分部分项工程清单与计价表",
        "主要工日一览表",
        "主要材料和工程设备一览表",
        "主要机械台班一览表",
        "工程量确认单",
    }
    known_non_process_title_keys = {_compact(title) for title in known_non_process_titles}

    def image_ref_from_line(line: str, line_no: int) -> dict[str, Any] | None:
        text = line.strip()
        if not text:
            return None
        md_match = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", text)
        if md_match:
            return {
                "type": "markdown_image",
                "alt": md_match.group(1),
                "target": md_match.group(2),
                "line_no": line_no,
                "raw": text,
            }
        if re.search(r"<img\b[^>]*>", text, flags=re.IGNORECASE):
            src_match = re.search(r"\bsrc=[\"']?([^\"'\s>]+)", text, flags=re.IGNORECASE)
            return {
                "type": "html_image",
                "target": src_match.group(1) if src_match else "",
                "line_no": line_no,
                "raw": text,
            }
        if re.search(r"data:image/[a-zA-Z0-9.+-]+;base64,", text):
            return {
                "type": "embedded_image",
                "target": "",
                "line_no": line_no,
                "raw": "<embedded image omitted>",
            }
        return None

    def strip_markdown_heading(line: str) -> str:
        return re.sub(r"^\s*#{1,6}\s*", "", line).strip()

    def numbered_marker(line: str) -> int | None:
        match = re.match(r"^\s*(\d+)(?:\.\d+)*[、.．]\s*", line)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def numbered_heading(line: str) -> tuple[int, str] | None:
        match = re.match(r"^\s*(\d+)(?:\.\d+)*[、.．]\s*(.+?)\s*$", strip_markdown_heading(line))
        if not match:
            return None
        return int(match.group(1)), _compact(match.group(2))

    def looks_like_toc_heading(index: int) -> bool:
        current = numbered_heading(lines[index])
        if not current:
            return False
        current_number, current_title = current
        if current_title != "施工工艺":
            return False
        next_number = current_number + 1
        hits = 0
        for line in lines[index + 1:index + 5]:
            item = numbered_heading(line)
            if not item:
                continue
            number, title = item
            if number == next_number:
                hits += 1
                next_number += 1
                if title in known_non_process_title_keys or title in {"施工方案", "现场踏勘记录表"}:
                    return True
        return hits >= 2

    def is_continuing_numbered_list(line: str, expected_number: int | None) -> bool:
        current = numbered_marker(line)
        if current is None:
            return False
        if expected_number is None:
            return current == 1
        return current == expected_number

    start = -1
    for index, line in enumerate(lines):
        compact = _compact(line)
        if compact in known_non_process_title_keys:
            continue
        if heading_re.match(line) and not looks_like_toc_heading(index):
            start = index
            break
    if start < 0:
        return "", []

    collected: list[str] = []
    image_refs: list[dict[str, Any]] = []
    expected_list_number: int | None = None
    for offset, line in enumerate(lines[start:], start=start + 1):
        if collected and next_heading_re.match(line) and "施工工艺" not in line:
            if is_continuing_numbered_list(line, expected_list_number):
                current_number = numbered_marker(line)
                expected_list_number = current_number + 1 if current_number is not None else expected_list_number
            else:
                break
        if _compact(line) in known_non_process_title_keys:
            break
        if collected and any(_compact(line).startswith(prefix) for prefix in ["项目主审", "复核人员", "抄送", "共印"]):
            break
        image_ref = image_ref_from_line(line, offset)
        if image_ref:
            image_refs.append(image_ref)
            continue
        if line:
            cleaned_line = strip_markdown_heading(line)
            collected.append(cleaned_line)
            if expected_list_number is None:
                current_number = numbered_marker(line)
                if current_number == 1:
                    expected_list_number = 2

    content_lines = []
    for line in collected[1:]:
        compact = _compact(line)
        if not compact:
            continue
        if compact in known_non_process_title_keys:
            continue
        content_lines.append(line)

    if not content_lines:
        return "", image_refs
    if not any(len(_compact(line)) >= 6 for line in content_lines):
        return "", image_refs

    return "\n".join(collected).strip(), image_refs


SUB_PROJECT_PATTERNS = [
    re.compile(r"工程名称[：:]\s*([\w\u4e00-\u9fa5\-\—–]+(?:\d+(?:[、,，]\d+)*(?:[—\-–]\d+)*))\s*(?:标段|$)"),
    re.compile(r"(\d+(?:[—\-–]\d+)*\s*(?:幢|幛|号楼?|栋|单元?|户))"),
    re.compile(r"[（(]\s*(\d+(?:[—\-–]\d+)*\s*(?:幢|幛|号楼?|栋|单元?|户))"),
    re.compile(r"第\s*(\d+(?:[—\-–]\d+)*\s*(?:幢|幛|号楼?|栋|单元?|户))"),
]

_PROJECT_NAME_PATTERN = re.compile(r"工程名称[：:]\s*(.+?)(?:\s+标段|$)", re.DOTALL)
_ROOM_ID_PATTERN = re.compile(r"(\d+(?:[、,，]\d+)*(?:[—\-–]\d+)*)")
_ROOM_CONTINUATION_PATTERN = re.compile(r"\d+(?:[、,，—\-–]\d+)*(?:室|户)?")
_SUB_PROJECT_NOISE_PATTERN = re.compile(r"(?:标段[:：]?|第?\d+\s*页\s*共\s*\d+\s*页|页共\d+页|第?\d+页|共\d+页)")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_SUB_PROJECT_TABLE_TITLE_PATTERN = re.compile(
    r"(?:单位（专业）工程招标控制价费用表|单位工程费用表|分部分项工程清单与计价表|工程量确认单|专业工程费用表|专业费用表)"
)
_SUB_PROJECT_SKIP_LINE_PATTERN = re.compile(
    r"^(?:"
    r"标段[:：]?|"
    r"第?\d+页(?:共\d+页)?|"
    r"共\d+页|"
    r"序号|"
    r"项目编码|"
    r"项目名称|"
    r"项目特征|"
    r"计量单位|"
    r"工程量|"
    r"综合单价|"
    r"合价|"
    r"金额|"
    r"费用名称|"
    r"计算公式|"
    r"备注|"
    r"人工费|"
    r"机械费|"
    r"暂估价|"
    r"[（(]?元[）)]?"
    r")$"
)


def _collapse_repeated_sub_project_phrase(text: str) -> str:
    for size in range(1, len(text) // 2 + 1):
        if len(text) % size:
            continue
        piece = text[:size]
        if piece and piece * (len(text) // size) == text:
            return piece
    return text


def _clean_detected_sub_project(value: str) -> str:
    text = _HTML_TAG_PATTERN.sub("", value or "")
    text = re.sub(r"\s+", "", text)
    if "工程名称" in text:
        text = re.split(r"工程名称[：:]?", text)[-1]
    text = _SUB_PROJECT_TABLE_TITLE_PATTERN.sub("", text)
    text = re.split(
        r"(?:标段[:：]?|第?\d+页|序号|费用名称|计算公式|金额(?:（元）?)?|项目编码|项目名称|项目特征|计量单位|工程量|综合单价|合价|人工费|机械费|暂估价|备注)",
        text,
        maxsplit=1,
    )[0]
    text = _SUB_PROJECT_NOISE_PATTERN.sub("", text)
    text = re.sub(r"(?:工程名称[：:]?)+", "", text)
    text = _collapse_repeated_sub_project_phrase(text)
    text = re.sub(r"(?:[-—–_]X?\d+){5,}$", "", text, flags=re.IGNORECASE)
    text = text.rstrip("-—–_:：")
    text = re.sub(r"厘$", "幢", text)
    return text


def _line_project_name_tail(line: str) -> str:
    remainder = _strip_ocr_score_prefix(line)
    for sep in ["工程名称：", "工程名称:"]:
        if sep in remainder:
            remainder = remainder.split(sep, 1)[1]
            break
    return remainder.strip()


def _strip_ocr_score_prefix(value: str) -> str:
    return re.sub(r"^\s*(?:0(?:\.\d+)?|1(?:\.0+)?)\s*[\t ]+", "", value or "").strip()


def _is_sub_project_skip_piece(value: str) -> bool:
    text = _HTML_TAG_PATTERN.sub("", value or "")
    text = re.sub(r"\s+", "", text)
    text = text.strip("_＿—-－~～·•:：")
    if not text:
        return True
    if _SUB_PROJECT_TABLE_TITLE_PATTERN.fullmatch(text):
        return True
    return bool(_SUB_PROJECT_SKIP_LINE_PATTERN.fullmatch(text))


def _clean_sub_project_piece(value: str) -> str:
    text = _clean_detected_sub_project(_strip_ocr_score_prefix(value))
    if _is_sub_project_skip_piece(text):
        return ""
    return text


_SUB_PROJECT_TABLE_HEADER_RE = re.compile(
    r"^(?:序号|项目编码|项目名称|项目特征|计量单位|工程量|综合单价|合价|"
    r"金额(?:[（(]元[）)])?|费用名称|计算公式|备注|人工费|机械费|暂估价)$"
)
_SUB_PROJECT_METADATA_RE = re.compile(
    r"^(?:标段(?:[:：].*)?|第?\d+页(?:共\d+页)?|共\d+页|页共\d+页)$"
)


def _is_sub_project_table_header(value: str) -> bool:
    """Whether a line starts the table body after a project-name header."""
    text = re.sub(r"\s+", "", _HTML_TAG_PATTERN.sub("", value or ""))
    return bool(_SUB_PROJECT_TABLE_HEADER_RE.fullmatch(text))


def _is_sub_project_metadata(value: str) -> bool:
    text = re.sub(r"\s+", "", _HTML_TAG_PATTERN.sub("", value or ""))
    return bool(_SUB_PROJECT_METADATA_RE.fullmatch(text))


def _append_sub_project_addendum(project_name: str, addendum: str) -> str:
    """Attach a standalone building/room identifier to an existing project name."""
    if not project_name:
        return addendum
    if not addendum or addendum == project_name:
        return project_name
    if project_name.endswith(("-", "—", "–", "_")) or addendum.startswith(("-", "—", "–", "_")):
        return project_name + addendum
    return f"{project_name}-{addendum}"


def _extract_project_name_from_text_lines(page_text: str, lookahead: int = 6) -> str:
    lines = [line.strip() for line in page_text.replace("\r", "").split("\n") if line.strip()]
    for index, line in enumerate(lines):
        if "工程名称" not in line:
            continue
        pieces: list[str] = []
        raw_first_tail = _line_project_name_tail(line)
        first_needs_continuation_dash = raw_first_tail.rstrip().endswith(("-", "—", "–"))
        first_needs_continuation_word = raw_first_tail.rstrip().endswith(
            ("及", "及其", "和", "与", "同", "、", "，", ",", "（", "(")
        )
        first_piece = _clean_sub_project_piece(_line_project_name_tail(line))
        if first_piece:
            pieces.append(first_piece)

        # A table often prints a building/room identifier on its own line after
        # "工程名称". Scan only the nearby header area: skip metadata such as
        # "标段" and page numbers, then stop before the actual table columns.
        # This handles 42#, 1-15幢, 1101室, etc. without making one format a
        # special case.
        project_name = "".join(pieces)
        for next_line in lines[index + 1:index + 1 + lookahead]:
            raw_next_line = _strip_ocr_score_prefix(next_line)
            if _is_sub_project_table_header(raw_next_line):
                break
            if _is_sub_project_metadata(raw_next_line):
                continue
            piece = _clean_sub_project_piece(next_line)
            if piece:
                if (
                    pieces
                    and first_needs_continuation_dash
                    and len(pieces) == 1
                    and _ROOM_CONTINUATION_PATTERN.fullmatch(piece)
                ):
                    project_name = project_name.rstrip("-—–") + "-" + piece
                elif first_needs_continuation_dash or first_needs_continuation_word:
                    project_name += piece
                # Standalone suffixes must contain a number. This avoids
                # joining arbitrary prose that happens to sit above a table.
                elif re.search(r"\d", piece):
                    project_name = _append_sub_project_addendum(project_name, piece)
        if project_name:
            return _clean_detected_sub_project(project_name)
    return ""


def _build_full_project_name(row: list) -> str:
    row_text = _row_text(row)
    name_match = _PROJECT_NAME_PATTERN.search(row_text)
    if not name_match:
        return ""
    project_name = name_match.group(1).strip()
    project_name = re.sub(r"\s+", "", project_name)
    if project_name.endswith("-") or project_name.endswith("—") or project_name.endswith("–"):
        cells = _cells(row)
        for cell in cells:
            cell = cell.strip()
            if not cell:
                continue
            room_match = _ROOM_ID_PATTERN.match(cell)
            if room_match and cell != project_name:
                project_name = project_name + room_match.group(1)
                break
    return _clean_detected_sub_project(project_name)


def detect_sub_project(table: dict[str, Any] | None, page_text: str) -> str:
    if page_text:
        full_name = _extract_project_name_from_text_lines(page_text)
        if full_name:
            return _clean_detected_sub_project(full_name)

        for pattern in SUB_PROJECT_PATTERNS:
            m = pattern.search(page_text)
            if m:
                return _clean_detected_sub_project(m.group(1).strip())

    if isinstance(table, dict):
        for row in table.get("grid", [])[:5]:
            full_name = _build_full_project_name(row)
            if full_name:
                return _clean_detected_sub_project(full_name)
            compact = _compact(_row_text(row))
            for pattern in SUB_PROJECT_PATTERNS:
                m = pattern.search(compact)
                if m:
                    return _clean_detected_sub_project(m.group(1).strip())

    return ""


SUB_PROJECT_MATCH_PATTERNS = [
    re.compile(r"(\d+(?:[—\-–]\d+)*\s*(?:幢|幛|号楼?|栋|单元?|户))"),
]


def match_sub_project(text: str) -> str:
    if not text:
        return ""
    for pattern in SUB_PROJECT_MATCH_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1).strip()
    return ""


def merge_sub_item_project_rows(
    page_rows_map: dict[int, list[SubItemProjectRow]],
) -> list[SubItemProjectRow]:
    if not page_rows_map:
        return []
    merged: list[SubItemProjectRow] = []
    global_idx = 0
    for page_no in sorted(page_rows_map.keys()):
        rows = page_rows_map[page_no]
        for row in rows:
            if not row.project_name.strip() and not row.project_code.strip() and not row.seq.strip():
                continue
            header_like = (
                _compact(row.project_name) in ["项目名称", "项目编码"]
                or _compact(row.project_code) in ["项目编码", "编码"]
            )
            if header_like:
                continue
            global_idx += 1
            row.row_index = global_idx
            merged.append(row)
    return merged
