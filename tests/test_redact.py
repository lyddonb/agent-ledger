"""Redaction: a corpus of planted fake secrets, in every encoding a transcript can hold them,
never survives; the result is confirmed or refused, never guessed.

Every fake secret is assembled at run time from pieces, so this file holds no string that a
secret scanner (or GitHub push protection) would flag.
"""
import json
import random
import string
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts src on the path)

from agent_ledger.redact import BUILTIN, PolicyError, Redactor, Unconfirmed, policy_patterns, read_policy_patterns

ALNUM = string.ascii_letters + string.digits
UPPER_DIGITS = string.ascii_uppercase + string.digits


def rand(rng, n, alphabet=ALNUM):
    return "".join(rng.choice(alphabet) for _ in range(n))


# name -> make(rng) -> (the secret as written, a fragment that must not survive)
def _pem(rng):
    body = "\n".join(rand(rng, 64, ALNUM + "+/") for _ in range(3))
    kind = rng.choice(["RSA ", "EC ", "OPENSSH ", ""])
    return f"-----BEGIN {kind}PRIVATE KEY-----\n{body}\n-----END {kind}PRIVATE KEY-----", body[:40]


def _with_tail(prefix, n, alphabet=ALNUM):
    def make(rng):
        tail = rand(rng, n, alphabet)
        return prefix + tail, tail
    return make


def _assigned(key, n=24):
    def make(rng):
        value = rand(rng, n)
        sep = rng.choice(["=", ": ", " = ", "="])
        quote = rng.choice(["", '"', "'"])
        return f"{key}{sep}{quote}{value}{quote}", value
    return make


def _url_password(rng):
    pw = rand(rng, 14)
    return f"postgres://app:{pw}@db.internal:5432/main", pw


def _bearer(rng):
    tok = rand(rng, 40, ALNUM + "._-")
    return f"Authorization: Bearer {tok}", tok


def _jwt(rng):
    parts = ["ey" + "J" + rand(rng, 20, ALNUM + "_-") for _ in range(2)] + [rand(rng, 30, ALNUM + "_-")]
    return ".".join(parts), parts[2]


def _aws_secret(rng):
    value = rand(rng, 40, ALNUM + "/+")
    return f"aws_secret_access_key = {value}", value


CORPUS = {
    "private_key": _pem,
    "anthropic_key": _with_tail("sk-" + "ant-api03-", 40, ALNUM + "_-"),
    "openai_key": _with_tail("sk-" + "proj-", 40, ALNUM + "_-"),
    "github_token": _with_tail("gh" + "p_", 36),
    "github_pat": _with_tail("github" + "_pat_", 60, ALNUM + "_"),
    "gitlab_token": _with_tail("gl" + "pat-", 20),
    "aws_access_key_id": _with_tail("AK" + "IA", 16, UPPER_DIGITS),
    "aws_secret_access_key": _aws_secret,
    "google_api_key": _with_tail("AI" + "za", 35, ALNUM + "_-"),
    "slack_token": _with_tail("xo" + "xb-", 30, ALNUM + "-"),
    "slack_webhook": _with_tail("https://hooks.slack.com/" + "services/T000/B000/", 24),
    "stripe_key": _with_tail("sk" + "_live_", 24),
    "npm_token": _with_tail("np" + "m_", 36),
    "jwt": _jwt,
    "bearer_token": _bearer,
    "url_password": _url_password,
    "password": _assigned("password"),
    "api_key": _assigned("API_KEY"),
    "client_secret": _assigned("client_secret"),
}

# How a secret reaches a transcript: as typed, inside a JSON string, inside JSON nested in a JSON
# string (a tool result), and with its first character JSON-escaped.
ENCODINGS = {
    "plain": lambda s: f"ran it with {s} and moved on",
    "json": lambda s: json.dumps({"type": "user", "content": f"use {s} here"}),
    "nested_json": lambda s: json.dumps({"tool_result": json.dumps({"env": f"{s}"})}),
    "ascii_escaped": lambda s: json.dumps({"text": s}).replace(s[0], "\\u%04x" % ord(s[0]), 1),
    "shell": lambda s: json.dumps({"command": f"export X=1; curl -s '{s}'"}),
}

# A transcript is JSON lines; plain text can hold a multi-line key, which changes the line count.
JSON_ENCODINGS = [wrap for name, wrap in ENCODINGS.items() if name != "plain"]


def survives(fragment, text):
    """The fragment is in the text, or in any string decoded from JSON lines in it, at any depth."""
    if fragment in text:
        return True
    pending = list(text.split("\n"))
    while pending:
        s = pending.pop()
        try:
            v = json.loads(s)
        except ValueError:
            continue
        stack = [v]
        while stack:
            x = stack.pop()
            if isinstance(x, str):
                if fragment in x:
                    return True
                pending.append(x)
            elif isinstance(x, dict):
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)
    return False


class PlantedSecretTest(unittest.TestCase):
    def test_every_planted_secret_is_removed_in_every_encoding(self):
        r = Redactor()
        rng = random.Random(3)
        for name, make in CORPUS.items():
            for enc, wrap in ENCODINGS.items():
                for _ in range(5):
                    secret, fragment = make(rng)
                    text = wrap(secret)
                    with self.subTest(pattern=name, encoding=enc, text=text[:80]):
                        out = r.redact_transcript(text).text
                        self.assertFalse(survives(fragment, out), f"secret survived: {out}")
                        self.assertIn("[REDACTED:", out)

    def test_secrets_scattered_through_a_long_transcript_all_go(self):
        r = Redactor()
        for seed in range(20):
            rng = random.Random(seed)
            lines, fragments = [], []
            for _ in range(rng.randint(5, 30)):
                if rng.random() < 0.4:
                    secret, fragment = rng.choice(list(CORPUS.values()))(rng)
                    lines.append(rng.choice(JSON_ENCODINGS)(secret))
                    fragments.append(fragment)
                else:
                    lines.append(json.dumps({"text": helpers.rand_text(rng)}))
            text = "\n".join(lines) + "\n"
            with self.subTest(seed=seed):
                out = r.redact_transcript(text).text
                for fragment in fragments:
                    self.assertFalse(survives(fragment, out), f"seed {seed}: {fragment} survived")
                self.assertTrue(out.endswith("\n"))
                self.assertEqual(len(out.split("\n")), len(text.split("\n")), "line count changed")

    def test_redacting_twice_changes_nothing(self):
        r = Redactor()
        rng = random.Random(11)
        for name, make in CORPUS.items():
            text = ENCODINGS["json"](make(rng)[0])
            once = r.redact_transcript(text).text
            with self.subTest(pattern=name):
                self.assertEqual(r.redact_transcript(once).text, once)

    def test_every_builtin_pattern_is_exercised_by_the_corpus(self):
        r = Redactor()
        rng = random.Random(5)
        seen = set()
        for make in CORPUS.values():
            seen.update(r.redact(make(rng)[0]).counts)
        self.assertEqual(seen, {p.name for p in BUILTIN})


class OrdinaryTextTest(unittest.TestCase):
    def test_text_without_secrets_is_unchanged(self):
        r = Redactor()
        rng = random.Random(8)
        for _ in range(500):
            line = json.dumps({"k": helpers.rand_text(rng, helpers._DATA_POOL), "n": rng.randint(0, 10**9)})
            self.assertEqual(r.redact_transcript(line).text, line)

    def test_ledger_shaped_text_is_left_alone(self):
        r = Redactor()
        for text in ["commit 0ad83b2", "a" * 64, "input_tokens: 123456789", "see src/agent_ledger/run.py:42",
                     "max_tokens=4096", "the token budget is 8000000"]:
            self.assertEqual(r.redact(text).text, text)


class RefusalTest(unittest.TestCase):
    def test_a_line_it_cannot_clean_is_refused(self):
        # A secret split by a JSON escape inside an object key: values get redacted, keys don't.
        key = "sk-" + "ant-" + "k" * 30
        line = json.dumps({key: 1}).replace("s", "\\u0073", 1)
        with self.assertRaises(Unconfirmed):
            Redactor().redact_transcript(line)

    def test_policy_patterns_join_the_builtins(self):
        r = Redactor([r"\bacme_[a-z0-9]{12}\b"])
        out = r.redact("deploy key acme_abcdef123456 ok")
        self.assertEqual(out.text, "deploy key [REDACTED:policy_1] ok")
        self.assertEqual(dict(out.counts), {"policy_1": 1})

    def test_a_bad_policy_regex_is_an_error(self):
        with self.assertRaises(PolicyError):
            Redactor(["(unclosed"])

    def test_redact_value_keeps_structure_and_types(self):
        clean, counts = Redactor().redact_value(
            {"a": ["x", "password=" + "q" * 12], "n": 3, "b": True, "z": None})
        self.assertEqual(clean, {"a": ["x", "password=[REDACTED:secret_assignment]"], "n": 3, "b": True, "z": None})
        self.assertEqual(counts, {"secret_assignment": 1})


class PolicyFileTest(unittest.TestCase):
    def write(self, text):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d))
        p = d / "policy.yaml"
        p.write_text(text)
        return p

    def test_reads_a_block_list_of_single_quoted_patterns(self):
        p = self.write("version: 1\nsecurity:\n  network_allow: [github.com]\n"
                       "  redact_patterns:   # ours\n    - '\\bacme_[a-z]{4}\\b'\n    - 'it''s'\n"
                       "audit:\n  sinks: [repo]\n")
        self.assertEqual(read_policy_patterns(p), [r"\bacme_[a-z]{4}\b", "it's"])

    def test_empty_list_and_missing_key_mean_no_patterns(self):
        self.assertEqual(read_policy_patterns(self.write("security:\n  redact_patterns: []\n")), [])
        self.assertEqual(read_policy_patterns(self.write("version: 1\ngates: [deploy]\n")), [])

    def test_a_key_outside_security_is_ignored(self):
        p = self.write("other:\n  redact_patterns:\n    - 'x'\nsecurity:\n  redact_patterns: []\n")
        self.assertEqual(read_policy_patterns(p), [])

    def test_shapes_it_cannot_read_are_errors_not_skipped(self):
        for text in ["security:\n  redact_patterns: ['a', 'b']\n",
                     "security:\n  redact_patterns:\n    - \"a\\\\b\"\n",
                     "security:\n  redact_patterns:\n    - plain\n",
                     "security:\n  redact_patterns:\n"]:
            with self.subTest(text=text), self.assertRaises(PolicyError):
                read_policy_patterns(self.write(text))

    def test_user_and_project_patterns_are_unioned(self):
        user = self.write("security:\n  redact_patterns:\n    - 'one'\n    - 'two'\n")
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        (root / ".agents").mkdir()
        (root / ".agents" / "policy.yaml").write_text("security:\n  redact_patterns:\n    - 'two'\n    - 'three'\n")
        self.assertEqual(policy_patterns(root, user_policy=user), ["one", "two", "three"])


if __name__ == "__main__":
    unittest.main()
