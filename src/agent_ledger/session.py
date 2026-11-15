"""`ledger session add`: redact a transcript, archive it beside the run's ledger, and append a
`session` entry holding both hashes (S3 "Session archive")."""
from __future__ import annotations

import gzip
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .entry import Actor, Entry, EntryType
from .redact import Redactor, Unconfirmed
from .run import RedactionRefused, append
from .sink import LedgerError, RepoSink

SESSIONS_DIR = "sessions"
SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]{0,127}")
# Policy audit.sessions.lfs_over_mb defaults to 20; routing big files to LFS comes with policy
# reading in unit 7, so until then a big archive only gets a warning.
LFS_OVER_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class SessionResult:
    entry: Entry
    archived: bool
    new: bool  # False when this exact transcript was already recorded, so nothing was written
    warning: str | None = None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _compress(data: bytes) -> bytes:
    # mtime=0 and no file name, so the same redacted text always gives the same archive bytes.
    return gzip.compress(data, compresslevel=9, mtime=0)


def _stage(folder: Path, name: str, data: bytes) -> Path:
    """Write `data` to a temporary file in `folder`, synced; the caller renames it into place."""
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{name}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o644)  # mkstemp makes 0600; an archive is an ordinary repo file
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return Path(tmp)


def _where(path: Path) -> str:
    """A transcript's location for the ledger: under the home folder it is written `~/...`, so
    the committed entry doesn't carry the user name or machine layout."""
    path = path.resolve()
    try:
        return "~/" + path.relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _sessions_dir(sink: RepoSink, run_id: str) -> Path:
    run_dir = sink.run_dir(run_id)
    folder = run_dir / SESSIONS_DIR
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise LedgerError(f"{folder}: refusing: not a plain folder")
    folder.mkdir(exist_ok=True)
    return folder


def _latest(sink: RepoSink, run_id: str, session_id: str) -> Entry | None:
    for entry in reversed(sink.read(run_id).entries):
        if entry.type is EntryType.SESSION and entry.data.get("session_id") == session_id:
            return entry
    return None


def add_session(
    sink: RepoSink,
    run_id: str,
    transcript: Path,
    *,
    actor: Actor,
    redactor: Redactor,
    session_id: str | None = None,
    root: Path | None = None,
) -> SessionResult:
    """Archive one transcript. Safe to re-run: archiving it again on pause and at close only
    adds an entry when the transcript changed.

    If redaction can't be confirmed, nothing is written to the sessions folder; the entry records
    where the raw transcript is instead, and `archived` is False. That covers the transcript, the
    archive path and the archived entry itself: when the redactor refuses the entry, the pointer
    entry is written instead.
    """
    if session_id is None:
        session_id = transcript.stem  # x.one.jsonl and x.two.jsonl stay apart
    if not SESSION_ID_RE.fullmatch(session_id) or ".." in session_id:
        raise LedgerError(f"bad session id {session_id!r}: use letters, digits, '.', '_' and '-'")
    if redactor.redact(session_id).text != session_id:
        raise LedgerError(f"session id {session_id!r} looks like a secret to the redactor; pass --session")
    raw = transcript.read_bytes()
    raw_sha = _sha256(raw)
    folder = sink.run_dir(run_id) / SESSIONS_DIR
    name = f"{session_id}.jsonl.gz"
    rel = (folder / name).relative_to(root) if root is not None else folder / name

    warnings = []
    if root is not None and transcript.resolve().is_relative_to(root.resolve()):
        warnings.append(f"the raw transcript {transcript} is inside the repo; keep it out of git")

    previous = _latest(sink, run_id, session_id)  # also verifies the chain before any write
    if (previous is not None and previous.data.get("archived") is True
            and previous.data.get("raw_sha256") == raw_sha
            and previous.data.get("patterns_sha256") == redactor.fingerprint
            and (folder / name).is_file()):
        return SessionResult(previous, True, new=False, warning="; ".join(warnings) or None)

    base: dict[str, Any] = {"session_id": session_id, "raw_sha256": raw_sha, "raw_bytes": len(raw),
                            "patterns_sha256": redactor.fingerprint}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        reason: str | None = f"not UTF-8 at byte {e.start}, so it can't be scanned"
    else:
        try:
            redacted = redactor.redact_transcript(text)
            reason = None
        except Unconfirmed as e:
            reason = str(e)

    def pointer(why_not: str) -> SessionResult:
        """The entry for a transcript that stays out of git: its hashes and where it is."""
        entry = append(sink, run_id, actor=actor, type=EntryType.SESSION,
                       summary=f"Session {session_id} not archived: redaction couldn't be confirmed",
                       why="A transcript whose redaction can't be confirmed stays out of git (S3)",
                       data={**base, "archived": False, "location": _where(transcript), "reason": why_not},
                       redactor=redactor)
        return SessionResult(entry, False, new=True, warning="; ".join(warnings) or None)

    if reason is None and redactor.redact(rel.as_posix()).text != rel.as_posix():
        reason = "the archive path looks like a secret to the redactor; rename the run or pass --session"
    if reason is not None:
        return pointer(reason)
    body = redacted.text.encode("utf-8")
    archive = _compress(body)
    staged = _stage(_sessions_dir(sink, run_id), name, archive)
    # Pairs, not a name -> count object: "url_password":16} reads to the redactor as a password
    # assignment, so the entry would be refused.
    data = {**base, "archived": True, "path": rel.as_posix(), "redacted_sha256": _sha256(body),
            "redactions": [[name, n] for name, n in sorted(redacted.counts.items())]}
    total = sum(redacted.counts.values())
    if len(archive) > LFS_OVER_BYTES:
        warnings.append(f"{rel} is {len(archive) // (1024 * 1024)} MB compressed, over the 20 MB "
                        f"LFS threshold; it isn't routed to Git LFS yet")
    # The entry goes first, so a failed append leaves the previous archive untouched. A crash
    # between the two leaves an entry with no file; adding again redoes the file.
    try:
        entry = append(sink, run_id, actor=actor, type=EntryType.SESSION,
                       summary=f"Session {session_id} archived, {total} redaction{'s' if total != 1 else ''}",
                       why="Archived on pause or close so the run's work can be reviewed later (S3)",
                       evidence=[data["path"]], data=data, redactor=redactor)
    except RedactionRefused as e:
        staged.unlink(missing_ok=True)
        return pointer(f"the archived entry failed redaction ({e.detail}), so the archive wasn't kept")
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    os.replace(staged, folder / name)
    return SessionResult(entry, True, new=True, warning="; ".join(warnings) or None)
