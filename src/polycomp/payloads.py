"""Construct the exact provider request bodies used by the primary run.

This module never reads API keys and contains no submission or network code.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from polycomp.data import (
    assets_by_id,
    problems_by_id,
    protocol,
    request_hashes_by_key,
    resolve_release_path,
)

PROVIDERS = ("openai", "anthropic", "gemini")
PRESENTATIONS = ("single_image", "multi_image_generic", "multi_image_descriptive")
REQUEST_FINGERPRINT_SCHEMA = "frontier-spatial-request-v3"
CELL_FINGERPRINT_SCHEMA = "frontier-spatial-cell-v1"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _attachment_rows(problem_identifier: str, presentation: str) -> list[dict[str, Any]]:
    if presentation not in PRESENTATIONS:
        raise ValueError(f"Unknown presentation: {presentation}")
    try:
        asset_case = assets_by_id()[problem_identifier]
    except KeyError as exc:
        raise ValueError(f"Unknown problem: {problem_identifier}") from exc

    presentation_spec = protocol()["presentations"][presentation]
    if presentation == "single_image":
        source_rows = [asset_case["single_image"]]
    else:
        source_rows = asset_case["crops"]
    labels = presentation_spec["labels"]
    filenames = presentation_spec["submitted_filenames"]
    if not (len(source_rows) == len(labels) == len(filenames)):
        raise ValueError(f"Attachment metadata mismatch for {problem_identifier}/{presentation}")
    return [
        {
            **source,
            "label": label,
            "submitted_filename": filename,
            "absolute_path": resolve_release_path(source["path"]),
        }
        for source, label, filename in zip(source_rows, labels, filenames, strict=True)
    ]


def attachment_hash_bindings(problem_identifier: str, presentation: str) -> list[dict[str, str]]:
    return [
        {
            "submitted_filename": row["submitted_filename"],
            "sha256": row["file_sha256"],
        }
        for row in _attachment_rows(problem_identifier, presentation)
    ]


def _image_data(path: Path) -> tuple[str, str]:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return data, f"data:image/png;base64,{data}"


def build_payload(
    provider: str,
    problem_identifier: str,
    presentation: str,
    *,
    redact_images: bool = False,
) -> dict[str, Any]:
    """Return a direct API request body; no credentials or endpoint call is involved."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    presentation_spec = protocol()["presentations"][presentation]
    provider_spec = protocol()["providers"][provider]
    attachments = _attachment_rows(problem_identifier, presentation)
    prompt = presentation_spec["prompt"]

    if provider == "openai":
        content: list[dict[str, Any]] = []
        for attachment in attachments:
            if attachment["label"] is not None:
                content.append({"type": "input_text", "text": attachment["label"]})
            if redact_images:
                data_url = "data:image/png;base64,<image-bytes-redacted>"
            else:
                _, data_url = _image_data(attachment["absolute_path"])
            content.append(
                {
                    "type": "input_image",
                    "image_url": data_url,
                    "detail": "original",
                }
            )
        content.append({"type": "input_text", "text": prompt})
        return {
            "model": provider_spec["model"],
            "reasoning": provider_spec["settings"]["reasoning"],
            "store": False,
            "max_output_tokens": provider_spec["settings"]["max_output_tokens"],
            "input": [{"role": "user", "content": content}],
        }

    if provider == "anthropic":
        content = []
        for attachment in attachments:
            if attachment["label"] is not None:
                content.append({"type": "text", "text": attachment["label"]})
            data = (
                "<image-bytes-redacted>"
                if redact_images
                else _image_data(attachment["absolute_path"])[0]
            )
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": data,
                    },
                }
            )
        content.append({"type": "text", "text": prompt})
        payload: dict[str, Any] = {
            "model": provider_spec["model"],
            "max_tokens": provider_spec["settings"]["max_tokens"],
            "output_config": provider_spec["settings"]["output_config"],
            "messages": [{"role": "user", "content": content}],
        }
        thinking = provider_spec["settings"].get("thinking")
        if isinstance(thinking, dict) and thinking.get("type"):
            payload["thinking"] = thinking
        return payload

    parts: list[dict[str, Any]] = []
    for attachment in attachments:
        if attachment["label"] is not None:
            parts.append({"text": attachment["label"]})
        data = (
            "<image-bytes-redacted>"
            if redact_images
            else _image_data(attachment["absolute_path"])[0]
        )
        parts.append({"inline_data": {"mime_type": "image/png", "data": data}})
    parts.append({"text": prompt})
    return {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": provider_spec["settings"]["generationConfig"],
        "store": False,
    }


def provider_request_metadata(provider: str) -> dict[str, str]:
    endpoints = protocol()["endpoints"]
    if provider == "openai":
        return {"endpoint": endpoints["openai"]}
    if provider == "anthropic":
        return {"endpoint": endpoints["anthropic"], "api_version": "2023-06-01"}
    if provider == "gemini":
        model = protocol()["providers"][provider]["model"]
        return {"endpoint": endpoints["gemini"].format(model=model)}
    raise ValueError(f"Unknown provider: {provider}")


def logical_request_sha256(provider: str, problem_identifier: str, presentation: str) -> str:
    return canonical_json_sha256(
        {
            "schema": REQUEST_FINGERPRINT_SCHEMA,
            "provider": provider,
            "request_metadata": provider_request_metadata(provider),
            "payload": build_payload(
                provider,
                problem_identifier,
                presentation,
                redact_images=True,
            ),
            "attachments": attachment_hash_bindings(problem_identifier, presentation),
        }
    )


def cell_sha256(provider: str, problem_identifier: str, presentation: str) -> str:
    problem = problems_by_id()[problem_identifier]
    logical_hash = logical_request_sha256(provider, problem_identifier, presentation)
    return canonical_json_sha256(
        {
            "schema": CELL_FINGERPRINT_SCHEMA,
            "request_fingerprint": logical_hash,
            "task_id": problem["dataset_case_id"],
            "problem_id": problem_identifier,
            "formulation_id": presentation,
            "phase_id": "primary",
            "trial_index": 1,
            "execution_mode": "batch",
            "problem_order_sha256": protocol()["frozen_run_plan"]["problem_order_sha256"],
        }
    )


def provider_envelope(
    provider: str,
    custom_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    if provider == "openai":
        return {"custom_id": custom_id, "method": "POST", "url": "/v1/responses", "body": body}
    if provider == "anthropic":
        return {"custom_id": custom_id, "params": body}
    if provider == "gemini":
        return {"key": custom_id, "request": body}
    raise ValueError(f"Unknown provider: {provider}")


def historical_envelope(
    provider: str,
    problem_identifier: str,
    presentation: str,
) -> dict[str, Any]:
    row = request_hashes_by_key()[(provider, problem_identifier, presentation)]
    return provider_envelope(
        provider,
        row["custom_id"],
        build_payload(provider, problem_identifier, presentation),
    )


def historical_batch_bytes(
    provider: str,
    presentation: str,
    ordered_problem_identifiers: Iterable[str],
) -> bytes:
    envelopes = [
        historical_envelope(provider, problem_identifier, presentation)
        for problem_identifier in ordered_problem_identifiers
    ]
    if provider == "anthropic":
        return canonical_json_bytes({"requests": envelopes}) + b"\n"
    return b"".join(canonical_json_bytes(envelope) + b"\n" for envelope in envelopes)
