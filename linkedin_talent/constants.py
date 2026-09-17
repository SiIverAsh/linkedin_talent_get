"""DOM selectors and language-detection patterns."""

from __future__ import annotations

import re

CARD_SELECTOR = 'a[data-test-link-to-profile-link="true"]'
DRAWER_SELECTOR = '#profile-container[data-test-profile-container], [data-test-profile-container]'
DETAIL_CLOSE_SELECTOR = (
    '[data-test-close-pagination-header-button], [aria-label="Exit profile view"]'
)

CHINESE_LANGUAGE_RE = re.compile(
    r"(?:\bchinese\b|\bmandarin\b|\bcantonese\b|中文|汉语|漢語|普通话|普通話|国语|國語|粤语|粵語)",
    re.IGNORECASE,
)
NATIVE_LEVEL_RE = re.compile(
    r"(?:native(?:\s+or\s+bilingual)?(?:\s+proficiency)?|mother\s+tongue|母语|母語|双语|雙語)",
    re.IGNORECASE,
)
