"""Command-line interface for local, non-submitting benchmark reproduction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from polycomp.data import problems, request_hashes_by_key
from polycomp.generate import generate_presentations
from polycomp.payloads import (
    PRESENTATIONS,
    PROVIDERS,
    build_payload,
    canonical_json_bytes,
    historical_batch_bytes,
    provider_envelope,
)
from polycomp.render import (
    RerenderPrerequisiteError,
    RerenderVerificationError,
    rerender_assets,
)
from polycomp.verify import verify_release


def _ordered_problem_ids() -> list[str]:
    return [
        row["problem_identifier"]
        for row in sorted(problems(), key=lambda item: int(item["primary_position"]))
    ]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _run_generate(args: argparse.Namespace) -> dict[str, Any]:
    identifiers = _ordered_problem_ids() if args.all else args.problem
    presentations = PRESENTATIONS if args.presentation == "all" else (args.presentation,)
    return generate_presentations(args.output, identifiers, presentations)


def _run_rerender(args: argparse.Namespace) -> dict[str, Any]:
    identifiers = _ordered_problem_ids() if args.all else args.problem
    return rerender_assets(
        args.output,
        identifiers,
        resvg=args.resvg,
        verify_frozen=args.verify_frozen,
    )


def _run_payload(args: argparse.Namespace) -> dict[str, Any]:
    identifiers = _ordered_problem_ids() if args.all else [args.problem]
    if args.batch:
        if not args.all:
            raise ValueError("--batch requires --all")
        payload = historical_batch_bytes(args.provider, args.presentation, identifiers)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload)
        return {
            "provider": args.provider,
            "presentation": args.presentation,
            "problems": len(identifiers),
            "format": "historical batch input",
            "output": str(args.output.resolve()),
        }

    if args.all:
        args.output.mkdir(parents=True, exist_ok=True)
        for problem_identifier in identifiers:
            _write_json(
                args.output / f"{problem_identifier}.json",
                build_payload(args.provider, problem_identifier, args.presentation),
            )
        return {
            "provider": args.provider,
            "presentation": args.presentation,
            "problems": len(identifiers),
            "format": "direct request bodies",
            "output": str(args.output.resolve()),
        }

    body = build_payload(args.provider, args.problem, args.presentation)
    if args.envelope:
        row = request_hashes_by_key()[(args.provider, args.problem, args.presentation)]
        body = provider_envelope(args.provider, row["custom_id"], body)
    _write_json(args.output, body)
    return {
        "provider": args.provider,
        "presentation": args.presentation,
        "problems": 1,
        "format": "historical batch envelope" if args.envelope else "direct request body",
        "output": str(args.output.resolve()),
        "canonical_bytes": len(canonical_json_bytes(body)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polycomp",
        description="Verify and reproduce the frozen 120-problem BlockSplit benchmark.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("verify", help="Verify manifests, assets, payloads, and scores.")

    generate = subparsers.add_parser("generate", help="Materialize image presentations.")
    generate.add_argument("--output", type=Path, default=Path("generated"))
    selection = generate.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", help="Generate all 120 problems.")
    selection.add_argument("--problem", action="append", help="Problem identifier; repeatable.")
    generate.add_argument(
        "--presentation",
        choices=("all", *PRESENTATIONS),
        default="all",
    )

    rerender = subparsers.add_parser(
        "rerender",
        help="Reconstruct SVGs and PNGs from normalized geometry with resvg (macOS).",
        description=(
            "Reconstruct SVGs and PNGs from normalized geometry with resvg. "
            "This release publishes rerender output on macOS only."
        ),
    )
    rerender.add_argument("--output", type=Path, default=Path("rerendered"))
    rerender_selection = rerender.add_mutually_exclusive_group(required=True)
    rerender_selection.add_argument("--all", action="store_true", help="Rerender all 120 problems.")
    rerender_selection.add_argument(
        "--problem", action="append", help="Problem identifier; repeatable."
    )
    rerender.add_argument(
        "--resvg",
        type=Path,
        help="Path to a resvg executable; otherwise discover it on PATH.",
    )
    rerender.add_argument(
        "--verify-frozen",
        action="store_true",
        help="Fail if any SVG, PNG, crop byte, dimension, or pixel differs from the reference.",
    )

    payload = subparsers.add_parser("payload", help="Create request bodies without submitting.")
    payload.add_argument("--provider", choices=PROVIDERS, required=True)
    payload.add_argument("--presentation", choices=PRESENTATIONS, required=True)
    payload.add_argument("--output", type=Path, required=True)
    payload_selection = payload.add_mutually_exclusive_group(required=True)
    payload_selection.add_argument("--all", action="store_true", help="Create all 120 payloads.")
    payload_selection.add_argument("--problem", help="Create one problem payload.")
    payload.add_argument(
        "--batch",
        action="store_true",
        help="With --all, recreate the complete historical batch input file.",
    )
    payload.add_argument(
        "--envelope",
        action="store_true",
        help="With --problem, wrap the direct body in its historical batch envelope.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "verify":
            result = verify_release()
        elif args.command == "generate":
            result = _run_generate(args)
        elif args.command == "rerender":
            result = _run_rerender(args)
        elif args.command == "payload":
            result = _run_payload(args)
        else:  # pragma: no cover - argparse enforces the command set
            raise AssertionError(args.command)
    except RerenderVerificationError as exc:
        parser.exit(1, f"{parser.prog}: verification failed: {exc}\n")
    except RerenderPrerequisiteError as exc:
        parser.exit(3, f"{parser.prog}: unavailable: {exc}\n")
    except ValueError as exc:
        parser.exit(2, f"{parser.prog}: error: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
