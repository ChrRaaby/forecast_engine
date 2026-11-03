"""Record provenance (Christian, 2026-11-03): every live and shadow record says exactly which code, prompts, settings and libraries
made it, plus one short version ID.

Design doc: https://claude.ai/artifact/7c2seJZqVg52CJA6H6y2Tc (decisions 1-5 chosen 2026-11-03). ADR-0011.

- `RunInfo` is gathered once per run (main.py): git commit, whether the checkout is clean, the code fingerprint, Python and
  library versions, the GitHub Actions run. It is written to `data/forecasts/<run_id>/manifest.json`.
- `record_provenance()` adds what differs per record (its settings, prompt versions, role and roster) and the `version_id`.
- The code fingerprint covers every file that can change what we submit (see `CODE_FILES`). Each distinct fingerprint must be
  registered in `code_versions.json` (append only); `tests/test_provenance.py` fails until it is, so CI turns red on an unregistered
  change. A live run whose fingerprint is not registered still publishes (decision 1) but flags each record `version_unregistered`.
- Nothing here changes a forecast, a prompt or `BotConfig.config_hash()`.

Registering a code change (one commit, with the change):
  1. `poetry run pytest tests/test_provenance.py` prints the new fingerprint;
  2. append it to forecast_engine/code_versions.json with a dated label and a one-line note. Never edit or remove an entry;
  3. if the change alters forecasts, also bump CONFIG_VERSION (config.py) as before, and archive a new prompt version if the
     prompt changed (evals/prompt_archive.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from .config import CONFIG_VERSION, BotConfig
from .polarity import POLARITY_PROMPT_VERSION

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1  # of the provenance block itself
UNREGISTERED_FLAG = "version_unregistered"  # on every record of a run whose code fingerprint is not in code_versions.json

# Everything that can change what is forecast or submitted: the whole engine package, the live adapter (question conversion,
# CDF building, comment), its helpers, and the locked library versions. Fingerprinted by their exact text with LF line endings, so
# it is the same on every OS and Python version. Strict on purpose: even a comment edit needs a registered fingerprint.
CODE_FILES: tuple[str, ...] = ("forecast_engine/*.py", "main.py", "bot_helpers.py", "poetry.lock")
LIBRARIES: tuple[str, ...] = ("forecasting-tools", "openai", "asknews", "requests")

# code fingerprint -> {"label", "note"}. Kept in a JSON file outside CODE_FILES, so registering a fingerprint doesn't change it.
# Append only: older records name their fingerprint, so an entry is never changed or removed.
CODE_VERSIONS_PATH = Path(__file__).resolve().parent / "code_versions.json"


def code_versions() -> dict[str, dict[str, str]]:
    return json.loads(CODE_VERSIONS_PATH.read_text(encoding="utf-8"))


def code_paths(root: Path = ROOT) -> list[Path]:
    paths: list[Path] = []
    for pattern in CODE_FILES:
        paths += sorted(root.glob(pattern))
    return paths


def code_fingerprint(root: Path = ROOT) -> str:
    h = hashlib.sha256()
    for path in code_paths(root):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")  # same on a Windows (CRLF) checkout
        h.update(path.relative_to(root).as_posix().encode() + b"\0" + text.encode() + b"\0")
    return h.hexdigest()[:16]


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _lock_sha() -> str | None:
    lock = ROOT / "poetry.lock"
    return hashlib.sha256(lock.read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()[:16] if lock.exists() else None


def _version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    mode: str
    publish: bool
    git_commit: str | None
    clean_checkout: bool | None  # None = not a git checkout
    code_fingerprint: str
    code_version: str | None  # label from code_versions.json; None = unregistered
    github_run: str | None
    trigger: str | None
    python: str
    libraries: dict[str, str | None]
    lockfile_sha: str | None

    @property
    def registered(self) -> bool:
        return self.code_version is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "run": {"run_id": self.run_id, "mode": self.mode, "publish": self.publish, "github_run": self.github_run,
                    "trigger": self.trigger},
            "code": {"git_commit": self.git_commit, "clean_checkout": self.clean_checkout,
                     "code_fingerprint": self.code_fingerprint, "code_version": self.code_version,
                     "config_version": CONFIG_VERSION},
            "runtime": {"python": self.python, "libraries": self.libraries, "lockfile_sha": self.lockfile_sha},
        }

    def write(self, run_dir: Path) -> Path:
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "manifest.json"
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path


def gather_run_info(run_id: str, mode: str, publish: bool, env: dict[str, str] | None = None) -> RunInfo:
    env = dict(os.environ) if env is None else env
    commit = env.get("GITHUB_SHA") or _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--untracked-files=no")
    run_url = None
    if env.get("GITHUB_RUN_ID"):
        server, repo = env.get("GITHUB_SERVER_URL", "https://github.com"), env.get("GITHUB_REPOSITORY", "")
        run_url = f"{server}/{repo}/actions/runs/{env['GITHUB_RUN_ID']}"
        if env.get("GITHUB_RUN_ATTEMPT") not in (None, "", "1"):
            run_url += f"/attempts/{env['GITHUB_RUN_ATTEMPT']}"
    fp = code_fingerprint()
    return RunInfo(
        run_id=run_id, mode=mode, publish=publish, git_commit=commit,
        clean_checkout=None if status is None else status == "",
        code_fingerprint=fp, code_version=code_versions().get(fp, {}).get("label"),
        github_run=run_url, trigger=env.get("GITHUB_EVENT_NAME") or "local",
        python=platform.python_version(), libraries={d: _version(d) for d in LIBRARIES}, lockfile_sha=_lock_sha(),
    )


def record_provenance(run: RunInfo, cfg: BotConfig, *, role: str, roster: str) -> dict[str, Any]:
    """The block stored in a record. role: live | live-fallback | shadow. version_id hashes code + runtime + settings + prompts."""
    prompts = {"forecast": cfg.prompt_version, "polarity_check": POLARITY_PROMPT_VERSION if cfg.polarity_check_model else None}
    if cfg.research.followup is not None:
        prompts["research_followup"] = cfg.research.followup.prompt_version
    base = run.to_dict()
    settings = {"config_version": CONFIG_VERSION, "config_hash": cfg.config_hash(), "full": cfg.to_dict()}
    identity = {"code": {k: base["code"][k] for k in ("code_fingerprint", "config_version")},
                "runtime": base["runtime"], "settings": settings, "prompts": prompts}
    blob = json.dumps(identity, sort_keys=True, default=str).encode()
    return {
        "version_id": "v-" + hashlib.sha256(blob).hexdigest()[:8],
        "role": role,
        "roster": roster,
        **base,
        "prompts": prompts,
        "settings": settings,
    }


def roster_name(cfg: BotConfig) -> str:
    """frontier | cheap | wide for the configs main.py runs live; a shadow passes its own arm name instead."""
    from .config import DEFAULT_CONFIG, TOURNAMENT_CONFIG, WIDE_CONFIG

    for name, known in (("frontier", TOURNAMENT_CONFIG), ("cheap", DEFAULT_CONFIG), ("wide", WIDE_CONFIG)):
        if cfg.config_hash() == known.config_hash():
            return name
    return f"custom-{cfg.config_hash()}"

