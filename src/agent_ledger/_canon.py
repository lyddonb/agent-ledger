"""Ledger JSON: the strict JSON subset entries are written in, and its one canonical form.

Private module. The normative rule lives in docs/specs/S3-audit-ledger.md ("Canonical form");
this is its only Python implementation.
"""
from __future__ import annotations

import json
import re
from types import MappingProxyType
from typing import Any, Mapping, Union

JsonValue = Union[None, bool, int, str, "tuple[JsonValue, ...]", "Mapping[str, JsonValue]"]

MAX_SAFE_INT = 2**53 - 1
KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")


class CanonError(ValueError):
    """`kind` is one of "bad-utf8", "bad-json", "non-canonical"."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _check(value: Any, path: str) -> None:
    # bool before int: bool is a subclass of int, and True must never pass as 1.
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise CanonError("bad-json", f"{path}: string has a lone surrogate") from None
        return
    if isinstance(value, int):
        if not -MAX_SAFE_INT <= value <= MAX_SAFE_INT:
            raise CanonError("bad-json", f"{path}: integer {value} is outside +-(2**53-1)")
        return
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _check(item, f"{path}[{i}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not KEY_RE.fullmatch(key):
                raise CanonError("bad-json", f"{path}: key {key!r} must match [a-z][a-z0-9_]{{0,63}}")
            _check(item, f"{path}.{key}")
        return
    raise CanonError("bad-json", f"{path}: {type(value).__name__} is not allowed in ledger JSON")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(v) for v in value]
    return value


def freeze(value: Any) -> JsonValue:
    """Deep-copy into immutable form: lists become tuples, dicts become read-only mappings."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def canonical_bytes(value: Any) -> bytes:
    """CANON(value) as UTF-8 bytes. Raises CanonError("bad-json") for anything outside ledger JSON."""
    _check(value, "$")
    # With these options json.dumps emits exactly RFC 8785 string escaping (\uXXXX in lowercase
    # for other C0 controls, everything else raw) and code-point key order, which equals byte
    # order because keys are ASCII. tests/test_canon.py pins this with golden vectors.
    text = json.dumps(_thaw(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    return text.encode("utf-8")


def _reject_float(token: str) -> Any:
    raise CanonError("bad-json", f"number {token} is not an integer (floats are not ledger JSON)")


def _reject_constant(token: str) -> Any:
    raise CanonError("bad-json", f"{token} is not ledger JSON")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise CanonError("bad-json", f"duplicate key {key!r}")
        out[key] = value
    return out


def parse_canonical(body: bytes) -> Mapping[str, JsonValue]:
    """Parse one line body (no trailing LF) and prove it is already in canonical form.

    Returns the top-level object, deep-frozen. The byte comparison is what makes every
    whitespace, escape-spelling, key-order or "-0" edit an error instead of something
    silently normalised away before hashing.
    """
    try:
        text = body.decode("utf-8", "strict")
    except UnicodeDecodeError as e:
        raise CanonError("bad-utf8", f"not valid UTF-8 at byte {e.start}") from None
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_float=_reject_float,
                           parse_constant=_reject_constant)
    except CanonError:
        raise
    except (ValueError, RecursionError) as e:
        raise CanonError("bad-json", f"not JSON: {e}") from None
    if not isinstance(value, dict):
        raise CanonError("bad-json", "line is not a JSON object")
    if canonical_bytes(value) != body:
        raise CanonError("non-canonical", "bytes differ from the canonical encoding of the same value")
    return freeze(value)  # type: ignore[return-value]
