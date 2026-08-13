"""Read the frozen release data without importing benchmark-internal tooling."""

from __future__ import annotations

import csv
import json
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


@cache
def protocol() -> dict[str, Any]:
    return json.loads((DATA_DIR / "protocol.json").read_text(encoding="utf-8"))


@cache
def provenance() -> dict[str, Any]:
    return json.loads((DATA_DIR / "provenance.json").read_text(encoding="utf-8"))


@cache
def assets() -> dict[str, Any]:
    return json.loads((DATA_DIR / "assets.json").read_text(encoding="utf-8"))


@cache
def rendering_reference() -> dict[str, Any]:
    return json.loads((DATA_DIR / "rendering_reference.json").read_text(encoding="utf-8"))


@cache
def problems() -> tuple[dict[str, Any], ...]:
    rows = []
    with (DATA_DIR / "problems.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return tuple(rows)


@cache
def problems_by_id() -> dict[str, dict[str, Any]]:
    return {row["problem_identifier"]: row for row in problems()}


@cache
def assets_by_id() -> dict[str, dict[str, Any]]:
    return {row["problem_identifier"]: row for row in assets()["cases"]}


@cache
def request_hashes() -> tuple[dict[str, str], ...]:
    with (DATA_DIR / "request_hashes.csv").open(newline="", encoding="utf-8") as handle:
        return tuple(csv.DictReader(handle))


@cache
def request_hashes_by_key() -> dict[tuple[str, str, str], dict[str, str]]:
    return {
        (row["provider"], row["problem_identifier"], row["presentation"]): row
        for row in request_hashes()
    }


def resolve_release_path(relative_path: str) -> Path:
    path = (ROOT / relative_path).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"Release path escapes the repository: {relative_path}")
    return path
