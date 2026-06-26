from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import sys
from typing import Any

from .path_compat import ascii_temp_image_path
from .utils import ensure_dir, make_json_safe, page_file_name, write_json

_VL_ENGINE: Any | None = None
_WINDOWS_DLL_DIR_HANDLES: list[Any] = []


def _env_bool(name: str, default: bool | None = None) -> bool | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return int(value)


def _configure_windows_nvidia_dlls() -> None:
    """Expose pip-installed CUDA DLLs before PaddleX imports PyTorch."""
    if os.name != "nt":
        return

    nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    bin_dirs = [
        nvidia_root / package / "bin"
        for package in ("cuda_runtime", "cublas", "cudnn", "cufft", "curand", "cusolver", "cusparse", "nvjitlink")
    ]
    existing_dirs = [path for path in bin_dirs if path.is_dir()]
    if not existing_dirs:
        return

    path_values = {value.lower() for value in os.environ.get("PATH", "").split(";")}
    additions = [str(path) for path in existing_dirs if str(path).lower() not in path_values]
    if additions:
        os.environ["PATH"] = ";".join(additions + [os.environ.get("PATH", "")])
    if hasattr(os, "add_dll_directory"):
        _WINDOWS_DLL_DIR_HANDLES.extend(os.add_dll_directory(str(path)) for path in existing_dirs)


def _get_vl_engine() -> Any:
    global _VL_ENGINE
    if _VL_ENGINE is None:
        _configure_windows_nvidia_dlls()
        from paddleocr import PaddleOCRVL

        kwargs: dict[str, Any] = {
            "pipeline_version": os.getenv("AUDIT_OCR_VL_PIPELINE_VERSION", "v1.5"),
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_layout_detection": _env_bool("AUDIT_OCR_VL_USE_LAYOUT_DETECTION", True),
            "use_chart_recognition": False,
            "use_seal_recognition": False,
            "use_ocr_for_image_block": False,
            "format_block_content": True,
        }
        engine = os.getenv("AUDIT_OCR_VL_ENGINE")
        if engine:
            kwargs["engine"] = engine
        device = os.getenv("AUDIT_OCR_VL_DEVICE")
        if device:
            kwargs["device"] = device
        use_queues = _env_bool("AUDIT_OCR_VL_USE_QUEUES")
        if use_queues is not None:
            kwargs["use_queues"] = use_queues
        _VL_ENGINE = PaddleOCRVL(**kwargs)
    return _VL_ENGINE


def _value_from_result_property(result: Any, property_name: str) -> Any:
    try:
        value = getattr(result, property_name)
    except Exception:
        return None
    if isinstance(value, dict) and "res" in value:
        return value["res"]
    return value


def _extract_raw_json(result: Any) -> dict[str, Any]:
    raw_json = _value_from_result_property(result, "json")
    if isinstance(raw_json, dict):
        return raw_json
    return make_json_safe(result)


def _extract_markdown_text(result: Any) -> str:
    markdown = _value_from_result_property(result, "markdown")
    if isinstance(markdown, dict):
        return str(markdown.get("markdown_texts") or markdown.get("text") or "")
    if isinstance(markdown, str):
        return markdown
    return ""


def _extract_html_values(result: Any, raw_json: dict[str, Any]) -> list[str]:
    html_values: list[str] = []
    html_data = _value_from_result_property(result, "html")
    if isinstance(html_data, dict):
        for value in html_data.values():
            if isinstance(value, str) and value.strip():
                html_values.append(value)
    elif isinstance(html_data, str) and html_data.strip():
        html_values.append(html_data)

    for block in raw_json.get("parsing_res_list", []):
        if not isinstance(block, dict):
            continue
        content = block.get("block_content")
        if isinstance(content, str) and "<table" in content.lower():
            html_values.append(content)

    deduped: list[str] = []
    seen: set[str] = set()
    for html in html_values:
        if html not in seen:
            seen.add(html)
            deduped.append(html)
    return deduped


def _summarize_vl_json(raw_json: dict[str, Any]) -> dict[str, Any]:
    blocks = raw_json.get("parsing_res_list", [])
    labels = Counter()
    table_blocks = 0
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        label = str(block.get("block_label") or "")
        labels[label] += 1
        content = str(block.get("block_content") or "")
        if label.lower() == "table" or "<table" in content.lower():
            table_blocks += 1
    return {
        "block_count": len(blocks) if isinstance(blocks, list) else 0,
        "table_blocks": table_blocks,
        "label_counts": dict(labels),
    }


def run_vl_ocr(image_path: str, output_dir: str, page_no: int) -> dict:
    output = ensure_dir(output_dir)
    engine = _get_vl_engine()
    with ascii_temp_image_path(image_path) as engine_image_path:
        predict_kwargs: dict[str, Any] = {}
        prompt_label = os.getenv("AUDIT_OCR_VL_PROMPT_LABEL")
        if prompt_label:
            predict_kwargs["prompt_label"] = prompt_label
        max_new_tokens = _env_int("AUDIT_OCR_VL_MAX_NEW_TOKENS")
        if max_new_tokens is not None:
            predict_kwargs["max_new_tokens"] = max_new_tokens
        min_pixels = _env_int("AUDIT_OCR_VL_MIN_PIXELS")
        if min_pixels is not None:
            predict_kwargs["min_pixels"] = min_pixels
        max_pixels = _env_int("AUDIT_OCR_VL_MAX_PIXELS")
        if max_pixels is not None:
            predict_kwargs["max_pixels"] = max_pixels
        raw_results = list(engine.predict(input=engine_image_path, **predict_kwargs))
    page_payloads: list[dict[str, Any]] = []
    markdown_parts: list[str] = []
    html_count = 0

    for result_index, result in enumerate(raw_results, start=1):
        raw_json = make_json_safe(_extract_raw_json(result))
        markdown_text = _extract_markdown_text(result)
        html_values = _extract_html_values(result, raw_json)
        summary = _summarize_vl_json(raw_json)

        page_payloads.append(
            {
                "result_index": result_index,
                "summary": summary,
                "raw_json": raw_json,
            }
        )
        if markdown_text.strip():
            markdown_parts.append(markdown_text)
        for html in html_values:
            html_count += 1
            if html_count == 1:
                html_path = output / page_file_name(page_no, "_vl.html")
            else:
                html_path = output / page_file_name(page_no, f"_vl_{html_count:02d}.html")
            html_path.write_text(html, encoding="utf-8")

    combined_markdown = "\n\n".join(markdown_parts).strip()
    if combined_markdown:
        (output / page_file_name(page_no, "_vl.md")).write_text(combined_markdown, encoding="utf-8")

    page_summary = {
        "block_count": sum(item["summary"]["block_count"] for item in page_payloads),
        "table_blocks": sum(item["summary"]["table_blocks"] for item in page_payloads),
        "label_counts": dict(
            sum((Counter(item["summary"]["label_counts"]) for item in page_payloads), Counter())
        ),
        "has_markdown": bool(combined_markdown),
        "html_files": html_count,
        "generated_preview_html": False,
    }
    payload = {
        "image_path": str(Path(image_path)),
        "page_no": page_no,
        "mode": "paddleocr_vl",
        "summary": page_summary,
        "results": page_payloads,
    }
    write_json(output / page_file_name(page_no, "_vl.json"), payload)
    return make_json_safe(payload)
