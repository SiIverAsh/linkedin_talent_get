"""Playwright DOM extraction for Recruiter result cards and profile drawers."""

from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Locator, Page

from .constants import CARD_SELECTOR, DETAIL_CLOSE_SELECTOR, DRAWER_SELECTOR
from .models import Candidate, Education, Language, Position
from .parsers import infer_native_chinese, normalize_public_url, parse_card_text
from .text import clean_text, normalize_url, short_delay, split_skills, unique_texts


def locator_text(locator: Locator) -> str:
    try:
        return clean_text(locator.inner_text(timeout=2_000))
    except Exception:
        return ""


def expected_card_count(page: Page) -> int:
    try:
        body = page.locator("body").inner_text(timeout=3_000)
        import re
        match = re.search(r"共\s*([\d,]+)\s*条", body)
        if match:
            return min(25, int(match.group(1).replace(",", "")))
    except Exception:
        pass
    return 25


def expand_result_cards(page: Page) -> int:
    import re
    selectors = (
        '[data-test-expandable-list-button], '
        '[data-test-row-decorations__skills-match-insights-show-all-cta]'
    )
    buttons = page.locator(selectors)
    clicked = 0
    for index in range(min(buttons.count(), 100)):
        button = buttons.nth(index)
        try:
            label = clean_text(button.inner_text(timeout=1_000))
            if (
                button.is_visible()
                and re.search(r"显示全部|显示更多|show\s+all|show\s+more|see\s+all|see\s+more", label, re.I)
                and not re.search(r"breakdown|search|analysis|report|saved|project|insight|analytics|fewer|less", label, re.I)
                and button.get_attribute("aria-expanded") != "true"
            ):
                button.scroll_into_view_if_needed(timeout=2_000)
                button.click(timeout=2_000)
                clicked += 1
                time.sleep(0.35)
        except Exception:
            continue
    return clicked


def scroll_results(page: Page) -> int:
    expected = expected_card_count(page)
    page.evaluate("window.scrollTo(0, 0)")
    short_delay()
    last_count = 0
    stable = 0
    for _ in range(60):
        expand_result_cards(page)
        page.evaluate(
            """
            () => {
              const first = document.querySelector('a[data-test-link-to-profile-link="true"]');
              let node = first ? first.parentElement : null;
              const targets = [];
              while (node && node !== document.documentElement) {
                if (node.scrollHeight > node.clientHeight + 20) targets.push(node);
                node = node.parentElement;
              }
              targets.sort((a, b) =>
                (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
              for (const target of targets) target.scrollBy({top: 320, behavior: 'auto'});
              window.scrollBy({top: 320, behavior: 'auto'});
            }
            """
        )
        time.sleep(0.65)
        count = len({
            normalize_url(url)
            for url in page.locator(CARD_SELECTOR).evaluate_all(
                "links => links.map(link => link.href)"
            )
            if url
        })
        if count >= expected:
            return count
        if count == last_count:
            stable += 1
            if stable >= 8:
                return count
        else:
            last_count = count
            stable = 0
    return last_count


def extract_card_candidates(page: Page) -> list[Candidate]:
    links = page.locator(CARD_SELECTOR)
    output: list[Candidate] = []
    seen: set[str] = set()
    for index in range(links.count()):
        link = links.nth(index)
        try:
            href = normalize_url(link.get_attribute("href") or "")
            if not href or href in seen:
                continue
            card = link.locator("xpath=ancestor::article[1]")
            if not card.count():
                continue
            parsed = parse_card_text(card.inner_text(timeout=3_000))
            skill_nodes = card.locator(
                '[data-test-row-decorations__skills-match-insights] .base-decoration__trigger-text'
            )
            skills = split_skills(skill_nodes.all_inner_texts() if skill_nodes.count() else [])
            output.append(Candidate(
                name=parsed["name"] or clean_text(link.inner_text()),
                current_title=parsed["current_title"],
                current_company=parsed["company"],
                location=parsed["location"],
                headline=parsed["headline"],
                recruiter_url=href,
                core_skills=skills,
                experience_fallback=parsed["experience"],
                education_fallback=parsed["education"],
            ))
            seen.add(href)
        except Exception as error:
            print(f"  [warn] 无法读取第 {index + 1} 张候选人卡片：{error}")
    return output


def wait_for_drawer_stable(drawer: Locator, timeout_seconds: float = 7.0) -> bool:
    started = time.monotonic()
    previous = ""
    stable_since = time.monotonic()
    while time.monotonic() - started < timeout_seconds:
        try:
            signature = drawer.evaluate(
                """
                node => `${node.innerText.length}|${node.querySelectorAll(
                  '[data-test-position-entity-title], [data-test-education-item], [data-test-skill-entity-skill-name]'
                ).length}`
                """
            )
            if signature != previous:
                previous = signature
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= 0.6:
                return True
        except Exception:
            return False
        time.sleep(0.12)
    return False


def expand_drawer_sections(drawer: Locator) -> None:
    import re
    selector = (
        '[data-test-expandable-list-button], '
        '[data-test-expandable-list-expand-btn], button, [role="button"]'
    )
    clicked_keys: set[str] = set()
    for _ in range(80):
        buttons = drawer.locator(selector)
        target: Locator | None = None
        target_key = ""
        for index in range(min(buttons.count(), 150)):
            button = buttons.nth(index)
            try:
                label = clean_text(button.inner_text(timeout=500))
                aria = clean_text(button.get_attribute("aria-label") or label)
                key = f"{index}|{label}|{aria}"
                if (
                    key not in clicked_keys
                    and button.is_visible()
                    and button.get_attribute("aria-expanded") != "true"
                    and re.search(r"显示全部|显示更多|show\s+all|show\s+more|see\s+all|see\s+more", label, re.I)
                    and re.search(r"profile\s+(?:experience|education|skills)|(?:experience|education|skills|languages|publications)", aria, re.I)
                ):
                    target, target_key = button, key
                    break
            except Exception:
                continue
        if target is None:
            return
        clicked_keys.add(target_key)
        try:
            target.scroll_into_view_if_needed(timeout=2_000)
            target.click(timeout=2_000)
            wait_for_drawer_stable(drawer, 3.5)
        except Exception:
            continue


def load_full_drawer(drawer: Locator) -> None:
    expand_drawer_sections(drawer)
    previous = ""
    stable = 0
    for _ in range(30):
        try:
            signature = drawer.evaluate(
                """
                drawer => {
                  const candidates = [drawer, ...drawer.querySelectorAll('*')]
                    .filter(node => node.scrollHeight > node.clientHeight + 20);
                  candidates.sort((a, b) =>
                    (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
                  const target = candidates[0] || drawer;
                  target.scrollBy({top: 340, behavior: 'auto'});
                  return `${target.scrollTop}|${target.scrollHeight}|${drawer.innerText.length}`;
                }
                """
            )
        except Exception:
            return
        wait_for_drawer_stable(drawer, 4.0)
        expand_drawer_sections(drawer)
        stable = stable + 1 if signature == previous else 0
        previous = signature
        if stable >= 2:
            return


def extract_drawer_detail(drawer: Locator) -> dict[str, Any]:
    result = drawer.evaluate(
        r"""
        drawer => {
          const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
          const multi = value => String(value || '')
            .replace(/\r\n?/g, '\n')
            .replace(/[ \t]+\n/g, '\n')
            .replace(/\n[ \t]+/g, '\n')
            .trim();
          const q = (root, selector) => {
            const node = root && root.querySelector(selector);
            return node ? clean(node.textContent) : '';
          };
          const qm = (root, selector) => {
            const node = root && root.querySelector(selector);
            return node ? multi(node.innerText) : '';
          };
          const texts = (root, selector) => [...root.querySelectorAll(selector)]
            .map(node => clean(node.textContent)).filter(Boolean);
          const unique = values => [...new Set(values)];
          const itemRoot = node => node.closest(
            '[data-test-position-entity], [data-test-education-item], li, article'
          ) || node.parentElement;

          const positions = [];
          const seenPositions = new Set();
          for (const titleNode of drawer.querySelectorAll(
            '[data-test-position-entity-title], [data-test-grouped-position-entity-title]'
          )) {
            const item = itemRoot(titleNode);
            const entry = {
              title: clean(titleNode.textContent),
              company: q(item, '[data-test-position-entity-company-name], [data-test-position-entity-company-without-link], [data-test-grouped-position-entity-company-name]'),
              date_range: q(item, '[data-test-position-entity-date-range], [data-test-grouped-position-entity-date-range], [data-test-grouped-position-entity-date-overall-range]'),
              duration: q(item, '[data-test-position-entity-duration], [data-test-grouped-position-entity-duration]'),
              location: q(item, '[data-test-position-entity-location], [data-test-grouped-position-entity-location]'),
              description: qm(item, '[data-test-position-entity-description], [data-test-grouped-position-entity-description]'),
              skills: unique(texts(item, '[data-test-position-skill-item], [data-test-skill-entity-skill-name], [data-test-grouped-position-entity-skills]')),
            };
            const key = `${entry.title}|${entry.company}|${entry.date_range}`;
            if (entry.title && !seenPositions.has(key)) {
              seenPositions.add(key);
              positions.push(entry);
            }
          }

          const education = [];
          const seenEducation = new Set();
          const educationItems = [...drawer.querySelectorAll('[data-test-education-item]')];
          const educationRoots = educationItems.length
            ? educationItems
            : [...drawer.querySelectorAll('[data-test-education-entity-school-name]')];
          for (const schoolNode of educationRoots) {
            const item = educationItems.length ? schoolNode : itemRoot(schoolNode);
            const entry = {
              school: educationItems.length
                ? q(item, '[data-test-education-entity-school-name]')
                : clean(schoolNode.textContent),
              degree: q(item, '[data-test-education-entity-degree-name]'),
              field_of_study: q(item, '[data-test-education-entity-field-of-study]'),
              date_range: q(item, '[data-test-education-entity-dates]'),
              activities: q(item, '[data-test-education-entity-activities]'),
              grade: q(item, '[data-test-education-entity-grade]'),
            };
            const key = `${entry.school}|${entry.degree}|${entry.date_range}`;
            if (entry.school && !seenEducation.has(key)) {
              seenEducation.add(key);
              education.push(entry);
            }
          }

          const languages = [];
          const seenLanguages = new Set();
          for (const nameNode of drawer.querySelectorAll('[data-test-language-name]')) {
            const name = clean(nameNode.textContent);
            const item = nameNode.closest('[data-test-language-entity], li, article') || nameNode.parentElement;
            const explicit = q(item, '[data-test-language-proficiency], [data-test-language-level]');
            const itemText = clean(item ? item.innerText : '');
            const proficiency = explicit || clean(itemText.replace(name, ''));
            const key = `${name}|${proficiency}`;
            if (name && !seenLanguages.has(key)) {
              seenLanguages.add(key);
              languages.push({name, proficiency});
            }
          }

          const publicLink = drawer.querySelector('[data-test-personal-info-profile-link]');
          const publicText = q(drawer, '[data-test-personal-info-profile-link-text]');
          return {
            public_url: publicText || (publicLink ? (publicLink.href || '') : ''),
            headline: q(drawer, '[data-test-row-lockup-headline]')
              || q(drawer, '[data-test-personal-info-profile-headline]'),
            positions,
            education,
            skills: unique(texts(drawer,
              '[data-test-profile-skills-card] [data-test-skill-entity-skill-name], '
              + '[data-test-profile-skills-card] .base-decoration__trigger-text')),
            languages,
            open_to_work: texts(drawer, '[data-test-open-candidate]'),
          };
        }
        """
    )
    return result if isinstance(result, dict) else {}


def close_drawer(page: Page) -> bool:
    drawer = page.locator(DRAWER_SELECTOR).first
    if not drawer.count() or not drawer.is_visible():
        return True
    close = page.locator(DETAIL_CLOSE_SELECTOR).first
    try:
        if close.count() and close.is_visible():
            close.click(timeout=3_000)
        else:
            page.keyboard.press("Escape")
        drawer.wait_for(state="hidden", timeout=5_000)
        return True
    except Exception:
        return False


def find_candidate_link(page: Page, recruiter_url: str) -> Locator | None:
    target = normalize_url(recruiter_url)
    links = page.locator(CARD_SELECTOR)
    for index in range(links.count()):
        link = links.nth(index)
        try:
            if normalize_url(link.get_attribute("href") or "") == target:
                return link
        except Exception:
            continue
    return None


def capture_candidate_detail(page: Page, candidate: Candidate) -> None:
    if not close_drawer(page):
        raise RuntimeError("无法关闭之前打开的详情抽屉")
    link = find_candidate_link(page, candidate.recruiter_url)
    if link is None:
        page.evaluate("window.scrollTo(0, 0)")
        scroll_results(page)
        link = find_candidate_link(page, candidate.recruiter_url)
    if link is None:
        raise RuntimeError("当前页面找不到候选人卡片")

    link.scroll_into_view_if_needed(timeout=5_000)
    link.click(timeout=5_000)
    drawer = page.locator(DRAWER_SELECTOR).first
    drawer.wait_for(state="visible", timeout=12_000)
    if not wait_for_drawer_stable(drawer):
        raise RuntimeError("详情内容未稳定加载")
    load_full_drawer(drawer)
    detail = extract_drawer_detail(drawer)

    candidate.public_url = normalize_public_url(detail.get("public_url", ""))
    candidate.headline = clean_text(detail.get("headline")) or candidate.headline
    candidate.positions = [Position(**item) for item in detail.get("positions", [])]
    candidate.education = [Education(**item) for item in detail.get("education", [])]
    candidate.languages = [Language(**item) for item in detail.get("languages", [])]
    candidate.open_to_work = unique_texts(detail.get("open_to_work", []))
    detail_skills = split_skills(detail.get("skills", []))
    candidate.core_skills = detail_skills or candidate.core_skills
    candidate.native_chinese = infer_native_chinese(candidate.languages)
    if candidate.positions:
        candidate.current_title = candidate.positions[0].title or candidate.current_title
        candidate.current_company = candidate.positions[0].company or candidate.current_company
        candidate.location = candidate.location or candidate.positions[0].location
    if not close_drawer(page):
        raise RuntimeError("详情已读取，但无法关闭详情抽屉")
