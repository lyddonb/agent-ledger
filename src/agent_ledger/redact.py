"""Secret redaction for ledger text and session transcripts (S3 "Session archive").

A miss commits a secret to git history, so redaction is built to fail closed:

- Every pattern is matched against the original text, and the spans are merged, so overlapping
  patterns can't leave a fragment behind.
- The result is confirmed two ways. Redacting it again must change nothing (no pattern finds
  anything but a placeholder), and when the text is JSON lines, every string decoded from it is
  scanned too, which catches a secret hidden behind JSON escapes such as \\u0073k-ant-....
- Anything that can't be confirmed raises `Unconfirmed`. Callers keep that text out of git.

Over-redacting a transcript costs little; under-redacting can't be undone. Patterns lean wide.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

PLACEHOLDER = "[REDACTED:{name}]"
_PLACEHOLDER_RE = re.compile(r"\[REDACTED:[a-z][a-z0-9_]*\]")

# A secret value, as it appears after `key=` or `key: `: stops at whitespace, quotes and
# backslashes (a JSON escape ends the value inside a transcript line).
_VALUE = r"[^\s\"'\\`]{8,}"


@dataclass(frozen=True)
class Pattern:
    """A named secret pattern. When the regex has a group named `secret`, only that group is
    replaced (`password=` stays readable); otherwise the whole match is."""
    name: str
    regex: re.Pattern[str]

    def spans(self, text: str) -> Iterable[tuple[int, int]]:
        for m in self.regex.finditer(text):
            start, end = m.span("secret") if "secret" in self.regex.groupindex else m.span()
            if end > start:
                yield start, end


def _p(name: str, regex: str, flags: int = 0) -> Pattern:
    return Pattern(name, re.compile(regex, flags))


# Order matters only for naming: when spans overlap, the earliest pattern's name labels the
# merged span. Specific vendor patterns come before generic ones.
BUILTIN: tuple[Pattern, ...] = (
    # A PEM or OpenSSH private key, with real or JSON-escaped newlines. A key cut off before
    # its END line is redacted to the end of the JSON string or the text.
    _p("private_key", r"-----BEGIN[A-Z ]*PRIVATE KEY(?: BLOCK)?-----"
                      r"(?:.*?-----END[A-Z ]*PRIVATE KEY(?: BLOCK)?-----|[^\"]*)", re.DOTALL),
    _p("anthropic_key", r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    _p("openai_key", r"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}"),
    _p("github_token", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    _p("gitlab_token", r"\bglpat-[A-Za-z0-9_\-]{20,}"),
    _p("aws_access_key_id", r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
    _p("aws_secret_access_key",
       r"(?i)aws_?secret_?(?:access_?)?key(?:\\?[\"'`])?\s*[:=]\s*(?:\\?[\"'`])?(?P<secret>[A-Za-z0-9/+=]{40})"),
    _p("google_api_key", r"\bAIza[0-9A-Za-z_\-]{35}"),
    _p("slack_token", r"\bxox[abposre]-[A-Za-z0-9\-]{10,}"),
    _p("slack_webhook", r"https://hooks\.slack\.com/services/[A-Za-z0-9/_\-]+"),
    _p("stripe_key", r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    _p("npm_token", r"\bnpm_[A-Za-z0-9]{36}\b"),
    _p("jwt", r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    _p("bearer_token", r"(?i)\bbearer\s+(?P<secret>[A-Za-z0-9._~+/\-]{16,}=*)"),
    _p("url_password", r"\b[a-z][a-z0-9+.\-]*://[^\s:/@\"'\\]+:(?P<secret>[^\s@/\"'\\]+)@"),
    _p("secret_assignment",
       r"(?i)(?:password|passwd|pwd|secret|api_?key|apikey|access_?token|auth_?token|"
       r"client_?secret|private_?key|token)(?:\\?[\"'`])?\s*[:=]\s*(?:\\?[\"'`])?(?P<secret>" + _VALUE + ")"),
)


class Unconfirmed(Exception):
    """Redaction ran but can't be shown complete: the text stays out of git."""


class PolicyError(ValueError):
    """A policy pattern is missing, unreadable or not a valid regex."""


@dataclass(frozen=True)
class Redacted:
    text: str
    counts: Mapping[str, int] = field(default_factory=dict)  # pattern name -> spans redacted


class Redactor:
    def __init__(self, extra: Iterable[str] = ()) -> None:
        patterns = list(BUILTIN)
        for i, source in enumerate(extra, 1):
            try:
                patterns.append(Pattern(f"policy_{i}", re.compile(source)))
            except re.error as e:
                raise PolicyError(f"redact pattern {i} {source!r} is not a valid regex: {e}") from None
        self.patterns: tuple[Pattern, ...] = tuple(patterns)

    def _redact_once(self, text: str) -> Redacted:
        spans: list[tuple[int, int, str]] = []
        for p in self.patterns:
            spans.extend((s, e, p.name) for s, e in p.spans(text))
        if not spans:
            return Redacted(text, {})
        # Merge overlapping or touching spans; the merged span takes its first pattern's name.
        spans.sort(key=lambda t: (t[0], -t[1]))
        merged: list[list[Any]] = []
        for s, e, name in spans:
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e, name])
        out, counts, pos = [], {}, 0
        for s, e, name in merged:
            out.append(text[pos:s])
            out.append(PLACEHOLDER.format(name=name))
            counts[name] = counts.get(name, 0) + 1
            pos = e
        out.append(text[pos:])
        return Redacted("".join(out), counts)

    def _leftover(self, text: str) -> str | None:
        """The name of a pattern that still finds something other than placeholders, or None."""
        for p in self.patterns:
            for s, e in p.spans(text):
                if _PLACEHOLDER_RE.sub("", text[s:e]).strip():
                    return p.name
        return None

    def redact(self, text: str) -> Redacted:
        """Redact `text`, or raise Unconfirmed if the result still matches a pattern."""
        result = self._redact_once(text)
        leftover = self._leftover(result.text)
        if leftover is not None:
            raise Unconfirmed(f"a {leftover} match survived redaction")
        return result

    def redact_transcript(self, text: str) -> Redacted:
        """Redact a transcript of JSON lines (other lines get the text scan only).

        Each JSON line also has every decoded string scanned. A secret the text scan can't see,
        such as one written with JSON escapes or inside JSON nested in a string, makes that line
        get rebuilt from its decoded value with every string redacted, then checked again.
        """
        result = self.redact(text)
        counts = dict(result.counts)
        lines = result.text.split("\n")
        for n, line in enumerate(lines):
            try:
                value = json.loads(line) if line.strip() else None
            except ValueError:
                continue  # not JSON: the text scan above already covered it
            if not any(self._leftover(s) for s in _strings(value)):
                continue
            clean, extra = self.redact_value(value)
            rebuilt = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
            leftover = self._leftover(rebuilt) or next(
                (name for s in _strings(json.loads(rebuilt)) if (name := self._leftover(s))), None)
            if leftover is not None:
                raise Unconfirmed(f"line {n + 1}: a {leftover} match survived redaction")
            lines[n] = rebuilt
            for k, c in extra.items():
                counts[k] = counts.get(k, 0) + c
        return Redacted("\n".join(lines), counts)

    def redact_value(self, value: Any) -> tuple[Any, dict[str, int]]:
        """Redact every string inside a JSON value (keys are names, not content, and are left)."""
        counts: dict[str, int] = {}

        def walk(v: Any) -> Any:
            if isinstance(v, str):
                r = self.redact(v)
                for k, c in r.counts.items():
                    counts[k] = counts.get(k, 0) + c
                return r.text
            if isinstance(v, Mapping):
                return {k: walk(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [walk(x) for x in v]
            return v

        return walk(value), counts


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


# --- policy -----------------------------------------------------------------------------------

POLICY_FILES = (Path(".agents") / "policy.yaml",)  # relative to the repo root

_KEY_LINE = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z_][A-Za-z0-9_]*):(?P<rest>.*)$")
_ITEM_LINE = re.compile(r"^(?P<indent> *)- *(?P<value>.*?) *$")
_SINGLE_QUOTED = re.compile(r"'((?:[^']|'')*)'")


def _strip_comment(rest: str) -> str:
    rest = rest.strip()
    return "" if rest.startswith("#") else re.sub(r"\s+#.*$", "", rest)


def read_policy_patterns(path: Path) -> list[str]:
    """`security.redact_patterns` from one policy file.

    There is no YAML parser in the standard library, so this reads only the shapes the policy
    uses for this key: `redact_patterns: []`, or a block list of single-quoted strings (single
    quotes keep regex backslashes literal). Anything else is a PolicyError: guessing at a
    pattern list would silently drop a pattern the policy author relies on.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise PolicyError(f"{path}: can't read: {e}") from None
    section_indent: int | None = None
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _KEY_LINE.match(line)
        if not m:
            continue
        indent = len(m["indent"])
        if indent == 0:
            section_indent = 0 if m["key"] == "security" else None
            continue
        if section_indent is None or m["key"] != "redact_patterns":
            continue
        rest = _strip_comment(m["rest"])
        if rest == "[]":
            return []
        if rest:
            raise PolicyError(f"{path}:{i + 1}: write security.redact_patterns as [] or as a list of "
                              f"single-quoted strings, one per line")
        patterns: list[str] = []
        for j in range(i + 1, len(lines)):
            item = lines[j]
            if not item.strip() or item.lstrip().startswith("#"):
                continue
            im = _ITEM_LINE.match(item)
            if not im or len(im["indent"]) < indent:
                break
            value = _strip_comment(im["value"])
            q = _SINGLE_QUOTED.fullmatch(value)
            if not q:
                raise PolicyError(f"{path}:{j + 1}: quote each redact pattern in single quotes, "
                                  f"like '\\bacme_[a-z0-9]{{32}}\\b'")
            patterns.append(q.group(1).replace("''", "'"))
        if not patterns:
            raise PolicyError(f"{path}:{i + 1}: security.redact_patterns is empty; write [] if that's meant")
        return patterns
    return []


def user_policy_path() -> Path:
    return Path.home() / ".agents" / "policy.yaml"


def policy_patterns(root: Path, user_policy: Path | None = None) -> list[str]:
    """The union of redact patterns from the user (~/.agents) and project policy files that exist."""
    user_policy = user_policy or user_policy_path()
    found: list[str] = []
    for path in [user_policy, *(root / p for p in POLICY_FILES)]:
        if path.is_file():
            for pattern in read_policy_patterns(path):
                if pattern not in found:
                    found.append(pattern)
    return found
