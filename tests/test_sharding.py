from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from linkedin_talent.sharding import load_or_create_plan, parse_human_count, split_group
from linkedin_talent.surname_workflow import run_surname_shards, should_use_surname_sharding


class ShardingPrimitivesTest(unittest.TestCase):
    def test_count_parsing_and_split(self) -> None:
        self.assertEqual(parse_human_count("About 1,000+ results"), 1001)
        self.assertEqual(parse_human_count("共 1.2 万位候选人"), 12000)
        self.assertEqual(split_group(["Ai", "An", "Ao", "Ba"]), (["Ai", "An"], ["Ao", "Ba"]))

    def test_auto_mode_threshold_and_resume(self) -> None:
        self.assertTrue(should_use_surname_sharding(1000, False, False))
        self.assertTrue(should_use_surname_sharding(1001, False, False))
        self.assertTrue(should_use_surname_sharding(1, True, False))
        self.assertFalse(should_use_surname_sharding(5000, True, True))

    def test_plan_can_be_created_without_target_company_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.shards.json"
            plan = load_or_create_plan(path, "https://www.linkedin.com/talent/search?start=25", ["Ai", "An"], True)
            self.assertIsNone(plan["criteria"])
            self.assertEqual(plan["pending"], [["Ai"], ["An"]])
            self.assertIn("start=0", plan["base_url"])


class AdaptiveWorkflowTest(unittest.TestCase):
    def test_single_surname_shards_are_scraped(self) -> None:
        plan = {
            "base_url": "https://www.linkedin.com/talent/search?start=0",
            "pending": [["Ai"], ["An"]],
            "completed": [],
            "unresolved": [],
            "active": None,
        }
        counts = iter([500, 600])

        with tempfile.TemporaryDirectory() as directory, \
             patch("linkedin_talent.surname_workflow.goto_search_page"), \
             patch("linkedin_talent.surname_workflow.apply_surname_group"), \
             patch("linkedin_talent.surname_workflow.read_result_count", side_effect=lambda page: next(counts)), \
             patch("linkedin_talent.surname_workflow.persist_plan"), \
             patch("linkedin_talent.surname_workflow._scrape_shard", return_value=(0, 1, True)):
            run_surname_shards(
                page=SimpleNamespace(url="https://www.linkedin.com/talent/search?start=0"),
                records=[], output=Path(directory) / "out.csv",
                checkpoint=Path(directory) / "out.checkpoint.jsonl", plan=plan,
                plan_path=Path(directory) / "out.shards.json", max_per_shard=900,
                max_pages=40, max_candidates=10_000, no_details=True,
            )

        self.assertEqual(plan["pending"], [])
        self.assertEqual(len(plan["completed"]), 2)
        self.assertEqual([item["result_count"] for item in plan["completed"]], [500, 600])

    def test_single_oversized_surname_is_scraped(self) -> None:
        plan = {
            "base_url": "https://www.linkedin.com/talent/search?start=0",
            "pending": [["Wang"]],
            "completed": [],
            "unresolved": [],
            "active": None,
        }

        with tempfile.TemporaryDirectory() as directory, \
             patch("linkedin_talent.surname_workflow.goto_search_page"), \
             patch("linkedin_talent.surname_workflow.apply_surname_group"), \
             patch("linkedin_talent.surname_workflow.read_result_count", return_value=1001), \
               patch("linkedin_talent.surname_workflow.persist_plan"), \
               patch("linkedin_talent.surname_workflow._scrape_shard", return_value=(25, 40, True)) as scrape:
            run_surname_shards(
                page=SimpleNamespace(url="https://www.linkedin.com/talent/search?start=0"),
                records=[], output=Path(directory) / "out.csv",
                checkpoint=Path(directory) / "out.checkpoint.jsonl", plan=plan,
                plan_path=Path(directory) / "out.shards.json", max_per_shard=900,
                max_pages=40, max_candidates=10_000, no_details=True,
            )

        self.assertEqual(plan["pending"], [])
        self.assertEqual(len(plan["completed"]), 1)
        self.assertEqual(plan["unresolved"], [])
        self.assertEqual(plan["completed"][0]["surnames"], ["Wang"])
        self.assertEqual(plan["completed"][0]["pages"], 41)
        self.assertEqual(scrape.call_args.args[1], 1001)
        self.assertEqual(scrape.call_args.args[9], 40)

    def test_active_checkpoint_persists_shard_size_index_and_page(self) -> None:
        plan = {
            "version": 4,
            "base_url": "https://www.linkedin.com/talent/search?start=0",
            "pending": [["Ai"]],
            "completed": [],
            "unresolved": [],
            "active": None,
        }

        with tempfile.TemporaryDirectory() as directory, \
             patch("linkedin_talent.surname_workflow.goto_search_page"), \
             patch("linkedin_talent.surname_workflow.apply_surname_group"), \
             patch("linkedin_talent.surname_workflow.read_result_count", return_value=75), \
             patch("linkedin_talent.surname_workflow._scrape_shard", return_value=(10, 0, False)):
            plan_path = Path(directory) / "out.shards.json"
            run_surname_shards(
                page=SimpleNamespace(url="https://www.linkedin.com/talent/search?start=0"),
                records=[], output=Path(directory) / "out.csv",
                checkpoint=Path(directory) / "out.checkpoint.jsonl", plan=plan,
                plan_path=plan_path, max_per_shard=900, max_pages=40,
                max_candidates=10_000, no_details=True,
            )
            saved = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["settings"]["max_per_shard"], 900)
        self.assertEqual(saved["active"]["shard_index"], 1)
        self.assertEqual(saved["active"]["surname_count"], 1)
        self.assertEqual(saved["active"]["result_count"], 75)
        self.assertEqual(saved["active"]["current_page"], 1)
        self.assertEqual(saved["active"]["total_pages"], 3)

    def test_resume_starts_at_saved_shard_page(self) -> None:
        resume_url = "https://www.linkedin.com/talent/search?start=50"
        plan = {
            "version": 4,
            "base_url": "https://www.linkedin.com/talent/search?start=0",
            "pending": [["Ai"]],
            "completed": [],
            "unresolved": [],
            "active": {
                "id": "surname:ai",
                "surnames": ["Ai"],
                "result_count": 75,
                "next_page": 3,
                "page_url": resume_url,
            },
        }

        with tempfile.TemporaryDirectory() as directory, \
             patch("linkedin_talent.surname_workflow.goto_search_page") as goto, \
             patch("linkedin_talent.surname_workflow.apply_surname_group") as apply_group, \
             patch("linkedin_talent.surname_workflow._scrape_shard", return_value=(0, 0, False)) as scrape:
            run_surname_shards(
                page=SimpleNamespace(url=resume_url), records=[],
                output=Path(directory) / "out.csv",
                checkpoint=Path(directory) / "out.checkpoint.jsonl", plan=plan,
                plan_path=Path(directory) / "out.shards.json", max_per_shard=900,
                max_pages=40, max_candidates=10_000, no_details=True,
            )

        goto.assert_called_once_with(unittest.mock.ANY, resume_url)
        apply_group.assert_not_called()
        self.assertEqual(scrape.call_args.args[8], 3)
        self.assertEqual(plan["active"]["shard_index"], 1)
        self.assertEqual(plan["active"]["surname_count"], 1)
        self.assertEqual(plan["active"]["current_page"], 3)
        self.assertEqual(plan["active"]["total_pages"], 3)


if __name__ == "__main__":
    unittest.main()
