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
    page_numbers: list[int] | None = None,
) -> list[str]:
    pdf = Path(pdf_path)
    pages_dir = ensure_dir(Path(output_dir) / pdf_output_name(pdf))
    image_paths: list[str] = []
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)

    with fitz.open(pdf) as document:
        total_pages = len(document)
        if page_numbers:
            selected_pages = sorted({page_no for page_no in page_numbers if 1 <= page_no <= total_pages})
            if start_page is not None:
                selected_pages = [page_no for page_no in selected_pages if page_no >= start_page]
            if end_page is not None:
                selected_pages = [page_no for page_no in selected_pages if page_no <= end_page]
        else:
            first_page = max(1, start_page or 1)
            last_page = min(total_pages, end_page or total_pages)
            if first_page > last_page:
                return []
            selected_pages = list(range(first_page, last_page + 1))

        for index in selected_pages:
            page = document[index - 1]
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image_path = pages_dir / page_file_name(index, ".png")
            pixmap.save(image_path)
            image_paths.append(str(image_path))

    return image_paths
