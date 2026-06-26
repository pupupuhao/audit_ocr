#!/usr/bin/env python3
"""Find files/pages where Qwen likely produced misaligned sub-item rows."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root is not object: {path}")
    return data


def resolve_business_root(input_path: Path) -> Path:
    input_path = input_path.expanduser().resolve()
    if input_path.name == "business_json_vl_llm":
        return input_path
    nested = input_path / "business_json_vl_llm"
    if nested.exists():
        return nested
    nested = input_path / "audit_ocr_vl_llm_json" / "business_json_vl_llm"
    if nested.exists():
        return nested
    return input_path


def is_blank(value: Any) -> bool:
    return str(value or "").strip() == ""


def is_amount(value: Any) -> bool:
    text = re.sub(r"[￥¥,\s]", "", str(value or "").strip())
    return bool(text and re.fullmatch(r"\d+(?:\.\d+)?", text))


def is_project_code(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(re.fullmatch(r"\d{9,15}", text))


def row_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    project_code = str(row.get("project_code") or "").strip()
    unit_price = str(row.get("unit_price") or "").strip()
    total_price = str(row.get("total_price") or "").strip()
    labor_cost = str(row.get("labor_cost") or "").strip()
    machinery_cost = str(row.get("machinery_cost") or "").strip()

    if unit_price and (unit_price == project_code or is_project_code(unit_price)):
        reasons.append("unit_price像项目编码")

    if (
        unit_price
        and (unit_price == project_code or is_project_code(unit_price))
        and is_amount(total_price)
        and is_amount(labor_cost)
    ):
        reasons.append("疑似金额列整体右移")

    if is_project_code(total_price) or is_project_code(labor_cost) or is_project_code(machinery_cost):
        reasons.append("金额列出现项目编码")

    if not is_blank(row.get("seq")) and not is_blank(row.get("project_name")) and not is_blank(row.get("project_code")):
        numeric_fields = ["quantity", "unit_price", "total_price", "labor_cost", "machinery_cost"]
        bad_numeric = [field for field in numeric_fields if row.get(field) and not is_amount(row.get(field))]
        if bad_numeric:
            reasons.append("数值列非金额:" + ",".join(bad_numeric))

    return reasons


def iter_sub_item_rows(payload: dict[str, Any]):
    sub_projects = payload.get("sub_projects")
    if not isinstance(sub_projects, list):
        return
    for sp_index, sub_project in enumerate(sub_projects):
        if not isinstance(sub_project, dict):
            continue
        sub_project_id = str(sub_project.get("sub_project_id") or sub_project.get("sub_project_name") or "")
        rows = sub_project.get("sub_item_project_rows")
        if not isinstance(rows, list):
            continue
        for row_index, row in enumerate(rows):
            if isinstance(row, dict):
                yield sp_index, sub_project_id, row_index, row


def row_preview(row: dict[str, Any]) -> str:
    fields = [
        row.get("seq"),
        row.get("project_code"),
        row.get("project_name"),
        row.get("unit"),
        row.get("quantity"),
        row.get("unit_price"),
        row.get("total_price"),
        row.get("labor_cost"),
        row.get("machinery_cost"),
    ]
    return " | ".join(str(value or "").strip() for value in fields).strip(" |")


def main() -> None:
    parser = argparse.ArgumentParser(description="Find Qwen misaligned file/page candidates.")
    parser.add_argument("--input", required=True, help="business_json_vl_llm dir or audit_ocr_vl_llm_json dir.")
    parser.add_argument("--output", default="qwen_misaligned_pages.tsv", help="Output TSV path.")
    parser.add_argument("--details-output", default="qwen_misaligned_rows.json", help="Output detail JSON path.")
    args = parser.parse_args()

    business_root = resolve_business_root(Path(args.input))
    json_files = sorted(business_root.rglob("business_extract.json"))

    page_hits: dict[tuple[str, int], dict[str, Any]] = {}
    detail_rows: list[dict[str, Any]] = []

    for json_file in json_files:
        try:
            payload = load_json(json_file)
        except Exception:
            continue
        file_name = str(payload.get("file_name") or json_file.parent.name)
        for sp_index, sub_project_id, row_index, row in iter_sub_item_rows(payload):
            reasons = row_reasons(row)
            if not reasons:
                continue
            page_no = int(row.get("page_no") or 0)
            key = (file_name, page_no)
            item = page_hits.setdefault(key, {
                "file_name": file_name,
                "page_no": page_no,
                "hit_rows": 0,
                "reasons": set(),
                "sample": "",
                "json_path": str(json_file),
            })
            item["hit_rows"] += 1
            item["reasons"].update(reasons)
            if not item["sample"]:
                item["sample"] = row_preview(row)
            detail_rows.append({
                "file_name": file_name,
                "page_no": page_no,
                "sub_project_index": sp_index,
                "sub_project_id": sub_project_id,
                "row_index": row_index,
                "reasons": reasons,
                "preview": row_preview(row),
                "row": row,
                "json_path": str(json_file),
            })

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    headers = ["file_name", "page_no", "hit_rows", "reasons", "sample", "json_path"]
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, delimiter="\t")
        writer.writeheader()
        for item in sorted(page_hits.values(), key=lambda x: (x["file_name"], x["page_no"])):
            writer.writerow({
                "file_name": item["file_name"],
                "page_no": item["page_no"],
                "hit_rows": item["hit_rows"],
                "reasons": "、".join(sorted(item["reasons"])),
                "sample": item["sample"],
                "json_path": item["json_path"],
            })

    details_path = Path(args.details_output).expanduser()
    details_path.parent.mkdir(parents=True, exist_ok=True)
    details_path.write_text(json.dumps(detail_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"业务JSON数量: {len(json_files)}")
    print(f"疑似错位文件数: {len({item['file_name'] for item in page_hits.values()})}")
    print(f"疑似错位页数: {len(page_hits)}")
    print(f"疑似错位行数: {len(detail_rows)}")
    print(f"页清单TSV: {output_path}")
    print(f"行明细JSON: {details_path}")


if __name__ == "__main__":
    main()
