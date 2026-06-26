#!/usr/bin/env python3
"""Batch-run OCR/VL pipelines for a directory or an explicit file list.

This is a thin orchestrator over existing entrypoints. It does not introduce a
new OCR path; it keeps models warm inside one Python process while processing
multiple PDFs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from run_auto_vl_eval import (
    DEFAULT_DET_MODEL,
    DEFAULT_REC_KEYS,
    DEFAULT_REC_MODEL,
    process_pdf_auto_vl,
)
from src.utils import ensure_dir, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch OCR runner for local audit PDFs.")
    parser.add_argument("--input", default="input", help="Input PDF directory. Default: input")
    parser.add_argument("--file", action="append", help="One PDF path/name. Can be used multiple times.")
    parser.add_argument("--file-list", help="Text file with one PDF path/name per line.")
    parser.add_argument("--output", default="output_batch_ocr", help="Output root. Default: output_batch_ocr")
    parser.add_argument("--dpi", type=int, default=160, help="PDF render DPI. Default: 160")
    parser.add_argument("--page", action="append", help="Specific 1-based page number(s), comma-separated or repeated.")
    parser.add_argument("--start-page", type=int, default=None, help="First 1-based page number to process.")
    parser.add_argument("--end-page", type=int, default=None, help="Last 1-based page number to process.")
    parser.add_argument("--max-pages", type=int, default=None, help="Process only first N selected pages per PDF.")
    parser.add_argument("--det-model-path", default=_default_model(DEFAULT_DET_MODEL), help="RapidOCR det ONNX model for auto-vl.")
    parser.add_argument("--rec-model-path", default=_default_model(DEFAULT_REC_MODEL), help="RapidOCR rec ONNX model for auto-vl.")
    parser.add_argument("--rec-keys-path", default=_default_model(DEFAULT_REC_KEYS), help="RapidOCR rec dictionary for auto-vl.")
    parser.add_argument("--dry-run", action="store_true", help="Print selected files without running OCR.")
    args = parser.parse_args()
    try:
        args.page_numbers = _parse_page_numbers(args.page)
    except ValueError as exc:
        parser.error(str(exc))
    return args


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


def _default_model(path: Path) -> str | None:
    return str(path) if path.exists() else None


def _read_file_list(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"file list not found: {path}")
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        values.append(line)
    return values


def _resolve_one_file(input_dir: Path, value: str) -> Path | None:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = input_dir / path
    if path.exists() and path.is_file() and path.suffix.lower() == ".pdf":
        return path
    return None


def resolve_pdf_files(input_dir: Path, file_args: list[str] | None, file_list: str | None) -> tuple[list[Path], list[str]]:
    requested: list[str] = []
    missing: list[str] = []

    if file_args:
        requested.extend(file_args)
    if file_list:
        requested.extend(_read_file_list(Path(file_list).expanduser()))

    if requested:
        files: list[Path] = []
        seen: set[Path] = set()
        for value in requested:
            path = _resolve_one_file(input_dir, value)
            if not path:
                missing.append(value)
                continue
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(path)
        return files, missing

    if not input_dir.exists():
        return [], [str(input_dir)]
    files = sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
    return files, []


def run_one_pdf(pdf_path: Path, output_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    return process_pdf_auto_vl(
        pdf_path,
        output_root,
        args.dpi,
        args.max_pages,
        start_page=args.start_page,
        end_page=args.end_page,
        page_numbers=args.page_numbers,
        det_model_path=args.det_model_path,
        rec_model_path=args.rec_model_path,
        rec_keys_path=args.rec_keys_path,
    )


def _native_summary_name() -> str:
    """Return the aggregate report name used by the auto-VL single-run entrypoint."""
    return "all_files_auto_vl_summary.json"


def _native_batch_summary(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the exact aggregate schema emitted by run_auto_vl_eval.py."""
    return {
        "file_count": len(summaries),
        "total_pages": sum(summary.get("total_pages", 0) for summary in summaries),
        "text_page_count": sum(summary.get("text_page_count", 0) for summary in summaries),
        "vl_page_count": sum(summary.get("vl_page_count", 0) for summary in summaries),
        "html_page_count": sum(summary.get("html_page_count", 0) for summary in summaries),
        "files": summaries,
    }


def _write_native_batch_summary(reports_dir: Path, summaries: list[dict[str, Any]]) -> Path:
    path = reports_dir / _native_summary_name()
    write_json(path, _native_batch_summary(summaries))
    return path


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input).expanduser()
    output_root = Path(args.output).expanduser()
    reports_dir = ensure_dir(output_root / "reports")
    started_at = time.perf_counter()

    try:
        pdf_files, missing = resolve_pdf_files(input_dir, args.file, args.file_list)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        _write_native_batch_summary(reports_dir, [])
        write_json(reports_dir / "batch_ocr_errors.json", {"mode": "auto-vl", "errors": [{"stage": "resolve_files", "error": str(exc)}]})
        return

    if missing:
        print("Missing files:")
        for item in missing:
            print(f"  - {item}")

    if not pdf_files:
        print("No PDF files selected.")
        _write_native_batch_summary(reports_dir, [])
        if missing:
            write_json(reports_dir / "batch_ocr_errors.json", {"missing": missing, "errors": []})
        return

    print(f"Batch OCR mode=auto-vl dpi={args.dpi} files={len(pdf_files)}")
    for index, path in enumerate(pdf_files, start=1):
        print(f"  [{index}/{len(pdf_files)}] {path}")

    if args.dry_run:
        print("Dry run: no OCR output files were written.")
        return

    summaries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for index, pdf_path in enumerate(pdf_files, start=1):
        print(f"\n### Batch item {index}/{len(pdf_files)}: {pdf_path.name}")
        try:
            summaries.append(run_one_pdf(pdf_path, output_root, args))
        except Exception as exc:
            print(f"ERROR processing {pdf_path}: {exc}")
            errors.append({"file": str(pdf_path), "error": str(exc)})

    summary_path = _write_native_batch_summary(reports_dir, summaries)
    if missing or errors:
        write_json(
            reports_dir / "batch_ocr_errors.json",
            {
                "mode": "auto-vl",
                "dpi": args.dpi,
                "requested_file_count": len(pdf_files) + len(missing),
                "completed_count": len(summaries),
                "missing": missing,
                "errors": errors,
                "elapsed_seconds": round(time.perf_counter() - started_at, 3),
            },
        )
    print(f"\nDone. OCR reports written to {summary_path}")


if __name__ == "__main__":
    main()
