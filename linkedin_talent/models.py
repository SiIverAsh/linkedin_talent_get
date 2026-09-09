"""Domain models shared by scraping, persistence, and export code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Position:
    title: str = ""
    company: str = ""
    date_range: str = ""
    duration: str = ""
    location: str = ""
    description: str = ""
    skills: list[str] = field(default_factory=list)


@dataclass
class Education:
    school: str = ""
    degree: str = ""
    field_of_study: str = ""
    date_range: str = ""
    activities: str = ""
    grade: str = ""


@dataclass
class Language:
    name: str = ""
    proficiency: str = ""


@dataclass
class Candidate:
    name: str = ""
    current_title: str = ""
    current_company: str = ""
    location: str = ""
    headline: str = ""
    public_url: str = ""
    recruiter_url: str = ""
    core_skills: list[str] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    experience_fallback: str = ""
    education: list[Education] = field(default_factory=list)
    education_fallback: str = ""
    open_to_work: list[str] = field(default_factory=list)
    languages: list[Language] = field(default_factory=list)
    native_chinese: bool | None = None
    detail_error: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Candidate":
        data = dict(value)
        data["positions"] = [Position(**item) for item in data.get("positions", [])]
        data["education"] = [Education(**item) for item in data.get("education", [])]
        data["languages"] = [Language(**item) for item in data.get("languages", [])]
        return cls(**data)
