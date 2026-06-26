#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
import traceback
from pathlib import Path
from typing import Any

from src.pdf_converter import pdf_to_images
from src.rapid_text_ocr import run_rapid_text_ocr
from src.utils import ensure_dir, page_file_name, pdf_output_name, write_json


DEFAULT_DET_MODEL = Path("models/onnx/ppocrv5_mobile_det.onnx")
DEFAULT_REC_MODEL = Path("models/onnx/ppocrv5_mobile_rec.onnx")
DEFAULT_REC_KEYS = Path("models/onnx/ppocrv5_mobile_rec_keys.txt")


def _default_model(path: Path) -> str | None:
    return str(path) if path.exists() else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill missing rapid_screen_ocr and page_texts outputs for existing OCR/VL output roots."
    )
    parser.add_argument("--input", default="input", help="Input PDF directory. Default: input")
    parser.add_argument("--output", required=True, help="Output root to fill, e.g. output_vl_batch")
    parser.add_argument("--file", default=None, help="Process one PDF file name or absolute path.")
    parser.add_argument("--dpi", type=int, default=200, help="Render DPI if page images are missing. Default: 200")
    parser.add_argument("--page", action="append", help="Specific 1-based page number(s), comma-separated or repeated.")
    parser.add_argument("--start-page", type=int, default=None, help="First 1-based page number to process.")
    parser.add_argument("--end-page", type=int, default=None, help="Last 1-based page number to process.")
    parser.add_argument("--max-pages", type=int, default=None, help="Process only first N selected pages.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing rapid/page_text files.")
    parser.add_argument("--det-model-path", default=_default_model(DEFAULT_DET_MODEL), help="RapidOCR det ONNX model.")
    parser.add_argument("--rec-model-path", default=_default_model(DEFAULT_REC_MODEL), help="RapidOCR rec ONNX model.")
    parser.add_argument("--rec-keys-path", default=_default_model(DEFAULT_REC_KEYS), help="RapidOCR rec dictionary.")
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


def _resolve_pdf_files(input_dir: Path, file_arg: str | None) -> list[Path]:
    if file_arg:
        file_path = Path(file_arg)
        if not file_path.is_absolute():
            file_path = input_dir / file_path
        return [file_path] if file_path.exists() else []
    if not input_dir.exists():
        return []
    return sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")


def _page_no_from_image_path(image_path: str) -> int:
    try:
        return int(Path(image_path).stem.split("_")[-1])
    except (TypeError, ValueError):
        return 0


def _bbox_center(item: dict[str, Any]) -> tuple[float, float] | None:
    bbox = item.get("bbox")
    if bbox is None:
        return None
    try:
        points = bbox.tolist() if hasattr(bbox, "tolist") else bbox
        xs = [float(point[0]) for point in points if isinstance(point, (list, tuple)) and len(point) >= 2]
        ys = [float(point[1]) for point in points if isinstance(point, (list, tuple)) and len(point) >= 2]
    except (TypeError, ValueError):
        return None
    if not xs or not ys:
        return None
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _text_lines_from_ocr(ocr_result: dict[str, Any]) -> list[str]:
    items = []
    for item in ocr_result.get("items", []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        center = _bbox_center(item)
        if center:
            items.append((center[1], center[0], text))
        else:
            items.append((0.0, 0.0, text))
    return [text for _, _, text in sorted(items)]


def _save_page_text(ocr_result: dict[str, Any], output_dir: Path, page_no: int) -> dict[str, Any]:
    ensure_dir(output_dir)
    lines = _text_lines_from_ocr(ocr_result)
    text = "\n".join(lines)
    txt_path = output_dir / page_file_name(page_no, "_text.txt")
    json_path = output_dir / page_file_name(page_no, "_text.json")

    txt_path.write_text(text, encoding="utf-8")
    write_json(
        json_path,
        {
            "image_path": ocr_result.get("image_path"),
            "page_no": page_no,
            "engine": ocr_result.get("engine", "rapidocr_onnxruntime"),
            "line_count": len(lines),
            "lines": lines,
            "text": text,
        },
    )
    return {
        "line_count": len(lines),
        "txt_path": str(txt_path),
        "json_path": str(json_path),
    }


def _existing_page_images(
    pages_dir: Path,
    start_page: int | None,
    end_page: int | None,
    max_pages: int | None,
    page_numbers: list[int] | None = None,
) -> list[str]:
    paths = sorted(pages_dir.glob("page_*.png"))
    page_set = set(page_numbers or [])
    selected = []
    for path in paths:
        page_no = _page_no_from_image_path(str(path))
        if page_set and page_no not in page_set:
            continue
        if start_page is not None and page_no < start_page:
            continue
        if end_page is not None and page_no > end_page:
            continue
        selected.append(str(path))
    if max_pages is not None:
        selected = selected[:max_pages]
    return selected


def process_pdf(
    pdf_path: Path,
    output_root: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    pdf_name = pdf_output_name(pdf_path)
    started_at = time.perf_counter()
    errors: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []

    pages_dir = output_root / "pages" / pdf_name
    rapid_dir = output_root / "rapid_screen_ocr" / pdf_name
    text_dir = output_root / "page_texts" / pdf_name

    image_paths = _existing_page_images(
        pages_dir,
        args.start_page,
        args.end_page,
        args.max_pages,
        page_numbers=args.page_numbers,
    )
    if not image_paths:
        try:
            image_paths = pdf_to_images(
                str(pdf_path),
                str(output_root / "pages"),
                dpi=args.dpi,
                start_page=args.start_page,
                end_page=args.end_page,
                page_numbers=args.page_numbers,
            )
            if args.max_pages is not None:
                image_paths = image_paths[:args.max_pages]
        except Exception as exc:
            errors.append({"stage": "pdf_to_images", "error": str(exc), "traceback": traceback.format_exc()})
            image_paths = []

    print(f"\n==> Fill RapidOCR texts: {pdf_path.name} ({len(image_paths)} pages)")
    for ordinal, image_path in enumerate(image_paths, start=1):
        page_no = _page_no_from_image_path(image_path) or ordinal
        rapid_json = rapid_dir / page_file_name(page_no, "_rapid_ocr.json")
        text_json = text_dir / page_file_name(page_no, "_text.json")
        page_result: dict[str, Any] = {
            "page_no": page_no,
            "image_path": image_path,
            "rapid_json": str(rapid_json),
            "text_json": str(text_json),
            "skipped": False,
            "errors": [],
        }

        if not args.overwrite and rapid_json.exists() and text_json.exists():
            page_result["skipped"] = True
            page_result["reason"] = "rapid_screen_ocr and page_texts already exist"
            print(f"  - Page {page_no}: skip existing")
            pages.append(page_result)
            continue

        try:
            print(f"  - Page {page_no}: RapidOCR")
            rapid_result = run_rapid_text_ocr(
                image_path,
                str(rapid_dir),
                page_no,
                det_model_path=args.det_model_path,
                rec_model_path=args.rec_model_path,
                rec_keys_path=args.rec_keys_path,
            )
            page_result["line_summary"] = _save_page_text(rapid_result, text_dir, page_no)
            page_result["ocr_items"] = len(rapid_result.get("items", []))
            page_result["elapsed_seconds"] = rapid_result.get("elapsed_seconds")
        except Exception as exc:
            error = {"stage": "rapid_text_ocr", "error": str(exc), "traceback": traceback.format_exc()}
            page_result["errors"].append(error)
            errors.append({"page_no": page_no, **error})
            print(f"    ERROR: {exc}")
        pages.append(page_result)

    return {
        "file_name": pdf_path.name,
        "pdf_name": pdf_name,
        "mode": "fill_rapid_screen_ocr_and_page_texts",
        "output_root": str(output_root),
        "dpi": args.dpi,
        "processed_pages": len([page for page in pages if not page.get("skipped")]),
        "skipped_pages": len([page for page in pages if page.get("skipped")]),
        "page_count": len(pages),
        "pages": pages,
        "errors": errors,
        "elapsed_seconds": round(time.perf_counter() - started_at, 3),
    }


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input)
    output_root = Path(args.output)
    reports_dir = ensure_dir(output_root / "reports")
    pdf_files = _resolve_pdf_files(input_dir, args.file)
    if args.file and not pdf_files:
        file_path = Path(args.file)
        if not file_path.is_absolute():
            file_path = input_dir / file_path
        print(f"PDF file not found: {file_path}")
        write_json(reports_dir / "all_fill_rapid_texts_summary.json", {"file_count": 0, "files": []})
        return
    if not pdf_files:
        print(f"No PDF files found in {input_dir}")
        write_json(reports_dir / "all_fill_rapid_texts_summary.json", {"file_count": 0, "files": []})
        return

    summaries = [process_pdf(pdf_path, output_root, args) for pdf_path in pdf_files]
    for summary in summaries:
        write_json(reports_dir / f"{summary['pdf_name']}_fill_rapid_texts_summary.json", summary)
    all_summary = {
        "file_count": len(summaries),
        "files": summaries,
    }
    write_json(reports_dir / "all_fill_rapid_texts_summary.json", all_summary)
    print(f"\nDone. Filled RapidOCR/page_texts reports written to {reports_dir}")


if __name__ == "__main__":
    main()
