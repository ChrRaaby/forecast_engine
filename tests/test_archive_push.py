"""tools/build_monitor.push_archive: the data archive push survives commits pushed to the remote from elsewhere (fix 2026-11-08).
Offline: a local bare repo stands in for the private data repo; synthetic files only."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _tool():
    spec = importlib.util.spec_from_file_location("build_monitor", ROOT / "tools" / "build_monitor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    """(local data/ clone, another clone of the same remote, the bare remote)."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True)
    _git(seed, "checkout", "-q", "-b", "main")
    (seed / "README.md").write_text("archive\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "init")
    _git(seed, "push", "-q", "origin", "main")
    data = tmp_path / "data"
    subprocess.run(["git", "clone", "-q", str(remote), str(data)], check=True)
    return data, seed, remote


def _remote_log(remote):
    return _git(remote, "log", "--format=%s", "main").splitlines()


def test_pushes_when_the_remote_moved_ahead(repos):
    data, other, remote = repos
    (other / "notes.md").write_text("miss review\n")  # pushed from elsewhere (e.g. a cloud session)
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "miss review")
    _git(other, "push", "-q", "origin", "main")
    (data / "runs").mkdir()
    (data / "runs" / "q1.json").write_text("{}")
    assert _tool().push_archive(data) == "archive pushed (1 commit)"
    log = _remote_log(remote)
    assert log[0].startswith("Archive sync") and "miss review" in log


def test_earlier_unpushed_commits_are_pushed_even_with_nothing_new(repos):
    data, _, remote = repos
    (data / "a.txt").write_text("a")
    _git(data, "add", "-A")
    _git(data, "commit", "-q", "-m", "old sync that never got pushed")
    assert _tool().push_archive(data) == "archive pushed (1 commit)"
    assert _remote_log(remote)[0] == "old sync that never got pushed"
    assert _tool().push_archive(data) == "archive up to date"


def test_conflict_is_aborted_and_reported_without_losing_local_work(repos):
    data, other, remote = repos
    (other / "README.md").write_text("remote edit\n")
    _git(other, "commit", "-qam", "remote edit")
    _git(other, "push", "-q", "origin", "main")
    (data / "README.md").write_text("local edit\n")
    with pytest.raises(RuntimeError, match="conflict"):
        _tool().push_archive(data)
    assert (data / "README.md").read_text() == "local edit\n"
    assert _git(data, "log", "-1", "--format=%s").startswith("Archive sync")
    assert "remote edit" in _remote_log(remote) and not any(s.startswith("Archive sync") for s in _remote_log(remote))
