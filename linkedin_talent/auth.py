"""Load browser-exported LinkedIn cookies into a Playwright context."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext

_SAME_SITE = {
    "strict": "Strict",
    "lax": "Lax",
    "none": "None",
    "no_restriction": "None",
}


def _is_linkedin_domain(domain: str) -> bool:
    hostname = domain.lstrip(".").casefold()
    return hostname == "linkedin.com" or hostname.endswith(".linkedin.com")


def load_cookies(path: Path) -> list[dict[str, Any]]:
    """Convert a browser-extension cookie export to Playwright's format."""
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    source = payload.get("cookies") if isinstance(payload, dict) else payload
    if not isinstance(source, list):
        raise ValueError("Cookie 文件必须是 JSON 数组，或包含 cookies 数组的 JSON 对象")

    cookies: list[dict[str, Any]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        value = str(item.get("value", ""))
        domain = str(item.get("domain", ""))
        if not name or not domain or not _is_linkedin_domain(domain):
            continue

        cookie: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": str(item.get("path") or "/"),
            "httpOnly": bool(item.get("httpOnly", False)),
            "secure": bool(item.get("secure", False)),
        }
        expires = item.get("expires", item.get("expirationDate"))
        if expires is not None and not item.get("session", False):
            try:
                cookie["expires"] = float(expires)
            except (TypeError, ValueError):
                pass
        same_site = _SAME_SITE.get(str(item.get("sameSite", "")).casefold())
        if same_site:
            cookie["sameSite"] = same_site
        cookies.append(cookie)

    if not cookies:
        raise ValueError("Cookie 文件中没有可用的 LinkedIn Cookie")
    return cookies


def install_cookies(context: BrowserContext, path: Path) -> int:
    cookies = load_cookies(path)
    context.add_cookies(cookies)
    return len(cookies)
