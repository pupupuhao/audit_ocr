from __future__ import annotations

import argparse
import time
import traceback
from pathlib import Path
from typing import Any

from src.pdf_converter import pdf_to_images
from src.rapid_text_ocr import run_rapid_text_ocr
from src.utils import ensure_dir, page_file_name, pdf_output_name, write_json
from src.visualizer import draw_ocr_boxes
from src.vl_ocr import run_vl_ocr


DEFAULT_DET_MODEL = Path("models/onnx/ppocrv5_mobile_det.onnx")
DEFAULT_REC_MODEL = Path("models/onnx/ppocrv5_mobile_rec.onnx")
DEFAULT_REC_KEYS = Path("models/onnx/ppocrv5_mobile_rec_keys.txt")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Direct PaddleOCR-VL evaluation for selected PDF pages.")
    parser.add_argument("--input", default="input", help="Input PDF directory. Default: input")
    parser.add_argument("--output", default="output_vl", help="Output directory. Default: output_vl")
    parser.add_argument("--file", default=None, help="Process one PDF file name or path.")
    parser.add_argument("--dpi", type=int, default=160, help="PDF render DPI. Default: 160")
    parser.add_argument("--page", action="append", help="Specific 1-based page number(s), comma-separated or repeated.")
    parser.add_argument("--max-pages", type=int, default=None, help="Process only the first N selected pages.")
    parser.add_argument("--start-page", type=int, default=None, help="First 1-based page number to process.")
    parser.add_argument("--end-page", type=int, default=None, help="Last 1-based page number to process.")
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


def _default_model(path: Path) -> str | None:
    return str(path) if path.exists() else None


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


def _record_error(errors: list[dict[str, Any]], page_errors: list[dict[str, Any]], stage: str, exc: Exception) -> None:
    error = {"stage": stage, "error": str(exc), "traceback": traceback.format_exc()}
    errors.append(error)
    page_errors.append(error)


def _bbox_stats(item: dict[str, Any]) -> dict[str, float] | None:
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
    return {"x_center": sum(xs) / len(xs), "y_center": sum(ys) / len(ys)}


def _save_page_text(ocr_result: dict[str, Any], output_dir: Path, page_no: int) -> dict[str, Any]:
    """Write page text in the same format used by the auto-VL pipeline."""
    ensure_dir(output_dir)
    lines_with_position = []
    for item in ocr_result.get("items", []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        stats = _bbox_stats(item)
        if stats:
            lines_with_position.append((stats["y_center"], stats["x_center"], text))
        else:
            lines_with_position.append((0.0, 0.0, text))

    lines = [text for _, _, text in sorted(lines_with_position)]
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
    return {"line_count": len(lines), "txt_path": str(txt_path), "json_path": str(json_path)}


def process_pdf_vl(
    pdf_path: Path,
    output_root: Path,
    dpi: int,
    max_pages: int | None,
    start_page: int | None = None,
    end_page: int | None = None,
    page_numbers: list[int] | None = None,
    det_model_path: str | None = None,
    rec_model_path: str | None = None,
    rec_keys_path: str | None = None,
) -> dict[str, Any]:
    pdf_name = pdf_output_name(pdf_path)
    print(f"\n==> VL processing {pdf_path.name}")
    started_at = time.perf_counter()
    errors: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []

    pages_root = output_root / "pages"
    rapid_dir = output_root / "rapid_screen_ocr" / pdf_name
    text_dir = output_root / "page_texts" / pdf_name
    visual_dir = output_root / "visual" / pdf_name
    vl_dir = output_root / "vl" / pdf_name
    reports_dir = ensure_dir(output_root / "reports")

    try:
        image_paths = pdf_to_images(
            str(pdf_path),
            str(pages_root),
            dpi=dpi,
            start_page=start_page,
            end_page=end_page,
            page_numbers=page_numbers,
        )
    except Exception as exc:
        errors.append({"stage": "pdf_to_images", "error": str(exc), "traceback": traceback.format_exc()})
        summary = _build_summary(pdf_path, pages, errors, dpi, started_at)
        write_json(reports_dir / f"{pdf_name}_vl_summary.json", summary)
        return summary

    if max_pages is not None:
        image_paths = image_paths[:max_pages]

    for ordinal, image_path in enumerate(image_paths, start=1):
        page_no = _page_no_from_image_path(image_path) or ordinal
        print(f"  - Page {page_no} ({ordinal}/{len(image_paths)}): {image_path}")
        page_started_at = time.perf_counter()
        page_errors: list[dict[str, Any]] = []
        page_result: dict[str, Any] = {
            "page_no": page_no,
            "image_path": image_path,
            "screen_engine": "rapidocr_onnxruntime",
            "errors": page_errors,
        }

        # Keep direct-VL output complete and compatible with auto-VL output:
        # every processed page has RapidOCR source text and a box visualization.
        try:
            ocr_result = run_rapid_text_ocr(
                image_path,
                str(rapid_dir),
                page_no,
                det_model_path=det_model_path,
                rec_model_path=rec_model_path,
                rec_keys_path=rec_keys_path,
            )
            page_result["text_summary"] = _save_page_text(ocr_result, text_dir, page_no)
            page_result["screen_ocr_items"] = len(ocr_result.get("items", []))
            page_result["screen_elapsed_seconds"] = ocr_result.get("elapsed_seconds")
            ensure_dir(visual_dir)
            visual_path = visual_dir / page_file_name(page_no, "_ocr_boxes.png")
            draw_ocr_boxes(image_path, ocr_result, str(visual_path))
            page_result["visual_path"] = str(visual_path)
        except Exception as exc:
            _record_error(errors, page_errors, "rapid_text_ocr", exc)
            print(f"    RapidOCR ERROR: {exc}")

        try:
            vl_result = run_vl_ocr(image_path, str(vl_dir), page_no)
            page_result["vl_summary"] = vl_result.get("summary", {})
        except Exception as exc:
            _record_error(errors, page_errors, "vl_ocr", exc)
            print(f"    ERROR: {exc}")
            page_result["vl_summary"] = {}

        page_result["elapsed_seconds"] = round(time.perf_counter() - page_started_at, 3)
        pages.append(page_result)

    summary = _build_summary(pdf_path, pages, errors, dpi, started_at)
    write_json(reports_dir / f"{pdf_name}_vl_summary.json", summary)
    return summary


def _build_summary(
    pdf_path: Path,
    pages: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    dpi: int,
    started_at: float,
) -> dict[str, Any]:
    text_pages = [page["page_no"] for page in pages if page.get("text_summary")]
    html_pages = [
        page["page_no"]
        for page in pages
        if int(page.get("vl_summary", {}).get("html_files", 0)) > 0
    ]
    markdown_pages = [
        page["page_no"]
        for page in pages
        if page.get("vl_summary", {}).get("has_markdown")
    ]
    return {
        "file_name": pdf_path.name,
        "mode": "direct_vl",
        "dpi": dpi,
        "total_pages": len(pages),
        "processed_pages": len([page for page in pages if not page.get("errors")]),
        "text_pages": text_pages,
        "text_page_count": len(text_pages),
        "markdown_pages": markdown_pages,
        "markdown_page_count": len(markdown_pages),
        "html_pages": html_pages,
        "html_page_count": len(html_pages),
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
        write_json(reports_dir / "all_files_vl_summary.json", {"file_count": 0, "files": []})
        return
    if not pdf_files:
        print(f"No PDF files found in {input_dir}")
        write_json(reports_dir / "all_files_vl_summary.json", {"file_count": 0, "files": []})
        return

    summaries = [
        process_pdf_vl(
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
        for pdf_path in pdf_files
    ]
    write_json(
        reports_dir / "all_files_vl_summary.json",
        {
            "file_count": len(summaries),
            "total_pages": sum(summary.get("total_pages", 0) for summary in summaries),
            "text_page_count": sum(summary.get("text_page_count", 0) for summary in summaries),
            "markdown_page_count": sum(summary.get("markdown_page_count", 0) for summary in summaries),
            "html_page_count": sum(summary.get("html_page_count", 0) for summary in summaries),
            "files": summaries,
        },
    )
    print(f"\nDone. VL reports written to {reports_dir}")


if __name__ == "__main__":
    main()
