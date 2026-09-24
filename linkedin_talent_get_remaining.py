#!/usr/bin/env python3
"""Resume the existing run with all remaining surname shards."""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from linkedin_talent.cli import build_parser, run
from linkedin_talent.run_logging import capture_run_output, run_log_path


def _remaining_surname_file(plan_path: Path, output: Path) -> Path:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    active = plan.get("active") or {}
    groups: list[list[str]] = []
    if active.get("surnames"):
        groups.append([str(item) for item in active["surnames"]])
    groups.extend(
        [[str(item) for item in group] for group in plan.get("pending", [])]
    )
    surnames = [surname for group in groups for surname in group]
    path = output.with_suffix(".remaining-surnames.json")
    path.write_text(json.dumps(surnames, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"后续姓氏关键词：{len(surnames)} 个，已写入：{path}")
    return path


def run_remaining_surnames(args, output: Path, plan_path: Path) -> int:
    """Continue the existing active shard and pending single-surname shards."""
    if not plan_path.exists():
        raise ValueError(f"未找到分片计划：{plan_path}")

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    active = plan.get("active") or {}
    pending = plan.get("pending") or []
    active_label = ", ".join(str(item) for item in active.get("surnames", [])) or "无"
    print(f"当前活动姓氏：{active_label}；待处理单姓氏分片：{len(pending)} 个")
    print("本次进程将连续处理以上全部待处理分片，不会在当前姓氏完成后退出。")

    args.output = output
    args.plan = plan_path
    args.surnames = _remaining_surname_file(plan_path, output)
    args.no_details = False
    args.no_expand_details = True
    if args.partial_marker is None:
        args.partial_marker = output.with_suffix(".partial.jsonl")
    return run(args)


def execute() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.fresh:
        parser.error("此入口只允许续跑已有分片计划，不能使用 --fresh")

    output = (args.output or Path("linkedin_ON10kcandidates.csv")).resolve()
    plan_path = (args.plan or output.with_suffix(".shards.json")).resolve()
    return run_remaining_surnames(args, output, plan_path)


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
            print("请确认已登录 LinkedIn Recruiter，并停留在候选人搜索结果页。", file=sys.stderr)
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