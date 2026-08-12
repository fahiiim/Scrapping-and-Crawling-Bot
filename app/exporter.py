"""CSV output helpers."""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from typing import Iterable, Mapping

from .parser import FIELDS


def _clean_rows(rows: Iterable[Mapping[str, object]]) -> list[dict[str, str]]:
    clean: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        normalized = {field: str(row.get(field, "") or "").strip() for field in FIELDS}
        if (
            not normalized["Name"]
            or not normalized["URL"]
            or normalized["Status"] not in {"active", "out_of_stock"}
        ):
            continue
        key = normalized["URL"]
        if key in seen:
            continue
        seen.add(key)
        clean.append(normalized)
    return clean


def write_csv(rows: Iterable[Mapping[str, object]], output_path: Path) -> int:
    clean = _clean_rows(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=output_path.parent,
            prefix=f".{output_path.stem}.",
            suffix=".csv",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            writer = csv.DictWriter(
                handle,
                fieldnames=list(FIELDS),
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(clean)
        os.replace(temporary_path, output_path)
    except BaseException:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
        raise
    return len(clean)


def read_csv(output_path: Path) -> list[dict[str, str]]:
    if not output_path.exists():
        return []
    with output_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [
            {field: str(row.get(field, "") or "").strip() for field in FIELDS}
            for row in reader
        ]
    return _clean_rows(rows)


def clean_existing_csv(output_path: Path) -> int:
    return write_csv(read_csv(output_path), output_path)
