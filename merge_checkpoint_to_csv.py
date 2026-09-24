from __future__ import annotations

import csv
import shutil
from datetime import datetime
from pathlib import Path

from linkedin_talent.csv_export import CSV_HEADERS
from linkedin_talent.excel import format_education, format_languages, format_positions
from linkedin_talent.models import Candidate
from linkedin_talent.persistence import load_checkpoint

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "linkedin_IM8kcandidates.csv"
CHECKPOINT = ROOT / "linkedin_IM8kcandidates.checkpoint.jsonl"


def key_from_row(row: dict[str, str]) -> str:
    recruiter_url = (row.get("Recruiter URL") or "").strip().casefold()
    if recruiter_url:
        return f"url:{recruiter_url}"
    return "name:{0}\x1f{1}".format(
        (row.get("姓名") or "").strip().casefold(),
        (row.get("当前职位") or "").strip().casefold(),
    )


def key_from_candidate(candidate: Candidate) -> str:
    recruiter_url = candidate.recruiter_url.strip().casefold()
    if recruiter_url:
        return f"url:{recruiter_url}"
    return "name:{0}\x1f{1}".format(
        candidate.name.strip().casefold(), candidate.current_title.strip().casefold()
    )


def row_from_candidate(candidate: Candidate) -> dict[str, str]:
    native = "" if candidate.native_chinese is None else (
        "TRUE" if candidate.native_chinese else "FALSE"
    )
    return {
        "姓名": candidate.name,
        "当前职位": candidate.current_title,
        "当前公司": candidate.current_company,
        "地点": candidate.location,
        "Headline": candidate.headline,
        "LinkedIn Public URL": candidate.public_url,
        "Recruiter URL": candidate.recruiter_url,
        "核心技能": " | ".join(candidate.core_skills),
        "完整工作经历": format_positions(candidate),
        "完整教育经历": format_education(candidate),
        "求职开放信息": "\n".join(candidate.open_to_work),
        "语言": format_languages(candidate.languages),
        "母语为中文": native,
    }


def main() -> None:
    if not OUTPUT.exists() or not CHECKPOINT.exists():
        raise SystemExit("CSV 或 checkpoint 不存在")

    records, _, _ = load_checkpoint(CHECKPOINT)
    with OUTPUT.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = [{header: row.get(header, "") for header in CSV_HEADERS} for row in reader]

    known = {key_from_row(row) for row in rows}
    additions = []
    for record in records:
        key = key_from_candidate(record)
        if key not in known:
            additions.append(row_from_candidate(record))
            known.add(key)

    if not additions:
        print(f"无需追加；CSV已有 {len(rows)} 条，checkpoint有 {len(records)} 条")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = OUTPUT.with_name(f"{OUTPUT.stem}.before-merge-{stamp}{OUTPUT.suffix}")
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".merge.tmp")
    shutil.copy2(OUTPUT, backup)
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
        writer.writerows(additions)
    temporary.replace(OUTPUT)
    print(f"CSV原有记录={len(rows)}")
    print(f"checkpoint记录={len(records)}")
    print(f"本次追加={len(additions)}")
    print(f"合并后记录={len(rows) + len(additions)}")
    print(f"备份={backup}")


if __name__ == "__main__":
    main()
