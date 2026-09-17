"""Per-run console tee logging for the command-line entry point."""

from __future__ import annotations

import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Iterator, TextIO


class Tee:
    def __init__(self, console: TextIO, log: TextIO) -> None:
        self.console = console
        self.log = log

    def write(self, value: str) -> int:
        self.console.write(value)
        self.log.write(value)
        return len(value)

    def flush(self) -> None:
        self.console.flush()
        self.log.flush()

    def isatty(self) -> bool:
        return self.console.isatty()

    @property
    def encoding(self) -> str | None:
        return self.console.encoding


def run_log_path(project_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return project_dir / "logs" / f"run_{timestamp}.log"


@contextmanager
def capture_run_output(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", buffering=1) as log:
        with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
            yield
