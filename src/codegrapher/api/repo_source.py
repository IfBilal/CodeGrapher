"""Resolves what the user submitted (a git URL or an already-local path)
into an actual local directory for the parser to read.

repo_name derivation matters beyond just "what to call it": Neo4j and
Qdrant namespace every synced node/point by repo_name (see graph_sync.py,
vector_sync.py), and parse_repo() derives it from the directory's own
basename. If a cloned repo landed in a randomly-named temp directory, the
graph would get synced under that random name, and the API's graph-lookup
endpoint (which derives repo_name from the job's submitted URL/path
independently) would never find it. derive_repo_name() is the single
source of truth both sides call, so the name used at sync time and the
name used at query time can never drift apart.
"""

import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def is_git_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "git@")) or value.endswith(".git")


def derive_repo_name(repo_path_or_url: str) -> str:
    if is_git_url(repo_path_or_url):
        return repo_path_or_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    return Path(repo_path_or_url).name


@contextmanager
def resolve_repo(repo_path_or_url: str) -> Iterator[Path]:
    """Yields a local directory for the given repo. A git URL gets a
    shallow clone into a temp directory (removed on exit); an already-local
    path is yielded unchanged - nothing to clean up in that case."""
    if not is_git_url(repo_path_or_url):
        yield Path(repo_path_or_url)
        return

    parent_dir = tempfile.mkdtemp(prefix="codegrapher_")
    clone_dir = Path(parent_dir) / derive_repo_name(repo_path_or_url)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_path_or_url, str(clone_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        yield clone_dir
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"git clone failed: {exc.stderr.strip()}") from exc
    finally:
        shutil.rmtree(parent_dir, ignore_errors=True)
