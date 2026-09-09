"""Configurable search criteria for the target-biologist workflow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .text import clean_text, unique_texts


@dataclass(frozen=True)
class CompanyCriterion:
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class SearchCriteria:
    keywords: tuple[str, ...]
    degrees: tuple[str, ...]
    companies: tuple[CompanyCriterion, ...]

    @property
    def keyword_expression(self) -> str:
        return "(" + " OR ".join(self.keywords) + ")"

    @property
    def degree_expression(self) -> str:
        return " OR ".join(self.degrees)

    @property
    def recruiter_companies(self) -> tuple[str, ...]:
        """Company entity names to select in Recruiter's Current companies facet."""
        return tuple(alias for company in self.companies for alias in company.aliases)

    def to_plan_dict(self) -> dict[str, Any]:
        return {
            "keywords": self.keyword_expression,
            "keyword_terms": list(self.keywords),
            "degrees": list(self.degrees),
            "degree_logic": "OR",
            "current_companies": [
                {"name": item.name, "aliases": list(item.aliases)}
                for item in self.companies
            ],
            "company_logic": "OR",
        }


def _require_strings(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"检索配置中的 {field} 必须是字符串数组")
    items = unique_texts(str(item) for item in value if isinstance(item, str))
    if len(items) != len(value) or not items:
        raise ValueError(f"检索配置中的 {field} 不能包含空值或非字符串，且至少需要一项")
    return tuple(items)


def load_search_criteria(path: Path) -> SearchCriteria:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"找不到检索配置文件：{path}") from error
    if not isinstance(payload, dict):
        raise ValueError("检索配置必须是 JSON 对象")

    keywords = _require_strings(payload.get("keywords"), "keywords")
    degrees = _require_strings(payload.get("degrees"), "degrees")
    raw_companies = payload.get("companies")
    if not isinstance(raw_companies, list) or not raw_companies:
        raise ValueError("检索配置中的 companies 必须是非空数组")

    companies: list[CompanyCriterion] = []
    for index, item in enumerate(raw_companies, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"companies 第 {index} 项必须是对象")
        name = clean_text(item.get("name", ""))
        aliases = _require_strings(item.get("aliases"), f"companies[{index}].aliases")
        if not name:
            raise ValueError(f"companies 第 {index} 项缺少 name")
        companies.append(CompanyCriterion(name=name, aliases=aliases))
    return SearchCriteria(keywords=keywords, degrees=degrees, companies=tuple(companies))


def normalize_company_name(value: str) -> str:
    value = clean_text(value).casefold()
    value = re.sub(r"\s*[·|]\s*(?:full[- ]?time|全职|正式|合同).*$", "", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def matches_target_company(value: str, criteria: SearchCriteria) -> bool:
    """Return whether a parsed current-company value matches a configured alias."""
    company = normalize_company_name(value)
    if not company:
        return False
    padded = f" {company} "
    for alias in criteria.recruiter_companies:
        normalized_alias = normalize_company_name(alias)
        if normalized_alias and f" {normalized_alias} " in padded:
            return True
    return False
