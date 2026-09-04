"""Materialize large CAD assets that live in Azure Blob Storage, not git.

Walks every task.toml under the repo, reads its [[metadata.assets]] entries
(path, url, sha256), and downloads any file that is missing on disk or fails
its checksum. Run once after cloning (or after tools/sync.py changes the remote):

    python3 tools/fetch.py            # fetch everything missing
    python3 tools/fetch.py FreeCAD    # restrict to a subtree
"""
import hashlib
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ok(dest: Path, sha256: str) -> bool:
    return (dest.exists()
            and hashlib.sha256(dest.read_bytes()).hexdigest() == sha256)


MISSING: list[str] = []


def _download(url: str, dest: Path, sha256: str) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        try:
            urllib.request.urlretrieve(url, dest)
            break
        except urllib.error.HTTPError as exc:
            # A pin can point at a blob that has not been synced up yet (or
            # was deleted remotely): record it and keep fetching the rest.
            MISSING.append(f"{dest.relative_to(ROOT)} ({exc.code})")
            dest.unlink(missing_ok=True)
            return False
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            if attempt == 3:
                MISSING.append(f"{dest.relative_to(ROOT)} (network: {exc})")
                dest.unlink(missing_ok=True)
                return False
            time.sleep(2 ** attempt)
    if not _ok(dest, sha256):
        raise SystemExit(f"checksum mismatch after download: {dest}")
    return True


def _fetch_folder(task_dir: Path, asset: dict) -> int:
    """Folder pin: download each manifest row (relpath, sha256, bytes)
    under the entry's url prefix. Blob names may contain spaces, so path
    segments are percent-quoted."""
    fetched = 0
    base = asset["url"].rstrip("/")
    for rel, sha, size in asset["manifest"]:
        dest = task_dir / asset["path"] / rel
        if _ok(dest, sha):
            continue
        print(f"fetching {dest.relative_to(ROOT)} ({size} bytes)")
        if _download(f"{base}/{urllib.parse.quote(rel)}", dest, sha):
            fetched += 1
    return fetched


def fetch_all(subtree: str | None = None) -> int:
    fetched = 0
    for toml_path in sorted(ROOT.glob("*/*/task.toml")):
        task_dir = toml_path.parent
        if subtree and subtree not in str(task_dir.relative_to(ROOT)):
            continue
        meta = tomllib.loads(toml_path.read_text()).get("metadata", {})
        for asset in meta.get("assets", []):
            if "manifest" in asset:
                fetched += _fetch_folder(task_dir, asset)
                continue
            dest = task_dir / asset["path"]
            if _ok(dest, asset["sha256"]):
                continue
            print(f"fetching {dest.relative_to(ROOT)} "
                  f"({asset.get('bytes', '?')} bytes)")
            if _download(asset["url"], dest, asset["sha256"]):
                fetched += 1
    print(f"done; {fetched} file(s) fetched")
    if MISSING:
        print(f"{len(MISSING)} pinned file(s) not in blob storage "
              "(not yet synced up, or deleted remotely):")
        for m in MISSING:
            print(f"  {m}")
    return fetched


if __name__ == "__main__":
    fetch_all(sys.argv[1] if len(sys.argv) > 1 else None)
