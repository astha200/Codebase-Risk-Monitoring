from __future__ import annotations

import re
from typing import List

from .config import settings
from .models import CommitInput, TriageSignals

TEST_PATTERNS = re.compile(r"(^|/)(tests?|__tests__|spec)/|(_test|_spec|\.test|\.spec)\.[a-z]+$", re.IGNORECASE)
MIGRATION_PATTERNS = re.compile(r"(^|/)(migrations?|alembic|prisma/migrations|db/migrate)(/|$)", re.IGNORECASE)
DEPENDENCY_MANIFESTS = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "requirements.txt", "pyproject.toml", "poetry.lock", "Pipfile.lock",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "Gemfile", "Gemfile.lock",
    "composer.json", "composer.lock",
}


def _is_test_file(path: str) -> bool:
    return bool(TEST_PATTERNS.search(path))


def _touches_migration(paths: List[str]) -> bool:
    return any(MIGRATION_PATTERNS.search(p) for p in paths)


def _touches_dependency(paths: List[str]) -> bool:
    return any(p.split("/")[-1] in DEPENDENCY_MANIFESTS for p in paths)


def _sensitive_hits(paths: List[str]) -> List[str]:
    sens = settings.sensitive_path_list
    hits = []
    for p in paths:
        for s in sens:
            if s and s in p and s not in hits:
                hits.append(s)
    return hits


def compute_signals(commit: CommitInput) -> TriageSignals:
    paths = [f.path for f in commit.files]
    test_files = sum(1 for p in paths if _is_test_file(p))
    code_files = max(1, len(paths) - test_files)
    sensitive = _sensitive_hits(paths)

    sig = TriageSignals(
        files_changed=len(paths),
        lines_changed=commit.total_changed_lines,
        test_file_ratio=test_files / code_files if code_files else 0.0,
        touches_sensitive_path=bool(sensitive),
        sensitive_paths_touched=sensitive,
        touches_migration=_touches_migration(paths),
        touches_dependency_manifest=_touches_dependency(paths),
        off_hours_commit=commit.timestamp.hour < 6 or commit.timestamp.hour >= 22,
        weekend_commit=commit.timestamp.weekday() >= 5,
    )

    score = 0
    score += min(30, sig.lines_changed // 50)
    score += min(15, sig.files_changed * 2)
    score += 20 if sig.touches_sensitive_path else 0
    score += 15 if sig.touches_migration else 0
    score += 10 if sig.touches_dependency_manifest else 0
    score += 5 if sig.off_hours_commit else 0
    score += 5 if sig.weekend_commit else 0
    if sig.lines_changed > 200 and sig.test_file_ratio < 0.1:
        # large diff with almost no test files — historically the riskiest pattern
        score += 15
    sig.triage_score = min(100, score)
    return sig
