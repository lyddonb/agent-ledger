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
from .run import append
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


def _write_atomic(folder: Path, name: str, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, folder / name)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


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

    If redaction can't be confirmed, nothing is written to the run folder; the entry records
    where the raw transcript is instead, and `archived` is False.
    """
    if session_id is None:
        session_id = transcript.name.split(".")[0]
    if not SESSION_ID_RE.fullmatch(session_id) or ".." in session_id:
        raise LedgerError(f"bad session id {session_id!r}: use letters, digits, '.', '_' and '-'")
    raw = transcript.read_bytes()
    raw_sha = _sha256(raw)

    previous = _latest(sink, run_id, session_id)  # also verifies the chain before any write
    if previous is not None and previous.data.get("raw_sha256") == raw_sha:
        return SessionResult(previous, bool(previous.data.get("archived")), new=False)

    data: dict[str, Any] = {"session_id": session_id, "raw_sha256": raw_sha, "raw_bytes": len(raw)}
    warning = None
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

    if reason is not None:
        data.update(archived=False, location=str(transcript.resolve()), reason=reason)
        summary = f"Session {session_id} not archived: redaction couldn't be confirmed"
        why = "A transcript whose redaction can't be confirmed stays out of git (S3)"
    else:
        body = redacted.text.encode("utf-8")
        archive = _compress(body)
        folder = _sessions_dir(sink, run_id)
        name = f"{session_id}.jsonl.gz"
        _write_atomic(folder, name, archive)
        rel = folder / name
        if root is not None:
            rel = rel.relative_to(root)
        data.update(archived=True, path=rel.as_posix(), redacted_sha256=_sha256(body),
                    redactions=dict(sorted(redacted.counts.items())))
        total = sum(redacted.counts.values())
        summary = f"Session {session_id} archived, {total} redaction{'s' if total != 1 else ''}"
        why = "Archived on pause or close so the run's work can be reviewed later (S3)"
        if len(archive) > LFS_OVER_BYTES:
            warning = (f"{rel} is {len(archive) // (1024 * 1024)} MB compressed, over the 20 MB "
                       f"LFS threshold; it isn't routed to Git LFS yet")
    entry = append(sink, run_id, actor=actor, type=EntryType.SESSION, summary=summary, why=why,
                   evidence=[data["path"]] if data["archived"] else [], data=data, redactor=redactor)
    return SessionResult(entry, data["archived"], new=True, warning=warning)
