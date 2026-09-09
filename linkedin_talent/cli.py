"""Command-line workflow for a standard Recruiter export."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from .auth import install_cookies
from .browser import advance_to_next_page, check_access_page, open_context
from .constants import CARD_SELECTOR
from .output import DEFAULT_EXPORT_BATCH_SIZE, export_batch_if_due, export_records
from .models import Candidate
from .persistence import (
    load_checkpoint,
    reset_checkpoint,
    resolve_checkpoint_paths,
    save_checkpoint,
)
from .scraper import capture_candidate_detail, close_drawer, extract_card_candidates, scroll_results
from .text import clean_text, normalize_cli_url, normalize_url, short_delay


def build_parser() -> argparse.ArgumentParser:
    project_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="将已获授权访问的 LinkedIn Recruiter 搜索结果导出为 CSV 或 Excel。"
    )
    parser.add_argument("--url", help="LinkedIn Recruiter 搜索结果页 URL")
    parser.add_argument("--output", type=Path, help="输出路径（支持 .csv 和 .xlsx）")
    parser.add_argument("--profile-dir", type=Path, default=Path(".browser-profile"), help="浏览器用户目录")
    parser.add_argument("--max-pages", type=int, default=40, help="最多采集页数（默认 40）")
    parser.add_argument("--max-candidates", type=int, default=1000, help="最多采集人数（默认 1000）")
    parser.add_argument("--no-details", action="store_true", help="不打开详情抽屉，仅采集搜索结果卡片")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口（默认无头运行）")
    parser.add_argument(
        "--cookies",
        type=Path,
        default=project_dir / "config" / "cookie.json",
        help="浏览器导出的 Cookie JSON（默认 config/cookie.json）",
    )
    parser.add_argument("--no-cookies", action="store_true", help="不导入 Cookie 文件")
    parser.add_argument("--resume", type=Path, help="指定要读取和更新的断点文件")
    parser.add_argument("--fresh", action="store_true", help="忽略已有断点，从当前搜索页重新开始")
    parser.add_argument("--skip-prompt", action="store_true", help="不等待终端确认，页面载入后直接开始")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.max_pages < 1:
        raise ValueError("--max-pages 必须大于 0")
    if args.max_candidates < 1:
        raise ValueError("--max-candidates 必须大于 0")
    if args.output and args.output.suffix.casefold() not in {".csv", ".xlsx"}:
        raise ValueError("--output 仅支持 .csv 或 .xlsx 文件")


def run(args: argparse.Namespace) -> int:
    validate_args(args)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = (args.output or Path(f"linkedin_candidates_{timestamp}.csv")).resolve()
    checkpoint, checkpoint_source = resolve_checkpoint_paths(output, args.resume)
    profile_dir = args.profile_dir.resolve()

    records: list[Candidate] = []
    completed_pages = 0
    checkpoint_url = ""
    if args.fresh:
        reset_checkpoint(checkpoint)
    elif checkpoint_source.exists():
        records, completed_pages, checkpoint_url = load_checkpoint(checkpoint_source)
        print(f"已读取断点：{len(records)} 人，已完成 {completed_pages} 页")
        if checkpoint_source != checkpoint:
            print(f"旧版断点将在本次运行中迁移为增量 JSONL：{checkpoint}")

    seen = {normalize_url(record.recruiter_url) for record in records if record.recruiter_url}
    start_url = normalize_cli_url(args.url) if args.url else checkpoint_url
    interrupted = False

    with sync_playwright() as playwright:
        context = open_context(playwright, profile_dir, headless=not args.headed)
        if not args.no_cookies:
            cookie_path = args.cookies.resolve()
            if cookie_path.exists():
                count = install_cookies(context, cookie_path)
                print(f"已载入 {count} 条 LinkedIn Cookie：{cookie_path}")
            else:
                print(f"未找到 Cookie 文件，将使用浏览器配置中的登录状态：{cookie_path}")
        page = context.pages[0] if context.pages else context.new_page()
        try:
            if start_url:
                print("正在打开 LinkedIn Recruiter 页面……")
                page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)

            if not args.skip_prompt and args.headed:
                print("\n请在打开的浏览器中完成登录，并进入候选人搜索结果页。")
                input("页面准备好后，回到这里按 Enter 开始采集：")

            check_access_page(page)
            page.locator(CARD_SELECTOR).first.wait_for(state="visible", timeout=20_000)

            while completed_pages < args.max_pages:
                if len(records) >= args.max_candidates:
                    break
                page_number = completed_pages + 1
                print(f"\n第 {page_number} 页：加载候选人卡片……")
                check_access_page(page)
                loaded = scroll_results(page)
                page_candidates = extract_card_candidates(page)
                print(f"检测到 {loaded} 个链接，解析出 {len(page_candidates)} 位候选人")

                new_on_page = 0
                for candidate in page_candidates:
                    key = normalize_url(candidate.recruiter_url)
                    if key in seen:
                        continue
                    if len(records) >= args.max_candidates:
                        break

                    ordinal = len(records) + 1
                    print(f"  [{ordinal}/{args.max_candidates}] {candidate.name}")
                    if not args.no_details:
                        last_error = ""
                        for attempt in range(1, 3):
                            try:
                                capture_candidate_detail(page, candidate)
                                last_error = ""
                                break
                            except Exception as error:
                                last_error = clean_text(error)
                                close_drawer(page)
                                if attempt < 2:
                                    print(f"    详情读取失败，准备重试：{last_error}")
                                    short_delay(1.0, 1.8)
                        candidate.detail_error = last_error
                        if last_error:
                            print(f"    [warn] 详情未获取：{last_error}")

                    records.append(candidate)
                    seen.add(key)
                    new_on_page += 1
                    save_checkpoint(checkpoint, records, completed_pages, page.url)
                    if export_batch_if_due(records, output):
                        print(f"已累计采集 {len(records)} 人，已写入：{output}")
                    short_delay(0.9, 1.7)

                completed_pages += 1
                save_checkpoint(checkpoint, records, completed_pages, page.url)
                print(f"第 {page_number} 页完成：新增 {new_on_page} 人，累计 {len(records)} 人")

                if len(records) >= args.max_candidates or completed_pages >= args.max_pages:
                    break
                if not advance_to_next_page(page, completed_pages):
                    print("没有可用的下一页，采集结束。")
                    break
                save_checkpoint(checkpoint, records, completed_pages, page.url)
                short_delay(1.2, 2.1)

        except KeyboardInterrupt:
            interrupted = True
            print("\n收到 Ctrl+C，正在保存当前进度……")
        finally:
            if records:
                save_checkpoint(checkpoint, records, completed_pages, page.url)
                if len(records) % DEFAULT_EXPORT_BATCH_SIZE or not output.exists():
                    export_records(records, output)
            context.close()

    print(f"\n已导出 {len(records)} 位候选人：{output}")
    print(f"断点文件：{checkpoint}")
    return 130 if interrupted else 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run(args)
    except (ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    except PlaywrightTimeoutError as error:
        print(f"等待页面超时：{error}", file=sys.stderr)
        print("请确认已登录 LinkedIn Recruiter，并停留在候选人搜索结果页。", file=sys.stderr)
        return 3
