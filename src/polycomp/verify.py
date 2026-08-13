"""End-to-end verification for release assets, payloads, and reported scores."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image

from polycomp.data import (
    DATA_DIR,
    assets,
    assets_by_id,
    problems,
    protocol,
    provenance,
    rendering_reference,
    request_hashes,
    request_hashes_by_key,
    resolve_release_path,
)
from polycomp.generate import sha256_file
from polycomp.geometry import PROPER_CUBE_ROTATIONS, verify_problem_geometry
from polycomp.payloads import (
    PRESENTATIONS,
    PROVIDERS,
    build_payload,
    canonical_json_sha256,
    cell_sha256,
    historical_batch_bytes,
    logical_request_sha256,
)
from polycomp.render import SVG_FONT_FAMILY, render_svg_bytes
from polycomp.scoring import selected_option

MODELS = ("gpt-5.6-sol", "claude-fable-5", "gemini-3.1-pro-preview")


def _image_pixel_sha256(path: Path) -> str:
    with Image.open(path) as image:
        return hashlib.sha256(image.tobytes()).hexdigest()


def _verify_problem_manifests() -> dict[str, int]:
    rows = list(problems())
    if len(rows) != 120:
        raise ValueError(f"Expected 120 problems, found {len(rows)}")
    identifiers = [row["problem_identifier"] for row in rows]
    if len(set(identifiers)) != 120:
        raise ValueError("Duplicate problem identifiers")
    if sorted(int(row["primary_position"]) for row in rows) != list(range(1, 121)):
        raise ValueError("Primary positions are not exactly 1 through 120")

    geometry_proofs = 0
    option_proofs = 0
    for row in rows:
        path = resolve_release_path(row["manifest_path"])
        if sha256_file(path) != row["manifest_sha256"]:
            raise ValueError(f"Problem manifest hash mismatch: {path}")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("schema") != "polycomp-problem-v2":
            raise ValueError(f"Unexpected problem-manifest schema: {path}")
        if "provenance" in manifest:
            raise ValueError(f"Private source provenance found in problem manifest: {path}")
        if manifest["problem_identifier"] != row["problem_identifier"]:
            raise ValueError(f"Problem manifest identity mismatch: {path}")
        target = manifest["geometry"]["target_cells"]
        if len(target) != int(row["block_count"]):
            raise ValueError(f"Target block count mismatch: {row['problem_identifier']}")
        options = manifest["geometry"]["options"]
        if set(options) != {"A", "B", "C", "D"}:
            raise ValueError(f"Option set mismatch: {row['problem_identifier']}")
        for option, pieces in options.items():
            if len(pieces["left"]) + len(pieces["right"]) != len(target):
                raise ValueError(
                    f"Option block count mismatch: {row['problem_identifier']}/{option}"
                )
        geometry_result = verify_problem_geometry(manifest)
        geometry_proofs += 1
        option_proofs += int(geometry_result["option_proofs"])
    return {
        "problems": 120,
        "problem_manifests": 120,
        "proper_cube_rotations": len(PROPER_CUBE_ROTATIONS),
        "geometry_proofs": geometry_proofs,
        "option_assembly_proofs": option_proofs,
    }


def _verify_assets() -> dict[str, int]:
    asset_manifest = assets()
    if asset_manifest.get("schema") != "polycomp-assets-v2":
        raise ValueError("Unexpected asset-manifest schema")
    if "source_hash_manifest" in asset_manifest:
        raise ValueError("Private source-manifest metadata found in asset manifest")
    if asset_manifest["case_count"] != 120 or len(asset_manifest["cases"]) != 120:
        raise ValueError("Asset manifest does not contain 120 cases")
    singles = 0
    crops = 0
    for problem_identifier, case in assets_by_id().items():
        single = case["single_image"]
        single_path = resolve_release_path(single["path"])
        if sha256_file(single_path) != single["file_sha256"]:
            raise ValueError(f"Single-image hash mismatch: {problem_identifier}")
        if _image_pixel_sha256(single_path) != single["pixel_sha256"]:
            raise ValueError(f"Single-image pixel hash mismatch: {problem_identifier}")
        with Image.open(single_path) as image:
            if list(image.size) != [single["width"], single["height"]]:
                raise ValueError(f"Single-image dimensions mismatch: {problem_identifier}")
            for crop in case["crops"]:
                crop_path = resolve_release_path(crop["path"])
                if sha256_file(crop_path) != crop["file_sha256"]:
                    raise ValueError(f"Crop hash mismatch: {problem_identifier}/{crop['role']}")
                if _image_pixel_sha256(crop_path) != crop["pixel_sha256"]:
                    raise ValueError(
                        f"Crop pixel hash mismatch: {problem_identifier}/{crop['role']}"
                    )
                expected_pixels = image.crop(tuple(crop["crop_box"]))
                with Image.open(crop_path) as committed_crop:
                    if expected_pixels.size != committed_crop.size:
                        raise ValueError(
                            f"Crop dimensions mismatch: {problem_identifier}/{crop['role']}"
                        )
                    if expected_pixels.tobytes() != committed_crop.tobytes():
                        raise ValueError(
                            f"Crop pixels differ from single image: "
                            f"{problem_identifier}/{crop['role']}"
                        )
                crops += 1
        singles += 1
    return {"single_images": singles, "crops": crops}


def _verify_rendering_reference() -> dict[str, int]:
    reference = rendering_reference()
    if reference["schema"] != "polycomp-rendering-reference-v2":
        raise ValueError("Unexpected rendering-reference schema")
    if "source_snapshot" in reference:
        raise ValueError("Private source-snapshot metadata found in rendering reference")
    if reference["dataset_id"] != protocol()["dataset_id"]:
        raise ValueError("Rendering-reference dataset identity mismatch")
    cases = reference["cases"]
    if reference["case_count"] != 120 or len(cases) != 120:
        raise ValueError("Rendering reference does not contain 120 cases")

    ordered_problems = sorted(problems(), key=lambda item: int(item["primary_position"]))
    expected_ids = [row["problem_identifier"] for row in ordered_problems]
    if [row["problem_identifier"] for row in cases] != expected_ids:
        raise ValueError("Rendering-reference problem order mismatch")
    if [int(row["primary_position"]) for row in cases] != list(range(1, 121)):
        raise ValueError("Rendering-reference primary positions are not 1 through 120")
    adaptation = reference["release_adaptation"]
    if adaptation["path"] != "src/polycomp/render.py":
        raise ValueError("Unexpected rendering implementation path")
    if sha256_file(resolve_release_path(adaptation["path"])) != adaptation["sha256"]:
        raise ValueError("Rendering implementation hash mismatch")
    fonts = reference["observed_release_replay_environment"]["fonts"]
    if fonts["bundled"] or fonts["hash_pinned_font_resource"]:
        raise ValueError("Rendering reference must document, not bundle or hash-pin, fonts")
    if len(fonts["reference_files"]) != 2:
        raise ValueError("Rendering reference must document the two reference Arial files")
    if fonts["svg_font_family"] != SVG_FONT_FAMILY:
        raise ValueError("Rendering-reference SVG font-family mismatch")

    tree = hashlib.sha256()
    total_bytes = 0
    for problem, case in zip(ordered_problems, cases, strict=True):
        manifest_path = resolve_release_path(problem["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        svg_bytes = render_svg_bytes(manifest)
        total_bytes += len(svg_bytes)
        if len(svg_bytes) != int(case["size_bytes"]):
            raise ValueError(f"Reconstructed SVG size mismatch: {problem['problem_identifier']}")
        if hashlib.sha256(svg_bytes).hexdigest() != case["file_sha256"]:
            raise ValueError(f"Reconstructed SVG hash mismatch: {problem['problem_identifier']}")
        identifier = problem["problem_identifier"].encode()
        tree.update(identifier)
        tree.update(b"\0")
        tree.update(len(svg_bytes).to_bytes(8, "big"))
        tree.update(svg_bytes)

    expected_tree = reference["svg_tree"]
    if int(expected_tree["case_count"]) != len(cases):
        raise ValueError("Reconstructed SVG tree case-count mismatch")
    if total_bytes != int(expected_tree["total_bytes"]):
        raise ValueError("Reconstructed SVG tree byte-count mismatch")
    if tree.hexdigest() != expected_tree["sha256"]:
        raise ValueError("Reconstructed SVG tree hash mismatch")
    return {"reference_svgs": len(cases), "rendering_reference_cases": len(cases)}


def _verify_requests() -> dict[str, int]:
    rows = list(request_hashes())
    expected_fields = [
        "provider",
        "model",
        "problem_identifier",
        "presentation",
        "historical_audit_payload_sha256",
        "historical_logical_request_sha256",
        "historical_cell_sha256",
        "release_derived_transmitted_body_sha256",
        "custom_id",
    ]
    if not rows or list(rows[0]) != expected_fields:
        raise ValueError("Unexpected historical request-hash columns")
    if len(rows) != 1080 or len(request_hashes_by_key()) != 1080:
        raise ValueError("Historical request hash matrix is not exactly 1,080 unique cells")
    ordered_problems = [
        row["problem_identifier"]
        for row in sorted(problems(), key=lambda item: int(item["primary_position"]))
    ]

    checked = 0
    for row in rows:
        provider = row["provider"]
        problem_identifier = row["problem_identifier"]
        presentation = row["presentation"]
        redacted_hash = canonical_json_sha256(
            build_payload(provider, problem_identifier, presentation, redact_images=True)
        )
        if redacted_hash != row["historical_audit_payload_sha256"]:
            raise ValueError(
                f"Audit payload hash mismatch: {provider}/{problem_identifier}/{presentation}"
            )
        if (
            logical_request_sha256(provider, problem_identifier, presentation)
            != row["historical_logical_request_sha256"]
        ):
            raise ValueError(
                f"Logical request hash mismatch: {provider}/{problem_identifier}/{presentation}"
            )
        calculated_cell_hash = cell_sha256(provider, problem_identifier, presentation)
        if calculated_cell_hash != row["historical_cell_sha256"]:
            raise ValueError(f"Cell hash mismatch: {provider}/{problem_identifier}/{presentation}")
        if row["custom_id"] != f"fsb_{calculated_cell_hash[:40]}":
            raise ValueError(f"Custom id mismatch: {provider}/{problem_identifier}/{presentation}")
        full_body_hash = canonical_json_sha256(
            build_payload(provider, problem_identifier, presentation)
        )
        if full_body_hash != row["release_derived_transmitted_body_sha256"]:
            raise ValueError(
                f"Transmitted body hash mismatch: {provider}/{problem_identifier}/{presentation}"
            )
        checked += 1

    release_provenance = provenance()
    expected_provenance_fields = {
        "schema",
        "dataset_id",
        "release_files",
        "historical_request_integrity",
        "historical_batch_inputs",
    }
    if set(release_provenance) != expected_provenance_fields:
        raise ValueError("Unexpected release-provenance fields")
    if release_provenance["schema"] != "polycomp-provenance-v2":
        raise ValueError("Unexpected release-provenance schema")
    if release_provenance["dataset_id"] != protocol()["dataset_id"]:
        raise ValueError("Release-provenance dataset identity mismatch")

    batches = release_provenance["historical_batch_inputs"]["batches"]
    expected_batch_fields = {
        "provider",
        "presentation",
        "request_count",
        "file_sha256",
        "size_bytes",
    }
    if any(set(row) != expected_batch_fields for row in batches):
        raise ValueError("Unexpected historical batch metadata fields")
    batches_by_key = {(row["provider"], row["presentation"]): row for row in batches}
    if len(batches) != 9 or len(batches_by_key) != 9:
        raise ValueError("Expected exactly nine historical batch inputs")
    for provider in PROVIDERS:
        for presentation in PRESENTATIONS:
            payload = historical_batch_bytes(provider, presentation, ordered_problems)
            batch = batches_by_key[(provider, presentation)]
            if int(batch["request_count"]) != 120:
                raise ValueError(
                    f"Historical batch request count mismatch: {provider}/{presentation}"
                )
            if len(payload) != int(batch["size_bytes"]):
                raise ValueError(f"Complete batch input size mismatch: {provider}/{presentation}")
            if hashlib.sha256(payload).hexdigest() != batch["file_sha256"]:
                raise ValueError(f"Complete batch input hash mismatch: {provider}/{presentation}")
    return {"request_cells": checked, "complete_batch_inputs": 9}


def _load_results() -> list[dict[str, str]]:
    with (DATA_DIR / "results.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        expected_fields = [
            "model",
            "problem_identifier",
            "presentation",
            "model_response",
            "correct_response",
        ]
        if reader.fieldnames != expected_fields:
            raise ValueError(f"Unexpected result columns: {reader.fieldnames}")
        return list(reader)


def _verify_results() -> dict[str, int]:
    result_provenance = json.loads(
        (DATA_DIR / "results_provenance.json").read_text(encoding="utf-8")
    )
    expected_provenance_fields = {
        "schema",
        "dataset_id",
        "result_rows",
        "matrix",
        "extraction_policy",
        "response_text_policy",
        "source_material_policy",
        "empty_refusal",
        "release_artifacts",
    }
    if set(result_provenance) != expected_provenance_fields:
        raise ValueError("Unexpected result-provenance fields")
    if result_provenance["schema"] != "polycomp-results-provenance-v2":
        raise ValueError("Unexpected result-provenance schema")
    if result_provenance["dataset_id"] != protocol()["dataset_id"]:
        raise ValueError("Result-provenance dataset identity mismatch")

    rows = _load_results()
    keys = {(r["model"], r["problem_identifier"], r["presentation"]) for r in rows}
    expected = {
        (model, problem["problem_identifier"], presentation)
        for model in MODELS
        for problem in problems()
        for presentation in PRESENTATIONS
    }
    if len(rows) != 1080 or keys != expected:
        raise ValueError(
            f"Result matrix mismatch: rows={len(rows)}, missing={len(expected - keys)}, "
            f"extra={len(keys - expected)}"
        )

    empty = [row for row in rows if row["model_response"] == ""]
    expected_empty_key = (
        "claude-fable-5",
        "block_split_explore_joinery_19",
        "single_image",
    )
    if [(r["model"], r["problem_identifier"], r["presentation"]) for r in empty] != [
        expected_empty_key
    ]:
        raise ValueError(f"Unexpected empty response cells: {empty}")

    correct_counts: Counter[tuple[str, str | None]] = Counter()
    response_counts: Counter[tuple[str, str | None]] = Counter()
    problem_index = {row["problem_identifier"]: row for row in problems()}
    for row in rows:
        expected_response = json.dumps(
            problem_index[row["problem_identifier"]]["correct_response"],
            separators=(",", ":"),
        )
        if row["correct_response"] != expected_response:
            raise ValueError(
                f"Correct-response mismatch: {row['model']}/{row['problem_identifier']}"
            )
        expected_option = json.loads(row["correct_response"])["option"]
        is_correct = selected_option(row["model_response"]) == expected_option
        for group in ((row["model"], None), (row["model"], row["presentation"])):
            response_counts[group] += 1
            correct_counts[group] += int(is_correct)

    reported = json.loads((DATA_DIR / "reported_scores.json").read_text(encoding="utf-8"))
    for row in reported["overall"]:
        key = (row["model"], None)
        if response_counts[key] != row["responses"] or correct_counts[key] != row["correct"]:
            raise ValueError(f"Reported overall score mismatch: {row['model']}")
        if round(correct_counts[key] / response_counts[key], 6) != row["accuracy"]:
            raise ValueError(f"Reported overall accuracy mismatch: {row['model']}")
    for row in reported["by_presentation"]:
        key = (row["model"], row["presentation"])
        if response_counts[key] != row["responses"] or correct_counts[key] != row["correct"]:
            raise ValueError(
                f"Reported presentation score mismatch: {row['model']}/{row['presentation']}"
            )
        if round(correct_counts[key] / response_counts[key], 6) != row["accuracy"]:
            raise ValueError(
                f"Reported presentation accuracy mismatch: {row['model']}/{row['presentation']}"
            )
    return {"result_rows": len(rows), "empty_refusals": len(empty), "score_groups": 12}


def _verify_release_bindings() -> dict[str, int]:
    checked = 0
    for relative, expected in provenance()["release_files"].items():
        path = resolve_release_path(relative)
        if sha256_file(path) != expected:
            raise ValueError(f"Release provenance hash mismatch: {relative}")
        checked += 1
    return {"release_file_bindings": checked}


def verify_release() -> dict[str, Any]:
    checks: dict[str, Any] = {
        "dataset_id": protocol()["dataset_id"],
        **_verify_problem_manifests(),
        **_verify_assets(),
        **_verify_rendering_reference(),
        **_verify_results(),
        **_verify_requests(),
        **_verify_release_bindings(),
    }
    checks["status"] = "verified"
    return checks
