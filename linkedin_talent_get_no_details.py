#!/usr/bin/env python3
"""Resume the existing Recruiter run with visible details only."""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from linkedin_talent.cli import build_parser, run
from linkedin_talent.run_logging import capture_run_output, run_log_path


def execute() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.fresh:
        parser.error("此入口只允许读取已有断点，不能使用 --fresh")
    args.no_details = False
    args.no_expand_details = True
    if args.partial_marker is None:
        output = args.output or Path("linkedin_candidates.csv")
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
