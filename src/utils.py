from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BULKY_IMAGE_KEYS = {
    "input_img",
    "output_img",
    "ori_img",
    "rot_img",
    "unwarped_img",
    "doc_preprocessor_img",
    "image",
    "img",
}


def ensure_dir(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_json(path: str | Path, data: Any) -> None:
    target = Path(path)
    ensure_dir(target.parent)
    target.write_text(
        json.dumps(make_json_safe(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_probably_embedded_image(value: Any) -> bool:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return False
    if isinstance(value, (list, tuple)):
        return True
    if hasattr(value, "shape") or hasattr(value, "size"):
        return True
    return False


def make_json_safe(obj: Any) -> Any:
    try:
        import numpy as np
    except Exception:
        np = None

    if np is not None:
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)

    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        safe_dict = {}
        for key, value in obj.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if (key_lower in BULKY_IMAGE_KEYS or key_lower.endswith("_img")) and _is_probably_embedded_image(value):
                safe_dict[key_text] = "<omitted embedded image array; see output/pages image file>"
            else:
                safe_dict[key_text] = make_json_safe(value)
        return safe_dict
    if isinstance(obj, (list, tuple)):
        return [make_json_safe(value) for value in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def page_file_name(page_no: int, suffix: str) -> str:
    return f"page_{page_no:03d}{suffix}"


def pdf_output_name(pdf_path: str | Path) -> str:
    return Path(pdf_path).stem
