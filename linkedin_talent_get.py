#!/usr/bin/env python3
"""Backward-compatible entry point for the modular LinkedIn export package.

Existing imports from this module remain supported. New code should import from
the focused modules under :mod:`linkedin_talent`.
"""

from linkedin_talent.browser import (
    advance_to_next_page,
    check_access_page,
    first_card_url,
    open_context,
)
from linkedin_talent.cli import build_parser, main, run, validate_args
from linkedin_talent.constants import CARD_SELECTOR, DETAIL_CLOSE_SELECTOR, DRAWER_SELECTOR
from linkedin_talent.csv_export import export_csv
from linkedin_talent.excel import (
    excel_safe,
    export_excel,
    format_education,
    format_languages,
    format_positions,
)
from linkedin_talent.models import Candidate, Education, Language, Position
from linkedin_talent.output import (
    DEFAULT_EXPORT_BATCH_SIZE,
    export_batch_if_due,
    export_records,
)
from linkedin_talent.parsers import infer_native_chinese, normalize_public_url, parse_card_text
from linkedin_talent.persistence import (
    load_checkpoint,
    reset_checkpoint,
    resolve_checkpoint_paths,
    save_checkpoint,
)
from linkedin_talent.scraper import (
    capture_candidate_detail,
    close_drawer,
    expand_drawer_sections,
    expand_result_cards,
    expected_card_count,
    extract_card_candidates,
    extract_drawer_detail,
    find_candidate_link,
    load_full_drawer,
    locator_text,
    scroll_results,
    wait_for_drawer_stable,
)
from linkedin_talent.text import (
    clean_text,
    normalize_cli_url,
    normalize_url,
    short_delay,
    split_skills,
    unique_texts,
)

__all__ = [
    "CARD_SELECTOR",
    "DETAIL_CLOSE_SELECTOR",
    "DRAWER_SELECTOR",
    "Candidate",
    "DEFAULT_EXPORT_BATCH_SIZE",
    "Education",
    "Language",
    "Position",
    "advance_to_next_page",
    "build_parser",
    "capture_candidate_detail",
    "check_access_page",
    "clean_text",
    "close_drawer",
    "excel_safe",
    "export_batch_if_due",
    "export_csv",
    "expand_drawer_sections",
    "expand_result_cards",
    "expected_card_count",
    "export_excel",
    "export_records",
    "extract_card_candidates",
    "extract_drawer_detail",
    "find_candidate_link",
    "first_card_url",
    "format_education",
    "format_languages",
    "format_positions",
    "infer_native_chinese",
    "load_checkpoint",
    "load_full_drawer",
    "locator_text",
    "main",
    "normalize_public_url",
    "normalize_cli_url",
    "normalize_url",
    "open_context",
    "parse_card_text",
    "run",
    "reset_checkpoint",
    "resolve_checkpoint_paths",
    "save_checkpoint",
    "scroll_results",
    "short_delay",
    "split_skills",
    "unique_texts",
    "validate_args",
    "wait_for_drawer_stable",
]


if __name__ == "__main__":
    raise SystemExit(main())
