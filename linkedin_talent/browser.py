"""Browser-session and result-page navigation helpers."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import BrowserContext, Locator, Page

from .constants import CARD_SELECTOR
from .text import clean_text, normalize_url


def first_card_url(page: Page) -> str:
    links = page.locator(CARD_SELECTOR)
    if not links.count():
        return ""
    try:
        return normalize_url(links.first.get_attribute("href") or "")
    except Exception:
        return ""


def check_access_page(page: Page) -> None:
    url = page.url.lower()
    if "linkedin.com" not in url:
        raise RuntimeError("当前页面不是 LinkedIn。请在浏览器中打开 Recruiter 搜索结果页。")
    try:
        body = page.locator("body").inner_text(timeout=3_000)[:8_000]
    except Exception:
        body = ""
    if re.search(
        r"security verification|unusual activity|verify (?:your )?identity|captcha|安全验证|异常活动|验证您的身份",
        body,
        re.I,
    ):
        raise RuntimeError("LinkedIn 正在显示验证或异常访问页面。请停止脚本并人工处理。")


def advance_to_next_page(page: Page, completed_pages: int) -> bool:
    before = first_card_url(page)
    controls = page.locator("a, button")
    target: Locator | None = None
    for index in range(controls.count()):
        control = controls.nth(index)
        try:
            if not control.is_visible():
                continue
            text = clean_text(control.inner_text(timeout=400))
            aria = clean_text(control.get_attribute("aria-label") or "")
            disabled = (
                control.get_attribute("disabled") is not None
                or control.get_attribute("aria-disabled") == "true"
            )
            is_next = bool(
                re.search(r"前进到第\s*\d+\s*页", f"{text} {aria}")
                or re.match(r"^(?:go to\s+)?next(?:\s+page)?(?:\s+\d+)?$", aria, re.I)
                or (
                    re.match(r"^(?:下一页|下一步|next)$", text, re.I)
                    and re.search(r"next|page|页", aria, re.I)
                )
            )
            if is_next and not disabled:
                target = control
                break
        except Exception:
            continue
    if target is None:
        return False

    try:
        target.scroll_into_view_if_needed(timeout=3_000)
        target.click(timeout=5_000)
    except Exception as error:
        print(f"  [warn] 下一页按钮点击失败：{error}")
    else:
        for _ in range(24):
            time.sleep(0.75)
            check_access_page(page)
            after = first_card_url(page)
            if after and after != before:
                return True

    parts = urlsplit(page.url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["start"] = str(completed_pages * 25)
    fallback = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    try:
        print(f"  尝试使用 URL 翻页：start={completed_pages * 25}")
        page.goto(fallback, wait_until="domcontentloaded", timeout=45_000)
        page.locator(CARD_SELECTOR).first.wait_for(state="visible", timeout=20_000)
        return first_card_url(page) != before
    except Exception as error:
        print(f"  [warn] URL 翻页也失败：{error}")
        return False


def open_context(
    playwright: Any,
    profile_dir: Path,
    *,
    headless: bool = True,
) -> BrowserContext:
    profile_dir.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        viewport={"width": 1440, "height": 1000},
        slow_mo=50,
    )
