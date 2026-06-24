from __future__ import annotations

import re
from typing import Any


DEFAULT_FRONT_PAGE_LIMIT = 10


def _clean_text(value: str) -> str:
    value = (value or "").replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _clean_extracted_value(value: str) -> str:
    value = _clean_text(value)
    value = re.sub(r"^[\s_＿—\-－~～·•]+", "", value)
    value = re.sub(r"[\s_＿—\-－~～·•]+$", "", value)
    return value.strip()


def _page_text(page: dict[str, Any]) -> str:
    text = str(page.get("text") or "")
    if text.strip():
        return _clean_text(text)
    lines = page.get("lines")
    if isinstance(lines, list):
        return _clean_text("\n".join(str(line) for line in lines))
    return ""


def _front_pages(pages: list[dict[str, Any]], max_pages: int) -> list[dict[str, Any]]:
    sorted_pages = sorted(pages, key=lambda item: int(item.get("page_no") or 0))
    return sorted_pages[:max_pages]


def _non_empty_lines(text: str) -> list[str]:
    return [_clean_text(line) for line in text.splitlines() if _clean_text(line)]


def _looks_like_new_section_or_label(value: str) -> bool:
    compact = re.sub(r"\s+", "", value or "")
    if not compact:
        return False
    return bool(
        re.match(r"^[一二三四五六七八九十][、.．]", compact)
        or re.match(r"^[（(][一二三四五六七八九十]+[）)]", compact)
        or re.match(
            r"^(委托单位|建设单位|施工单位|咨询人|审核人|编制人|日期|目录|工程名称|咨询业务类别|咨询报告日期)[：:]?",
            compact,
        )
    )


def _looks_like_document_type_title(value: str) -> bool:
    compact = re.sub(r"\s+", "", value or "")
    if not compact:
        return False
    titles = {
        "结算审核",
        "结算审价",
        "结算报告",
        "审核报告",
        "咨询报告",
        "工程造价咨询报告书",
        "工程造价咨询报告",
        "工程结算审核报告书",
        "工程结算审核报告",
    }
    if compact in titles:
        return True
    return bool(re.fullmatch(r".{0,12}(?:报告书|报告|审核|审价|咨询)$", compact) and "工程" not in compact)


def _is_consultation_project_value(value: str) -> bool:
    value = _clean_extracted_value(value)
    compact = re.sub(r"\s+", "", value or "")
    if not compact:
        return False
    if _looks_like_document_type_title(compact):
        return False
    if _looks_like_new_section_or_label(compact):
        return False
    if re.search(r"(咨询(?:项目|项)(?:全程|全称)|咨询业务类别|咨询报告日期)", compact):
        return False
    if re.search(r"(工程造价咨询报告书|工程造价咨询报告|结算审核|结算审价|咨询业务类别)", compact):
        return False
    return True


def _consultation_project_value_needs_continuation(value: str) -> bool:
    compact = re.sub(r"\s+", "", _clean_extracted_value(value))
    if not compact:
        return True
    return compact.endswith(("-", "—", "–", "、", "，", ",", "（", "(", "及", "及其", "和", "与", "同"))


def _join_project_pieces(pieces: list[str]) -> str:
    result: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        value = _clean_extracted_value(piece)
        compact = re.sub(r"\s+", "", value)
        if not compact or compact in seen:
            continue
        seen.add(compact)
        result.append(value)
    return _clean_extracted_value("".join(result))


def _consultation_project_candidate_lines(
    lines: list[str],
    label_index: int,
    same_line_value: str,
) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []

    for offset in range(2, 0, -1):
        line_index = label_index - offset
        if line_index < 0:
            continue
        line = lines[line_index]
        if _is_consultation_project_value(line):
            candidates.append((line_index, line))

    if _is_consultation_project_value(same_line_value):
        candidates.append((label_index, same_line_value))

    for line_index in range(label_index + 1, min(len(lines), label_index + 5)):
        line = lines[line_index]
        if _looks_like_new_section_or_label(line):
            break
        if _is_consultation_project_value(line):
            candidates.append((line_index, line))

    return candidates


def _select_consultation_project_pieces(candidates: list[tuple[int, str]], label_index: int) -> list[str]:
    if not candidates:
        return []

    same_or_after = [(idx, text) for idx, text in candidates if idx >= label_index]
    before = [(idx, text) for idx, text in candidates if idx < label_index]

    if same_or_after:
        selected = same_or_after
        if before and _consultation_project_value_needs_continuation(before[-1][1]):
            selected = [before[-1], *selected]
    else:
        selected = before[-1:]

    selected.sort(key=lambda item: item[0])
    return [text for _, text in selected]


def _extract_consultation_project_name(pages: list[dict[str, Any]]) -> dict[str, Any]:
    # OCR may drop "目" in "咨询项目全称", producing "咨询项全称".
    pattern = re.compile(r"咨询(?:项目|项)(?:全程|全称)\s*[：:]\s*(.*)")
    for page in pages:
        text = _page_text(page)
        lines = _non_empty_lines(text)
        for index, line in enumerate(lines):
            match = pattern.search(line)
            if not match:
                continue
            same_line_value = _clean_extracted_value(match.group(1))
            candidates = _consultation_project_candidate_lines(lines, index, same_line_value)
            pieces = _select_consultation_project_pieces(candidates, index)

            value = _join_project_pieces(pieces)
            return {
                "content": value,
                "page_no": int(page.get("page_no") or 0),
                "keyword": "咨询项目/咨询项全称/全程",
            }
    return {"content": "", "page_no": 0, "keyword": "咨询项目全称/全程"}


def _extract_renovation_content(pages: list[dict[str, Any]]) -> dict[str, Any]:
    section_heading = re.compile(r"(?:^|\n)\s*一[、.．]\s*工程概况")
    content_heading = re.compile(r"(?:^|\n)\s*[（(]\s*一\s*[）)]\s*改造内容\s*[：:]?\s*")
    stop_heading = re.compile(
        r"(?:^|\n)\s*(?:[（(]\s*[二三四五六七八九十]+\s*[）)]|[二三四五六七八九十][、.．])"
    )

    for page in pages:
        text = _page_text(page)
        if "改造内容" not in text:
            continue
        search_text = text
        section_match = section_heading.search(text)
        if section_match:
            search_text = text[section_match.start():]

        content_match = content_heading.search(search_text)
        if not content_match:
            continue

        content = search_text[content_match.end():]
        stop_match = stop_heading.search(content)
        if stop_match:
            content = content[:stop_match.start()]
        content = _clean_extracted_value(content)
        if not content:
            lines_after_heading = _non_empty_lines(search_text[content_match.end():])
            for line in lines_after_heading:
                if _looks_like_new_section_or_label(line):
                    break
                content = _clean_extracted_value(line)
                if content:
                    break
        if content:
            return {
                "content": content,
                "page_no": int(page.get("page_no") or 0),
                "keyword": "一、工程概况/（一）改造内容",
            }

    return {"content": "", "page_no": 0, "keyword": "一、工程概况/（一）改造内容"}


def extract_document_info(
    pages: list[dict[str, Any]],
    front_page_limit: int = DEFAULT_FRONT_PAGE_LIMIT,
) -> dict[str, Any]:
    front = _front_pages(pages, front_page_limit)
    consultation_project = _extract_consultation_project_name(front)
    renovation_content = _extract_renovation_content(front)
    return {
        "consultation_project_name": consultation_project.get("content", ""),
        "renovation_content": renovation_content.get("content", ""),
        "consultation_project_full_name": consultation_project,
        "project_overview_renovation_content": renovation_content,
        "front_page_limit": front_page_limit,
    }
