"""Pure parsers for Recruiter card and profile data."""

from __future__ import annotations

import re

from .constants import CHINESE_LANGUAGE_RE, NATIVE_LEVEL_RE
from .models import Language
from .text import clean_text, normalize_url


def parse_card_text(text: str) -> dict[str, str]:
    """Parse the bilingual Recruiter result-card format."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return {
            "name": "", "headline": "", "current_title": "",
            "company": "", "location": "", "experience": "", "education": "",
        }

    def is_exp(line: str) -> bool:
        return line == "工作经历" or bool(
            re.match(r"^(?:Profile\s+)?(?:Work\s+)?(?:Experience|Employment)$", line, re.I)
        )

    def is_edu(line: str) -> bool:
        return line == "教育经历" or bool(re.match(r"^(?:Profile\s+)?Education$", line, re.I))

    def is_skills(line: str) -> bool:
        return line in {"技能", "技能匹配"} or bool(
            re.match(r"^(?:Profile\s+)?Skills?(?:\s+Match)?(?:\s*\(\s*\d+\s*\))?$", line, re.I)
        )

    def is_intent(line: str) -> bool:
        return line == "意向" or bool(re.match(r"^(?:Profile\s+)?(?:Intents?|Interest)$", line, re.I))

    def is_tail(line: str) -> bool:
        return bool(re.search(
            r"Profile (?:interest|activity)|^Interest$|^Activity$|^Moderate likelihood|"
            r"^Very likely|^Unlikely|Save to project|^Message |More actions|^\d+ connections?$",
            line,
            re.I,
        ))

    def skip_intro(line: str) -> bool:
        return bool(
            re.match(r"^·?\s*\d+(?:st|nd|rd|th)?\s*(?:度|degree)?(?:\s+(?:connection|人脉))?", line, re.I)
            or re.search(r"度人脉|premium subscription|高级帐号", line, re.I)
            or re.match(r"^~~+$", line)
        )

    exp_index = next((i for i, line in enumerate(lines) if is_exp(line)), -1)
    edu_index = next((i for i, line in enumerate(lines) if is_edu(line)), -1)

    headline_lines: list[str] = []
    location = ""
    for line in lines[1:]:
        if is_exp(line) or is_edu(line) or is_skills(line) or is_tail(line):
            break
        if skip_intro(line):
            continue
        location_match = re.match(r"^(.+?)\s·\s(.+)$", line)
        if location_match and not re.search(r"degree", location_match.group(1), re.I):
            location = location_match.group(1).strip()
            break
        headline_lines.append(line)

    company = ""
    current_title = ""
    experiences: list[str] = []
    if exp_index >= 0:
        end = edu_index if edu_index > exp_index else len(lines)
        for line in lines[exp_index + 1:end]:
            if is_tail(line) or is_edu(line) or is_skills(line) or is_intent(line):
                break
            if is_exp(line) or re.match(r"^(?:Show all|Show fewer|Show less|显示全部|显示更多|显示更少|收起|Total items)", line, re.I):
                continue
            english = re.match(r"^(.+?)\s+at\s+(.+?)(?:\s·\s(.+))?$", line)
            chinese = re.match(r"^(.+?)\s+-\s+(.+?)(?:\s·\s(.+))?$", line)
            if english:
                title, firm, dates = english.group(1).strip(), english.group(2).strip(), english.group(3) or ""
                experiences.append(f"{title} at {firm}" + (f" · {dates.strip()}" if dates else ""))
            elif chinese:
                firm, title, dates = chinese.group(1).strip(), chinese.group(2).strip(), chinese.group(3) or ""
                experiences.append(f"{title} at {firm}" + (f" · {dates.strip()}" if dates else ""))
            else:
                experiences.append(line)
                continue
            if not company:
                company, current_title = firm, title

    education_lines: list[str] = []
    if edu_index >= 0:
        for line in lines[edu_index + 1:]:
            if is_tail(line) or is_skills(line) or is_intent(line):
                break
            if is_edu(line) or re.match(r"^(?:Show all|Show fewer|Show less|显示全部|显示更多|显示更少|收起|Total items)", line, re.I):
                continue
            education_lines.append(line)

    return {
        "name": lines[0],
        "headline": " | ".join(headline_lines),
        "current_title": current_title,
        "company": company,
        "location": location,
        "experience": " | ".join(experiences),
        "education": " | ".join(education_lines),
    }


def normalize_public_url(value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    if value.startswith("www."):
        value = "https://" + value
    elif value.startswith("linkedin.com/"):
        value = "https://www." + value
    return normalize_url(value)


def infer_native_chinese(languages: list[Language]) -> bool | None:
    chinese_native = any(
        CHINESE_LANGUAGE_RE.search(item.name)
        and NATIVE_LEVEL_RE.search(f"{item.name} {item.proficiency}")
        for item in languages
    )
    if chinese_native:
        return True
    other_native = any(
        not CHINESE_LANGUAGE_RE.search(item.name)
        and NATIVE_LEVEL_RE.search(f"{item.name} {item.proficiency}")
        for item in languages
    )
    return False if other_native else None
