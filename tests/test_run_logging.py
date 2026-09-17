from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from linkedin_talent import cli
from linkedin_talent.run_logging import capture_run_output, run_log_path


class RunLoggingTest(unittest.TestCase):
    def test_stdout_and_stderr_are_written_to_one_log(self) -> None:
        console_out = io.StringIO()
        console_err = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "logs" / "run.log"
            with patch.object(sys, "stdout", console_out), patch.object(sys, "stderr", console_err):
                with capture_run_output(path):
                    print("normal output")
                    print("error output", file=sys.stderr)
            content = path.read_text(encoding="utf-8")

        self.assertIn("normal output", content)
        self.assertIn("error output", content)
        self.assertIn("normal output", console_out.getvalue())
        self.assertIn("error output", console_err.getvalue())

    def test_log_path_uses_logs_directory_and_unique_timestamp(self) -> None:
        path = run_log_path(Path("/project"))
        self.assertEqual(path.parent, Path("/project/logs"))
        self.assertRegex(path.name, r"^run_\d{8}_\d{6}_\d{6}\.log$")

    def test_main_writes_validation_errors_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "logs" / "run.log"
            with patch.object(cli, "run_log_path", return_value=path), \
                 patch.object(sys, "argv", ["linkedin_talent_get.py", "--max-per-shard", "1001"]):
                exit_code = cli.main()
            content = path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 2)
        self.assertIn("--max-per-shard 必须在 1 到 1000 之间", content)
        self.assertIn("退出码：2", content)


if __name__ == "__main__":
    unittest.main()
