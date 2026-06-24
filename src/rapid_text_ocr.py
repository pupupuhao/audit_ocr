from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from .path_compat import ascii_temp_image_path
from .utils import ensure_dir, make_json_safe, page_file_name, write_json

_RAPID_OCR_ENGINE: Any | None = None
_RAPID_OCR_ENGINE_KEY: tuple[str | None, str | None, str | None, bool] | None = None
_WINDOWS_DLL_DIR_HANDLES: list[Any] = []


def _configure_windows_nvidia_dlls() -> None:
    """Expose CUDA DLLs installed by pip packages to ONNX Runtime on Windows."""
    if os.name != "nt":
        return

    nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    bin_dirs = [
        nvidia_root / package / "bin"
        for package in ("cuda_runtime", "cublas", "cudnn", "cufft", "curand", "cusolver", "cusparse", "nvjitlink")
    ]
    existing_dirs = [path for path in bin_dirs if path.is_dir()]
    if existing_dirs:
        existing_values = {value.lower() for value in os.environ.get("PATH", "").split(";")}
        additions = [str(path) for path in existing_dirs if str(path).lower() not in existing_values]
        if additions:
            os.environ["PATH"] = ";".join(additions + [os.environ.get("PATH", "")])
        if hasattr(os, "add_dll_directory"):
            _WINDOWS_DLL_DIR_HANDLES.extend(os.add_dll_directory(str(path)) for path in existing_dirs)


def _onnx_cuda_available() -> bool:
    try:
        _configure_windows_nvidia_dlls()
        import onnxruntime as ort

        return "CUDAExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


def _preload_torch_cuda_runtime() -> None:
    """Load PyTorch first so ONNX Runtime does not claim incompatible cuDNN DLLs."""
    try:
        _configure_windows_nvidia_dlls()
        import torch

        if torch.cuda.is_available():
            torch.cuda.init()
    except Exception:
        # PyTorch is optional for CPU-only RapidOCR installations.
        return


def _engine_providers(engine: Any) -> dict[str, list[str]]:
    modules = {
        "det": getattr(engine, "text_det", None),
        "cls": getattr(engine, "text_cls", None),
        "rec": getattr(engine, "text_rec", None),
    }
    providers: dict[str, list[str]] = {}
    for name, module in modules.items():
        session = getattr(getattr(module, "infer", None), "session", None)
        if session is not None:
            providers[name] = list(session.get_providers())
    return providers


def _get_rapid_ocr_engine(
    det_model_path: str | None = None,
    rec_model_path: str | None = None,
    rec_keys_path: str | None = None,
) -> Any:
    global _RAPID_OCR_ENGINE, _RAPID_OCR_ENGINE_KEY
    use_cuda = _onnx_cuda_available()
    engine_key = (det_model_path, rec_model_path, rec_keys_path, use_cuda)
    if _RAPID_OCR_ENGINE is None or _RAPID_OCR_ENGINE_KEY != engine_key:
        from rapidocr_onnxruntime import RapidOCR

        if use_cuda:
            _preload_torch_cuda_runtime()
        kwargs = {}
        if det_model_path:
            kwargs["det_model_path"] = det_model_path
        if rec_model_path:
            kwargs["rec_model_path"] = rec_model_path
        if rec_keys_path:
            kwargs["rec_keys_path"] = rec_keys_path
        # RapidOCR maps these flags to ONNX Runtime's CUDAExecutionProvider.
        kwargs["det_use_cuda"] = use_cuda
        kwargs["cls_use_cuda"] = use_cuda
        kwargs["rec_use_cuda"] = use_cuda
        _RAPID_OCR_ENGINE = RapidOCR(**kwargs)
        _RAPID_OCR_ENGINE_KEY = engine_key
    return _RAPID_OCR_ENGINE


def _normalize_rapid_result(raw_result: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not raw_result:
        return items

    for row in raw_result:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        bbox, text, score = row[0], row[1], row[2]
        try:
            score_value = float(score)
        except (TypeError, ValueError):
            score_value = 0.0
        items.append(
            {
                "text": str(text),
                "score": score_value,
                "bbox": bbox,
            }
        )
    return items


def run_rapid_text_ocr(
    image_path: str,
    output_dir: str,
    page_no: int,
    det_model_path: str | None = None,
    rec_model_path: str | None = None,
    rec_keys_path: str | None = None,
) -> dict[str, Any]:
    output = ensure_dir(output_dir)
    engine = _get_rapid_ocr_engine(
        det_model_path=det_model_path,
        rec_model_path=rec_model_path,
        rec_keys_path=rec_keys_path,
    )
    started_at = time.perf_counter()
    with ascii_temp_image_path(image_path) as engine_image_path:
        raw_result, rapid_elapse = engine(engine_image_path)
    wall_seconds = round(time.perf_counter() - started_at, 4)

    result = {
        "image_path": str(Path(image_path)),
        "page_no": page_no,
        "engine": "rapidocr_onnxruntime",
        "det_model_path": det_model_path,
        "rec_model_path": rec_model_path,
        "rec_keys_path": rec_keys_path,
        "execution_providers": _engine_providers(engine),
        "elapsed_seconds": wall_seconds,
        "rapid_elapse": rapid_elapse,
        "items": _normalize_rapid_result(raw_result),
        "raw_result": make_json_safe(raw_result),
    }

    json_path = output / page_file_name(page_no, "_rapid_ocr.json")
    txt_path = output / page_file_name(page_no, "_rapid_ocr.txt")
    write_json(json_path, result)

    lines = [f"{item['score']:.4f}\t{item['text']}" for item in result["items"]]
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    return result
