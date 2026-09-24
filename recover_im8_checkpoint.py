from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from linkedin_talent.models import Candidate
from linkedin_talent.text import normalize_cli_url

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "linkedin_IM8kcandidates.csv"
CHECKPOINT = ROOT / "linkedin_IM8kcandidates.checkpoint.jsonl"
PLAN = ROOT / "linkedin_IM8kcandidates.shards.json"
SURNAMES = ROOT / "chinese_surnames.json"
BASE_URL = (
    "https://www.linkedin.com/talent/search?"
    "searchContextId=bd990bf3-acf6-48af-abdd-244c3a76e49b&"
    "searchHistoryId=21575243132&searchKeyword=&"
    "searchRequestId=3f791eb5-4c81-46e5-8cdb-000eb6497fc2&"
    "start=0&uiOrigin=ADVANCED_SEARCH"
)
COMPLETED = {
    "Ai", "An", "Ao", "Ba", "Bai", "Ban", "Bao", "Bei", "Bi", "Bian",
    "Bie", "Bing", "Bo", "Bu", "Cai", "Cang", "Cao", "Cen", "Chai",
    "Chang", "Chao", "Che", "Chen", "Cheng", "Chi", "Chong", "Chou",
    "Chu", "Cong", "Cui", "Dai",
}


def parse_bool(value: str) -> bool | None:
    normalized = value.strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def read_candidates() -> list[Candidate]:
    with OUTPUT.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream)
        candidates = []
        seen = set()
        for row in rows:
            recruiter_url = (row.get("Recruiter URL") or "").strip()
            key = recruiter_url.casefold() or (
                (row.get("姓名") or "").strip().casefold(),
                (row.get("当前职位") or "").strip().casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
            candidates.append(Candidate(
                name=(row.get("姓名") or "").strip(),
                current_title=(row.get("当前职位") or "").strip(),
                current_company=(row.get("当前公司") or "").strip(),
                location=(row.get("地点") or "").strip(),
                headline=(row.get("Headline") or "").strip(),
                public_url=(row.get("LinkedIn Public URL") or "").strip(),
                recruiter_url=recruiter_url,
                core_skills=[item.strip() for item in (row.get("核心技能") or "").split("|") if item.strip()],
                native_chinese=parse_bool(row.get("母语为中文") or ""),
            ))
    return candidates


def main() -> None:
    if not OUTPUT.exists():
        raise SystemExit(f"missing output: {OUTPUT}")
    candidates = read_candidates()
    surnames = json.loads(SURNAMES.read_text(encoding="utf-8"))
    pending = [[surname] for surname in surnames if surname not in COMPLETED]
    now = datetime.now().astimezone().isoformat(timespec="seconds")

    with CHECKPOINT.open("w", encoding="utf-8", newline="") as stream:
        for candidate in candidates:
            stream.write(json.dumps({
                "type": "record", "version": 2, "saved_at": now,
                "record": asdict(candidate),
            }, ensure_ascii=False, separators=(",", ":")) + "\n")

    plan = {
        "version": 4,
        "created_at": now,
        "updated_at": now,
        "criteria": None,
        "base_url": normalize_cli_url(BASE_URL),
        "pending": pending,
        "completed": [
            {"id": f"surname:{surname.casefold()}", "surnames": [surname],
             "surname_count": 1, "new_unique_candidates": None}
            for surname in surnames if surname in COMPLETED
        ],
        "unresolved": [],
        "active": None,
        "base_result_count": 9800,
        "recovered_from_csv": True,
        "recovered_candidate_count": len(candidates),
    }
    PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"recovered_candidates={len(candidates)}")
    print(f"completed_surnames={len(COMPLETED)}")
    print(f"pending_surnames={len(pending)}")
    print(f"checkpoint={CHECKPOINT}")
    print(f"plan={PLAN}")


if __name__ == "__main__":
    main()
