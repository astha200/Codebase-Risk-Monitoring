from __future__ import annotations

import asyncio
from typing import List

from .agents.base import run_all_specialists
from .config import settings
from .llm import build_agent, judge_model, _rate_limiter
from .models import (
    CommitInput,
    Dimension,
    DimensionScore,
    RiskBand,
    RiskReport,
    TriageSignals,
)
from .signals import compute_signals
from pydantic import BaseModel, Field


DIMENSION_WEIGHTS = {
    Dimension.SECURITY: 0.30,
    Dimension.MIGRATION: 0.20,
    Dimension.BLAST_RADIUS: 0.15,
    Dimension.BREAKING: 0.15,
    Dimension.TESTS: 0.10,
    Dimension.COMPLEXITY: 0.10,
}


def _band(score: int) -> RiskBand:
    if score >= 80:
        return RiskBand.CRITICAL
    if score >= 60:
        return RiskBand.HIGH
    if score >= 35:
        return RiskBand.MEDIUM
    return RiskBand.LOW


def _action(band: RiskBand, dimensions: List[DimensionScore]) -> str:
    # security overrides band — a confirmed 9+ should never quietly pass
    for d in dimensions:
        if d.dimension == Dimension.SECURITY and d.score >= 9 and d.confidence >= 0.7:
            return "block"
    return {
        RiskBand.LOW: "ok",
        RiskBand.MEDIUM: "monitor",
        RiskBand.HIGH: "request_review",
        RiskBand.CRITICAL: "block",
    }[band]


def _weighted_score(dimensions: List[DimensionScore]) -> int:
    by_dim = {d.dimension: d for d in dimensions}
    total = 0.0
    for dim, w in DIMENSION_WEIGHTS.items():
        d = by_dim.get(dim)
        if not d:
            continue
        eff = d.score * (0.5 + 0.5 * d.confidence)  # confidence-weighted
        total += eff * 10 * w
    return int(min(100, max(0, round(total))))


def _sanity_check(dimensions: List[DimensionScore]) -> List[str]:
    notes: List[str] = []
    by_dim = {d.dimension: d for d in dimensions}
    sec = by_dim.get(Dimension.SECURITY)
    tests = by_dim.get(Dimension.TESTS)
    complexity = by_dim.get(Dimension.COMPLEXITY)
    if sec and sec.score >= 7 and not sec.evidence:
        notes.append("security score high but evidence missing — flag for human review")
    if complexity and tests and complexity.score >= 7 and tests.score <= 2:
        notes.append("high complexity + low test-risk score is inconsistent")
    return notes


class JudgeOutput(BaseModel):
    summary: str = Field(min_length=10, max_length=400)


async def _summarize(commit: CommitInput, dimensions: List[DimensionScore], score: int, band: RiskBand) -> str:
    dims_text = "\n".join(
        f"- {d.dimension.value}: {d.score}/10 (conf {d.confidence:.2f}) — {d.rationale}"
        for d in dimensions
    )
    prompt = f"""Write a 1-2 sentence reviewer summary for this commit.
State the top concern(s) and what a reviewer should check first.
Do not restate the score. Do not invent details not present in the dimensions.

Commit: {commit.sha[:8]} by {commit.author}
Message: {commit.message[:200]}
Final score: {score}/100 ({band.value})

Per-dimension findings:
{dims_text}
"""
    system = "You write concise risk summaries for code reviewers. Be specific, no fluff."
    agent = build_agent(judge_model(), system, JudgeOutput)
    try:
        await _rate_limiter.acquire()
        result = await agent.run(prompt)
        return result.output.summary
    except Exception as e:
        top = sorted(dimensions, key=lambda d: d.score, reverse=True)[:2]
        fallback = ", ".join(f"{d.dimension.value} ({d.score}/10)" for d in top)
        return f"Top risk areas: {fallback}. Full summary unavailable — retry or re-scan."


async def assess_commit(commit: CommitInput) -> RiskReport:
    signals = compute_signals(commit)

    if signals.triage_score < settings.triage_threshold:
        return RiskReport(
            sha=commit.sha,
            risk_score=signals.triage_score,
            risk_band=_band(signals.triage_score),
            dimensions=[],
            summary=f"Triage-only assessment; score {signals.triage_score} below LLM threshold ({settings.triage_threshold}).",
            recommended_action="ok",
            triage_signals=signals,
            model_version=f"triage_only",
            skipped_deep_analysis=True,
            skipped_reason="triage_below_threshold",
        )

    dimensions = await run_all_specialists(commit, signals)
    score = _weighted_score(dimensions)
    band = _band(score)
    summary = await _summarize(commit, dimensions, score, band)
    notes = _sanity_check(dimensions)
    if notes:
        summary = f"{summary} [sanity: {'; '.join(notes)}]"

    return RiskReport(
        sha=commit.sha,
        risk_score=score,
        risk_band=band,
        dimensions=dimensions,
        summary=summary,
        recommended_action=_action(band, dimensions),
        triage_signals=signals,
        model_version=f"specialist={settings.model_specialist};judge={settings.model_judge}",
    )


async def assess_many(commits: List[CommitInput], concurrency: int = 4) -> List[RiskReport]:
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(c: CommitInput) -> RiskReport:
        async with sem:
            return await assess_commit(c)

    return await asyncio.gather(*[_bounded(c) for c in commits])
