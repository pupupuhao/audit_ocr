from __future__ import annotations

import re
from typing import Any

TABLE_KEYWORDS = [
    "序号",
    "项目编码",
    "项目名称",
    "单位工程",
    "费用名称",
    "计算公式",
    "计量",
    "单位",
    "工程量",
    "综合单价",
    "合价",
    "金额",
    "人工费",
    "机械费",
    "暂估价",
    "合同价",
    "送审",
    "审定",
    "核减",
    "核增",
    "备注",
    "合计",
]

STRONG_TABLE_KEYWORDS = [
    "文件内容",
    "单位工程名称",
    "费用名称",
    "计算公式",
    "项目编码",
    "项目名称",
    "综合单价",
    "现场踏勘记录表",
    "记录表",
]

TARGET_TABLE_WHITELIST = [
    "分部分项工程清单与计价表",
]

TARGET_TABLE_BLACKLIST = [
    # These tables are intentionally kept as RapidOCR/page_text output only;
    # they are not part of the current VL extraction scope.
    "单位（专业）工程招标控制价费用表",
    "单位（专业）工程费用表",
    "专业工程费用表",
    "专业费用表",
    "主要工日一览表",
    "主要材料和工程设备一览表",
    "主要机械台班一览表",
    "主要施工机械台班一览表",
    "工程量确认单",
    "施工工艺",
    "咨询报告书目录",
    "工程造价审定单",
    "工程结算审核造价汇总表",
    "现场踏勘记录表",
    "工程现场勘察签到单",
]

NUMBER_RE = re.compile(r"^[￥¥]?\s*[-+]?\d[\d,]*(?:\.\d+)?%?$")


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


def _bucket_count(values: list[float], bucket_size: int) -> int:
    if not values:
        return 0
    return len({int(value // bucket_size) for value in values})


def _compact_text(value: str) -> str:
    value = value.replace("（", "(").replace("）", ")")
    return re.sub(r"[\s:：,，、.。;；\-—_/\\|()\[\]【】]+", "", value)


def _joined_ocr_text(ocr_result: dict[str, Any] | None) -> str:
    if not isinstance(ocr_result, dict):
        return ""
    items = [item for item in ocr_result.get("items", []) if isinstance(item, dict)]
    texts = [str(item.get("text", "")).strip() for item in items if str(item.get("text", "")).strip()]
    return "\n".join(texts)


def _keyword_hits(compact_text: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if _compact_text(keyword) in compact_text]


def _target_pattern_hits(compact_text: str) -> list[str]:
    checks = [
        ("分部分项工程清单与计价表", ["分部分项", "清单", "计价"]),
    ]
    return [name for name, parts in checks if all(part in compact_text for part in parts)]


def _skip_pattern_hits(compact_text: str) -> list[str]:
    """Recognize excluded table titles even if OCR loses a little punctuation."""
    checks = [
        ("单位（专业）工程招标控制价费用表", ["招标控制价", "费用"]),
        ("单位（专业）工程费用表", ["单位", "专业", "工程费用表"]),
        ("施工工艺", ["施工工艺"]),
        ("专业工程费用表", ["专业工程", "费用表"]),
        ("专业费用表", ["专业", "费用表"]),
        ("主要工日一览表", ["主要", "工日", "一览表"]),
        ("主要材料和工程设备一览表", ["主要", "材料", "工程设备", "一览表"]),
        ("主要机械台班一览表", ["主要", "机械", "台班", "一览表"]),
        ("工程量确认单", ["工程量", "确认单"]),
    ]
    return [name for name, parts in checks if all(_compact_text(part) in compact_text for part in parts)]


def _target_structure_hits(compact_text: str) -> list[str]:
    checks = [
        ("sub_item_project_columns", ["项目编码", "项目名称", "综合单价"]),
        ("sub_item_project_columns", ["项目编码", "项目名称", "合价"]),
        ("sub_item_project_long_text_columns", ["项目名称", "项目特征", "工程量"]),
    ]
    hits = []
    for name, parts in checks:
        if all(_compact_text(part) in compact_text for part in parts):
            hits.append(name)
    return sorted(set(hits))


def detect_target_table(ocr_result: dict[str, Any] | None) -> dict[str, Any]:
    joined_text = _joined_ocr_text(ocr_result)
    compact = _compact_text(joined_text)
    whitelist_hits = sorted(set(_keyword_hits(compact, TARGET_TABLE_WHITELIST) + _target_pattern_hits(compact)))
    structure_hits = _target_structure_hits(compact)
    blacklist_hits = sorted(set(_keyword_hits(compact, TARGET_TABLE_BLACKLIST) + _skip_pattern_hits(compact)))

    # Excluded table types win over generic table structures. This prevents a
    # resource/confirmation/cost page from reaching VL merely because it has
    # columns and numbers like a target table.
    if blacklist_hits:
        return {
            "should_run_paddleocr": False,
            "reason": f"blacklist_hits={','.join(blacklist_hits)}",
            "whitelist_hits": whitelist_hits,
            "structure_hits": structure_hits,
            "blacklist_hits": blacklist_hits,
            "policy": "skip_blacklist_priority",
        }

    if whitelist_hits:
        return {
            "should_run_paddleocr": True,
            "reason": f"whitelist_hits={','.join(whitelist_hits)}",
            "whitelist_hits": whitelist_hits,
            "structure_hits": structure_hits,
            "blacklist_hits": blacklist_hits,
            "policy": "whitelist_priority",
        }

    if structure_hits:
        return {
            "should_run_paddleocr": True,
            "reason": f"structure_hits={','.join(structure_hits)}",
            "whitelist_hits": whitelist_hits,
            "structure_hits": structure_hits,
            "blacklist_hits": blacklist_hits,
            "policy": "target_structure_priority",
        }

    return {
        "should_run_paddleocr": False,
        "reason": "no_target_table_header",
        "whitelist_hits": whitelist_hits,
        "structure_hits": structure_hits,
        "blacklist_hits": blacklist_hits,
        "policy": "skip_non_target_table",
    }


def detect_table_page(ocr_result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ocr_result, dict):
        return {
            "is_table_like": True,
            "reason": "no_ocr_result_fallback_to_structure",
            "score": 0,
        }

    items = [item for item in ocr_result.get("items", []) if isinstance(item, dict)]
    texts = [str(item.get("text", "")).strip() for item in items if str(item.get("text", "")).strip()]
    joined_text = "\n".join(texts)
    compact = _compact_text(joined_text)
    keyword_hits = sorted({keyword for keyword in TABLE_KEYWORDS if keyword in joined_text})
    strong_keyword_hits = sorted({keyword for keyword in STRONG_TABLE_KEYWORDS if keyword in joined_text})
    target_structure_hits = _target_structure_hits(compact)
    numeric_count = len([text for text in texts if NUMBER_RE.match(text.replace(" ", ""))])
    short_text_count = len([text for text in texts if len(text) <= 8])
    short_text_ratio = short_text_count / len(texts) if texts else 0.0

    centers = [_bbox_center(item) for item in items]
    centers = [center for center in centers if center is not None]
    x_count = _bucket_count([center[0] for center in centers], 90)
    y_count = _bucket_count([center[1] for center in centers], 32)

    score = 0
    if len(keyword_hits) >= 2:
        score += 3
    elif keyword_hits:
        score += 1
    if numeric_count >= 8:
        score += 2
    elif numeric_count >= 4:
        score += 1
    if len(items) >= 25 and x_count >= 4 and y_count >= 6:
        score += 2
    elif len(items) >= 15 and x_count >= 3 and y_count >= 4:
        score += 1
    if any(keyword in joined_text for keyword in ["项目编码", "综合单价", "计算公式", "单位工程名称"]):
        score += 2
    if target_structure_hits:
        score += 3

    is_dense_cell_text = short_text_count >= 12 and short_text_ratio >= 0.5
    is_long_text_target_table = bool(target_structure_hits) and len(items) >= 8
    is_table_like = (
        is_long_text_target_table
        or
        (bool(strong_keyword_hits) and is_dense_cell_text)
        or (numeric_count >= 6 and is_dense_cell_text)
        or (score >= 7 and short_text_ratio >= 0.45)
    )

    reasons = []
    if keyword_hits:
        reasons.append(f"keyword_hits={','.join(keyword_hits)}")
    if strong_keyword_hits:
        reasons.append(f"strong_keyword_hits={','.join(strong_keyword_hits)}")
    if target_structure_hits:
        reasons.append(f"target_structure_hits={','.join(target_structure_hits)}")
    if numeric_count:
        reasons.append(f"numeric_count={numeric_count}")
    reasons.append(f"short_text={short_text_count}/{len(texts)}")
    if centers:
        reasons.append(f"layout_buckets=x{x_count}/y{y_count}")
    reasons.append(f"ocr_items={len(items)}")

    return {
        "is_table_like": is_table_like,
        "reason": "; ".join(reasons),
        "score": score,
        "keyword_hits": keyword_hits,
        "strong_keyword_hits": strong_keyword_hits,
        "target_structure_hits": target_structure_hits,
        "numeric_count": numeric_count,
        "short_text_count": short_text_count,
        "short_text_ratio": round(short_text_ratio, 4),
        "ocr_items": len(items),
        "x_bucket_count": x_count,
        "y_bucket_count": y_count,
    }
