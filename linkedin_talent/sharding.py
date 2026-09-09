"""Pure planning and persistence primitives for surname-based search shards."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .criteria import SearchCriteria
from .text import clean_text, unique_texts


def shard_id(surnames: list[str]) -> str:
    return "surname:" + "|".join(item.casefold() for item in surnames)


def reset_start(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "start"
    ]
    query.append(("start", "0"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def load_surnames(path: Path) -> list[str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("姓氏文件必须是 JSON 字符串数组")
    return unique_texts(value)


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_or_create_plan(
    path: Path,
    base_url: str,
    surnames: list[str],
    fresh: bool,
    criteria: SearchCriteria,
) -> dict[str, Any]:
    expected_criteria = criteria.to_plan_dict()
    if path.exists() and not fresh:
        plan = json.loads(path.read_text(encoding="utf-8"))
        if plan.get("criteria") != expected_criteria:
            raise ValueError("已有分片计划使用了不同的关键词、学历或公司配置；请加 --fresh 重建计划")
        existing_url = str(plan.get("base_url", ""))
        if base_url and existing_url and reset_start(base_url) != reset_start(existing_url):
            raise ValueError("当前 --url 与已有分片计划不一致；请继续使用原 URL，或加 --fresh 新建计划")
        return plan
    return {
        "version": 2,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "updated_at": "",
        "criteria": expected_criteria,
        "base_url": reset_start(base_url),
        "pending": [surnames],
        "completed": [],
        "unresolved": [],
    }


def persist_plan(path: Path, plan: dict[str, Any]) -> None:
    plan["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    save_json(path, plan)


def parse_human_count(text: str) -> int | None:
    normalized = clean_text(text).replace(",", "")
    patterns = [
        r"(?:about\s+)?([\d.]+)\s*([KM])?\s*(\+)?\s*(?:results?|candidates?)",
        r"共\s*([\d.]+)\s*([万千])?\s*(\+)?\s*(?:条|位|个)?(?:结果|候选人|人)",
        r"([\d.]+)\s*([万千])?\s*(\+)?\s*(?:条)?结果",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, re.I)
        if not match:
            continue
        number = float(match.group(1))
        unit = (match.group(2) or "").upper()
        multiplier = {"K": 1_000, "M": 1_000_000, "千": 1_000, "万": 10_000}.get(unit, 1)
        value = int(number * multiplier)
        return value + 1 if match.group(3) else value
    return None


def split_group(surnames: list[str]) -> tuple[list[str], list[str]]:
    midpoint = max(1, len(surnames) // 2)
    return surnames[:midpoint], surnames[midpoint:]
