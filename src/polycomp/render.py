"""Reconstruct the reference SVGs and raster presentations from normalized geometry.

The committed PNGs remain the authoritative model-visible assets.  This module
is a behavior-preserving release-local adaptation of the renderer at the
recorded source snapshot. Interested users can rerun it and optionally compare
every reconstructed byte with the frozen submission.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from PIL import Image

from polycomp.data import (
    ROOT,
    assets_by_id,
    problems,
    problems_by_id,
    protocol,
    rendering_reference,
    resolve_release_path,
)
from polycomp.generate import sha256_file

Color = tuple[int, int, int]
Point2 = tuple[float, float]
Point3 = tuple[int, int, int]
Vec3 = tuple[float, float, float]
Matrix3 = tuple[Vec3, Vec3, Vec3]

BACKGROUND: Color = (248, 249, 251)
PANEL_BG: Color = (255, 255, 255)
PANEL_BORDER: Color = (218, 223, 231)
LABEL_BG: Color = (238, 241, 246)
TEXT: Color = (72, 80, 94)
EDGE: Color = (45, 51, 58)
SVG_FONT_FAMILY = "Arial, Helvetica, sans-serif"

LAYOUT_WIDTH = 2500
LAYOUT_HEIGHT = 2500
PANEL_WIDTH = 1200
PANEL_HEIGHT = 720
PAGE_MARGIN_X = 30
PAGE_MARGIN_Y = 90
PANEL_GAP_X = 40
PANEL_GAP_Y = 80

RESVG_CANDIDATES = ("resvg", "/opt/homebrew/bin/resvg", "/usr/local/bin/resvg")


class RerenderVerificationError(ValueError):
    """The reconstructed output could not be verified."""


class RerenderPrerequisiteError(ValueError):
    """An external rasterization prerequisite is unavailable."""


def _rename_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing an existing path (macOS)."""
    library_path = ctypes.util.find_library("c")
    if library_path is None:
        raise RerenderVerificationError("Could not load the platform C library for publication")
    library = ctypes.CDLL(library_path, use_errno=True)
    renamex_np = getattr(library, "renamex_np", None)
    if renamex_np is None:
        raise RerenderVerificationError("Atomic no-replace publication is unavailable")
    renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    renamex_np.restype = ctypes.c_int
    if renamex_np(os.fsencode(source), os.fsencode(destination), 0x00000004) == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ValueError(f"Rerender output appeared during rendering: {destination}")
    raise RerenderVerificationError(
        f"Could not atomically publish rerender output: errno {error_number}"
    )


def _check_publication_support() -> None:
    if sys.platform != "darwin":
        raise RerenderPrerequisiteError(
            "atomic rerender publication is currently supported only on macOS"
        )
    library_path = ctypes.util.find_library("c")
    if library_path is None or not hasattr(ctypes.CDLL(library_path), "renamex_np"):
        raise RerenderPrerequisiteError("the macOS no-replace rename primitive is unavailable")


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class RenderObject:
    cells: set[Point3]
    view_name: str
    view: Matrix3


FACE_DEFS = [
    (
        (1, 0, 0),
        lambda x, y, z: [
            (x + 1, y, z),
            (x + 1, y + 1, z),
            (x + 1, y + 1, z + 1),
            (x + 1, y, z + 1),
        ],
    ),
    (
        (-1, 0, 0),
        lambda x, y, z: [
            (x, y, z),
            (x, y, z + 1),
            (x, y + 1, z + 1),
            (x, y + 1, z),
        ],
    ),
    (
        (0, 1, 0),
        lambda x, y, z: [
            (x, y + 1, z),
            (x + 1, y + 1, z),
            (x + 1, y + 1, z + 1),
            (x, y + 1, z + 1),
        ],
    ),
    (
        (0, -1, 0),
        lambda x, y, z: [
            (x, y, z),
            (x + 1, y, z),
            (x + 1, y, z + 1),
            (x, y, z + 1),
        ],
    ),
    (
        (0, 0, 1),
        lambda x, y, z: [
            (x, y, z + 1),
            (x + 1, y, z + 1),
            (x + 1, y + 1, z + 1),
            (x, y + 1, z + 1),
        ],
    ),
    (
        (0, 0, -1),
        lambda x, y, z: [
            (x, y, z),
            (x, y + 1, z),
            (x + 1, y + 1, z),
            (x + 1, y, z),
        ],
    ),
]


def _color_hex(color: Color) -> str:
    return f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"


class SvgCanvas:
    """Minimal serializer adapted from the renderer used for the submitted assets."""

    def __init__(self, width: int, height: int, background: Color) -> None:
        self.width = width
        self.height = height
        self.elements = [
            (
                f'<rect x="0" y="0" width="{width}" height="{height}" '
                f'fill="{_color_hex(background)}"/>'
            )
        ]

    def fill_rect(self, rect: Rect, color: Color) -> None:
        self.elements.append(
            f'<rect x="{rect.x}" y="{rect.y}" width="{rect.width}" height="{rect.height}" '
            f'fill="{_color_hex(color)}"/>'
        )

    def draw_rect_border(self, rect: Rect, color: Color, width: int = 2) -> None:
        inset = width / 2
        self.elements.append(
            f'<rect x="{rect.x + inset:g}" y="{rect.y + inset:g}" '
            f'width="{rect.width - width:g}" height="{rect.height - width:g}" '
            f'fill="none" stroke="{_color_hex(color)}" stroke-width="{width}" '
            f'shape-rendering="crispEdges"/>'
        )

    def draw_polygon(
        self,
        points: list[Point2],
        fill: Color,
        stroke: Color = EDGE,
        stroke_width: float = 2.2,
    ) -> None:
        point_text = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
        self.elements.append(
            f'<polygon points="{point_text}" fill="{_color_hex(fill)}" '
            f'stroke="{_color_hex(stroke)}" stroke-width="{stroke_width:g}" '
            f'stroke-linejoin="round"/>'
        )

    def draw_text(
        self,
        text: str,
        x: int,
        y: int,
        font_size: int,
        color: Color = TEXT,
        weight: int = 750,
    ) -> None:
        self.elements.append(
            f'<text x="{x}" y="{y}" fill="{_color_hex(color)}" '
            f'font-family="{SVG_FONT_FAMILY}" font-size="{font_size}" '
            f'font-weight="{weight}" dominant-baseline="hanging" '
            f'letter-spacing="0">{escape(text)}</text>'
        )

    def bytes(self) -> bytes:
        svg = "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                (
                    f'<svg xmlns="http://www.w3.org/2000/svg" '
                    f'width="{self.width}" height="{self.height}" '
                    f'viewBox="0 0 {self.width} {self.height}">'
                ),
                *self.elements,
                "</svg>",
                "",
            ]
        )
        return svg.encode("utf-8")


def _mat_apply(matrix: Matrix3, point: Vec3) -> Vec3:
    return tuple(
        sum(matrix[row][column] * point[column] for column in range(3)) for row in range(3)
    )  # type: ignore[return-value]


def _normalize(vector: Vec3) -> Vec3:
    length = math.sqrt(sum(value * value for value in vector))
    return tuple(value / length for value in vector)  # type: ignore[return-value]


def _dot(left: Vec3, right: Vec3) -> float:
    return sum(left[index] * right[index] for index in range(3))


def _cube_vertices(cell: Point3) -> list[Vec3]:
    x, y, z = cell
    return [
        (float(x + dx), float(y + dy), float(z + dz))
        for dx in (0, 1)
        for dy in (0, 1)
        for dz in (0, 1)
    ]


def _object_center(cells: set[Point3]) -> Vec3:
    xs = [cell[0] for cell in cells]
    ys = [cell[1] for cell in cells]
    zs = [cell[2] for cell in cells]
    return (
        (min(xs) + max(xs) + 1) / 2,
        (min(ys) + max(ys) + 1) / 2,
        (min(zs) + max(zs) + 1) / 2,
    )


def _transform_point(point: Vec3, view: Matrix3, center: Vec3) -> Vec3:
    local = tuple(point[index] - center[index] for index in range(3))
    return _mat_apply(view, local)  # type: ignore[arg-type]


def _project(point: Vec3, scale: float, origin: Point2) -> Point2:
    return (origin[0] + point[0] * scale, origin[1] - point[1] * scale)


def _projected_bounds(
    cells: set[Point3], view: Matrix3, scale: float = 1.0
) -> tuple[float, float, float, float]:
    center = _object_center(cells)
    points = [
        _project(_transform_point(vertex, view, center), scale, (0.0, 0.0))
        for cell in cells
        for vertex in _cube_vertices(cell)
    ]
    return (
        min(x for x, _ in points),
        min(y for _, y in points),
        max(x for x, _ in points),
        max(y for _, y in points),
    )


def _shade_for_normal(normal: Vec3) -> Color:
    light = _normalize((0.25, 0.35, 0.90))
    brightness = 0.54 + 0.30 * max(0.0, _dot(_normalize(normal), light))
    channel = max(92, min(172, round(190 * brightness)))
    return (channel, channel + 4, channel + 8)


def _face_polygons(
    cells: set[Point3],
    view: Matrix3,
    scale: float,
    origin: Point2,
) -> list[tuple[float, Color, list[Point2]]]:
    polygons = []
    center = _object_center(cells)
    for cell in sorted(cells):
        x, y, z = cell
        for normal, corners_fn in FACE_DEFS:
            neighbor = (x + normal[0], y + normal[1], z + normal[2])
            if neighbor in cells:
                continue
            view_normal = _mat_apply(view, tuple(float(value) for value in normal))
            if view_normal[2] <= 0.035:
                continue
            transformed = [
                _transform_point(tuple(float(value) for value in corner), view, center)
                for corner in corners_fn(x, y, z)
            ]
            depth = sum(point[2] for point in transformed) / len(transformed)
            polygons.append(
                (
                    depth,
                    _shade_for_normal(view_normal),
                    [_project(point, scale, origin) for point in transformed],
                )
            )
    return sorted(polygons, key=lambda item: item[0])


def _layout_panel_rects() -> dict[str, Rect]:
    left = PAGE_MARGIN_X
    right = PAGE_MARGIN_X + PANEL_WIDTH + PANEL_GAP_X
    top = PAGE_MARGIN_Y
    middle = PAGE_MARGIN_Y + PANEL_HEIGHT + PANEL_GAP_Y
    bottom = PAGE_MARGIN_Y + (PANEL_HEIGHT + PANEL_GAP_Y) * 2
    return {
        "target_view_1": Rect(left, top, PANEL_WIDTH, PANEL_HEIGHT),
        "target_view_2": Rect(right, top, PANEL_WIDTH, PANEL_HEIGHT),
        "A": Rect(left, middle, PANEL_WIDTH, PANEL_HEIGHT),
        "B": Rect(right, middle, PANEL_WIDTH, PANEL_HEIGHT),
        "C": Rect(left, bottom, PANEL_WIDTH, PANEL_HEIGHT),
        "D": Rect(right, bottom, PANEL_WIDTH, PANEL_HEIGHT),
    }


def _reference_object_rect(panel: Rect) -> Rect:
    return Rect(panel.x + 100, panel.y + 120, panel.width - 200, panel.height - 170)


def _choice_object_rects(panel: Rect) -> tuple[Rect, Rect]:
    inner = Rect(panel.x + 100, panel.y + 120, panel.width - 200, panel.height - 170)
    gap = 70
    half_width = (inner.width - gap) // 2
    return (
        Rect(inner.x, inner.y, half_width, inner.height),
        Rect(inner.x + half_width + gap, inner.y, half_width, inner.height),
    )


def _draw_panel(canvas: SvgCanvas, rect: Rect, label: str) -> None:
    canvas.fill_rect(rect, PANEL_BG)
    canvas.draw_rect_border(rect, PANEL_BORDER, width=3)
    badge_width = 220 if len(label) > 1 else 72
    badge = Rect(rect.x + 32, rect.y + 28, badge_width, 58)
    canvas.fill_rect(badge, LABEL_BG)
    canvas.draw_rect_border(badge, PANEL_BORDER, width=2)
    canvas.draw_text(label, badge.x + 15, badge.y + 10, 34, TEXT, 800)


def _draw_object(canvas: SvgCanvas, render_object: RenderObject, rect: Rect, scale: float) -> None:
    min_x, min_y, max_x, max_y = _projected_bounds(render_object.cells, render_object.view, scale)
    origin = (
        rect.x + rect.width / 2 - (min_x + max_x) / 2,
        rect.y + rect.height / 2 - (min_y + max_y) / 2,
    )
    for _, color, polygon in _face_polygons(render_object.cells, render_object.view, scale, origin):
        canvas.draw_polygon(polygon, color, EDGE, stroke_width=2.4)


def _cells(raw_cells: list[list[int]]) -> set[Point3]:
    return {tuple(int(value) for value in cell) for cell in raw_cells}  # type: ignore[return-value]


def _matrix(raw_matrix: list[list[float]]) -> Matrix3:
    return tuple(tuple(float(value) for value in row) for row in raw_matrix)  # type: ignore[return-value]


def _render_objects(
    manifest: dict[str, Any],
) -> tuple[tuple[RenderObject, RenderObject], dict[str, tuple[RenderObject, RenderObject]], float]:
    geometry = manifest["geometry"]
    rendering = manifest["rendering"]
    target = _cells(geometry["target_cells"])

    if "target_views" in rendering:
        target_view_specs = rendering["target_views"]
        choice_view_specs = rendering["choice_views"]
    else:
        views_by_name = rendering["views"]
        target_view_specs = [
            {"name": "target_view_1", **views_by_name["target_view_1"]},
            {"name": "target_view_2", **views_by_name["target_view_2"]},
        ]
        choice_view_specs = {
            label: [
                {
                    "name": f"choice_{label.lower()}_left",
                    **views_by_name[f"choice_{label.lower()}_left"],
                },
                {
                    "name": f"choice_{label.lower()}_right",
                    **views_by_name[f"choice_{label.lower()}_right"],
                },
            ]
            for label in ("A", "B", "C", "D")
        }

    target_views = tuple(
        RenderObject(target, view["name"], _matrix(view["matrix"])) for view in target_view_specs
    )
    if len(target_views) != 2:
        raise ValueError(f"Expected two target views: {manifest['problem_identifier']}")

    choices = {}
    for label in ("A", "B", "C", "D"):
        option = geometry["options"][label]
        view_pair = choice_view_specs[label]
        choices[label] = (
            RenderObject(
                _cells(option["left"]), view_pair[0]["name"], _matrix(view_pair[0]["matrix"])
            ),
            RenderObject(
                _cells(option["right"]), view_pair[1]["name"], _matrix(view_pair[1]["matrix"])
            ),
        )
    return target_views, choices, float(rendering["shared_render_scale"])


def render_svg_bytes(manifest: dict[str, Any]) -> bytes:
    """Return the canonical SVG serialization for one normalized manifest."""
    target_views, choices, scale = _render_objects(manifest)
    canvas = SvgCanvas(LAYOUT_WIDTH, LAYOUT_HEIGHT, BACKGROUND)
    panel_rects = _layout_panel_rects()

    for label, render_object in zip(("VIEW 1", "VIEW 2"), target_views, strict=True):
        panel = panel_rects[render_object.view_name]
        _draw_panel(canvas, panel, label)
        _draw_object(canvas, render_object, _reference_object_rect(panel), scale)

    for label in ("A", "B", "C", "D"):
        panel = panel_rects[label]
        _draw_panel(canvas, panel, label)
        left_rect, right_rect = _choice_object_rects(panel)
        left, right = choices[label]
        _draw_object(canvas, left, left_rect, scale)
        _draw_object(canvas, right, right_rect, scale)
    return canvas.bytes()


def _find_resvg(explicit: Path | None) -> str:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise RerenderPrerequisiteError(f"resvg executable does not exist: {candidate}")
        return str(candidate)
    for candidate in RESVG_CANDIDATES:
        resolved = shutil.which(candidate) if "/" not in candidate else candidate
        if resolved and Path(resolved).is_file():
            return resolved
    raise RerenderPrerequisiteError(
        "resvg is required for PNG reconstruction; install resvg 0.47.0 or pass --resvg"
    )


def _resvg_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"], check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RerenderPrerequisiteError(f"Could not run resvg --version: {executable}") from exc
    return result.stdout.strip()


def _rasterize(executable: str, svg_path: Path, png_path: Path) -> None:
    try:
        subprocess.run(
            [executable, str(svg_path), str(png_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RerenderVerificationError(f"resvg failed for {svg_path}") from exc
    if not png_path.is_file():
        raise RerenderVerificationError(f"resvg did not create a PNG for {svg_path}")
    try:
        with Image.open(png_path) as image:
            if image.format != "PNG":
                raise RerenderVerificationError(f"resvg did not create a PNG for {svg_path}")
            image.verify()
    except RerenderVerificationError:
        raise
    except Exception as exc:
        raise RerenderVerificationError(f"resvg created an invalid PNG for {svg_path}") from exc


def _pixel_sha256(path: Path) -> str:
    with Image.open(path) as image:
        return hashlib.sha256(image.tobytes()).hexdigest()


def _manifest(problem_identifier: str) -> dict[str, Any]:
    row = problems_by_id().get(problem_identifier)
    if row is None:
        raise ValueError(f"Unknown problem(s): {problem_identifier}")
    return json.loads(resolve_release_path(row["manifest_path"]).read_text(encoding="utf-8"))


def _expected_svg_by_id() -> dict[str, dict[str, Any]]:
    return {row["problem_identifier"]: row for row in rendering_reference()["cases"]}


def _compare_file(
    path: Path,
    expected: dict[str, Any],
    label: str,
    *,
    check_pixels: bool = False,
) -> list[str]:
    errors = []
    if sha256_file(path) != expected["file_sha256"]:
        errors.append(f"{label}: file SHA-256 differs")
    if path.stat().st_size != int(expected["size_bytes"]):
        errors.append(f"{label}: byte size differs")
    if check_pixels:
        with Image.open(path) as image:
            if list(image.size) != [int(expected["width"]), int(expected["height"])]:
                errors.append(f"{label}: dimensions differ")
        if _pixel_sha256(path) != expected["pixel_sha256"]:
            errors.append(f"{label}: decoded pixels differ")
    return errors


def rerender_assets(
    output_dir: Path,
    problem_identifiers: list[str],
    *,
    resvg: Path | None = None,
    verify_frozen: bool = False,
) -> dict[str, Any]:
    """Reconstruct SVG, PNG, and both five-crop presentation layouts."""
    known = {row["problem_identifier"] for row in problems()}
    unknown = sorted(set(problem_identifiers) - known)
    if unknown:
        raise ValueError(f"Unknown problem(s): {', '.join(unknown)}")
    if not problem_identifiers:
        raise ValueError("At least one problem is required")
    if len(set(problem_identifiers)) != len(problem_identifiers):
        raise ValueError("Problem identifiers must not be repeated")

    resolved_output = output_dir.expanduser().resolve()
    protected_roots = (
        (ROOT / ".git").resolve(),
        (ROOT / "assets").resolve(),
        (ROOT / "data").resolve(),
    )
    if any(
        resolved_output == root or resolved_output.is_relative_to(root) for root in protected_roots
    ):
        raise ValueError(
            "Rerender output must not be inside the release .git, assets, or data directory"
        )

    if resolved_output.exists():
        raise ValueError(f"Rerender output already exists: {resolved_output}")

    _check_publication_support()
    executable = _find_resvg(resvg)
    version = _resvg_version(executable)
    generic_names = protocol()["presentations"]["multi_image_generic"]["submitted_filenames"]
    descriptive_names = protocol()["presentations"]["multi_image_descriptive"][
        "submitted_filenames"
    ]
    expected_svgs = _expected_svg_by_id()
    mismatches: list[str] = []
    files = 0

    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{resolved_output.name}.tmp-", dir=resolved_output.parent
    ) as temporary:
        staging = Path(temporary) / "output"
        staging.mkdir()
        for problem_identifier in problem_identifiers:
            manifest = _manifest(problem_identifier)
            single_dir = staging / "single_image" / problem_identifier
            single_dir.mkdir(parents=True, exist_ok=True)
            svg_path = single_dir / "image.svg"
            svg_path.write_bytes(render_svg_bytes(manifest))
            png_path = single_dir / "image.png"
            _rasterize(executable, svg_path, png_path)
            files += 2

            case = assets_by_id()[problem_identifier]
            mismatches.extend(
                _compare_file(
                    svg_path, expected_svgs[problem_identifier], f"{problem_identifier}/image.svg"
                )
            )
            mismatches.extend(
                _compare_file(
                    png_path,
                    case["single_image"],
                    f"{problem_identifier}/image.png",
                    check_pixels=True,
                )
            )

            with Image.open(png_path) as image:
                if image.size != (LAYOUT_WIDTH, LAYOUT_HEIGHT):
                    raise RerenderVerificationError(
                        f"Unexpected reconstructed dimensions for {problem_identifier}: {image.size}"
                    )
                generic_dir = staging / "multi_image_generic" / problem_identifier
                descriptive_dir = staging / "multi_image_descriptive" / problem_identifier
                generic_dir.mkdir(parents=True, exist_ok=True)
                descriptive_dir.mkdir(parents=True, exist_ok=True)
                for index, expected_crop in enumerate(case["crops"]):
                    generic_path = generic_dir / generic_names[index]
                    descriptive_path = descriptive_dir / descriptive_names[index]
                    image.crop(tuple(expected_crop["crop_box"])).save(generic_path)
                    shutil.copyfile(generic_path, descriptive_path)
                    files += 2
                    for path, presentation in (
                        (generic_path, "multi_image_generic"),
                        (descriptive_path, "multi_image_descriptive"),
                    ):
                        mismatches.extend(
                            _compare_file(
                                path,
                                expected_crop,
                                f"{problem_identifier}/{presentation}/{path.name}",
                                check_pixels=True,
                            )
                        )

        if mismatches and verify_frozen:
            raise RerenderVerificationError(
                f"Rerender differs from frozen submission ({len(mismatches)} checks); "
                "no output was published"
            )

        display_output = str(output_dir)
        result = {
            "status": "matched_frozen" if not mismatches else "rendered_with_drift",
            "output": display_output,
            "problems": len(problem_identifiers),
            "render_files": files,
            "svg_files": len(problem_identifiers),
            "single_images": len(problem_identifiers),
            "crop_files": len(problem_identifiers) * 10,
            "rasterizer": version,
            "font_policy": (
                "system resolution of Arial, Helvetica, sans-serif; font files are "
                "documented but not bundled or hash-pinned"
            ),
            "verification_required": verify_frozen,
            "mismatches": len(mismatches),
            "mismatch_examples": mismatches[:10],
            "report": str(output_dir / "rerender-report.json"),
        }
        report = {**result, "mismatch_details": mismatches}
        (staging / "rerender-report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        _rename_no_replace(staging, resolved_output)
    return result
