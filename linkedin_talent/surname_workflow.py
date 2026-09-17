"""Adaptive surname sharding for Recruiter result sets above its result cap."""

from __future__ import annotations

import math
import re
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .browser import advance_to_next_page, check_access_page, first_card_url
from .constants import CARD_SELECTOR
from .models import Candidate
from .output import export_batch_if_due
from .persistence import save_checkpoint
from .scraper import capture_candidate_detail, close_drawer, extract_card_candidates, locator_text, scroll_results
from .sharding import parse_human_count, persist_plan, shard_id, split_group
from .text import clean_text, normalize_url, short_delay

SURNAME_RE = re.compile(r"姓氏|last\s*names?|surnames?|apellidos?|familienname", re.I)
RESULT_COUNT_SELECTORS = (
    '[data-test-search-results-count], [data-test-results-count], '
    '[data-test-search-results-count-container], [class*="results-count"]'
)


def goto_search_page(page: Page, url: str, attempts: int = 3) -> None:
    """Open a Recruiter URL, retrying LinkedIn-aborted navigation."""
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            return
        except PlaywrightError as error:
            last_error = str(error)
            if "ERR_ABORTED" not in last_error or attempt == attempts:
                break
            print(f"  页面跳转被 LinkedIn 中断，正在重试（{attempt}/{attempts}）……")
            page.goto("about:blank", wait_until="commit", timeout=10_000)
            time.sleep(1)
    raise RuntimeError(f"无法打开 LinkedIn Recruiter 搜索页：{last_error}")


def find_surname_facet(page: Page) -> Locator | None:
    facets = page.locator("[data-test-facet]")
    for index in range(facets.count()):
        facet = facets.nth(index)
        try:
            labels = facet.locator("[data-test-facet-label]")
            label = locator_text(labels.first) if labels.count() else ""
            if SURNAME_RE.search(label) or SURNAME_RE.search(locator_text(facet)):
                return facet
        except Exception:
            continue
    return None


def wait_for_surname_facet(page: Page, timeout_seconds: float = 15) -> Locator | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        facet = find_surname_facet(page)
        if facet is not None:
            return facet
        time.sleep(0.25)
    return None


def find_surname_input(page: Page) -> Locator | None:
    selector = 'input.freeform-one-line-facet__input, input[type="text"], input.artdeco-typeahead__input'
    inputs = page.locator(selector)
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
    scoped = facet.locator(selector)
    for index in range(scoped.count()):
        if scoped.nth(index).is_visible():
            return scoped.nth(index)
    return None


def open_surname_facet(page: Page) -> None:
    facet = wait_for_surname_facet(page)
    if facet is None:
        raise RuntimeError("Recruiter 筛选栏加载超时，未找到 Last name(s) / 姓氏筛选器")

    for selector in (
        "button[data-test-toggle-button], button[data-test-facet-edit], "
        "[data-live-test-facet-edit], .collapsible-facet-wrapper__trigger",
        "button, [role='button']",
    ):
        buttons = facet.locator(selector)
        for index in range(buttons.count()):
            button = buttons.nth(index)
            try:
                text = f"{locator_text(button)} {clean_text(button.get_attribute('aria-label') or '')}"
                if button.is_visible() and (SURNAME_RE.search(text) or selector.startswith("button[data-test")):
                    button.click(timeout=3_000)
                    for _ in range(40):
                        if find_surname_input(page) is not None:
                            return
                        time.sleep(0.25)
            except Exception:
                continue

    buttons = facet.locator("button, [role='button']")
    for index in range(buttons.count()):
        button = buttons.nth(index)
        try:
            if button.is_visible():
                button.click(timeout=3_000)
                for _ in range(40):
                    if find_surname_input(page) is not None:
                        return
                    time.sleep(0.25)
        except Exception:
            continue


def clear_surname_filter(page: Page) -> None:
    print("  清除上一分片的姓氏筛选")
    selector = (
        'button[data-test-pill-facet-clear], [data-test-pill-facet-clear], '
        '[data-test-pill-facet-remove], [data-test-pill-remove], '
        'button[aria-label*="clear" i], button[aria-label*="remove" i], '
        'button[aria-label*="delete" i], button[aria-label*="dismiss" i], '
        'button[aria-label*="清除"], button[aria-label*="删除"], '
        'button[aria-label*="移除"]'
    )
    for _ in range(20):
        facet = find_surname_facet(page)
        if facet is None:
            return
        inputs = facet.locator('input.freeform-one-line-facet__input, input[type="text"], input.artdeco-typeahead__input')
        cleared_input = False
        for index in range(inputs.count()):
            input_field = inputs.nth(index)
            if input_field.is_visible() and input_field.input_value():
                input_field.fill("")
                cleared_input = True
        if cleared_input:
            input_field = find_surname_input(page)
            if input_field is not None:
                trigger_search(page, input_field)
        clear = facet.locator(selector)
        visible = [clear.nth(index) for index in range(clear.count()) if clear.nth(index).is_visible()]
        if not visible:
            buttons = facet.locator("button, [role='button']")
            for index in range(buttons.count()):
                button = buttons.nth(index)
                label = clean_text(
                    f"{button.get_attribute('aria-label') or ''} "
                    f"{button.get_attribute('title') or ''} {locator_text(button)}"
                )
                if button.is_visible() and re.search(
                    r"clear|remove|delete|dismiss|清除|删除|移除|取消",
                    label,
                    re.I,
                ):
                    visible.append(button)
        if not visible:
            return
        try:
            visible[0].click(timeout=3_000)
            time.sleep(0.8)
        except Exception as error:
            raise RuntimeError(f"无法清除已有姓氏筛选：{error}") from error
    raise RuntimeError("无法清空已有姓氏筛选：清除按钮仍然存在")


def trigger_search(page: Page, surname_input: Locator) -> None:
    before_url = page.url
    before_first = first_card_url(page)
    clicked = False

    try:
        form = surname_input.locator("xpath=ancestor::form[1]")
        if form.count():
            submit = form.first.locator('button[type="submit"], [data-test-submit]')
            if submit.count() and submit.first.is_visible():
                submit.first.click(timeout=3_000)
                clicked = True
    except Exception:
        pass

    if not clicked:
        selectors = (
            "[data-test-search-filters-bar-search-button], "
            "[data-test-search-button], [data-test-search-submit]"
        )
        buttons = page.locator(selectors)
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
                inside_facet = button.evaluate("node => Boolean(node.closest('[data-test-facet]'))")
                if (
                    not inside_facet
                    and button.is_visible()
                    and button.is_enabled()
                    and re.match(r"^(?:Search|搜索)$", locator_text(button), re.I)
                ):
                    button.click(timeout=3_000)
                    clicked = True
                    break
            except Exception:
                continue
    if not clicked:
        surname_input.press("Enter")

    changed = False
    for _ in range(30):
        time.sleep(0.5)
        if page.url != before_url or first_card_url(page) != before_first:
            changed = True
            break
    if not changed:
        try:
            surname_input.press("Enter")
        except Exception:
            pass
        for _ in range(20):
            time.sleep(0.5)
            if page.url != before_url or first_card_url(page) != before_first:
                break
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except PlaywrightTimeoutError:
        pass
    time.sleep(1)


def surname_filter_is_applied(page: Page, surname: str) -> bool:
    facet = find_surname_facet(page)
    if facet is None:
        return False
    return bool(re.search(rf"(?<!\w){re.escape(surname)}(?!\w)", locator_text(facet), re.I))


def apply_surname_group(page: Page, surnames: list[str]) -> None:
    if len(surnames) != 1:
        raise ValueError(f"每个分片必须只包含一个姓氏，当前包含 {len(surnames)} 个")
    if wait_for_surname_facet(page) is None:
        raise RuntimeError("Recruiter 筛选栏加载超时，未找到 Last name(s) / 姓氏筛选器")
    surname_input = find_surname_input(page)
    if surname_input is None:
        open_surname_facet(page)
    clear_surname_filter(page)
    surname_input = find_surname_input(page)
    if surname_input is None:
        print("  [warn] 姓氏筛选器未打开，刷新当前结果页后重试")
        page.reload(wait_until="domcontentloaded", timeout=45_000)
        check_access_page(page)
        if wait_for_surname_facet(page) is None:
            raise RuntimeError("刷新后仍找不到 Recruiter 的 Last name(s) / 姓氏筛选器")
        open_surname_facet(page)
        clear_surname_filter(page)
        surname_input = find_surname_input(page)
    if surname_input is None:
        raise RuntimeError("找不到 Recruiter 的 Last name(s) / 姓氏输入框")
    surname = surnames[0]
    print(f"  替换姓氏筛选：{surname}")
    surname_input.fill(surname, timeout=5_000)
    if clean_text(surname_input.input_value()) != surname:
        raise RuntimeError(f"姓氏输入失败：输入框当前值不是 {surname}")
    trigger_search(page, surname_input)
    check_access_page(page)
    for _ in range(20):
        if surname_filter_is_applied(page, surname):
            return
        time.sleep(0.5)
    raise RuntimeError(f"姓氏筛选未生效：页面未确认已应用 {surname}，为避免误抓已停止")


def read_result_count(page: Page) -> int:
    nodes = page.locator(RESULT_COUNT_SELECTORS)
    for index in range(min(nodes.count(), 20)):
        value = parse_human_count(locator_text(nodes.nth(index)))
        if value is not None:
            return value
    body = page.locator("body").inner_text(timeout=5_000)[:20_000]
    value = parse_human_count(body)
    if value is not None:
        return value
    if page.locator(CARD_SELECTOR).count():
        raise RuntimeError("页面已有候选人，但无法读取结果总数；为避免漏抓，分片计划已停止")
    return 0


def should_use_surname_sharding(result_count: int, plan_exists: bool, disabled: bool) -> bool:
    return not disabled


def _candidate_key(candidate: Candidate) -> tuple[str, str] | None:
    name = clean_text(candidate.name).casefold()
    title = clean_text(candidate.current_title).casefold()
    return (name, title) if name else None


def _total_pages(result_count: int, max_pages: int) -> int:
    return math.ceil(result_count / 25) if result_count else 0


def _active_checkpoint(
    plan: dict,
    group: list[str],
    group_key: str,
    result_count: int,
    current_page: int,
    total_pages: int,
    page_url: str,
) -> dict:
    shard_index = len(plan["completed"]) + len(plan["unresolved"]) + 1
    return {
        "id": group_key,
        "surnames": group,
        "surname_count": len(group),
        "result_count": result_count,
        "shard_index": shard_index,
        "known_shard_total": shard_index + len(plan["pending"]) - 1,
        "current_page": current_page,
        "next_page": current_page,
        "total_pages": total_pages,
        "page_url": page_url,
    }


def _capture_detail(page: Page, candidate: Candidate) -> None:
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
                short_delay(1, 1.8)
    candidate.detail_error = last_error
    print(f"      [warn] 详情未获取：{last_error}")


def _scrape_shard(
    page: Page,
    result_count: int,
    records: list[Candidate],
    output: Path,
    checkpoint: Path,
    plan: dict,
    plan_path: Path,
    group_key: str,
    start_page: int,
    max_pages: int,
    max_candidates: int,
    no_details: bool,
) -> tuple[int, int, bool]:
    if result_count == 0:
        return 0, 0, True
    seen_urls = {normalize_url(item.recruiter_url) for item in records if item.recruiter_url}
    seen_people = {key for item in records if (key := _candidate_key(item)) is not None}
    expected_pages = _total_pages(result_count, max_pages)
    before_total = len(records)
    pages_done = 0

    for page_index in range(start_page, expected_pages + 1):
        check_access_page(page)
        loaded = scroll_results(page)
        candidates = extract_card_candidates(page)
        print(f"    第 {page_index}/{expected_pages} 页：{loaded} 个链接，{len(candidates)} 位候选人")

        for candidate in candidates:
            url_key = normalize_url(candidate.recruiter_url)
            person_key = _candidate_key(candidate)
            if url_key in seen_urls or (person_key is not None and person_key in seen_people):
                continue
            if len(records) >= max_candidates:
                return len(records) - before_total, pages_done, False
            if not no_details:
                _capture_detail(page, candidate)
            detailed_key = _candidate_key(candidate)
            if detailed_key is not None and detailed_key in seen_people:
                continue

            records.append(candidate)
            if url_key:
                seen_urls.add(url_key)
            if detailed_key is not None:
                seen_people.add(detailed_key)
            print(f"      [match] {candidate.name} | {candidate.current_title}")
            save_checkpoint(checkpoint, records, 0, page.url)
            if export_batch_if_due(records, output):
                print(f"    已累计采集 {len(records)} 人，已写入：{output}")
            short_delay(0.9, 1.7)

        pages_done += 1
        if page_index >= expected_pages:
            return len(records) - before_total, pages_done, True
        if not advance_to_next_page(page, page_index):
            return len(records) - before_total, pages_done, False
        plan["active"]["current_page"] = page_index + 1
        plan["active"]["next_page"] = page_index + 1
        plan["active"]["page_url"] = page.url
        persist_plan(plan_path, plan)
        short_delay(1, 1.8)

    return len(records) - before_total, pages_done, True


def run_surname_shards(
    page: Page,
    records: list[Candidate],
    output: Path,
    checkpoint: Path,
    plan: dict,
    plan_path: Path,
    max_per_shard: int,
    max_pages: int,
    max_candidates: int,
    no_details: bool,
) -> None:
    """Evaluate pending groups, recursively split oversized groups, and scrape safe ones."""
    plan["settings"] = {
        "max_per_shard": max_per_shard,
        "max_pages_per_shard": max_pages,
        "max_candidates": max_candidates,
    }
    persist_plan(plan_path, plan)

    while plan["pending"] and len(records) < max_candidates:
        group = list(plan["pending"][0])
        if len(group) > 1:
            plan["pending"][:1] = [[surname] for surname in group]
            persist_plan(plan_path, plan)
            continue
        group_key = shard_id(group)
        active = plan.get("active")

        print(f"\n评估分片：{len(group)} 个姓氏（{group[0]} … {group[-1]}）")
        if active is not None:
            if active.get("id") != group_key:
                raise RuntimeError("分片计划中的活动分片与待处理队列不一致，请检查计划文件")
            start_page = max(1, int(active.get("current_page", active.get("next_page", 1))))
            result_count = int(active["result_count"])
            total_pages = max(
                int(active.get("total_pages", 0)),
                _total_pages(result_count, max_pages),
            )
            resume_url = str(active.get("page_url", ""))
            if not resume_url:
                raise RuntimeError("活动分片缺少续跑页面 URL，无法安全恢复")
            plan["active"] = _active_checkpoint(
                plan, group, group_key, result_count, start_page, total_pages, resume_url
            )
            persist_plan(plan_path, plan)
            goto_search_page(page, resume_url)
            active = plan["active"]
            print(
                f"  从断点继续：第 {active['shard_index']} 个分片，"
                f"第 {start_page}/{total_pages} 页，"
                f"{active['surname_count']} 个姓氏，{result_count} 条结果"
            )
        else:
            apply_surname_group(page, group)
            result_count = read_result_count(page)
            print(f"  LinkedIn 显示结果数：{result_count}")

            if result_count > max_per_shard:
                if len(group) > 1:
                    left, right = split_group(group)
                    plan["pending"][:1] = [left, right]
                    print(f"  超过 {max_per_shard}，拆成 {len(left)} + {len(right)} 个姓氏")
                    persist_plan(plan_path, plan)
                    continue

            start_page = 1
            total_pages = _total_pages(result_count, max_pages)
            if result_count > max_per_shard and len(group) == 1:
                print(f"  [single-surname] {group[0]} 有 {result_count} 人，将抓取全部 {total_pages} 页")
            plan["active"] = _active_checkpoint(
                plan, group, group_key, result_count, start_page, total_pages, page.url
            )
            persist_plan(plan_path, plan)
            active = plan["active"]
            print(
                f"  开始第 {active['shard_index']} 个分片"
                f"（当前已知共 {active['known_shard_total']} 个）："
                f"{active['surname_count']} 个姓氏，{result_count} 条结果，{total_pages} 页"
            )

        added, pages, complete = _scrape_shard(
            page, result_count, records, output, checkpoint, plan, plan_path,
            group_key, start_page, max_pages, max_candidates, no_details,
        )
        if not complete:
            if len(records) >= max_candidates:
                print(f"  已达到候选人上限 {max_candidates}，当前页断点已保存。")
            else:
                print("  未能进入下一页；当前页断点已保存，下次将从该页继续")
            return

        active = plan["active"]
        plan["completed"].append({
            "id": group_key,
            "surnames": group,
            "surname_count": len(group),
            "shard_index": active["shard_index"],
            "result_count": result_count,
            "new_unique_candidates": added,
            "pages": active["total_pages"],
            "pages_processed_this_run": pages,
            "search_url": page.url,
        })
        plan["pending"].pop(0)
        plan["active"] = None
        persist_plan(plan_path, plan)
        print(f"  分片完成：新增去重候选人 {added}，累计 {len(records)}")
