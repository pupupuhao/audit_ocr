#!/usr/bin/env python3
"""Check exported business JSON files for likely Qwen extraction issues."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


CORE_ROW_FIELDS = [
    "seq",
    "project_code",
    "project_name",
    "project_description",
    "unit",
    "quantity",
    "unit_price",
    "total_price",
    "provisional_estimate",
    "labor_cost",
    "machinery_cost",
    "remark",
]

ISSUE_LABELS = {
    "json_error": "JSON读取失败",
    "no_sub_item_rows": "没有分部分项清单行",
    "no_effective_sub_item_rows": "没有有效明细行",
    "has_empty_rows": "存在空行",
    "has_rows_without_seq": "存在缺少序号的行",
    "missing_report_root": "缺少reports目录，无法判断是否走Qwen",
    "missing_report": "缺少提取报告",
    "report_error": "提取报告读取失败",
    "no_qwen_pages_in_report": "报告中没有Qwen提取页",
    "qwen_rows_0_in_report": "报告中Qwen提取行数为0",
}

ROW_REASON_LABELS = {
    "empty_row": "空行",
    "missing_or_invalid_seq": "缺少或无效序号",
}

TSV_HEADERS = [
    ("file_name", "文件名"),
    ("issues", "问题"),
    ("total_rows", "清单行总数"),
    ("effective_rows", "有效明细行数"),
    ("empty_rows", "空行数"),
    ("rows_without_seq", "缺少序号行数"),
    ("qwen_pages", "Qwen提取页数"),
    ("qwen_rows", "Qwen提取行数"),
    ("report_status", "报告状态"),
    ("json_path", "JSON路径"),
]


def translate_issue(value: str) -> str:
    if value.startswith("json_error:"):
        return f"{ISSUE_LABELS['json_error']}:{value.split(':', 1)[1]}"
    if value.startswith("report_error:"):
        return f"{ISSUE_LABELS['report_error']}:{value.split(':', 1)[1]}"
    return ISSUE_LABELS.get(value, value)


def translate_issues(values: list[str]) -> str:
    return "，".join(translate_issue(value) for value in values)


def translate_report_status(value: str) -> str:
    return translate_issue(value) if value != "ok" else "正常"


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


def resolve_report_root(business_root: Path, reports_arg: str | None) -> Path | None:
    if reports_arg:
        path = Path(reports_arg).expanduser().resolve()
        return path if path.exists() else None
    candidates = [
        business_root.parent / "reports",
        business_root.parent.parent / "reports",
        business_root.parent / "audit_ocr_vl_llm_json" / "reports",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def is_blank(value: Any) -> bool:
    return str(value or "").strip() == ""


def is_empty_sub_item_row(row: dict[str, Any]) -> bool:
    return all(is_blank(row.get(field)) for field in CORE_ROW_FIELDS)


def is_valid_seq(value: Any) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", str(value or "").strip()))


def is_effective_sub_item_row(row: dict[str, Any]) -> bool:
    if not is_valid_seq(row.get("seq")):
        return False
    return not (is_blank(row.get("project_code")) and is_blank(row.get("project_name")))


def iter_sub_item_rows(payload: dict[str, Any]) -> list[tuple[int, str, int, dict[str, Any]]]:
    result: list[tuple[int, str, int, dict[str, Any]]] = []
    sub_projects = payload.get("sub_projects")
    if not isinstance(sub_projects, list):
        return result
    for sp_index, sub_project in enumerate(sub_projects):
        if not isinstance(sub_project, dict):
            continue
        sub_project_id = str(sub_project.get("sub_project_id") or sub_project.get("sub_project_name") or "")
        rows = sub_project.get("sub_item_project_rows")
        if not isinstance(rows, list):
            continue
        for row_index, row in enumerate(rows):
            if isinstance(row, dict):
                result.append((sp_index, sub_project_id, row_index, row))
    return result


def report_stats(pdf_name: str, report_root: Path | None) -> dict[str, Any]:
    if report_root is None:
        return {
            "report_status": "missing_report_root",
            "qwen_pages": 0,
            "qwen_rows": 0,
            "no_table_pages": 0,
            "zero_qwen_pages": 0,
        }
    report = report_root / f"{pdf_name}_vl_llm_extract_summary.json"
    if not report.exists():
        return {
            "report_status": "missing_report",
            "qwen_pages": 0,
            "qwen_rows": 0,
            "no_table_pages": 0,
            "zero_qwen_pages": 0,
        }
    try:
        data = load_json(report)
    except Exception as exc:
        return {
            "report_status": f"report_error:{exc}",
            "qwen_pages": 0,
            "qwen_rows": 0,
            "no_table_pages": 0,
            "zero_qwen_pages": 0,
        }
    pages = data.get("pages") or data.get("page_summaries") or []
    if not isinstance(pages, list):
        pages = []
    qwen_items = [
        item for item in pages
        if isinstance(item, dict) and item.get("extractor") == "rules_pipeline_qwen_fields"
    ]
    qwen_rows = sum(int(item.get("rows") or 0) for item in qwen_items)
    return {
        "report_status": "ok",
        "qwen_pages": len({item.get("page_no") for item in qwen_items}),
        "qwen_rows": qwen_rows,
        "no_table_pages": sum(
            1 for item in pages
            if isinstance(item, dict) and item.get("extractor") == "rules_pipeline_no_table"
        ),
        "zero_qwen_pages": sum(1 for item in qwen_items if int(item.get("rows") or 0) == 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Find likely Qwen extraction issues in business JSON outputs.")
    parser.add_argument("--input", required=True, help="business_json_vl_llm dir or audit_ocr_vl_llm_json dir.")
    parser.add_argument("--reports", help="Optional reports directory.")
    parser.add_argument("--output", default="qwen_result_issues.tsv", help="Output TSV path.")
    parser.add_argument("--details-output", default="qwen_result_issue_rows.json", help="Problem row details JSON path.")
    args = parser.parse_args()

    business_root = resolve_business_root(Path(args.input))
    report_root = resolve_report_root(business_root, args.reports)
    json_files = sorted(business_root.rglob("business_extract.json"))

    issue_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []

    for json_file in json_files:
        pdf_name = json_file.parent.name
        try:
            payload = load_json(json_file)
        except Exception as exc:
            issue_rows.append({
                "file_name": pdf_name,
                "issues": f"{ISSUE_LABELS['json_error']}:{exc}",
                "total_rows": 0,
                "effective_rows": 0,
                "empty_rows": 0,
                "rows_without_seq": 0,
                "qwen_pages": 0,
                "qwen_rows": 0,
                "report_status": "",
                "json_path": str(json_file),
            })
            continue

        rows = iter_sub_item_rows(payload)
        total_rows = len(rows)
        empty_rows = 0
        rows_without_seq = 0
        effective_rows = 0

        for sp_index, sub_project_id, row_index, row in rows:
            reasons = []
            if is_empty_sub_item_row(row):
                empty_rows += 1
                reasons.append("empty_row")
            elif not is_valid_seq(row.get("seq")):
                rows_without_seq += 1
                reasons.append("missing_or_invalid_seq")
            if is_effective_sub_item_row(row):
                effective_rows += 1
            if reasons:
                detail_rows.append({
                    "file_name": pdf_name,
                    "sub_project_index": sp_index,
                    "sub_project_id": sub_project_id,
                    "row_index": row_index,
                    "page_no": row.get("page_no"),
                    "reasons": [ROW_REASON_LABELS.get(reason, reason) for reason in reasons],
                    "row": row,
                    "json_path": str(json_file),
                })

        stats = report_stats(pdf_name, report_root)
        issues = []
        if total_rows == 0:
            issues.append("no_sub_item_rows")
        if effective_rows == 0:
            issues.append("no_effective_sub_item_rows")
        if empty_rows:
            issues.append("has_empty_rows")
        if rows_without_seq:
            issues.append("has_rows_without_seq")
        if stats["report_status"] != "ok":
            issues.append(stats["report_status"])
        elif stats["qwen_pages"] == 0:
            issues.append("no_qwen_pages_in_report")
        elif stats["qwen_rows"] == 0:
            issues.append("qwen_rows_0_in_report")
        if issues:
            issue_rows.append({
                "file_name": pdf_name,
                "issues": translate_issues(issues),
                "total_rows": total_rows,
                "effective_rows": effective_rows,
                "empty_rows": empty_rows,
                "rows_without_seq": rows_without_seq,
                "qwen_pages": stats["qwen_pages"],
                "qwen_rows": stats["qwen_rows"],
                "report_status": translate_report_status(stats["report_status"]),
                "json_path": str(json_file),
            })

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [field for field, _ in TSV_HEADERS]
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fieldnames,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writerow({field: label for field, label in TSV_HEADERS})
        writer.writerows(issue_rows)

    details_path = Path(args.details_output).expanduser()
    details_path.parent.mkdir(parents=True, exist_ok=True)
    details_path.write_text(json.dumps(detail_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"业务JSON数量: {len(json_files)}")
    print(f"存在问题的文件数: {len(issue_rows)}")
    print(f"问题行数量: {len(detail_rows)}")
    print(f"问题汇总TSV: {output_path}")
    print(f"问题行明细JSON: {details_path}")
    print(f"报告目录: {report_root or '(未找到)'}")


if __name__ == "__main__":
    main()
