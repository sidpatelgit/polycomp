"""Small, dependency-free implementation of the published option-scoring rule."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

OPTIONS = frozenset({"A", "B", "C", "D"})


class DuplicateMemberError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise DuplicateMemberError("duplicate JSON member")
    return dict(pairs)


def _json_objects(text: str) -> Iterator[dict[str, Any]]:
    for start, character in enumerate(text):
        if character != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            current = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(
                            text[start : index + 1],
                            object_pairs_hook=_unique_object,
                        )
                    except json.JSONDecodeError:
                        break
                    if isinstance(value, dict):
                        yield value
                    break


def selected_option(text: str) -> str | None:
    options: set[str] = set()
    try:
        for value in _json_objects(text):
            option = value.get("option")
            if isinstance(option, str) and option.strip().upper() in OPTIONS:
                options.add(option.strip().upper())
    except DuplicateMemberError:
        return None
    if len(options) == 1:
        return next(iter(options))
    return None
