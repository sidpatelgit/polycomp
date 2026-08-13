"""Independent integer-lattice proof for each BlockSplit answer.

The proof uses only the normalized release manifests. Components may undergo
any orientation-preserving cube rotation and any integer translation; mirror
reflections are not permitted.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from itertools import permutations, product
from typing import Any, TypeAlias

Cell: TypeAlias = tuple[int, int, int]
Shape: TypeAlias = frozenset[Cell]
Rotation: TypeAlias = tuple[Cell, Cell, Cell]


class GeometryVerificationError(ValueError):
    """A problem manifest fails a geometric validity requirement."""


def determinant(matrix: Rotation) -> int:
    a, b, c = matrix
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def _proper_cube_rotations() -> tuple[Rotation, ...]:
    matrices: set[Rotation] = set()
    for permutation in permutations(range(3)):
        for signs in product((-1, 1), repeat=3):
            rows = []
            for output_axis, input_axis in enumerate(permutation):
                row = [0, 0, 0]
                row[input_axis] = signs[output_axis]
                rows.append(tuple(row))
            matrix: Rotation = (rows[0], rows[1], rows[2])
            if determinant(matrix) == 1:
                matrices.add(matrix)
    result = tuple(sorted(matrices))
    if len(result) != 24:
        raise AssertionError(f"Expected 24 proper cube rotations, generated {len(result)}")
    return result


PROPER_CUBE_ROTATIONS = _proper_cube_rotations()


def cells(raw_cells: Iterable[Sequence[int]], *, label: str = "shape") -> Shape:
    parsed = []
    for index, raw_cell in enumerate(raw_cells):
        if len(raw_cell) != 3:
            raise GeometryVerificationError(f"{label}[{index}] is not a 3D cell")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in raw_cell):
            raise GeometryVerificationError(f"{label}[{index}] is not integer-valued")
        parsed.append((raw_cell[0], raw_cell[1], raw_cell[2]))
    result = frozenset(parsed)
    if len(result) != len(parsed):
        raise GeometryVerificationError(f"{label} contains duplicate cells")
    if not result:
        raise GeometryVerificationError(f"{label} is empty")
    return result


def rotate_cell(cell: Cell, matrix: Rotation) -> Cell:
    return tuple(sum(row[index] * cell[index] for index in range(3)) for row in matrix)  # type: ignore[return-value]


def normalize_translation(shape: Iterable[Cell]) -> Shape:
    frozen = frozenset(shape)
    if not frozen:
        return frozen
    minimums = tuple(min(cell[axis] for cell in frozen) for axis in range(3))
    return frozenset(
        (
            cell[0] - minimums[0],
            cell[1] - minimums[1],
            cell[2] - minimums[2],
        )
        for cell in frozen
    )


def oriented_shapes(shape: Shape) -> frozenset[Shape]:
    return frozenset(
        normalize_translation(rotate_cell(cell, matrix) for cell in shape)
        for matrix in PROPER_CUBE_ROTATIONS
    )


def canonical_shape(shape: Shape) -> tuple[Cell, ...]:
    return min(tuple(sorted(oriented)) for oriented in oriented_shapes(shape))


def congruent(left: Shape, right: Shape) -> bool:
    return len(left) == len(right) and canonical_shape(left) == canonical_shape(right)


def is_connected(shape: Shape) -> bool:
    if not shape:
        return False
    start = next(iter(shape))
    visited = {start}
    queue = deque([start])
    while queue:
        x, y, z = queue.popleft()
        for neighbor in (
            (x - 1, y, z),
            (x + 1, y, z),
            (x, y - 1, z),
            (x, y + 1, z),
            (x, y, z - 1),
            (x, y, z + 1),
        ):
            if neighbor in shape and neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return len(visited) == len(shape)


def can_partition_target(target: Shape, left: Shape, right: Shape) -> bool:
    """Prove whether two free polycubes can partition ``target`` exactly."""
    if len(left) + len(right) != len(target):
        return False

    moving, complement = (left, right) if len(left) <= len(right) else (right, left)
    complement_orientations = oriented_shapes(complement)
    target_cells = tuple(target)

    for oriented in oriented_shapes(moving):
        anchor = min(oriented)
        for target_anchor in target_cells:
            delta = (
                target_anchor[0] - anchor[0],
                target_anchor[1] - anchor[1],
                target_anchor[2] - anchor[2],
            )
            placed = frozenset(
                (cell[0] + delta[0], cell[1] + delta[1], cell[2] + delta[2]) for cell in oriented
            )
            if placed.issubset(target):
                remainder = target - placed
                if normalize_translation(remainder) in complement_orientations:
                    return True
    return False


def _require_connected(shape: Shape, label: str) -> None:
    if not is_connected(shape):
        raise GeometryVerificationError(f"{label} is not 6-neighbor connected")


def verify_problem_geometry(manifest: Mapping[str, Any]) -> dict[str, Any]:
    problem_identifier = str(manifest.get("problem_identifier") or "<unknown>")
    geometry = manifest.get("geometry")
    if not isinstance(geometry, Mapping):
        raise GeometryVerificationError(f"{problem_identifier}: missing geometry record")

    target = cells(geometry["target_cells"], label=f"{problem_identifier}.target")
    _require_connected(target, f"{problem_identifier}.target")

    correct = geometry.get("correct_components")
    if not isinstance(correct, Mapping):
        raise GeometryVerificationError(
            f"{problem_identifier}: missing frozen correct-component partition"
        )
    correct_left = cells(correct["left"], label=f"{problem_identifier}.correct.left")
    correct_right = cells(correct["right"], label=f"{problem_identifier}.correct.right")
    _require_connected(correct_left, f"{problem_identifier}.correct.left")
    _require_connected(correct_right, f"{problem_identifier}.correct.right")
    if not correct_left.isdisjoint(correct_right) or correct_left | correct_right != target:
        raise GeometryVerificationError(
            f"{problem_identifier}: frozen correct components do not partition the target"
        )

    raw_options = geometry.get("options")
    if not isinstance(raw_options, Mapping) or set(raw_options) != {"A", "B", "C", "D"}:
        raise GeometryVerificationError(f"{problem_identifier}: options must be exactly A-D")

    option_shapes: dict[str, tuple[Shape, Shape]] = {}
    valid_options = []
    for option in ("A", "B", "C", "D"):
        raw_pair = raw_options[option]
        if not isinstance(raw_pair, Mapping):
            raise GeometryVerificationError(f"{problem_identifier}.option_{option} is invalid")
        left = cells(raw_pair["left"], label=f"{problem_identifier}.option_{option}.left")
        right = cells(raw_pair["right"], label=f"{problem_identifier}.option_{option}.right")
        _require_connected(left, f"{problem_identifier}.option_{option}.left")
        _require_connected(right, f"{problem_identifier}.option_{option}.right")
        if len(left) + len(right) != len(target):
            raise GeometryVerificationError(
                f"{problem_identifier}.option_{option} has the wrong total block count"
            )
        option_shapes[option] = (left, right)
        if can_partition_target(target, left, right):
            valid_options.append(option)

    correct_response = manifest.get("correct_response")
    expected_option = (
        correct_response.get("option") if isinstance(correct_response, Mapping) else None
    )
    if expected_option not in {"A", "B", "C", "D"}:
        raise GeometryVerificationError(f"{problem_identifier}: invalid correct response")
    if valid_options != [expected_option]:
        raise GeometryVerificationError(
            f"{problem_identifier}: expected exactly the declared option {expected_option}, "
            f"but valid options are {valid_options}"
        )

    expected_left, expected_right = option_shapes[expected_option]
    matches_frozen_partition = (
        congruent(expected_left, correct_left) and congruent(expected_right, correct_right)
    ) or (congruent(expected_left, correct_right) and congruent(expected_right, correct_left))
    if not matches_frozen_partition:
        raise GeometryVerificationError(
            f"{problem_identifier}: declared option does not match the frozen target partition"
        )

    return {
        "problem_identifier": problem_identifier,
        "target_blocks": len(target),
        "valid_options": valid_options,
        "proper_rotations": len(PROPER_CUBE_ROTATIONS),
        "connected_shapes": 11,
        "option_proofs": 4,
    }
