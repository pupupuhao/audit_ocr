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


def _consultation_project_value_starts_continuation(value: str) -> bool:
    compact = re.sub(r"\s+", "", _clean_extracted_value(value))
    if not compact:
        return False
    return compact.startswith(("及", "及其", "和", "与", "、", "，", ",", "-", "—", "–"))


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


def _previous_consultation_project_candidates(
    lines: list[str],
    label_index: int,
) -> list[str]:
    candidates: list[tuple[int, str]] = []
    for offset in range(2, 0, -1):
        line_index = label_index - offset
        if line_index < 0:
            continue
        line = lines[line_index]
        if _is_consultation_project_value(line):
            candidates.append((line_index, line))
    candidates.sort(key=lambda item: item[0])
    return [text for _, text in candidates]


def _consultation_project_field_block_pieces(
    lines: list[str],
    label_index: int,
    same_line_value: str,
    lookahead: int = 8,
) -> list[str]:
    pieces: list[str] = []
    if _is_consultation_project_value(same_line_value):
        pieces.append(same_line_value)

    for line_index in range(label_index + 1, min(len(lines), label_index + 1 + lookahead)):
        line = lines[line_index]
        if _looks_like_new_section_or_label(line):
            break
        if _is_consultation_project_value(line):
            pieces.append(line)

    return pieces


def _consultation_project_value_looks_incomplete(value: str) -> bool:
    compact = re.sub(r"\s+", "", _clean_extracted_value(value))
    if not compact:
        return True
    if _consultation_project_value_starts_continuation(compact):
        return True
    return "工程" in compact and len(compact) <= 8


def _select_consultation_project_pieces(
    lines: list[str],
    label_index: int,
    same_line_value: str,
) -> list[str]:
    pieces = _consultation_project_field_block_pieces(lines, label_index, same_line_value)
    value = _join_project_pieces(pieces)
    if not _consultation_project_value_looks_incomplete(value):
        return pieces

    previous = _previous_consultation_project_candidates(lines, label_index)
    if previous:
        return [previous[-1], *pieces]
    return pieces


def _extract_consultation_project_name(pages: list[dict[str, Any]]) -> dict[str, Any]:
    # OCR may drop "目" in "咨询项目全称", producing "咨询项全称".
    pattern = re.compile(r"咨询(?:项目|项)(?:全程|全称)\s*[：:]?\s*(.*)")
    for page in pages:
        text = _page_text(page)
        lines = _non_empty_lines(text)
        for index, line in enumerate(lines):
            match = pattern.search(line)
            if not match:
                continue
            same_line_value = _clean_extracted_value(match.group(1))
            pieces = _select_consultation_project_pieces(lines, index, same_line_value)

            value = _join_project_pieces(pieces)
            return {
                "content": value,
                "page_no": int(page.get("page_no") or 0),
                "keyword": "咨询项目/咨询项全称/全程",
                "source": "field_block",
            }
    return {"content": "", "page_no": 0, "keyword": "咨询项目/咨询项全称/全程", "source": "field_block"}


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "○": 0,
    "O": 0,
    "o": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _chinese_number_to_int(value: str) -> int | None:
    text = re.sub(r"\s+", "", value or "")
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if text.startswith("十"):
        tail = _CHINESE_DIGITS.get(text[1:]) if len(text) == 2 else None
        return 10 + (tail or 0) if len(text) <= 2 else None
    if "十" in text:
        head, tail = text.split("十", 1)
        head_value = _CHINESE_DIGITS.get(head)
        tail_value = _CHINESE_DIGITS.get(tail) if tail else 0
        if head_value is None or tail_value is None:
            return None
        return head_value * 10 + tail_value
    if len(text) == 1:
        return _CHINESE_DIGITS.get(text)
    digits = []
    for char in text:
        digit = _CHINESE_DIGITS.get(char)
        if digit is None:
            return None
        digits.append(str(digit))
    return int("".join(digits)) if digits else None


def _normalize_report_date(value: str) -> str:
    compact = re.sub(r"\s+", "", _clean_extracted_value(value))
    if not compact:
        return ""

    arabic = re.search(r"((?:19|20)\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?", compact)
    if arabic:
        year, month, day = (int(arabic.group(1)), int(arabic.group(2)), int(arabic.group(3)))
        return f"{year:04d}-{month:02d}-{day:02d}"

    chinese = re.search(
        r"([零〇○Oo一二三四五六七八九]{4})年([十零〇○Oo一二两三四五六七八九]{1,3})月([十零〇○Oo一二两三四五六七八九]{1,3})日",
        compact,
    )
    if not chinese:
        return ""
    year_digits = []
    for char in chinese.group(1):
        digit = _CHINESE_DIGITS.get(char)
        if digit is None:
            return ""
        year_digits.append(str(digit))
    month = _chinese_number_to_int(chinese.group(2))
    day = _chinese_number_to_int(chinese.group(3))
    if not month or not day:
        return ""
    return f"{int(''.join(year_digits)):04d}-{month:02d}-{day:02d}"


def _is_report_date_value(value: str) -> bool:
    compact = re.sub(r"\s+", "", _clean_extracted_value(value))
    if not compact:
        return False
    if re.search(r"(咨询(?:项目|项)(?:全程|全称)|咨询业务类别|工程名称)", compact):
        return False
    return bool(_normalize_report_date(compact) or re.search(r"(?:19|20)\d{2}年\d{1,2}月\d{1,2}日?", compact))


def _extract_report_date(pages: list[dict[str, Any]]) -> dict[str, Any]:
    pattern = re.compile(r"咨询报告日期\s*[：:]?\s*(.*)")
    for page in pages:
        text = _page_text(page)
        lines = _non_empty_lines(text)
        for index, line in enumerate(lines):
            match = pattern.search(line)
            if not match:
                continue
            candidates = [_clean_extracted_value(match.group(1))]
            for next_line in lines[index + 1:index + 4]:
                if _looks_like_new_section_or_label(next_line):
                    break
                candidates.append(_clean_extracted_value(next_line))
            for prev_line in reversed(lines[max(0, index - 3):index]):
                if _is_report_date_value(prev_line):
                    candidates.append(_clean_extracted_value(prev_line))
            for candidate in candidates:
                if not _is_report_date_value(candidate):
                    continue
                content = re.sub(r"\s+", "", _clean_extracted_value(candidate))
                return {
                    "content": content,
                    "consultation_date": _normalize_report_date(content),
                    "page_no": int(page.get("page_no") or 0),
                    "keyword": "咨询报告日期",
                }
    return {"content": "", "consultation_date": "", "page_no": 0, "keyword": "咨询报告日期"}


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
    report_date = _extract_report_date(front)
    renovation_content = _extract_renovation_content(front)
    return {
        "consultation_project_name": consultation_project.get("content", ""),
        "consultation_report_date": report_date.get("content", ""),
        "consultation_date": report_date.get("consultation_date", ""),
        "renovation_content": renovation_content.get("content", ""),
        "consultation_project_full_name": consultation_project,
        "consultation_report_date_info": report_date,
        "project_overview_renovation_content": renovation_content,
        "front_page_limit": front_page_limit,
    }
