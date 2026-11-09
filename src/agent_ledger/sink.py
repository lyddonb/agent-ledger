"""Where entries go: the sink interface (S3 "Sinks") and the `repo` sink.

The repo sink is the chain's source of truth. Its `write` is a compare-and-append under an
exclusive lock on the run's ledger.jsonl: it re-verifies the file and refuses an entry that
doesn't extend the current head. So two writers can't fork a chain, whatever they hold.
Later sinks (M4) mirror entries after the repo sink has accepted them.
"""
from __future__ import annotations

import errno
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Protocol

from ._canon import show
from .entry import ChainHead, Entry, _check_run_id, encode_line
from .verify import LEDGER_FILE, Broken, Kind, Problem, Verified, verify_bytes


class LedgerError(Exception):
    """A ledger operation that couldn't run: missing run, existing run, no locking."""


class NoSuchRun(LedgerError):
    pass


class RunExists(LedgerError):
    pass


class StaleHead(LedgerError):
    """The entry was chained to a head that is no longer the tip: another writer got there first."""


class ChainBroken(LedgerError):
    """The run's ledger fails verify, so nothing may be appended to it or read from it as valid."""

    def __init__(self, report: Broken) -> None:
        super().__init__(f"{report.run_id}: the ledger fails verify")
        self.report = report


class Sink(Protocol):
    """S3's sink interface. Every write goes through one."""

    def write(self, entry: Entry) -> None: ...

    def seal(self, run_id: str, hash: str, commit: str) -> None: ...


def _broken(run_id: str, kind: Kind, message: str) -> ChainBroken:
    return ChainBroken(Broken(run_id, (Problem(0, kind, message),), 0))


@contextmanager
def _locked(path: Path, run_id: str, *, exclusive: bool) -> Iterator[int]:
    """Open `path` without following symlinks and hold flock on it until the block exits.

    The lock is on ledger.jsonl itself, so there is no lock file to leave behind or commit.
    flock locks belong to the open file, so threads and processes exclude each other alike.
    A lock on a file that was since replaced by rename (git checkout does this) guards
    nothing, so after locking, the open file must still be the one at `path`; if not, retry.
    """
    try:
        import fcntl
    except ImportError:
        raise LedgerError("the ledger lock needs fcntl (Linux or macOS), which this platform lacks") from None
    flags = (os.O_RDWR | os.O_APPEND) if exclusive else os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    while True:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            raise NoSuchRun(f"{run_id} has no {LEDGER_FILE}; start it with `ledger init`") from None
        if not stat.S_ISREG(mode):  # checked before open: opening a FIFO would block
            raise _broken(run_id, Kind.NOT_A_FILE, f"{LEDGER_FILE} is not a regular file")
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            continue  # removed between lstat and open; look again
        except OSError as e:
            if e.errno == errno.ELOOP:  # swapped for a symlink after the lstat
                raise _broken(run_id, Kind.NOT_A_FILE, f"{LEDGER_FILE} is a symlink") from None
            raise
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise _broken(run_id, Kind.NOT_A_FILE, f"{LEDGER_FILE} is not a regular file")
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                now = path.lstat()
            except FileNotFoundError:
                now = None
            if now is not None and (now.st_dev, now.st_ino) == (st.st_dev, st.st_ino):
                yield fd
                return
        finally:
            os.close(fd)  # releases the lock


def _read_all(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while chunk := os.read(fd, 1 << 16):
        chunks.append(chunk)
    return b"".join(chunks)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _verified(run_id: str, data: bytes) -> Verified:
    report = verify_bytes(run_id, data)
    if isinstance(report, Broken):
        raise ChainBroken(report)
    return report


class RepoSink:
    """Ledgers in the repo: `<runs_dir>/<run-id>/ledger.jsonl` (S3 layout)."""

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = Path(runs_dir)

    def run_dir(self, run_id: str) -> Path:
        _check_run_id(run_id)  # also keeps "../x" and "/" out of the path
        return self.runs_dir / run_id

    def _ledger(self, run_id: str) -> Path:
        """The run's ledger path, refusing a symlink anywhere from .audit down, so a hostile
        checkout can't point a write or a read outside the repo."""
        run_dir = self.run_dir(run_id)
        for folder in (self.runs_dir.parent, self.runs_dir, run_dir):
            if folder.is_symlink():
                raise LedgerError(f"{folder} is a symlink; the ledger only writes through real folders")
        return run_dir / LEDGER_FILE

    def create(self, first: Entry) -> None:
        """Start a run's ledger with its first entry. The run folder may exist (a charter is
        often written first); the ledger may not. Atomic: the file appears whole or not at all."""
        if first.seq != 1:
            raise ValueError(f"a ledger starts at seq 1, not {first.seq}")
        self._ledger(first.run_id)
        self.run_dir(first.run_id).mkdir(parents=True, exist_ok=True)
        path = self._ledger(first.run_id)  # again: mkdir may have made the folders just now
        run_dir = path.parent
        fd, tmp = tempfile.mkstemp(dir=run_dir, prefix=f".{LEDGER_FILE}.", suffix=".tmp")
        try:
            os.fchmod(fd, 0o644)  # mkstemp makes 0600; a ledger is an ordinary repo file
            _write_all(fd, encode_line(first))
            os.fsync(fd)
            os.close(fd)
            fd = -1
            try:
                os.link(tmp, path)  # fails if the ledger exists, unlike rename
            except FileExistsError:
                raise RunExists(f"{first.run_id} already has a {LEDGER_FILE}") from None
        finally:
            if fd >= 0:
                os.close(fd)
            os.unlink(tmp)
        _fsync_dir(run_dir)

    def read(self, run_id: str) -> Verified:
        """The run's verified entries, read under a shared lock so a write in progress is never
        seen half done. Raises ChainBroken when verify fails."""
        with _locked(self._ledger(run_id), run_id, exclusive=False) as fd:
            return _verified(run_id, _read_all(fd))

    def head(self, run_id: str) -> ChainHead:
        return self.read(run_id).head

    def write(self, entry: Entry) -> None:
        """Append `entry` iff it extends the current head; otherwise raise StaleHead."""
        run_id = entry.run_id
        with _locked(self._ledger(run_id), run_id, exclusive=True) as fd:
            data = _read_all(fd)
            head = _verified(run_id, data).head
            if (entry.seq, entry.prev) != (head.seq + 1, head.link):
                raise StaleHead(f"{run_id}: entry seq {entry.seq} was chained to "
                                f"{show(entry.prev, 20)}, but the head is now seq {head.seq}")
            try:
                _write_all(fd, encode_line(entry))
            except BaseException:
                os.ftruncate(fd, len(data))  # still under the lock: drop the torn part
                raise
            # The line is whole now. If fsync fails, the entry is written but maybe not durable;
            # the caller sees an OSError and must check the ledger before writing it again.
            os.fsync(fd)

    def seal(self, run_id: str, hash: str, commit: str) -> None:
        raise NotImplementedError("seal is M0 unit 4 (docs/specs/S3-audit-ledger.md)")


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # some filesystems can't fsync a directory; the file itself is already synced
    finally:
        os.close(fd)
