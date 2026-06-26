from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_ROOM_RE = re.compile(r"(\d+(?:[、,，]\d+)*(?:[—\-–]\d+)*)\s*(幢|幛|栋|号楼?|厘)")
_TRAILING_ROOM_NO_RE = re.compile(r"(?:[—\-–]|^)(\d{3,4}(?:[、,，—\-–]\d{3,4})*)(?:室|户)?$")
_TRAILING_ROOM_WITH_UNIT_RE = re.compile(r"(\d{3,4}(?:[、,，—\-–]\d{3,4})*)(?:室|户)$")
_NOISE_RE = re.compile(r"(?:标段[:：]?|第?\d+\s*页\s*共\s*\d+\s*页|页共\d+页|第?\d+页|共\d+页)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_PROJECT_NAME_LABEL_RE = re.compile(r"(?:单位（专业）)?工程名称[：:]")
_TABLE_TITLE_RE = re.compile(
    r"(?:单位（专业）工程招标控制价费用表|单位工程费用表|分部分项工程清单与计价表|工程量确认单|专业工程费用表|专业费用表)"
)
_TRAILING_COORDINATE_SUFFIX_RE = re.compile(r"(?:[-—–_]X?\d+){5,}$", re.IGNORECASE)


def _compact(value: str) -> str:
    value = _HTML_TAG_RE.sub("", value or "")
    return re.sub(r"\s+", "", value)


def _collapse_repeated_phrase(text: str) -> str:
    if not text:
        return text
    for size in range(1, len(text) // 2 + 1):
        if len(text) % size:
            continue
        piece = text[:size]
        if piece and piece * (len(text) // size) == text:
            return piece
    return text


def _extract_project_name_value(text: str) -> str:
    if not text:
        return ""
    matches = list(_PROJECT_NAME_LABEL_RE.finditer(text))
    if matches:
        start = matches[-1].end()
        text = text[start:]
    text = _TABLE_TITLE_RE.sub("", text)
    text = re.split(
        r"(?:标段[:：]?|第?\d+页|第?\d+页共\d+页|序号|费用名称|计算公式|金额(?:（元）?)?|项目编码|项目名称|项目特征|计量单位|工程量|综合单价|合价|人工费|机械费|暂估价|备注)",
        text,
        maxsplit=1,
    )[0]
    return text


def _clean_project_root(value: str) -> str:
    text = _compact(value)
    text = _NOISE_RE.sub("", text)
    text = _TRAILING_COORDINATE_SUFFIX_RE.sub("", text)
    text = re.sub(r"(?:结算|审核|咨询)?报告书$", "", text)
    text = re.sub(r"^\d{2,4}[.．-]\d{1,2}[.．-]\d{1,2}", "", text)
    text = re.sub(r"(?:-\s*)?\d+(?:[、,，]\d+)*(?:[—\-–]\d+)*\s*(?:幢|幛|栋|号楼?|厘).*$", "", text)
    text = text.rstrip("-—–_:：")
    return text


def _document_root(file_name: str, document_info: dict[str, Any] | None = None) -> str:
    info = document_info or {}
    candidates = [
        str(info.get("consultation_project_name") or ""),
        str(info.get("consultation_project_full_name", {}).get("content") or "")
        if isinstance(info.get("consultation_project_full_name"), dict)
        else "",
        Path(file_name).stem,
    ]
    for candidate in candidates:
        root = _clean_project_root(candidate)
        if root:
            return root
    return _clean_project_root(file_name)


def clean_sub_project_name(value: str, file_name: str = "", document_info: dict[str, Any] | None = None) -> str:
    text = _compact(value)
    if not text:
        return ""
    text = _extract_project_name_value(text)
    text = _NOISE_RE.sub("", text)
    text = re.sub(r"(?:工程名称[：:]?)+", "", text)
    text = _collapse_repeated_phrase(text)
    text = _TRAILING_COORDINATE_SUFFIX_RE.sub("", text)
    text = text.rstrip("-—–_:：")

    room_match = _ROOM_RE.search(text)
    if not room_match:
        return text

    room_no = room_match.group(1)
    unit = room_match.group(2)
    if unit == "厘":
        unit = "幢"

    root = _document_root(file_name, document_info)
    tail = text[room_match.end():]
    room_detail_match = _TRAILING_ROOM_NO_RE.search(tail) or _TRAILING_ROOM_WITH_UNIT_RE.search(tail)
    room_detail = room_detail_match.group(1) if room_detail_match else ""
    if root:
        if room_detail:
            return f"{root}-{room_no}{unit}-{room_detail}"
        return f"{root}-{room_no}{unit}"
    if room_detail:
        return f"{text[:room_match.start()].rstrip('-—–_:：')}-{room_no}{unit}-{room_detail}".strip("-")
    return f"{text[:room_match.start()].rstrip('-—–_:：')}-{room_no}{unit}".strip("-")


def is_incomplete_sub_project_name(value: str) -> bool:
    text = clean_sub_project_name(value)
    if not text:
        return True
    return text.endswith(("-", "—", "–", "、", "，", ",", "（", "(", "及", "及其", "和", "与", "同"))


def normalize_sub_project_ids(project: dict[str, Any]) -> dict[str, Any]:
    file_name = str(project.get("file_name") or "")
    document_info = project.get("document_info") if isinstance(project.get("document_info"), dict) else {}
    sub_projects = project.get("sub_projects")
    if not isinstance(sub_projects, list):
        return project

    id_map: dict[str, str] = {}
    merged: dict[str, dict[str, Any]] = {}
    top_level_quantity_rows = project.get("quantity_confirm_rows")
    if not isinstance(top_level_quantity_rows, list):
        top_level_quantity_rows = []

    for sub_project in sub_projects:
        if not isinstance(sub_project, dict):
            continue
        old_id = str(sub_project.get("sub_project_id") or sub_project.get("sub_project_name") or "")
        new_id = clean_sub_project_name(old_id, file_name=file_name, document_info=document_info)
        if not new_id:
            new_id = old_id or "_default"
        id_map[old_id] = new_id

        target = merged.setdefault(
            new_id,
            {
                "sub_project_id": new_id,
                "sub_project_name": new_id if new_id != "_default" else "",
                "parent_project": sub_project.get("parent_project", ""),
                "unit_project_fee_rows": [],
                "sub_item_project_rows": [],
            },
        )
        legacy_quantity_rows = sub_project.get("quantity_confirm_rows")
        if isinstance(legacy_quantity_rows, list):
            top_level_quantity_rows.extend(row for row in legacy_quantity_rows if isinstance(row, dict))

        for key in ["unit_project_fee_rows", "sub_item_project_rows"]:
            rows = sub_project.get(key) or []
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict):
                    row = dict(row)
                    row_id = str(row.get("sub_project_id") or "")
                    row_name = str(row.get("sub_project_name") or "")
                    if row_id:
                        row["sub_project_id"] = clean_sub_project_name(
                            row_id,
                            file_name=file_name,
                            document_info=document_info,
                        ) or new_id
                    else:
                        row["sub_project_id"] = new_id
                    if row_name:
                        row["sub_project_name"] = clean_sub_project_name(
                            row_name,
                            file_name=file_name,
                            document_info=document_info,
                        ) or row["sub_project_id"]
                    elif "sub_project_name" in row:
                        row["sub_project_name"] = row["sub_project_id"]
                    if row["sub_project_id"] != new_id and row["sub_project_id"] in id_map:
                        row["sub_project_id"] = id_map[row["sub_project_id"]]
                        if "sub_project_name" in row:
                            row["sub_project_name"] = row["sub_project_id"]
                target[key].append(row)

    project["sub_projects"] = list(merged.values())
    cleaned_quantity_rows = []
    for row in top_level_quantity_rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item.pop("sub_project_id", None)
        item.pop("sub_project_name", None)
        cleaned_quantity_rows.append(item)
    project["quantity_confirm_rows"] = cleaned_quantity_rows
    return project
