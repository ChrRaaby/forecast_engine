"""Content-addressed research cache for backtests (protocol §7; ADR-0008).

Key = sha256(question id, question text, as_of, retrieval-config hash). The text is in the key because the archive query is
built from it: research fetched with post-as_of wording must never be served to the as-of snapshot (T4; B-45 review).
A bundle is paid for once and then reused by every reasoning experiment, so reasoning-only changes are compared on identical
research. Changing the retrieval config changes the key (a new experiment).

Files live under data/research_cache/ (gitignored; the private data repo), because bundles hold licensed AskNews text:
    data/research_cache/<key[:2]>/<key>.json                      the bundle, its key fields and a content hash
    data/research_cache/<key[:2]>/<key>.screen-<screen hash>.json   leakage-screen verdicts, bound to the bundle's content hash

Guarantees: write-once across processes (a different bundle for an existing key raises), the content hash is verified on every
read, the as-of guard runs on every write and every read, and a stored screen verdict is only returned for the exact bundle it
was made for. So a tampered, mis-built or mismatched entry can't reach a forecast.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forecast_engine.schema import LlmCall, ResearchBundle, ResearchItem

from .asof import assert_bundle_as_of

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "data" / "research_cache"


class CacheError(RuntimeError):
    pass


def _iso(as_of: datetime) -> str:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    return as_of.astimezone(timezone.utc).isoformat()


def text_hash(question_text: str) -> str:
    return hashlib.sha256(question_text.encode()).hexdigest()


def cache_key(question_id: int, as_of: datetime, retrieval_config_hash: str, question_text: str) -> str:
    blob = json.dumps({"question_id": question_id, "as_of": _iso(as_of), "retrieval": retrieval_config_hash,
                       "question_sha256": text_hash(question_text)}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_hash(bundle_dict: dict) -> str:
    return hashlib.sha256(_canonical(bundle_dict).encode()).hexdigest()


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def bundle_from_dict(d: dict) -> ResearchBundle:
    """Inverse of ResearchBundle.to_dict()."""
    calls = []
    for c in d["calls"]:
        c = dict(c)
        c["requested_at"] = _dt(c["requested_at"])
        calls.append(LlmCall(**c))
    items = [ResearchItem(**{**i, "published_at": _dt(i.get("published_at"))}) for i in d["items"]]
    return ResearchBundle(as_of=_dt(d["as_of"]), providers=list(d["providers"]), items=items, calls=calls,
                          errors=list(d.get("errors", [])))


def _write_once(path: Path, text: str) -> bool:
    """Create `path` with `text` only if it doesn't exist yet, atomically across processes. False if it already existed.

    Writes a uniquely named temp file, then hard-links it into place. link() fails if the target exists, so two processes can never
    both "win"; the earlier os.replace version could silently overwrite a bundle (B-45 review).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.link(tmp, path)
        return True
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)


class ResearchCache:
    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    def path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def screen_path(self, key: str, screen_config_hash: str) -> Path:
        return self.root / key[:2] / f"{key}.screen-{screen_config_hash}.json"

    def get(self, question_id: int, as_of: datetime, retrieval_config_hash: str, question_text: str) -> ResearchBundle | None:
        key = cache_key(question_id, as_of, retrieval_config_hash, question_text)
        path = self.path(key)
        if not path.exists():
            return None
        entry = json.loads(path.read_text(encoding="utf-8"))
        expected = {"key": key, "question_id": question_id, "as_of": _iso(as_of), "retrieval_config_hash": retrieval_config_hash,
                    "question_sha256": text_hash(question_text)}
        for field, value in expected.items():
            if entry.get(field) != value:
                raise CacheError(f"{path.name}: {field} is {entry.get(field)!r}, expected {value!r}")
        if content_hash(entry["bundle"]) != entry.get("content_sha256"):
            raise CacheError(f"{path.name}: content hash mismatch (file changed after it was written)")
        bundle = bundle_from_dict(entry["bundle"])
        assert_bundle_as_of(bundle, as_of)
        return bundle

    def put(self, question_id: int, as_of: datetime, retrieval_config_hash: str, question_text: str,
            bundle: ResearchBundle) -> str:
        assert_bundle_as_of(bundle, as_of)
        if any(c.error for c in bundle.calls):
            raise CacheError("refusing to cache a bundle with a failed retrieval call; retry it instead")
        key = cache_key(question_id, as_of, retrieval_config_hash, question_text)
        bundle_dict = bundle.to_dict()
        digest = content_hash(bundle_dict)
        path = self.path(key)
        entry = {"key": key, "question_id": question_id, "as_of": _iso(as_of), "retrieval_config_hash": retrieval_config_hash,
                 "question_sha256": text_hash(question_text), "content_sha256": digest, "bundle": bundle_dict}
        if not _write_once(path, json.dumps(entry, indent=1, ensure_ascii=False)):
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("content_sha256") != digest:
                raise CacheError(f"{path.name} already holds a different bundle; cache entries are write-once")
        return key

    def bundle_sha256(self, key: str) -> str | None:
        """Content hash of the cached bundle for `key`, or None if nothing is cached."""
        path = self.path(key)
        return json.loads(path.read_text(encoding="utf-8")).get("content_sha256") if path.exists() else None

    def get_screen(self, key: str, screen_config_hash: str, bundle_sha256: str) -> dict | None:
        """A stored verdict, checked against the exact bundle it was made for (B-45 review: verdicts were trusted blindly)."""
        path = self.screen_path(key, screen_config_hash)
        if not path.exists():
            return None
        entry = json.loads(path.read_text(encoding="utf-8"))
        for field, value in {"key": key, "screen_config_hash": screen_config_hash, "bundle_sha256": bundle_sha256}.items():
            if entry.get(field) != value:
                raise CacheError(f"{path.name}: {field} is {entry.get(field)!r}, expected {value!r}")
        verdict = entry.get("verdict") or {}
        if verdict.get("status") not in ("clean", "leak"):
            raise CacheError(f"{path.name}: invalid stored status {verdict.get('status')!r}")
        return verdict

    def put_screen(self, key: str, screen_config_hash: str, bundle_sha256: str, verdict: dict) -> None:
        if self.bundle_sha256(key) != bundle_sha256:
            raise CacheError("refusing to store a verdict for a bundle that isn't the cached one")
        if verdict.get("status") not in ("clean", "leak"):
            raise CacheError(f"refusing to store verdict status {verdict.get('status')!r}")
        path = self.screen_path(key, screen_config_hash)
        entry = {"key": key, "screen_config_hash": screen_config_hash, "bundle_sha256": bundle_sha256, "verdict": verdict}
        if not _write_once(path, json.dumps(entry, indent=1, ensure_ascii=False)):
            raise CacheError(f"{path.name} exists; screen verdicts are write-once")
