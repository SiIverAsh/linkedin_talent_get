"""Command-line workflow for a standard Recruiter export."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
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
from .run_logging import capture_run_output, run_log_path
from .scraper import capture_candidate_detail, close_drawer, extract_card_candidates, scroll_results
from .sharding import load_or_create_plan, load_surnames
from .surname_workflow import read_result_count, run_surname_shards, should_use_surname_sharding
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
    parser.add_argument("--max-candidates", type=int, default=10000, help="最多采集人数（默认 10000）")
    parser.add_argument("--max-per-shard", type=int, default=900, help="姓氏分片安全阈值（默认 900）")
    parser.add_argument(
        "--surnames",
        type=Path,
        default=project_dir / "chinese_surnames.json",
        help="自动分片使用的姓氏 JSON",
    )
    parser.add_argument("--plan", type=Path, help="姓氏分片计划及进度文件")
    parser.add_argument("--no-surname-sharding", action="store_true", help="禁用逐个姓氏的自动分片")
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
    if not 1 <= args.max_per_shard <= 1000:
        raise ValueError("--max-per-shard 必须在 1 到 1000 之间")
    if args.output and args.output.suffix.casefold() not in {".csv", ".xlsx"}:
        raise ValueError("--output 仅支持 .csv 或 .xlsx 文件")


def run(args: argparse.Namespace) -> int:
    validate_args(args)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = (args.output or Path(f"linkedin_candidates_{timestamp}.csv")).resolve()
    checkpoint, checkpoint_source = resolve_checkpoint_paths(output, args.resume)
    plan_path = (args.plan or output.with_suffix(".shards.json")).resolve()
    profile_dir = args.profile_dir.resolve()

    saved_plan: dict = {}
    if args.fresh:
        plan_path.unlink(missing_ok=True)
    elif plan_path.exists():
        saved_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_available = bool(saved_plan)

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
    active_page_url = str((saved_plan.get("active") or {}).get("page_url", ""))
    requested_url = normalize_cli_url(args.url) if args.url else ""
    start_url = active_page_url or requested_url or str(saved_plan.get("base_url", "")) or checkpoint_url
    interrupted = False
    used_sharding = False

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

            saved_base_count = saved_plan.get("base_result_count")
            initial_count = (
                int(saved_base_count)
                if saved_base_count is not None
                else read_result_count(page)
            )
            count_label = (
                "基础搜索结果总数"
                if saved_base_count is not None or not plan_available
                else "当前分片结果总数"
            )
            print(f"\n{count_label}：{initial_count} results")
            used_sharding = should_use_surname_sharding(
                initial_count,
                plan_available,
                args.no_surname_sharding,
            )
            if used_sharding:
                if not plan_available:
                    print("启用逐个姓氏搜索。")
                surnames = load_surnames(args.surnames.resolve())
                base_url = requested_url or str(saved_plan.get("base_url", "")) or page.url
                plan = load_or_create_plan(plan_path, base_url, surnames, args.fresh)
                if not plan_available:
                    plan["base_result_count"] = initial_count
                run_surname_shards(
                    page=page,
                    records=records,
                    output=output,
                    checkpoint=checkpoint,
                    plan=plan,
                    plan_path=plan_path,
                    max_per_shard=args.max_per_shard,
                    max_pages=args.max_pages,
                    max_candidates=args.max_candidates,
                    no_details=args.no_details,
                )
                unresolved = len(plan.get("unresolved", []))
                if unresolved:
                    print(f"仍有 {unresolved} 个超限单姓分片，详见计划文件。")

            while not used_sharding and completed_pages < args.max_pages:
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
    if used_sharding:
        print(f"分片计划：{plan_path}")
    return 130 if interrupted else 0


def _execute() -> int:
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


def main() -> int:
    project_dir = Path(__file__).resolve().parent.parent
    log_path = run_log_path(project_dir)
    with capture_run_output(log_path):
        print(f"运行日志：{log_path}")
        print(f"开始时间：{datetime.now().astimezone().isoformat(timespec='seconds')}")
        try:
            exit_code = _execute()
        except SystemExit as error:
            exit_code = int(error.code or 0)
        except KeyboardInterrupt:
            print("\n收到 Ctrl+C，运行已中止。", file=sys.stderr)
            exit_code = 130
        except Exception:
            print("未处理错误：", file=sys.stderr)
            traceback.print_exc()
            exit_code = 1
        print(f"结束时间：{datetime.now().astimezone().isoformat(timespec='seconds')}，退出码：{exit_code}")
        return exit_code
