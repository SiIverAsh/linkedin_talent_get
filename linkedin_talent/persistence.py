"""Legacy JSON and incremental JSONL checkpoints for resumable runs."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Candidate

_JsonlState = tuple[int, int, str]
_jsonl_states: dict[Path, _JsonlState] = {}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def resolve_checkpoint_paths(output: Path, requested: Path | None) -> tuple[Path, Path]:
    """Return the JSONL write target and the best existing resume source."""
    if requested is not None:
        source = requested.resolve()
        target = source if source.suffix.casefold() == ".jsonl" else source.with_suffix(".jsonl")
        return target, source

    target = output.with_suffix(".checkpoint.jsonl").resolve()
    legacy = output.with_suffix(".checkpoint.json").resolve()
    source = target if target.exists() or not legacy.exists() else legacy
    return target, source


def reset_checkpoint(path: Path) -> None:
    """Start a fresh checkpoint without retaining events from an earlier run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("", encoding="utf-8")
    temporary.replace(path)
    _jsonl_states[path.resolve()] = (0, -1, "")


def _save_legacy_checkpoint(
    path: Path,
    records: list[Candidate],
    completed_pages: int,
    page_url: str,
) -> None:
    payload = {
        "version": 1,
        "saved_at": _now(),
        "completed_pages": completed_pages,
        "page_url": page_url,
        "records": [asdict(record) for record in records],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _load_jsonl(path: Path) -> tuple[list[Candidate], int, str]:
    records: list[Candidate] = []
    completed_pages = 0
    page_url = ""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            print(
                f"[warn] 跳过损坏的断点记录：第 {index + 1} 行，"
                f"JSON 解析错误：{error.msg}"
            )
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "record" and isinstance(event.get("record"), dict):
            records.append(Candidate.from_dict(event["record"]))
        elif event_type == "state":
            completed_pages = int(event.get("completed_pages", completed_pages))
            page_url = str(event.get("page_url", page_url))
    _jsonl_states[path.resolve()] = (len(records), completed_pages, page_url)
    return records, completed_pages, page_url


def save_checkpoint(
    path: Path,
    records: list[Candidate],
    completed_pages: int,
    page_url: str,
) -> None:
    if path.suffix.casefold() != ".jsonl":
        _save_legacy_checkpoint(path, records, completed_pages, page_url)
        return

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path not in _jsonl_states:
        if path.exists():
            _load_jsonl(path)
        else:
            _jsonl_states[path] = (0, -1, "")

    saved_count, saved_pages, saved_url = _jsonl_states[path]
    if len(records) < saved_count:
        reset_checkpoint(path)
        saved_count, saved_pages, saved_url = _jsonl_states[path]

    events: list[dict[str, Any]] = [
        {
            "type": "record",
            "version": 2,
            "saved_at": _now(),
            "record": asdict(record),
        }
        for record in records[saved_count:]
    ]
    if completed_pages != saved_pages or page_url != saved_url:
        events.append({
            "type": "state",
            "version": 2,
            "saved_at": _now(),
            "completed_pages": completed_pages,
            "page_url": page_url,
        })
    if events:
        with path.open("a", encoding="utf-8", newline="") as stream:
            for event in events:
                stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

    _jsonl_states[path] = (len(records), completed_pages, page_url)


def load_checkpoint(path: Path) -> tuple[list[Candidate], int, str]:
    if not path.exists():
        return [], 0, ""
    if path.suffix.casefold() == ".jsonl":
        return _load_jsonl(path.resolve())
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = [Candidate.from_dict(item) for item in payload.get("records", [])]
    return records, int(payload.get("completed_pages", 0)), str(payload.get("page_url", ""))
