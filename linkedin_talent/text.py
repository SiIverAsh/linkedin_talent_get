"""Text and URL normalization helpers."""

from __future__ import annotations

import random
import re
import time
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


def clean_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s*Related to search terms in your query\s*", " ", text, flags=re.I)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def unique_texts(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = clean_text(value)
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def normalize_url(value: str) -> str:
    if not value:
        return ""
    parts = urlsplit(value)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def normalize_cli_url(value: str) -> str:
    """Accept a raw URL or a Markdown link accidentally pasted into the CLI."""
    text = clean_text(value)
    markdown = re.fullmatch(r"\[[^\]]+\]\((https?://[^)]+)\)", text, re.I)
    if markdown:
        text = markdown.group(1)
    return text.replace(r"\&", "&").replace(r"\_", "_")


def split_skills(values: Iterable[str]) -> list[str]:
    pieces: list[str] = []
    for value in values:
        pieces.extend(re.split(r"\s*[|·•,，;；]\s*", clean_text(value)))
    return unique_texts(pieces)


def short_delay(low: float = 0.7, high: float = 1.4) -> None:
    time.sleep(random.uniform(low, high))
