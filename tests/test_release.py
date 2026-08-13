from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

import polycomp.render as render_module
from polycomp.cli import main
from polycomp.data import (
    DATA_DIR,
    assets,
    problems_by_id,
    provenance,
    rendering_reference,
    request_hashes_by_key,
    resolve_release_path,
)
from polycomp.generate import generate_presentations, sha256_file
from polycomp.geometry import (
    PROPER_CUBE_ROTATIONS,
    GeometryVerificationError,
    determinant,
    verify_problem_geometry,
)
from polycomp.payloads import build_payload, canonical_json_sha256
from polycomp.render import (
    RerenderPrerequisiteError,
    RerenderVerificationError,
    render_svg_bytes,
    rerender_assets,
)
from polycomp.verify import verify_release


def test_release_reconciles_assets_payloads_results_and_scores() -> None:
    result = verify_release()
    assert result["status"] == "verified"
    assert result["problems"] == 120
    assert result["proper_cube_rotations"] == 24
    assert result["geometry_proofs"] == 120
    assert result["option_assembly_proofs"] == 480
    assert result["single_images"] == 120
    assert result["crops"] == 600
    assert result["reference_svgs"] == 120
    assert result["request_cells"] == 1080
    assert result["complete_batch_inputs"] == 9
    assert result["result_rows"] == 1080
    assert result["empty_refusals"] == 1


def test_single_payload_matches_body_bound_to_historical_input() -> None:
    key = ("openai", "block_split_006", "single_image")
    expected = request_hashes_by_key()[key]["release_derived_transmitted_body_sha256"]
    actual = canonical_json_sha256(build_payload(*key))
    assert actual == expected


def test_generate_one_problem_all_presentations(tmp_path: Path) -> None:
    result = generate_presentations(
        tmp_path,
        ["block_split_006"],
        ["single_image", "multi_image_generic", "multi_image_descriptive"],
    )
    assert result["files"] == 11
    single = tmp_path / "single_image" / "block_split_006" / "image.png"
    assert single.is_file()
    assert sha256_file(single) == hashlib.sha256(single.read_bytes()).hexdigest()
    generic_target = tmp_path / "multi_image_generic" / "block_split_006" / "image_1.png"
    descriptive_target = (
        tmp_path / "multi_image_descriptive" / "block_split_006" / "target_object_views.png"
    )
    assert generic_target.read_bytes() == descriptive_target.read_bytes()


def test_payload_contains_no_credential_fields() -> None:
    payload = build_payload("gemini", "block_split_006", "multi_image_descriptive")
    serialized = json.dumps(payload).lower()
    assert "api_key" not in serialized
    assert "authorization" not in serialized
    assert "bearer " not in serialized


def test_public_metadata_omits_private_execution_history() -> None:
    with (DATA_DIR / "request_hashes.csv").open(newline="", encoding="utf-8") as handle:
        request_fields = csv.DictReader(handle).fieldnames
    assert request_fields == [
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

    release_provenance = provenance()
    assert release_provenance["schema"] == "polycomp-provenance-v2"
    assert set(release_provenance) == {
        "schema",
        "dataset_id",
        "release_files",
        "historical_request_integrity",
        "historical_batch_inputs",
    }
    batches = release_provenance["historical_batch_inputs"]["batches"]
    assert len(batches) == 9
    assert all(
        set(batch) == {"provider", "presentation", "request_count", "file_sha256", "size_bytes"}
        for batch in batches
    )

    result_provenance = json.loads(
        (DATA_DIR / "results_provenance.json").read_text(encoding="utf-8")
    )
    assert result_provenance["schema"] == "polycomp-results-provenance-v2"
    assert "raw_source_hashes" not in result_provenance
    assert "successful_source_counts" not in result_provenance

    asset_manifest = assets()
    assert asset_manifest["schema"] == "polycomp-assets-v2"
    assert "source_hash_manifest" not in asset_manifest
    for case in asset_manifest["cases"]:
        assert "original_path" not in case["single_image"]
        for crop in case["crops"]:
            assert "original_generic_path" not in crop
            assert "original_descriptive_path" not in crop

    for problem in problems_by_id().values():
        manifest = json.loads(
            resolve_release_path(problem["manifest_path"]).read_text(encoding="utf-8")
        )
        assert manifest["schema"] == "polycomp-problem-v2"
        assert "provenance" not in manifest

    reference = rendering_reference()
    assert reference["schema"] == "polycomp-rendering-reference-v2"
    assert "source_snapshot" not in reference

    public_metadata = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            DATA_DIR / "assets.json",
            DATA_DIR / "provenance.json",
            DATA_DIR / "rendering_reference.json",
            DATA_DIR / "request_hashes.csv",
            DATA_DIR / "results_provenance.json",
            *sorted((DATA_DIR / "manifests").glob("*.json")),
        ]
    )
    for forbidden in (
        "artifacts/",
        "tasks/",
        "source_job_id",
        "original_input_path",
        "source_git_commit",
        "snapshot_id",
        "renderer_lineage",
    ):
        assert forbidden not in public_metadata


def test_expected_cli_validation_errors_are_concise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = [
        (
            [
                "generate",
                "--problem",
                "not-a-problem",
                "--output",
                str(tmp_path / "generated"),
            ],
            "Unknown problem(s): not-a-problem",
        ),
        (
            [
                "rerender",
                "--problem",
                "not-a-problem",
                "--output",
                str(tmp_path / "rerendered"),
            ],
            "Unknown problem(s): not-a-problem",
        ),
        (
            [
                "payload",
                "--provider",
                "openai",
                "--presentation",
                "single_image",
                "--problem",
                "not-a-problem",
                "--output",
                str(tmp_path / "payload.json"),
            ],
            "Unknown problem: not-a-problem",
        ),
        (
            [
                "payload",
                "--provider",
                "openai",
                "--presentation",
                "single_image",
                "--problem",
                "block_split_006",
                "--batch",
                "--output",
                str(tmp_path / "batch.jsonl"),
            ],
            "--batch requires --all",
        ),
    ]

    for arguments, expected_message in cases:
        monkeypatch.setattr(sys, "argv", ["polycomp", *arguments])
        with pytest.raises(SystemExit) as exc_info:
            main()
        captured = capsys.readouterr()
        assert exc_info.value.code == 2
        assert captured.out == ""
        assert captured.err == f"polycomp: error: {expected_message}\n"

    assert not (tmp_path / "generated").exists()
    assert not (tmp_path / "rerendered").exists()
    assert not (tmp_path / "payload.json").exists()
    assert not (tmp_path / "batch.jsonl").exists()


def _manifest(problem_identifier: str) -> dict[str, object]:
    path = resolve_release_path(problems_by_id()[problem_identifier]["manifest_path"])
    return json.loads(path.read_text(encoding="utf-8"))


def test_rotation_group_has_24_distinct_proper_integer_matrices() -> None:
    assert len(PROPER_CUBE_ROTATIONS) == 24
    assert len(set(PROPER_CUBE_ROTATIONS)) == 24
    for matrix in PROPER_CUBE_ROTATIONS:
        assert determinant(matrix) == 1
        assert all(value in {-1, 0, 1} for row in matrix for value in row)
        assert all(sum(value != 0 for value in row) == 1 for row in matrix)
        assert all(sum(row[column] != 0 for row in matrix) == 1 for column in range(3))


def test_geometry_proof_finds_only_the_declared_option() -> None:
    result = verify_problem_geometry(_manifest("block_split_006"))
    assert result["valid_options"] == ["A"]
    assert result["proper_rotations"] == 24


def test_geometry_proof_rejects_an_ambiguous_option_set() -> None:
    manifest = deepcopy(_manifest("block_split_006"))
    manifest["geometry"]["options"]["B"] = deepcopy(manifest["geometry"]["options"]["A"])
    with pytest.raises(GeometryVerificationError, match=r"valid options are \['A', 'B'\]"):
        verify_problem_geometry(manifest)


def test_geometry_proof_rejects_a_broken_target_partition() -> None:
    manifest = deepcopy(_manifest("block_split_006"))
    manifest["geometry"]["correct_components"]["left"].pop()
    with pytest.raises(GeometryVerificationError, match="do not partition the target"):
        verify_problem_geometry(manifest)


def test_svg_renderer_matches_legacy_and_modern_reference_hashes() -> None:
    expected = {row["problem_identifier"]: row for row in rendering_reference()["cases"]}
    for identifier in ("block_split_006", "block_split_explore_cleaved_01"):
        svg = render_svg_bytes(_manifest(identifier))
        assert len(svg) == expected[identifier]["size_bytes"]
        assert hashlib.sha256(svg).hexdigest() == expected[identifier]["file_sha256"]


@pytest.mark.parametrize("protected", [".git", "assets", "data"])
def test_rerender_refuses_to_write_inside_release_metadata(protected: str) -> None:
    output = resolve_release_path(protected) / "do-not-write"
    with pytest.raises(ValueError, match=r"\.git, assets, or data"):
        rerender_assets(output, ["block_split_006"])
    assert not output.exists()


def test_reference_environment_rerender_matches_frozen_assets(tmp_path: Path) -> None:
    resvg = shutil.which("resvg")
    font_paths = [
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    if resvg is None or not all(path.is_file() for path in font_paths):
        pytest.skip("reference resvg/Arial environment is unavailable")
    version = subprocess.run(
        [resvg, "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if version != rendering_reference()["observed_release_replay_environment"]["resvg"]:
        pytest.skip("reference resvg version is unavailable")

    output = tmp_path / "rerendered"
    result = rerender_assets(
        output,
        ["block_split_006", "block_split_explore_cleaved_01"],
        verify_frozen=True,
    )
    assert result["status"] == "matched_frozen"
    assert result["mismatches"] == 0
    assert result["render_files"] == 24
    assert (output / "rerender-report.json").is_file()


def test_rerender_verification_failure_is_transactional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = "block_split_006"
    expected_row = next(
        row for row in rendering_reference()["cases"] if row["problem_identifier"] == identifier
    )
    tampered = deepcopy(expected_row)
    tampered["file_sha256"] = "0" * 64

    monkeypatch.setattr(render_module, "_expected_svg_by_id", lambda: {identifier: tampered})
    monkeypatch.setattr(render_module, "_check_publication_support", lambda: None)
    monkeypatch.setattr(
        render_module,
        "_rename_no_replace",
        lambda source, destination: source.rename(destination),
    )
    monkeypatch.setattr(render_module, "_find_resvg", lambda _: "fake-resvg")
    monkeypatch.setattr(render_module, "_resvg_version", lambda _: "0.47.0")

    frozen_png = resolve_release_path("assets/single_image/block_split_006/image.png")

    def fake_rasterize(_: str, __: Path, destination: Path) -> None:
        shutil.copyfile(frozen_png, destination)

    monkeypatch.setattr(render_module, "_rasterize", fake_rasterize)

    verified_output = tmp_path / "verified"
    with pytest.raises(RerenderVerificationError, match="differs from frozen submission"):
        rerender_assets(verified_output, [identifier], verify_frozen=True)
    assert not verified_output.exists()

    inspection_output = tmp_path / "inspection"
    result = rerender_assets(inspection_output, [identifier], verify_frozen=False)
    assert result["status"] == "rendered_with_drift"
    assert result["mismatches"] == 1
    assert inspection_output.is_dir()
    report = json.loads((inspection_output / "rerender-report.json").read_text())
    assert len(report["mismatch_details"]) == 1


def test_missing_rerender_prerequisite_is_concise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unavailable(_: Path | None) -> str:
        raise RerenderPrerequisiteError("resvg is unavailable")

    monkeypatch.setattr(render_module, "_check_publication_support", lambda: None)
    monkeypatch.setattr(render_module, "_find_resvg", unavailable)
    output = tmp_path / "rerendered"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "polycomp",
            "rerender",
            "--problem",
            "block_split_006",
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 3
    assert captured.out == ""
    assert captured.err == "polycomp: unavailable: resvg is unavailable\n"
    assert not output.exists()


def test_rerender_rejects_a_rasterizer_that_writes_no_png(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "fake-resvg"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    output = tmp_path / "rerendered"
    monkeypatch.setattr(render_module, "_check_publication_support", lambda: None)
    with pytest.raises(RerenderVerificationError, match="did not create a PNG"):
        rerender_assets(output, ["block_split_006"], resvg=executable, verify_frozen=True)
    assert not output.exists()


def test_rerender_rejects_invalid_rasterizer_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "fake-resvg"
    executable.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo 0.47.0; else echo junk > "$2"; fi\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    output = tmp_path / "rerendered"
    monkeypatch.setattr(render_module, "_check_publication_support", lambda: None)
    with pytest.raises(RerenderVerificationError, match="invalid PNG"):
        rerender_assets(output, ["block_split_006"], resvg=executable, verify_frozen=True)
    assert not output.exists()


@pytest.mark.parametrize("concurrent_kind", ["directory", "file", "symlink"])
def test_rerender_does_not_clobber_an_output_created_during_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, concurrent_kind: str
) -> None:
    output = tmp_path / "rerendered"
    frozen_png = resolve_release_path("assets/single_image/block_split_006/image.png")
    monkeypatch.setattr(render_module, "_find_resvg", lambda _: "fake-resvg")
    monkeypatch.setattr(render_module, "_resvg_version", lambda _: "0.47.0")
    monkeypatch.setattr(render_module, "_check_publication_support", lambda: None)

    def no_replace(source: Path, destination: Path) -> None:
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"Rerender output appeared during rendering: {destination}")
        source.rename(destination)

    monkeypatch.setattr(render_module, "_rename_no_replace", no_replace)

    def fake_rasterize(_: str, __: Path, destination: Path) -> None:
        if concurrent_kind == "directory":
            output.mkdir()
            (output / "sentinel.txt").write_text("preserve me", encoding="utf-8")
        elif concurrent_kind == "file":
            output.write_text("preserve me", encoding="utf-8")
        else:
            output.symlink_to("preserve-me-target")
        shutil.copyfile(frozen_png, destination)

    monkeypatch.setattr(render_module, "_rasterize", fake_rasterize)
    with pytest.raises(ValueError, match="appeared during rendering"):
        rerender_assets(output, ["block_split_006"], verify_frozen=True)
    if concurrent_kind == "directory":
        assert (output / "sentinel.txt").read_text(encoding="utf-8") == "preserve me"
    elif concurrent_kind == "file":
        assert output.read_text(encoding="utf-8") == "preserve me"
    else:
        assert output.is_symlink()
        assert output.readlink() == Path("preserve-me-target")


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin renamex_np test")
@pytest.mark.parametrize("existing_kind", ["directory", "file", "symlink"])
def test_darwin_no_replace_publication_preserves_existing_target(
    tmp_path: Path, existing_kind: str
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "new.txt").write_text("new", encoding="utf-8")
    if existing_kind == "directory":
        destination.mkdir()
    elif existing_kind == "file":
        destination.write_text("old", encoding="utf-8")
    else:
        destination.symlink_to("old-target")

    with pytest.raises(ValueError, match="appeared during rendering"):
        render_module._rename_no_replace(source, destination)
    assert source.is_dir()
    if existing_kind == "directory":
        assert destination.is_dir()
        assert list(destination.iterdir()) == []
    elif existing_kind == "file":
        assert destination.read_text(encoding="utf-8") == "old"
    else:
        assert destination.readlink() == Path("old-target")
