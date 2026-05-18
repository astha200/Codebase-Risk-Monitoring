from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class Dimension(str, Enum):
    SECURITY = "security"
    BLAST_RADIUS = "blast_radius"
    TESTS = "tests"
    BREAKING = "breaking"
    MIGRATION = "migration"
    COMPLEXITY = "complexity"


class RiskBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FileChange(BaseModel):
    path: str
    change_type: str  # A, M, D, R
    additions: int = 0
    deletions: int = 0


class CommitInput(BaseModel):
    sha: str
    parents: List[str] = Field(default_factory=list)
    author: str
    author_email: str
    timestamp: datetime
    message: str
    files: List[FileChange] = Field(default_factory=list)
    diff: str = ""

    @property
    def total_changed_lines(self) -> int:
        return sum(f.additions + f.deletions for f in self.files)


class TriageSignals(BaseModel):
    files_changed: int = 0
    lines_changed: int = 0
    test_file_ratio: float = 0.0
    touches_sensitive_path: bool = False
    sensitive_paths_touched: List[str] = Field(default_factory=list)
    touches_migration: bool = False
    touches_dependency_manifest: bool = False
    off_hours_commit: bool = False
    weekend_commit: bool = False
    triage_score: int = 0  # 0–100


class Evidence(BaseModel):
    file: str
    line: Optional[int] = None
    snippet: Optional[str] = None


class DimensionScore(BaseModel):
    dimension: Dimension
    score: int = Field(ge=0, le=10)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=10, max_length=600)
    evidence: List[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_evidence_for_nonzero(self):
        if self.score > 0 and not self.evidence:
            # Weaker models often skip file:line citations. Cap confidence so
            # unsupported scores are down-weighted rather than silently trusted.
            self.confidence = min(self.confidence, 0.3)
        return self


class RiskReport(BaseModel):
    sha: str
    risk_score: int = Field(ge=0, le=100)
    risk_band: RiskBand
    dimensions: List[DimensionScore] = Field(default_factory=list)
    summary: str
    recommended_action: str  # ok | monitor | request_review | block
    triage_signals: TriageSignals
    model_version: str = ""
    prompt_version: str = "v1"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    skipped_deep_analysis: bool = False
    skipped_reason: Optional[str] = None
