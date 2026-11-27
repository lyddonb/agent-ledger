"""The case file: a prompt, a fixture and the assertions a run is judged against."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agent_ledger.receipt import CRITERION_RE

_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class CaseError(ValueError):
    """A case file that can't be judged. Every problem found is in `problems`."""

    def __init__(self, path: Path, problems: list[str]):
        self.path = path
        self.problems = problems
        super().__init__(f"{path}: " + "; ".join(problems))


@dataclass(frozen=True)
class CallMatch:
    id: str
    tool: re.Pattern[str]
    args: tuple[str, ...]
    match: re.Pattern[str]


@dataclass(frozen=True)
class AfterCheck:
    id: str
    run: str


@dataclass(frozen=True)
class Case:
    id: str
    directory: Path
    criterion: str
    plugin: PurePosixPath
    fixture: Path
    prompt: str
    tools: tuple[str, ...]
    deny: tuple[str, ...]
    calls: tuple[CallMatch, ...]
    never_calls: tuple[CallMatch, ...]
    after: tuple[AfterCheck, ...]

    @property
    def plugin_name(self) -> str:
        return self.plugin.name


_FIELDS = {"id", "criterion", "plugin", "fixture", "prompt", "tools", "deny", "calls", "never_calls", "after"}
_REQUIRED = {"id", "criterion", "plugin", "fixture", "prompt", "tools"}
_CALL_FIELDS = {"id", "tool", "args", "match"}
_AFTER_FIELDS = {"id", "run"}


def load_case(path: Path) -> Case:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (OSError, ValueError) as e:
        raise CaseError(path, [f"can't read the case: {e}"]) from e
    problems: list[str] = []
    case = _parse(raw, path, problems)
    if problems:
        raise CaseError(path, problems)
    assert case is not None
    return case


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    keys = [k for k, _ in pairs]
    duplicated = sorted({k for k in keys if keys.count(k) > 1})
    if duplicated:
        raise ValueError("a key appears twice in one object: " + ", ".join(duplicated))
    return dict(pairs)


def _parse(raw: object, path: Path, problems: list[str]) -> Case | None:
    if not isinstance(raw, dict):
        problems.append("the case must be a JSON object")
        return None
    problems.extend(f"unknown field {k!r}" for k in sorted(raw.keys() - _FIELDS))
    problems.extend(f"missing field {k!r}" for k in sorted(_REQUIRED - raw.keys()))

    def text(key: str) -> str:
        value = raw.get(key, "")
        if not isinstance(value, str) or (key in _REQUIRED and not value.strip()):
            problems.append(f"{key!r} must be a non-empty string")
            return ""
        return value

    def strings(key: str, *, non_empty: bool) -> tuple[str, ...]:
        value = raw.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            problems.append(f"{key!r} must be a list of non-empty strings")
            return ()
        if non_empty and not value:
            problems.append(f"{key!r} must not be empty")
        return tuple(value)

    case_id = text("id")
    if case_id and not _ID.fullmatch(case_id):
        problems.append(f"'id' {case_id!r} must be lowercase letters, digits and '-'")
    criterion = text("criterion")
    if criterion and not CRITERION_RE.fullmatch(criterion):
        problems.append(f"'criterion' {criterion!r} must be a receipt's criterion id, like P1-C1")
    plugin = PurePosixPath(text("plugin"))
    fixture = text("fixture")
    for key, value in (("plugin", plugin.as_posix()), ("fixture", fixture)):
        if value and (PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts):
            problems.append(f"{key!r} must be a relative path inside its repo, not {value!r}")

    calls = _call_matches(raw.get("calls", []), "calls", problems)
    never_calls = _call_matches(raw.get("never_calls", []), "never_calls", problems)
    after = _after_checks(raw.get("after", []), problems)
    ids = [a.id for a in (*calls, *never_calls, *after)]
    if not ids:
        problems.append("a case needs at least one assertion in 'calls', 'never_calls' or 'after'")
    problems.extend(f"assertion id {i!r} is used twice" for i in sorted({i for i in ids if ids.count(i) > 1}))

    return Case(
        id=case_id,
        directory=path.parent,
        criterion=criterion,
        plugin=plugin,
        fixture=path.parent / fixture,
        prompt=text("prompt"),
        tools=strings("tools", non_empty=True),
        deny=strings("deny", non_empty=False),
        calls=calls,
        never_calls=never_calls,
        after=after,
    )


def _items(value: object, key: str, fields: set[str], problems: list[str]) -> list[tuple[str, dict]]:
    if not isinstance(value, list):
        problems.append(f"{key!r} must be a list")
        return []
    items = []
    for n, item in enumerate(value):
        where = f"{key}[{n}]"
        if not isinstance(item, dict):
            problems.append(f"{where} must be an object")
            continue
        problems.extend(f"{where}: unknown field {k!r}" for k in sorted(item.keys() - fields))
        problems.extend(f"{where}: missing field {k!r}" for k in sorted(fields - item.keys()))
        if item.keys() >= fields:
            item_id = item["id"]
            if not isinstance(item_id, str) or not _ID.fullmatch(item_id):
                problems.append(f"{where}: 'id' must be lowercase letters, digits and '-'")
            else:
                items.append((where, item))
    return items


def _regex(value: object, where: str, problems: list[str]) -> re.Pattern[str] | None:
    if not isinstance(value, str) or not value:
        problems.append(f"{where} must be a non-empty regular expression")
        return None
    try:
        return re.compile(value)
    except re.error as e:
        problems.append(f"{where} is not a valid regular expression: {e}")
        return None


def _call_matches(value: object, key: str, problems: list[str]) -> tuple[CallMatch, ...]:
    out = []
    for where, item in _items(value, key, _CALL_FIELDS, problems):
        tool = _regex(item["tool"], f"{where}.tool", problems)
        match = _regex(item["match"], f"{where}.match", problems)
        args = item["args"]
        if not isinstance(args, list) or not args or not all(isinstance(a, str) and a for a in args):
            problems.append(f"{where}.args must be a non-empty list of input keys")
            continue
        if tool and match:
            out.append(CallMatch(item["id"], tool, tuple(args), match))
    return tuple(out)


def _after_checks(value: object, problems: list[str]) -> tuple[AfterCheck, ...]:
    out = []
    for where, item in _items(value, "after", _AFTER_FIELDS, problems):
        if not isinstance(item["run"], str) or not item["run"].strip():
            problems.append(f"{where}.run must be a non-empty command")
            continue
        out.append(AfterCheck(item["id"], item["run"]))
    return tuple(out)
