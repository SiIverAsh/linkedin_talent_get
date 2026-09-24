#!/usr/bin/env python3
"""Scrape a fixed surname query without using the old shard plan."""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from linkedin_talent.cli import build_parser, run
from linkedin_talent.run_logging import capture_run_output, run_log_path
from linkedin_talent.text import normalize_cli_url


SURNAMES = (
    "Sam", "San", "Sau", "Se", "Seck", "See", "Sei", "Sek", "Shek",
    "Sheung", "Shih", "Shing", "Shiu", "Shue", "Shuen", "Shuk", "Shum",
    "Shun", "Sik", "Sim", "Sin", "Sit", "Siu", "So", "Suen", "Suet",
    "Sum", "Sung", "Sze", "Tak", "Tam", "Tat", "Tau", "Tin", "Ting",
    "Tip", "Tit", "To", "Tsai", "Tsam", "Tsang", "Tse", "Tsim", "Tso",
    "Tsoi", "Tsui", "Tuen", "Tung", "Ung", "Uy", "Vong", "Wah", "Wai",
    "Wat", "Wing", "Wong", "Woo", "Woon", "Wui", "Wun", "Wut", "Yam",
    "Yap", "Yat", "Yau", "Yee", "Yei", "Yen", "Yeuk", "Yeung", "Yick",
    "Yik", "Yim", "Yip", "Yiu", "Yuen", "Yuet", "Yuk", "Yun",
)


def _write_new_plan(plan_path: Path, base_url: str) -> None:
    plan_path.write_text(
        json.dumps(
            {
                "version": 4,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "updated_at": "",
                "criteria": None,
                "base_url": normalize_cli_url(base_url),
                "pending": [list(SURNAMES)],
                "completed": [],
                "unresolved": [],
                "active": None,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _ensure_one_batch(plan_path: Path) -> None:
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
        surnames = list(SURNAMES)
    plan["active"] = None
    plan["pending"] = [surnames]
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.url:
        parser.error("此新任务必须提供 --url，不会读取旧分片计划")
    if args.fresh:
        parser.error("此入口使用独立新计划；如需重跑，请删除本脚本生成的 selected 文件后再启动")

    project_dir = Path(__file__).resolve().parent
    output = (args.output or project_dir / "linkedin_selected_surnames.csv").resolve()
    print(f"联合姓氏查询：{' OR '.join(SURNAMES)}")
    plan_path = output.with_suffix(".shards.json")
    surname_path = output.with_suffix(".surnames.json")
    if not plan_path.exists():
        _write_new_plan(plan_path, args.url)
    else:
        _ensure_one_batch(plan_path)
    surname_path.write_text(json.dumps(list(SURNAMES), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    args.output = output
    args.plan = plan_path
    args.surnames = surname_path
    args.no_details = False
    args.no_expand_details = True
    args.all_remaining_surnames = True
    if args.partial_marker is None:
        args.partial_marker = output.with_suffix(".partial.jsonl")
    return run(args)


def main() -> int:
    project_dir = Path(__file__).resolve().parent
    log_path = run_log_path(project_dir)
    with capture_run_output(log_path):
        print(f"运行日志：{log_path}")
        print(f"本次固定姓氏数量：{len(SURNAMES)}")
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