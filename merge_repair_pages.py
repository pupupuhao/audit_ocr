#!/usr/bin/env python3
"""Merge selected page-level repair rows back into full business_extract JSON files."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root is not object: {path}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def read_page_targets(paths: list[str]) -> dict[str, set[int]]:
    targets: dict[str, set[int]] = defaultdict(set)
    for value in paths:
        path = Path(value).expanduser()
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                file_name = (row.get("file_name") or "").strip()
                page_text = (row.get("page_no") or "").strip()
                if not file_name or not page_text:
                    continue
                try:
                    page_no = int(page_text)
                except ValueError:
                    continue
                if page_no > 0:
                    targets[file_name].add(page_no)
    return targets


def business_json_path(root: Path, file_name: str) -> Path:
    return root / file_name / "business_extract.json"


def ensure_sub_projects(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sub_projects = payload.get("sub_projects")
    if not isinstance(sub_projects, list):
        sub_projects = []
        payload["sub_projects"] = sub_projects
    return [item for item in sub_projects if isinstance(item, dict)]


def sub_project_key(sub_project: dict[str, Any]) -> str:
    return str(sub_project.get("sub_project_id") or sub_project.get("sub_project_name") or "").strip()


def page_no_of(row: Any) -> int | None:
    if not isinstance(row, dict):
        return None
    try:
        return int(row.get("page_no") or 0)
    except (TypeError, ValueError):
        return None


def collect_repair_rows(repair_payload: dict[str, Any], pages: set[int]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for sub_project in ensure_sub_projects(repair_payload):
        key = sub_project_key(sub_project)
        for row in sub_project.get("sub_item_project_rows") or []:
            if not isinstance(row, dict):
                continue
            if page_no_of(row) in pages:
                rows.append((key, sub_project, row))
    return rows


def find_or_create_sub_project(payload: dict[str, Any], key: str, template: dict[str, Any]) -> dict[str, Any]:
    sub_projects = payload.setdefault("sub_projects", [])
    for sub_project in sub_projects:
        if isinstance(sub_project, dict) and sub_project_key(sub_project) == key:
            return sub_project

    new_sub_project = {
        "sub_project_id": template.get("sub_project_id", key),
        "sub_project_name": template.get("sub_project_name", key),
        "parent_project": template.get("parent_project", ""),
        "unit_project_fee_rows": template.get("unit_project_fee_rows", []),
        "sub_item_project_rows": [],
    }
    sub_projects.append(new_sub_project)
    return new_sub_project


def merge_one_file(original_payload: dict[str, Any], repair_payload: dict[str, Any], pages: set[int]) -> dict[str, int]:
    repair_rows = collect_repair_rows(repair_payload, pages)
    removed = 0
    inserted = 0

    for sub_project in ensure_sub_projects(original_payload):
        rows = sub_project.get("sub_item_project_rows")
        if not isinstance(rows, list):
            continue
        kept_rows = [row for row in rows if page_no_of(row) not in pages]
        removed += len(rows) - len(kept_rows)
        sub_project["sub_item_project_rows"] = kept_rows

    for key, repair_sub_project, repair_row in repair_rows:
        target = find_or_create_sub_project(original_payload, key, repair_sub_project)
        rows = target.setdefault("sub_item_project_rows", [])
        if isinstance(rows, list):
            rows.append(dict(repair_row))
            inserted += 1

    return {
        "removed_rows": removed,
        "inserted_rows": inserted,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge repaired page rows into full business_extract JSON files.")
    parser.add_argument("--original", required=True, help="Original business_json_vl_llm dir or audit_ocr_vl_llm_json dir.")
    parser.add_argument("--repair", required=True, help="Repair business_json_vl_llm dir or audit_ocr_vl_llm_json_repair dir.")
    parser.add_argument("--page-list", action="append", required=True, help="TSV with file_name and page_no. Can be repeated.")
    parser.add_argument("--output", required=True, help="Output business_json_vl_llm dir or audit_ocr_vl_llm_json dir.")
    parser.add_argument("--backup-original", action="store_true", help="Copy original JSON files to <output>/../merge_backups before writing.")
    args = parser.parse_args()

    original_root = resolve_business_root(Path(args.original))
    repair_root = resolve_business_root(Path(args.repair))
    output_root = resolve_business_root(Path(args.output))
    targets = read_page_targets(args.page_list)

    summaries: list[dict[str, Any]] = []
    backup_root = output_root.parent / "merge_backups" / datetime.now().strftime("%Y%m%d_%H%M%S")

    for file_name, pages in sorted(targets.items()):
        original_path = business_json_path(original_root, file_name)
        repair_path = business_json_path(repair_root, file_name)
        output_path = business_json_path(output_root, file_name)

        summary = {
            "file_name": file_name,
            "pages": ",".join(str(page) for page in sorted(pages)),
            "status": "ok",
            "removed_rows": 0,
            "inserted_rows": 0,
            "original_json": str(original_path),
            "repair_json": str(repair_path),
            "output_json": str(output_path),
        }

        if not original_path.exists():
            summary["status"] = "missing_original"
            summaries.append(summary)
            continue
        if not repair_path.exists():
            summary["status"] = "missing_repair"
            summaries.append(summary)
            continue

        payload = load_json(original_path)
        repair_payload = load_json(repair_path)
        stats = merge_one_file(payload, repair_payload, pages)
        summary.update(stats)

        if args.backup_original:
            backup_path = business_json_path(backup_root, file_name)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original_path, backup_path)

        write_json(output_path, payload)
        summaries.append(summary)

    summary_path = output_root.parent / "merge_repair_pages_summary.tsv"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "file_name",
        "pages",
        "status",
        "removed_rows",
        "inserted_rows",
        "original_json",
        "repair_json",
        "output_json",
    ]
    with summary_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, delimiter="\t")
        writer.writeheader()
        writer.writerows(summaries)

    print(f"目标文件数: {len(targets)}")
    print(f"成功文件数: {sum(1 for item in summaries if item['status'] == 'ok')}")
    print(f"缺少原始JSON: {sum(1 for item in summaries if item['status'] == 'missing_original')}")
    print(f"缺少修复JSON: {sum(1 for item in summaries if item['status'] == 'missing_repair')}")
    print(f"删除旧页行数: {sum(int(item['removed_rows']) for item in summaries)}")
    print(f"插入修复行数: {sum(int(item['inserted_rows']) for item in summaries)}")
    print(f"合并汇总: {summary_path}")


if __name__ == "__main__":
    main()
