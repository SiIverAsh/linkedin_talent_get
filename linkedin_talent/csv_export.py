"""CSV snapshot export for candidate records."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .excel import format_education, format_languages, format_positions
from .models import Candidate

CSV_HEADERS = [
    "姓名", "当前职位", "当前公司", "地点", "Headline",
    "LinkedIn Public URL", "Recruiter URL", "核心技能",
    "完整工作经历", "完整教育经历", "求职开放信息", "语言", "母语为中文",
]


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return value


def export_csv(records: list[Candidate], output: Path) -> None:
    """Atomically rewrite a complete UTF-8-BOM CSV snapshot."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(CSV_HEADERS)
        for record in records:
            writer.writerow([
                record.name,
                record.current_title,
                record.current_company,
                record.location,
                record.headline,
                record.public_url,
                record.recruiter_url,
                " | ".join(record.core_skills),
                format_positions(record),
                format_education(record),
                "\n".join(record.open_to_work),
                format_languages(record.languages),
                _csv_value(record.native_chinese),
            ])
    temporary.replace(output)
