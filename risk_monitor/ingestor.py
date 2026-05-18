from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from git import Repo
from git.objects.commit import Commit

from .models import CommitInput, FileChange

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9-_]{20,}"),
    re.compile(r"sk-or-v1-[A-Za-z0-9]{40,}"),
    re.compile(r"(?i)aws_secret_access_key\s*=\s*['\"]?[A-Za-z0-9/+=]{30,}"),
    re.compile(r"(?i)ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"(?i)password\s*=\s*['\"][^'\"]{6,}['\"]"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"),
]

BINARY_HINTS = (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".tar", ".gz",
                ".whl", ".so", ".dylib", ".class", ".jar", ".woff", ".woff2",
                ".ico", ".lock", "package-lock.json", "yarn.lock", "poetry.lock")


def scrub_secrets(text: str) -> str:
    for pat in SECRET_PATTERNS:
        text = pat.sub("[REDACTED_SECRET]", text)
    return text


def _is_binary_or_generated(path: str) -> bool:
    p = path.lower()
    return any(p.endswith(h) or p.endswith("/" + h) for h in BINARY_HINTS)


def _build_file_changes(commit: Commit) -> List[FileChange]:
    out: List[FileChange] = []
    parent = commit.parents[0] if commit.parents else None
    diffs = commit.diff(parent, create_patch=False) if parent else commit.diff(
        None, create_patch=False
    )
    stats = commit.stats.files
    for d in diffs:
        path = d.b_path or d.a_path or ""
        if not path:
            continue
        s = stats.get(path, {})
        out.append(
            FileChange(
                path=path,
                change_type=d.change_type or "M",
                additions=s.get("insertions", 0),
                deletions=s.get("deletions", 0),
            )
        )
    return out


def _build_diff(commit: Commit, max_chars: int = 60_000) -> str:
    parent = commit.parents[0] if commit.parents else None
    parts: List[str] = []
    total = 0
    diffs = commit.diff(parent, create_patch=True) if parent else commit.diff(
        None, create_patch=True
    )
    for d in diffs:
        path = d.b_path or d.a_path or ""
        if _is_binary_or_generated(path):
            parts.append(f"--- {path}\n[skipped: binary/generated]\n")
            continue
        try:
            patch = d.diff.decode("utf-8", errors="replace") if isinstance(d.diff, bytes) else str(d.diff)
        except Exception:
            patch = "[unreadable diff]"
        chunk = f"--- {path}\n{patch}\n"
        if total + len(chunk) > max_chars:
            # TODO: chunk by file rather than stopping mid-diff
            parts.append(f"--- {path}\n[truncated: diff size cap reached]\n")
            break
        parts.append(chunk)
        total += len(chunk)
    raw = "".join(parts)
    return scrub_secrets(raw)


def to_commit_input(commit: Commit) -> CommitInput:
    return CommitInput(
        sha=commit.hexsha,
        parents=[p.hexsha for p in commit.parents],
        author=commit.author.name or "unknown",
        author_email=commit.author.email or "",
        timestamp=datetime.fromtimestamp(commit.committed_date, tz=timezone.utc),
        message=commit.message.strip() if isinstance(commit.message, str) else "",
        files=_build_file_changes(commit),
        diff=_build_diff(commit),
    )


def iter_commits(
    repo_path: str | Path,
    since: Optional[str] = None,
    limit: Optional[int] = None,
    branch: Optional[str] = None,
) -> Iterable[CommitInput]:
    # `since` can be a single ref (HEAD~50) or a range (main..feature).
    # Single refs get expanded to `ref..HEAD` automatically.
    repo = Repo(str(repo_path))
    rev = since or (branch or repo.head.ref.name)
    if since and ".." not in since:
        rev = f"{since}..HEAD"
    kwargs = {"max_count": limit} if limit else {}
    for c in repo.iter_commits(rev=rev, **kwargs):
        yield to_commit_input(c)
