"""Materialize presentation-specific image layouts from frozen release assets."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from polycomp.data import assets_by_id, problems, protocol, resolve_release_path
from polycomp.payloads import PRESENTATIONS


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_rows(problem_identifier: str, presentation: str) -> list[dict[str, Any]]:
    case = assets_by_id()[problem_identifier]
    if presentation == "single_image":
        return [case["single_image"]]
    return list(case["crops"])


def generate_presentations(
    output_dir: Path,
    problem_identifiers: Iterable[str],
    presentations: Iterable[str],
) -> dict[str, int | str]:
    selected_problems = list(problem_identifiers)
    selected_presentations = list(presentations)
    known = {row["problem_identifier"] for row in problems()}
    unknown = sorted(set(selected_problems) - known)
    if unknown:
        raise ValueError(f"Unknown problem(s): {', '.join(unknown)}")
    invalid_presentations = sorted(set(selected_presentations) - set(PRESENTATIONS))
    if invalid_presentations:
        raise ValueError(f"Unknown presentation(s): {', '.join(invalid_presentations)}")

    copied = 0
    for presentation in selected_presentations:
        filenames = protocol()["presentations"][presentation]["submitted_filenames"]
        for problem_identifier in selected_problems:
            sources = _source_rows(problem_identifier, presentation)
            destination_dir = output_dir / presentation / problem_identifier
            destination_dir.mkdir(parents=True, exist_ok=True)
            for source, filename in zip(sources, filenames, strict=True):
                source_path = resolve_release_path(source["path"])
                if sha256_file(source_path) != source["file_sha256"]:
                    raise ValueError(f"Frozen source hash mismatch: {source['path']}")
                destination = destination_dir / filename
                shutil.copyfile(source_path, destination)
                if sha256_file(destination) != source["file_sha256"]:
                    raise ValueError(f"Generated file hash mismatch: {destination}")
                copied += 1
    return {
        "output": str(output_dir.resolve()),
        "problems": len(selected_problems),
        "presentations": len(selected_presentations),
        "files": copied,
    }
