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

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

PLACEHOLDER = "[REDACTED:{name}]"
_PLACEHOLDER_RE = re.compile(r"\[REDACTED:[a-z][a-z0-9_]*\]")

# A secret value, as it appears after `key=` or `key: `: stops at whitespace, quotes and
# backslashes (a JSON escape ends the value inside a transcript line).
_VALUE = r"[^\s\"'\\`]{4,}"
# Not preceded by a letter or digit, so "risk-..." and "task-..." aren't read as an `sk-` key.
_START = r"(?<![A-Za-z0-9])"


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


_ASSIGN_KEY = (r"(?i)(?:password|passwd|pwd|secret|api_?key|apikey|access_?token|auth_?token|"
               r"client_?secret|private_?key|token)(?:\\*[\"'`])?")

# Order matters only for naming: when spans overlap, the earliest pattern's name labels the
# merged span. Specific vendor patterns come before generic ones.
BUILTIN: tuple[Pattern, ...] = (
    # A PEM or OpenSSH private key, with real or JSON-escaped newlines. It never spans a quote,
    # so it stays inside one JSON string; a key cut off before END is redacted to the end of
    # the string or line. Escape pairs (\\x) are consumed whole, so a match never ends between
    # a backslash and the quote it escapes. A 4096-bit RSA key is about 3.3 KB, so 16 KB bounds the search.
    _p("private_key", r"-----BEGIN[A-Z ]*PRIVATE KEY(?: BLOCK)?-----"
                      r"(?:(?:[^\"\\]|\\.){0,16384}?-----END[A-Z ]*PRIVATE KEY(?: BLOCK)?-----|(?:[^\"\\\n]|\\.)*)"),
    _p("anthropic_key", _START + r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    _p("openai_key", _START + r"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}"),
    _p("github_token", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    _p("gitlab_token", r"\bglpat-[A-Za-z0-9_\-]{20,}"),
    _p("aws_access_key_id", r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
    _p("aws_secret_access_key",
       r"(?i)aws_?secret_?(?:access_?)?key(?:\\*[\"'`])?\s*[:=]\s*(?:\\*[\"'`])?(?P<secret>[A-Za-z0-9/+=]{40})"),
    _p("google_api_key", r"\bAIza[0-9A-Za-z_\-]{35}"),
    _p("slack_token", r"\bxox[abposre]-[A-Za-z0-9\-]{10,}"),
    _p("slack_webhook", r"https://hooks\.slack\.com/services/[A-Za-z0-9/_\-]+"),
    _p("stripe_key", r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    _p("npm_token", r"\bnpm_[A-Za-z0-9]{36}\b"),
    _p("jwt", r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    _p("bearer_token", r"(?i)\bbearer\s+(?P<secret>[A-Za-z0-9._~+/\-]{16,}=*)"),
    _p("url_password", r"(?<![a-z0-9+.\-])[a-z][a-z0-9+.\-]{0,30}://[^\s:/@\"'\\]+:(?P<secret>[^\s@/\"'\\]+)@"),
    _p("basic_auth", r"(?i)authorization(?:\\*[\"'`])?\s*[:=]\s*(?:\\*[\"'`])?basic\s+(?P<secret>[A-Za-z0-9+/]{4,}={0,2})"),
    # Quoted values go to the closing quote, so a short or spaced password is caught whole.
    _p("secret_assignment", _ASSIGN_KEY + r"\s*[:=]\s*\\*[\"'`](?P<secret>[^\"'`\\\r\n]{1,256})\\*[\"'`]"),
    _p("secret_assignment", _ASSIGN_KEY + r"\s*[:=]\s*(?:\\*[\"'`])?(?P<secret>" + _VALUE + ")"),
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
        # Only placeholders this redactor writes count as redacted, so a token dressed up as
        # `[REDACTED:ghp_...]` still matches.
        names = sorted({p.name for p in self.patterns}, key=len, reverse=True)
        self._placeholder_re = re.compile(r"\[REDACTED:(?:" + "|".join(map(re.escape, names)) + r")\]")
        # Names the exact pattern set, so an archive made with other patterns gets redone.
        self.fingerprint = hashlib.sha256(
            "\n".join(f"{p.name}\t{p.regex.pattern}" for p in self.patterns).encode()).hexdigest()

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
        """The name of a pattern that still finds something outside placeholders, or None."""
        # Blank out placeholders (same length, so spans line up), then a span matters if any
        # of its characters is left: linear in the text, however many placeholders there are.
        masked = self._placeholder_re.sub(lambda m: " " * len(m.group()), text)
        for p in self.patterns:
            for s, e in p.spans(text):
                if masked[s:e].strip():
                    return p.name
        return None

    def _leftover_in_views(self, line: str) -> str | None:
        """Check a line in every form a reader could decode it to: as written, with escapes
        collapsed, and (for JSON) every string decoded, recursing into strings that are JSON."""
        for view in _views(line):
            name = self._leftover(view)
            if name is not None:
                return name
        return None

    def find(self, line: str) -> str | None:
        """The name of a pattern that matches somewhere in `line`, in any decoded view, or None.
        Placeholders don't count, so redacted text scans clean."""
        return self._leftover_in_views(line)

    def redact(self, text: str) -> Redacted:
        """Redact `text`, or raise Unconfirmed if the result still matches a pattern."""
        result = self._redact_once(text)
        leftover = self._leftover(result.text)
        if leftover is not None:
            raise Unconfirmed(f"a {leftover} match survived redaction")
        return result

    def confirm(self, text: str) -> None:
        """Raise Unconfirmed if any line of `text`, in any decoded view, still matches."""
        for n, line in enumerate(text.split("\n"), 1):
            leftover = self._leftover_in_views(line)
            if leftover is not None:
                raise Unconfirmed(f"line {n}: a {leftover} match survived in a decoded view of the text")

    def redact_transcript(self, text: str) -> Redacted:
        """Redact a transcript.

        A transcript of JSON lines is redacted line by line. Each redacted line must still parse
        and must hold no match in any decoded view (escapes collapsed, strings decoded, JSON in
        strings decoded again); otherwise it is rebuilt from the original line's value with
        every string and key redacted, and checked again. Any other text is redacted whole
        (a multi-line key can span lines) and then must hold no match in any view.
        Whatever can't be confirmed raises Unconfirmed.
        """
        lines = text.split("\n")
        values = []
        for line in lines:
            try:
                values.append(json.loads(line) if line.strip() else None)
            except ValueError:
                result = self.redact(text)
                self.confirm(result.text)
                return result
        counts: dict[str, int] = {}
        out = []
        for n, (line, value) in enumerate(zip(lines, values), 1):
            r = self._redact_once(line)
            if self._clean_json_line(r.text):
                out.append(r.text)
                extra = r.counts
            else:
                clean, extra = self.redact_value(value)
                rebuilt = json.dumps(clean, ensure_ascii=False, separators=(",", ":")) if line.strip() else line
                leftover = self._leftover_in_views(rebuilt)
                if leftover is not None:
                    raise Unconfirmed(f"line {n}: a {leftover} match survived redaction")
                out.append(rebuilt)
            for k, c in extra.items():
                counts[k] = counts.get(k, 0) + c
        return Redacted("\n".join(out), counts)

    def _clean_json_line(self, line: str) -> bool:
        if line.strip():
            try:
                json.loads(line)
            except ValueError:
                return False
        return self._leftover_in_views(line) is None

    def redact_value(self, value: Any) -> tuple[Any, dict[str, int]]:
        """Redact every string and key inside a JSON value. A string that is itself JSON is
        decoded, redacted and encoded again, so nested escaping can't hide a secret."""
        counts: dict[str, int] = {}

        def text(v: str, depth: int) -> str:
            nested = _as_json(v) if depth < _MAX_NESTING else None
            if nested is not None:
                return json.dumps(walk(nested, depth + 1), ensure_ascii=False, separators=(",", ":"))
            r = self._redact_once(v)
            if "\\" in v and self._leftover(_collapse(r.text)) is not None:
                # A secret spelled with escapes inside the decoded string (\\u0073k-...): redact
                # the unescaped form. Only that string's escaping changes.
                r = self._redact_once(_collapse(v))
            for k, c in r.counts.items():
                counts[k] = counts.get(k, 0) + c
            return r.text

        def walk(v: Any, depth: int) -> Any:
            if isinstance(v, str):
                return text(v, depth)
            if isinstance(v, Mapping):
                return {text(k, depth): walk(x, depth) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [walk(x, depth) for x in v]
            return v

        return walk(value, 0), counts


_MAX_NESTING = 8
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_ESCAPED_CHAR = re.compile(r"\\+([\"'/])")


def _collapse(text: str) -> str:
    """Undo JSON-style escaping at any depth: \\uXXXX, and any run of backslashes before a quote
    or slash. The result isn't valid anything; it's only for scanning."""
    for _ in range(_MAX_NESTING):
        out = _ESCAPED_CHAR.sub(r"\1", _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text))
        if out == text:
            break
        text = out
    return text


def _as_json(text: str) -> Any:
    """The JSON value a string holds, when it is a JSON object or array; else None."""
    t = text.strip()
    if not t or t[0] not in "{[":
        return None
    try:
        return json.loads(t)
    except ValueError:
        return None


def _verbatim(s: str) -> bool:
    """The string appears as-is in its JSON line, so scanning the line already covered it. A
    string written with escapes in the line is caught by the line's collapsed view instead."""
    return "\\" not in s and json.dumps(s, ensure_ascii=False)[1:-1] == s


def _views(line: str) -> Iterable[str]:
    yield line
    if "\\" in line:
        yield _collapse(line)
    try:
        value = json.loads(line) if line.strip() else None
    except ValueError:
        return
    stack = [(value, 0)]
    while stack:
        v, depth = stack.pop()
        for s in _strings(v):
            if not _verbatim(s):
                yield s
                if "\\" in s:
                    yield _collapse(s)
            if depth < _MAX_NESTING:
                nested = _as_json(s)
                if nested is not None:
                    stack.append((nested, depth + 1))


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


def read_policy_patterns(path: Path, text: str | None = None) -> list[str]:
    """`security.redact_patterns` from one policy file.

    There is no YAML parser in the standard library, so this reads only the shapes the policy
    uses for this key: `redact_patterns: []`, or a block list of single-quoted strings (single
    quotes keep regex backslashes literal). Anything else is a PolicyError: guessing at a
    pattern list would silently drop a pattern the policy author relies on.
    """
    try:
        lines = (text if text is not None else path.read_text(encoding="utf-8-sig")).splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise PolicyError(f"{path}: can't read: {e}") from None
    mentions = [i for i, line in enumerate(lines)
                 if "redact_patterns" in line.split("#", 1)[0]]
    if len(mentions) > 1:
        raise PolicyError(f"{path}: redact_patterns appears {len(mentions)} times; keep one")
    if mentions and not _KEY_LINE.match(lines[mentions[0]]):
        raise PolicyError(f"{path}:{mentions[0] + 1}: write redact_patterns as a plain key under "
                          f"`security:`, on its own line")
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
    if mentions:
        raise PolicyError(f"{path}:{mentions[0] + 1}: redact_patterns must sit directly under a "
                          f"top-level `security:` key")
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


def base_policy_patterns(root: Path, base: str) -> list[str]:
    """Redact patterns from the project policy as committed at `base`. With `--base`, these are
    added to the working policy's, so a change can't remove a pattern and leak what it matched
    in the same pull request."""
    from . import _git
    rel = POLICY_FILES[0].as_posix()
    data = _git.file_at(root, _git.resolve(root, base), rel)
    if data is None:
        return []
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise PolicyError(f"{rel} at {base}: not UTF-8") from None
    return read_policy_patterns(Path(f"{rel} at {base}"), text)
