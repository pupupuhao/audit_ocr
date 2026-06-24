from __future__ import annotations

from pathlib import Path

import fitz

from .utils import ensure_dir, page_file_name, pdf_output_name


def pdf_to_images(
    pdf_path: str,
    output_dir: str,
    dpi: int = 300,
    start_page: int | None = None,
    end_page: int | None = None,
) -> list[str]:
    pdf = Path(pdf_path)
    pages_dir = ensure_dir(Path(output_dir) / pdf_output_name(pdf))
    image_paths: list[str] = []
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)

    with fitz.open(pdf) as document:
        total_pages = len(document)
        first_page = max(1, start_page or 1)
        last_page = min(total_pages, end_page or total_pages)
        if first_page > last_page:
            return []

        for index in range(first_page, last_page + 1):
            page = document[index - 1]
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image_path = pages_dir / page_file_name(index, ".png")
            pixmap.save(image_path)
            image_paths.append(str(image_path))

    return image_paths
