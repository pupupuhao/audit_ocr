from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _needs_ascii_temp_path(path: str | Path) -> bool:
    if os.name != "nt":
        return False
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError:
        return True
    return False


@contextmanager
def ascii_temp_image_path(image_path: str | Path) -> Iterator[str]:
    """Yield a Windows-safe ASCII path for OCR engines that cannot read Unicode paths."""
    source = Path(image_path)
    if not _needs_ascii_temp_path(source):
        yield str(source)
        return

    suffix = source.suffix or ".png"
    with tempfile.TemporaryDirectory(prefix="audit_ocr_img_") as tmp_dir:
        temp_path = Path(tmp_dir) / f"page{suffix}"
        shutil.copy2(source, temp_path)
        yield str(temp_path)
