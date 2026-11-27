"""Run a case: stage the fixture and plugins in a fresh folder, start `claude -p`, judge the run."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .calls import Run, StreamError, read_claude_stream
from .case import Case
from .judge import Judgment, Verdict, judge, run_after_checks

PASSED_ENV = ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG")


class SetupError(ValueError):
    """The case can't be run from this skill root. Nothing was started and nothing was spent."""


@dataclass(frozen=True)
class Settings:
    claude: str = "claude"
    model: str = "sonnet"
    budget_usd: float = 2.0
    timeout_s: float = 900


@dataclass(frozen=True)
class RunReport:
    judgment: Judgment
    cost_usd: float | None
    tokens: int
    wall_s: float
    load: tuple[float, float]


def find_plugins(skill_root: Path, plugin: str) -> dict[str, Path]:
    """The plugin and every plugin its `dependencies` name, by name, each found beside the first."""
    first = skill_root / plugin
    found: dict[str, Path] = {}
    pending = [first]
    while pending:
        folder = pending.pop()
        manifest = folder / ".claude-plugin" / "plugin.json"
        try:
            meta = json.loads(manifest.read_text(encoding="utf-8"))
            name, deps = meta["name"], meta.get("dependencies", [])
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise SetupError(f"{manifest}: not a plugin manifest with a name ({e})") from e
        if not isinstance(name, str) or not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise SetupError(f"{manifest}: 'name' must be a string and 'dependencies' a list of names")
        if name in found:
            continue
        found[name] = folder
        pending.extend(first.parent / d for d in deps if d not in found)
    return found


def scrubbed_env(environ: Mapping[str, str]) -> dict[str, str]:
    return {k: environ[k] for k in PASSED_ENV if k in environ}


def claude_command(case: Case, settings: Settings, plugin_dirs: Sequence[Path], mcp_config: Path) -> list[str]:
    command = [
        settings.claude, "-p", case.prompt, "--model", settings.model,
        "--output-format", "stream-json", "--verbose",
        "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", str(mcp_config),
    ]
    for folder in plugin_dirs:
        command += ["--plugin-dir", str(folder)]
    command += ["--tools", ",".join(case.tools), "--permission-mode", "dontAsk", "--allowedTools", *case.tools]
    if case.deny:
        command += ["--disallowedTools", *case.deny]
    command += ["--no-session-persistence", "--max-budget-usd", f"{settings.budget_usd:.2f}"]
    return command


def stage(case: Case, plugins: Mapping[str, Path], root: Path) -> tuple[Path, list[Path]]:
    """Copy the fixture to `root/repo` as a one-commit git repo, and each plugin to `root/plugins/<name>`."""
    repo = root / "repo"
    shutil.copytree(case.fixture, repo, ignore=shutil.ignore_patterns(".git"))
    git = ["git", "-C", str(repo)]
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.name", "fixture"], ["config", "user.email", "fixture@example.invalid"],
                 ["add", "-A"], ["commit", "-q", "-m", "fixture", "--no-verify"]):
        subprocess.run(git + args, check=True, stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    dirs = []
    for name, folder in plugins.items():
        dest = root / "plugins" / name
        shutil.copytree(folder, dest, ignore=shutil.ignore_patterns(".git"))
        dirs.append(dest)
    return repo, dirs


def run_case(case: Case, skill_root: Path, settings: Settings, keep: Path | None = None) -> RunReport:
    plugins = find_plugins(skill_root, case.plugin.as_posix())
    if not case.fixture.is_dir():
        raise SetupError(f"the fixture {case.fixture} is not a folder")
    root = Path(tempfile.mkdtemp(prefix="harness-"))
    try:
        repo, plugin_dirs = stage(case, plugins, root)
        mcp_config = root / "mcp.json"
        mcp_config.write_text('{"mcpServers": {}}\n')
        stream = root / "stream.jsonl"
        load_start = os.getloadavg()[0]
        started = time.monotonic()
        problem = ""
        with stream.open("w") as out, (root / "stderr.txt").open("w") as err:
            try:
                done = subprocess.run(claude_command(case, settings, plugin_dirs, mcp_config), cwd=repo,
                                      env=scrubbed_env(os.environ), stdin=subprocess.DEVNULL, stdout=out,
                                      stderr=err, timeout=settings.timeout_s)
                if done.returncode != 0:
                    problem = f"claude exited {done.returncode}"
            except subprocess.TimeoutExpired:
                problem = f"claude ran longer than {settings.timeout_s:g} s"
        wall_s = time.monotonic() - started
        load = (load_start, os.getloadavg()[0])
        try:
            with stream.open() as f:
                run = read_claude_stream(f)
        except StreamError as e:
            run, problem = Run((), None, None), problem or f"the stream can't be read: {e}"
        if problem:
            judgment = Judgment(Verdict.UNJUDGEABLE, (), problem)
        else:
            try:
                judgment = judge(case, run, run_after_checks(case, repo), plugins.keys())
            except subprocess.TimeoutExpired as e:
                judgment = Judgment(Verdict.UNJUDGEABLE, (), f"an after check ran longer than {e.timeout:g} s")
        if keep is not None:
            shutil.copytree(root, keep)
        return RunReport(judgment, run.cost_usd, run.tokens, wall_s, load)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def overall(reports: Sequence[RunReport]) -> Verdict:
    verdicts = {r.judgment.verdict for r in reports}
    if Verdict.FAIL in verdicts:
        return Verdict.FAIL
    if Verdict.UNJUDGEABLE in verdicts:
        return Verdict.UNJUDGEABLE
    return Verdict.PASS
