"""Output-format routing and periodic snapshot policy."""

from __future__ import annotations

from pathlib import Path

from .csv_export import export_csv
from .excel import export_excel
from .models import Candidate

DEFAULT_EXPORT_BATCH_SIZE = 300


def export_records(records: list[Candidate], output: Path) -> None:
    suffix = output.suffix.casefold()
    if suffix == ".csv":
        export_csv(records, output)
    else:
        export_excel(records, output)


def export_batch_if_due(
    records: list[Candidate],
    output: Path,
    batch_size: int = DEFAULT_EXPORT_BATCH_SIZE,
) -> bool:
    if not records or batch_size < 1 or len(records) % batch_size:
        return False
    export_records(records, output)
    return True
