#!/usr/bin/env python3
"""Search all remaining surnames in one Recruiter surname query."""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from linkedin_talent.cli import build_parser, run
from linkedin_talent.run_logging import capture_run_output, run_log_path


def _prepare_one_batch(plan_path: Path) -> int:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    active = plan.get("active") or {}
    groups = []
    if active.get("surnames"):
        groups.append(active["surnames"])
    groups.extend(plan.get("pending", []))

    surnames = []
    seen = set()
    for group in groups:
        for surname in group:
            value = str(surname).strip()
            if value and value.casefold() not in seen:
                surnames.append(value)
                seen.add(value.casefold())
    if not surnames:
        raise ValueError("分片计划中没有剩余姓氏")

    # Re-scan from page 1 while reusing the checkpoint; URL-level deduplication
    # prevents already saved candidates from being written again.
    plan["active"] = None
    plan["pending"] = [surnames]
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(surnames)


def execute() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.fresh:
        parser.error("此入口只允许续跑已有分片计划，不能使用 --fresh")

    output = (args.output or Path("linkedin_ON10kcandidates.csv")).resolve()
    plan_path = (args.plan or output.with_suffix(".shards.json")).resolve()
    if not plan_path.exists():
        parser.error(f"未找到分片计划：{plan_path}")

    count = _prepare_one_batch(plan_path)
    print(f"本次将一次性输入并处理 {count} 个后续姓氏")
    args.output = output
    args.plan = plan_path
    args.all_remaining_surnames = True
    args.no_details = False
    args.no_expand_details = True
    if args.partial_marker is None:
        args.partial_marker = output.with_suffix(".partial.jsonl")
    return run(args)


def main() -> int:
    project_dir = Path(__file__).resolve().parent
    log_path = run_log_path(project_dir)
    with capture_run_output(log_path):
        print(f"运行日志：{log_path}")
        print(f"开始时间：{datetime.now().astimezone().isoformat(timespec='seconds')}")
        try:
            exit_code = execute()
        except SystemExit as error:
            exit_code = int(error.code or 0)
        except (ValueError, RuntimeError, json.JSONDecodeError) as error:
            print(f"错误：{error}", file=sys.stderr)
            exit_code = 2
        except PlaywrightTimeoutError as error:
            print(f"等待页面超时：{error}", file=sys.stderr)
            exit_code = 3
        except KeyboardInterrupt:
            print("\n收到 Ctrl+C，运行已中止。", file=sys.stderr)
            exit_code = 130
        except Exception:
            print("未处理错误：", file=sys.stderr)
            traceback.print_exc()
            exit_code = 1
        print(f"结束时间：{datetime.now().astimezone().isoformat(timespec='seconds')}，退出码：{exit_code}")
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())