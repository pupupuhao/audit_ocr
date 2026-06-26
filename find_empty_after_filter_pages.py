#!/usr/bin/env python3
"""Find pages whose sub_item_project_rows become empty after export filtering."""

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


def is_valid_seq(value: Any) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", str(value or "").strip()))


def is_kept_by_export(row: dict[str, Any]) -> bool:
    return is_valid_seq(row.get("seq")) and not is_blank(row.get("project_name"))


def row_reason(row: dict[str, Any]) -> str:
    reasons: list[str] = []
    if not is_valid_seq(row.get("seq")):
        reasons.append("缺少seq")
    if is_blank(row.get("project_name")):
        reasons.append("缺少name")
    return "、".join(reasons) if reasons else "其他"


def row_preview(row: dict[str, Any]) -> str:
    fields = [
        row.get("seq"),
        row.get("project_code"),
        row.get("project_name"),
        row.get("project_description"),
        row.get("unit"),
        row.get("quantity"),
        row.get("unit_price"),
        row.get("total_price"),
    ]
    return " | ".join(str(value or "").strip() for value in fields).strip(" |")


def main() -> None:
    parser = argparse.ArgumentParser(description="Find pages empty after sub_item_project_rows filtering.")
    parser.add_argument("--input", required=True, help="business_json_vl_llm dir or audit_ocr_vl_llm_json dir.")
    parser.add_argument("--output", default="qwen_empty_after_filter_pages.tsv", help="Output TSV path.")
    parser.add_argument("--details-output", default="qwen_empty_after_filter_rows.json", help="Output detail JSON path.")
    args = parser.parse_args()

    business_root = resolve_business_root(Path(args.input))
    json_files = sorted(business_root.rglob("business_extract.json"))

    page_rows: dict[tuple[str, int], dict[str, Any]] = {}
    detail_rows: list[dict[str, Any]] = []

    for json_file in json_files:
        try:
            payload = load_json(json_file)
        except Exception:
            continue
        file_name = str(payload.get("file_name") or json_file.parent.name)
        sub_projects = payload.get("sub_projects")
        if not isinstance(sub_projects, list):
            continue
        for sp_index, sub_project in enumerate(sub_projects):
            if not isinstance(sub_project, dict):
                continue
            sub_project_id = str(sub_project.get("sub_project_id") or sub_project.get("sub_project_name") or "")
            rows = sub_project.get("sub_item_project_rows")
            if not isinstance(rows, list):
                continue
            for row_index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                page_no = int(row.get("page_no") or 0)
                if page_no <= 0:
                    continue
                key = (file_name, page_no)
                item = page_rows.setdefault(key, {
                    "file_name": file_name,
                    "page_no": page_no,
                    "raw_rows": 0,
                    "kept_rows": 0,
                    "reasons": defaultdict(int),
                    "sample": "",
                    "json_path": str(json_file),
                })
                item["raw_rows"] += 1
                if is_kept_by_export(row):
                    item["kept_rows"] += 1
                else:
                    reason = row_reason(row)
                    item["reasons"][reason] += 1
                    if not item["sample"]:
                        item["sample"] = row_preview(row)
                    detail_rows.append({
                        "file_name": file_name,
                        "page_no": page_no,
                        "sub_project_index": sp_index,
                        "sub_project_id": sub_project_id,
                        "row_index": row_index,
                        "reason": reason,
                        "preview": row_preview(row),
                        "row": row,
                        "json_path": str(json_file),
                    })

    empty_pages = [
        item for item in page_rows.values()
        if item["raw_rows"] > 0 and item["kept_rows"] == 0
    ]

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    headers = ["file_name", "page_no", "raw_rows", "kept_rows", "reasons", "sample", "json_path"]
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, delimiter="\t")
        writer.writeheader()
        for item in sorted(empty_pages, key=lambda x: (x["file_name"], x["page_no"])):
            writer.writerow({
                "file_name": item["file_name"],
                "page_no": item["page_no"],
                "raw_rows": item["raw_rows"],
                "kept_rows": item["kept_rows"],
                "reasons": "、".join(f"{key}:{value}" for key, value in sorted(item["reasons"].items())),
                "sample": item["sample"],
                "json_path": item["json_path"],
            })

    details_path = Path(args.details_output).expanduser()
    details_path.parent.mkdir(parents=True, exist_ok=True)
    details_path.write_text(json.dumps(detail_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"业务JSON数量: {len(json_files)}")
    print(f"过滤后为空页数: {len(empty_pages)}")
    print(f"页清单TSV: {output_path}")
    print(f"行明细JSON: {details_path}")


if __name__ == "__main__":
    main()
