#!/usr/bin/env python3
"""Adaptive surname sharding for an authorized LinkedIn Recruiter search.

The user prepares the base Recruiter search (keywords + degree + company filters).
This planner changes only the Last name(s) facet, recursively splits surname groups
whose result count is too large, and delegates each safe shard to the existing
DOM scraper in linkedin_talent_get.py.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import (
    Error as PlaywrightError,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from linkedin_talent.auth import install_cookies
from linkedin_talent.browser import advance_to_next_page, check_access_page, first_card_url, open_context
from linkedin_talent.constants import CARD_SELECTOR
from linkedin_talent.criteria import SearchCriteria, load_search_criteria, matches_target_company
from linkedin_talent.models import Candidate
from linkedin_talent.output import DEFAULT_EXPORT_BATCH_SIZE, export_batch_if_due, export_records
from linkedin_talent.persistence import (
    load_checkpoint,
    reset_checkpoint,
    resolve_checkpoint_paths,
    save_checkpoint,
)
from linkedin_talent.scraper import (
    capture_candidate_detail,
    close_drawer,
    extract_card_candidates,
    locator_text,
    scroll_results,
)
from linkedin_talent.sharding import (
    load_or_create_plan,
    load_surnames,
    parse_human_count,
    persist_plan,
    reset_start,
    shard_id,
    split_group,
)
from linkedin_talent.text import clean_text, normalize_cli_url, normalize_url, short_delay


SURNAME_RE = re.compile(r"姓氏|last\s*names?|surnames?|apellidos?|familienname", re.I)


def goto_search_page(page: Page, url: str, attempts: int = 3) -> None:
    """Open a Recruiter search URL, recovering from LinkedIn-aborted navigation."""
    last_error: PlaywrightError | None = None
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            return
        except PlaywrightError as error:
            last_error = error
            if "ERR_ABORTED" not in str(error) or attempt >= attempts:
                break
            print(f"  页面跳转被 LinkedIn 中断，正在重试（{attempt}/{attempts}）……")
            try:
                page.goto("about:blank", wait_until="commit", timeout=10_000)
            except PlaywrightError:
                pass
            time.sleep(1.0)
    raise RuntimeError(f"无法打开 LinkedIn Recruiter 搜索页：{last_error}") from last_error


def find_surname_facet(page: Page) -> Locator | None:
    facets = page.locator("[data-test-facet]")
    for index in range(facets.count()):
        facet = facets.nth(index)
        try:
            label = facet.locator("[data-test-facet-label]").first
            label_text = locator_text(label) if label.count() else ""
            if SURNAME_RE.search(label_text or locator_text(facet)):
                return facet
        except Exception:
            continue
    return None


def wait_for_surname_facet(page: Page, timeout_seconds: float = 15.0) -> Locator | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        facet = find_surname_facet(page)
        if facet is not None:
            return facet
        time.sleep(0.25)
    return None


def find_surname_input(page: Page) -> Locator | None:
    inputs = page.locator(
        'input.freeform-one-line-facet__input, input[type="text"], input.artdeco-typeahead__input'
    )
    for index in range(inputs.count()):
        item = inputs.nth(index)
        try:
            placeholder = clean_text(item.get_attribute("placeholder") or "")
            if item.is_visible() and SURNAME_RE.search(placeholder):
                return item
        except Exception:
            continue
    facet = find_surname_facet(page)
    if facet is None:
        return None
    scoped = facet.locator(
        'input.freeform-one-line-facet__input, input[type="text"], input.artdeco-typeahead__input'
    )
    for index in range(scoped.count()):
        try:
            if scoped.nth(index).is_visible():
                return scoped.nth(index)
        except Exception:
            continue
    return None


def open_surname_facet(page: Page) -> None:
    facet = wait_for_surname_facet(page)
    if facet is not None:
        buttons = facet.locator("button, [role='button']")
        for index in range(buttons.count()):
            button = buttons.nth(index)
            try:
                text = locator_text(button)
                aria = clean_text(button.get_attribute("aria-label") or "")
                if button.is_visible() and SURNAME_RE.search(f"{text} {aria}"):
                    button.click(timeout=3_000)
                    for _ in range(40):
                        if find_surname_input(page) is not None:
                            return
                        time.sleep(0.25)
            except Exception:
                continue

    toggles = page.locator(
        'button[data-test-toggle-button], button[data-test-facet-edit], '
        '[data-live-test-facet-edit], .collapsible-facet-wrapper__trigger'
    )
    for index in range(toggles.count()):
        button = toggles.nth(index)
        try:
            text = locator_text(button)
            aria = clean_text(button.get_attribute("aria-label") or "")
            if button.is_visible() and SURNAME_RE.search(f"{text} {aria}"):
                button.click(timeout=3_000)
                time.sleep(1.0)
                if find_surname_input(page) is not None:
                    return
        except Exception:
            continue


def clear_surname_filter(page: Page) -> None:
    facet = find_surname_facet(page)
    if facet is None:
        return
    clear = facet.locator(
        'button[data-test-pill-facet-clear], [data-test-pill-facet-clear], '
        'button[aria-label*="clear" i], button[aria-label*="清除"]'
    ).first
    try:
        if clear.count() and clear.is_visible():
            clear.click(timeout=3_000)
            time.sleep(0.8)
    except Exception as error:
        raise RuntimeError(f"无法清除已有姓氏筛选：{error}") from error


def trigger_search(page: Page, surname_input: Locator) -> None:
    before_url = page.url
    before_first = first_card_url(page)

    form = surname_input.locator("xpath=ancestor::form[1]")
    if form.count():
        submit = form.locator('button[type="submit"], [data-test-submit]').first
        try:
            if submit.count() and submit.is_visible():
                submit.click(timeout=3_000)
            else:
                surname_input.press("Enter")
        except Exception:
            surname_input.press("Enter")
    else:
        surname_input.press("Enter")
    time.sleep(0.8)

    selectors = (
        '[data-test-search-filters-bar-search-button], '
        '[data-test-search-button], [data-test-search-submit]'
    )
    buttons = page.locator(selectors)
    clicked = False
    for index in range(buttons.count()):
        button = buttons.nth(index)
        try:
            if button.is_visible() and button.is_enabled():
                button.click(timeout=3_000)
                clicked = True
                break
        except Exception:
            continue

    if not clicked:
        buttons = page.locator("button")
        for index in range(buttons.count()):
            button = buttons.nth(index)
            try:
                if button.is_visible() and re.match(
                    r"^(?:Search|搜索)$", locator_text(button), re.I
                ):
                    if button.evaluate("node => Boolean(node.closest('[data-test-facet]'))"):
                        continue
                    button.click(timeout=3_000)
                    clicked = True
                    break
            except Exception:
                continue

    for _ in range(30):
        time.sleep(0.5)
        if page.url != before_url or first_card_url(page) != before_first:
            break
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except PlaywrightTimeoutError:
        pass
    time.sleep(1.0)


def apply_surname_group(page: Page, surnames: list[str]) -> None:
    if wait_for_surname_facet(page) is None:
        raise RuntimeError("Recruiter 筛选栏加载超时，未找到 Last name(s) / 姓氏筛选器")
    clear_surname_filter(page)
    surname_input = find_surname_input(page)
    if surname_input is None:
        open_surname_facet(page)
        surname_input = find_surname_input(page)
    if surname_input is None:
        raise RuntimeError("找不到 Recruiter 的 Last name(s) / 姓氏输入框")
    expression = " OR ".join(surnames)
    surname_input.fill(expression, timeout=5_000)
    trigger_search(page, surname_input)
    check_access_page(page)


def read_result_count(page: Page) -> int:
    selectors = (
        '[data-test-search-results-count], [data-test-results-count], '
        '[data-test-search-results-count-container], [class*="results-count"]'
    )
    nodes = page.locator(selectors)
    for index in range(min(nodes.count(), 20)):
        value = parse_human_count(locator_text(nodes.nth(index)))
        if value is not None:
            return value
    body = page.locator("body").inner_text(timeout=5_000)
    value = parse_human_count(body[:20_000])
    if value is not None:
        return value
    if not page.locator(CARD_SELECTOR).count():
        return 0
    raise RuntimeError("页面已有候选人，但无法读取结果总数；为避免漏抓，分片计划已停止")


def capture_detail_with_retry(page: Page, candidate: Candidate) -> None:
    last_error = ""
    for attempt in range(1, 3):
        try:
            capture_candidate_detail(page, candidate)
            candidate.detail_error = ""
            return
        except Exception as error:
            last_error = clean_text(error)
            close_drawer(page)
            if attempt < 2:
                short_delay(1.0, 1.8)
    candidate.detail_error = last_error
    print(f"      [warn] 详情未获取：{last_error}")


def scrape_current_shard(
    page: Page,
    result_count: int,
    records: list[Candidate],
    output: Path,
    checkpoint: Path,
    no_details: bool,
    max_candidates: int,
    criteria: SearchCriteria,
) -> tuple[int, int]:
    seen = {normalize_url(item.recruiter_url) for item in records if item.recruiter_url}
    expected_pages = max(1, min(40, math.ceil(result_count / 25))) if result_count else 0
    before_total = len(records)
    pages_done = 0
    for page_index in range(expected_pages):
        if len(records) >= max_candidates:
            break
        check_access_page(page)
        loaded = scroll_results(page)
        candidates = extract_card_candidates(page)
        print(f"    第 {page_index + 1}/{expected_pages} 页：{loaded} 个链接，{len(candidates)} 位候选人")
        if not candidates:
            break
        for candidate in candidates:
            key = normalize_url(candidate.recruiter_url)
            if not key or key in seen:
                continue
            if not no_details:
                capture_detail_with_retry(page, candidate)
            if not matches_target_company(candidate.current_company, criteria):
                continue
            print(f"      [match] {candidate.name} | {candidate.current_company}")
            records.append(candidate)
            seen.add(key)
            save_checkpoint(checkpoint, records, 0, page.url)
            if export_batch_if_due(records, output):
                print(f"    已累计采集 {len(records)} 人，已写入：{output}")
            short_delay(0.9, 1.7)
            if len(records) >= max_candidates:
                break
        pages_done += 1
        if page_index + 1 >= expected_pages or len(records) >= max_candidates:
            break
        if not advance_to_next_page(page, page_index + 1):
            break
        short_delay(1.0, 1.8)
    return len(records) - before_total, pages_done


def build_parser() -> argparse.ArgumentParser:
    project_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="LinkedIn Recruiter 百家姓自适应分片抓取器")
    parser.add_argument(
        "--url",
        help="已设置关键词、学历和当前公司条件，但未设置姓氏的 Recruiter 搜索 URL",
    )
    parser.add_argument("--output", type=Path, default=Path("linkedin_biopharma_candidates.csv"))
    parser.add_argument("--profile-dir", type=Path, default=Path(".browser-profile"))
    parser.add_argument("--surnames", type=Path, default=project_dir / "chinese_surnames.json")
    parser.add_argument(
        "--criteria",
        type=Path,
        default=project_dir / "config" / "search_criteria.json",
        help="关键词、学历和目标公司配置（默认 config/search_criteria.json）",
    )
    parser.add_argument("--plan", type=Path, help="分片计划及进度文件")
    parser.add_argument("--checkpoint", type=Path, help="候选人数据断点文件")
    parser.add_argument("--max-per-shard", type=int, default=900, help="单片安全阈值，默认 900")
    parser.add_argument("--max-candidates", type=int, default=10_000)
    parser.add_argument("--no-details", action="store_true")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口（默认无头运行）")
    parser.add_argument(
        "--cookies",
        type=Path,
        default=project_dir / "config" / "cookie.json",
        help="浏览器导出的 Cookie JSON（默认 config/cookie.json）",
    )
    parser.add_argument("--no-cookies", action="store_true", help="不导入 Cookie 文件")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--skip-prompt", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    if not 1 <= args.max_per_shard <= 1_000:
        raise ValueError("--max-per-shard 必须在 1 到 1000 之间")
    if args.output.suffix.casefold() not in {".csv", ".xlsx"}:
        raise ValueError("--output 仅支持 .csv 或 .xlsx 文件")
    output = args.output.resolve()
    plan_path = (args.plan or output.with_suffix(".shards.json")).resolve()
    checkpoint, checkpoint_source = resolve_checkpoint_paths(output, args.checkpoint)
    surnames = load_surnames(args.surnames.resolve())
    criteria = load_search_criteria(args.criteria.resolve())
    if plan_path.exists() and not args.fresh:
        # Validate before loading/exporting a checkpoint, so data collected under
        # an older search definition can never leak into the new result set.
        load_or_create_plan(plan_path, "", surnames, False, criteria)
    elif checkpoint_source.exists() and not args.fresh:
        raise ValueError("发现候选人断点但缺少对应分片计划；请加 --fresh，避免混用未知检索条件的数据")
    records: list[Candidate] = []
    if args.fresh:
        reset_checkpoint(checkpoint)
    elif checkpoint_source.exists():
        records, _, _ = load_checkpoint(checkpoint_source)
        print(f"已读取候选人断点：{len(records)} 人")
        if checkpoint_source != checkpoint:
            print(f"旧版断点将在本次运行中迁移为增量 JSONL：{checkpoint}")

    interrupted = False
    with sync_playwright() as playwright:
        context = open_context(
            playwright,
            args.profile_dir.resolve(),
            headless=not args.headed,
        )
        if not args.no_cookies:
            cookie_path = args.cookies.resolve()
            if cookie_path.exists():
                count = install_cookies(context, cookie_path)
                print(f"已载入 {count} 条 LinkedIn Cookie：{cookie_path}")
            else:
                print(f"未找到 Cookie 文件，将使用浏览器配置中的登录状态：{cookie_path}")
        page = context.pages[0] if context.pages else context.new_page()
        try:
            if args.url:
                goto_search_page(page, normalize_cli_url(args.url))
            else:
                goto_search_page(page, "https://www.linkedin.com/talent/search")

            print("\n基础检索条件必须为：")
            print(f"Keywords: {criteria.keyword_expression}")
            print(f"Degrees:  {criteria.degree_expression}（满足其一）")
            print("Current companies（满足其一）:")
            for company in criteria.companies:
                print(f"  - {company.name}: {', '.join(company.aliases)}")
            print("Last name(s): 请保持为空，脚本会自动设置。")
            if not args.skip_prompt and args.headed:
                input("\n请完成登录和基础筛选，确认结果页正确后按 Enter：")

            check_access_page(page)
            base_url = reset_start(page.url)
            plan = load_or_create_plan(plan_path, base_url, surnames, args.fresh, criteria)
            if not plan.get("base_url"):
                plan["base_url"] = base_url
            base_url = str(plan["base_url"])
            persist_plan(plan_path, plan)

            first_pending_group = True
            while plan["pending"] and len(records) < args.max_candidates:
                group = list(plan["pending"][0])
                group_key = shard_id(group)
                print(f"\n评估分片：{len(group)} 个姓氏（{group[0]} … {group[-1]}）")
                if first_pending_group:
                    # The page is already at the base search after initial navigation.
                    # Reopening it immediately races LinkedIn's client-side redirect.
                    first_pending_group = False
                else:
                    goto_search_page(page, base_url)
                apply_surname_group(page, group)
                count = read_result_count(page)
                print(f"  LinkedIn 显示结果数：{count}")

                if count > args.max_per_shard and len(group) > 1:
                    left, right = split_group(group)
                    plan["pending"] = [left, right, *plan["pending"][1:]]
                    print(f"  超过 {args.max_per_shard}，拆成 {len(left)} + {len(right)} 个姓氏")
                    persist_plan(plan_path, plan)
                    continue

                if count > 1_000:
                    print(f"  [unresolved] 单一姓氏 {group[0]} 仍有 {count} 人，需要增加第二分片维度")
                    plan["unresolved"].append({
                        "id": group_key,
                        "surnames": group,
                        "result_count": count,
                        "reason": "single-surname-over-hard-limit",
                    })
                    plan["pending"].pop(0)
                    persist_plan(plan_path, plan)
                    continue

                added, pages = scrape_current_shard(
                    page=page,
                    result_count=count,
                    records=records,
                    output=output,
                    checkpoint=checkpoint,
                    no_details=args.no_details,
                    max_candidates=args.max_candidates,
                    criteria=criteria,
                )
                plan["completed"].append({
                    "id": group_key,
                    "surnames": group,
                    "result_count": count,
                    "new_unique_candidates": added,
                    "pages": pages,
                    "search_url": page.url,
                })
                plan["pending"].pop(0)
                persist_plan(plan_path, plan)
                print(f"  分片完成：新增去重候选人 {added}，累计 {len(records)}")

        except KeyboardInterrupt:
            interrupted = True
            print("\n收到 Ctrl+C，当前分片保留在待处理队列中。")
        finally:
            if records:
                save_checkpoint(checkpoint, records, 0, page.url)
                if len(records) % DEFAULT_EXPORT_BATCH_SIZE or not output.exists():
                    export_records(records, output)
            context.close()

    print(f"\n已导出 {len(records)} 位唯一候选人：{output}")
    print(f"分片计划：{plan_path}")
    if 'plan' in locals() and plan.get("unresolved"):
        print(f"仍有 {len(plan['unresolved'])} 个超限单姓分片，详见计划文件。")
        return 4
    return 130 if interrupted else 0


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    except PlaywrightTimeoutError as error:
        print(f"等待 LinkedIn 页面超时：{error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
