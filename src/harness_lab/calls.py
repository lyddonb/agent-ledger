"""A headless agent run as the judge sees it, and the adapter from `claude -p`'s stream."""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping


class Outcome(Enum):
    RAN = "ran"
    DENIED = "denied"
    REJECTED = "rejected"
    NO_RESULT = "no-result"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: Mapping[str, object]
    parent_id: str | None
    outcome: Outcome


@dataclass(frozen=True)
class Run:
    calls: tuple[ToolCall, ...]
    plugins: frozenset[str] | None
    ended: str | None


class StreamError(ValueError):
    """A stream the adapter can't read. The run can't be judged."""


_REJECTED_PREFIX = "<tool_use_error>"


def read_claude_stream(lines: Iterable[str]) -> Run:
    """Turn `claude -p --output-format stream-json --verbose` output into a Run.

    `plugins` is None when no `init` event arrived, and otherwise the plugins every `init`
    listed. `ended` is the last `result` event's subtype, or None when the stream has none. A run that resumed (a background task
    finishing) has several `init` and `result` events; its calls are all of them in order.
    """
    uses: list[tuple[dict, str | None]] = []
    results: dict[str, dict] = {}
    denied: set[str] = set()
    plugins: frozenset[str] | None = None
    ended: str | None = None
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as e:
            raise StreamError(f"line {n} is not JSON: {e}") from e
        if not isinstance(event, dict):
            raise StreamError(f"line {n} is not a JSON object")
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            loaded = frozenset(p["name"] for p in event.get("plugins", []) if isinstance(p, dict) and "name" in p)
            plugins = loaded if plugins is None else plugins & loaded
        elif kind == "assistant":
            for block in _blocks(event):
                if block.get("type") == "tool_use":
                    uses.append((block, event.get("parent_tool_use_id")))
        elif kind == "user":
            for block in _blocks(event):
                if block.get("type") == "tool_result":
                    results[block.get("tool_use_id")] = block
        elif kind == "result":
            ended = event.get("subtype") if not event.get("is_error") else "error"
            denied.update(d.get("tool_use_id") for d in event.get("permission_denials") or [] if isinstance(d, dict))
    calls = []
    for block, parent in uses:
        try:
            call_id, name, call_input = block["id"], block["name"], block["input"]
        except KeyError as e:
            raise StreamError(f"a tool_use block has no {e.args[0]!r}") from e
        if not isinstance(call_input, dict):
            raise StreamError(f"tool_use {call_id} has an input that is not an object")
        calls.append(ToolCall(call_id, name, MappingProxyType(call_input), parent, _outcome(call_id, results, denied)))
    return Run(tuple(calls), plugins, ended)


def _blocks(event: dict) -> list[dict]:
    content = (event.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _outcome(call_id: str, results: Mapping[str, dict], denied: set[str]) -> Outcome:
    if call_id in denied:
        return Outcome.DENIED
    result = results.get(call_id)
    if result is None:
        return Outcome.NO_RESULT
    if result.get("is_error") and _text(result.get("content")).startswith(_REJECTED_PREFIX):
        return Outcome.REJECTED
    return Outcome.RAN


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""
