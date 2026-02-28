#  Orchestration Engine - Analytics Routes
#
#  Admin-only analytics: cost breakdown, task outcomes, efficiency metrics.
#  Read-only queries on existing tables (no migrations needed).
#
#  Depends on: container.py, models/schemas.py, middleware/auth.py
#  Used by:    app.py

from dependency_injector.wiring import inject, Provide
from fastapi import APIRouter, Depends, Query

from backend.container import Container
from backend.db.connection import Database
from backend.middleware.auth import require_admin
from backend.models.schemas import (
    AnalyticsCostBreakdown,
    AnalyticsEfficiency,
    AnalyticsTaskOutcomes,
    CostByModelTier,
    CostByProject,
    CostEfficiencyItem,
    DailyCostTrend,
    RetryByTier,
    TaskOutcomeByTier,
    VerificationByTier,
    WaveThroughput,
)

router = APIRouter(prefix="/admin/analytics", tags=["analytics"])


# ---------------------------------------------------------------------------
# Cost Breakdown
# ---------------------------------------------------------------------------

@router.get("/cost-breakdown")
@inject
async def cost_breakdown(
    _admin: dict = Depends(require_admin),
    days: int = Query(default=30, ge=1, le=90),
    db: Database = Depends(Provide[Container.db]),
) -> AnalyticsCostBreakdown:
    """Cost breakdown by project, model tier, and daily trend."""

    # By project — from usage_log joined with projects
    project_rows = await db.fetchall(
        "SELECT u.project_id, p.name as project_name, "
        "SUM(u.cost_usd) as cost, COUNT(DISTINCT u.task_id) as task_count "
        "FROM usage_log u LEFT JOIN projects p ON p.id = u.project_id "
        "WHERE u.project_id IS NOT NULL "
        "GROUP BY u.project_id ORDER BY cost DESC"
    )
    by_project = [
        CostByProject(
            project_id=r["project_id"],
            project_name=r["project_name"] or "(deleted)",
            cost_usd=round(r["cost"], 6),
            task_count=r["task_count"],
        )
        for r in project_rows
    ]

    # By model tier — from tasks with terminal statuses
    tier_rows = await db.fetchall(
        "SELECT model_tier, SUM(cost_usd) as cost, COUNT(*) as task_count "
        "FROM tasks WHERE status IN ('completed', 'failed', 'needs_review') "
        "GROUP BY model_tier"
    )
    by_model_tier = [
        CostByModelTier(
            model_tier=r["model_tier"],
            cost_usd=round(r["cost"], 6),
            task_count=r["task_count"],
            avg_cost_per_task=round(r["cost"] / r["task_count"], 6) if r["task_count"] else 0,
        )
        for r in tier_rows
    ]

    # Daily trend — from pre-aggregated budget_periods
    trend_rows = await db.fetchall(
        "SELECT period_key, total_cost_usd, api_call_count "
        "FROM budget_periods WHERE period_type = 'daily' "
        "ORDER BY period_key DESC LIMIT ?",
        (days,),
    )
    daily_trend = [
        DailyCostTrend(
            date=r["period_key"],
            cost_usd=round(r["total_cost_usd"], 6),
            api_calls=r["api_call_count"],
        )
        for r in reversed(trend_rows)  # chronological order
    ]

    total = sum(p.cost_usd for p in by_project)

    return AnalyticsCostBreakdown(
        by_project=by_project,
        by_model_tier=by_model_tier,
        daily_trend=daily_trend,
        total_cost_usd=round(total, 6),
    )


# ---------------------------------------------------------------------------
# Task Outcomes
# ---------------------------------------------------------------------------

@router.get("/task-outcomes")
@inject
async def task_outcomes(
    _admin: dict = Depends(require_admin),
    db: Database = Depends(Provide[Container.db]),
) -> AnalyticsTaskOutcomes:
    """Task success rates and verification signal by model tier."""

    # Task outcomes by tier + status
    outcome_rows = await db.fetchall(
        "SELECT model_tier, status, COUNT(*) as cnt "
        "FROM tasks WHERE status IN ('completed', 'failed', 'needs_review') "
        "GROUP BY model_tier, status"
    )

    # Pivot into per-tier records
    tier_map: dict[str, dict] = {}
    for r in outcome_rows:
        tier = r["model_tier"]
        if tier not in tier_map:
            tier_map[tier] = {"completed": 0, "failed": 0, "needs_review": 0}
        tier_map[tier][r["status"]] = r["cnt"]

    by_tier = []
    for tier, counts in sorted(tier_map.items()):
        total = counts["completed"] + counts["failed"] + counts["needs_review"]
        by_tier.append(TaskOutcomeByTier(
            model_tier=tier,
            total=total,
            completed=counts["completed"],
            failed=counts["failed"],
            needs_review=counts["needs_review"],
            success_rate=round(counts["completed"] / total, 4) if total else 0,
        ))

    # Verification by tier
    verif_rows = await db.fetchall(
        "SELECT model_tier, verification_status, COUNT(*) as cnt "
        "FROM tasks WHERE verification_status IS NOT NULL "
        "GROUP BY model_tier, verification_status"
    )

    verif_map: dict[str, dict] = {}
    for r in verif_rows:
        tier = r["model_tier"]
        if tier not in verif_map:
            verif_map[tier] = {"passed": 0, "gaps_found": 0, "human_needed": 0}
        vs = r["verification_status"]
        if vs in verif_map[tier]:
            verif_map[tier][vs] = r["cnt"]

    verification_by_tier = []
    for tier, counts in sorted(verif_map.items()):
        total = counts["passed"] + counts["gaps_found"] + counts["human_needed"]
        verification_by_tier.append(VerificationByTier(
            model_tier=tier,
            total_verified=total,
            passed=counts["passed"],
            gaps_found=counts["gaps_found"],
            human_needed=counts["human_needed"],
            pass_rate=round(counts["passed"] / total, 4) if total else 0,
        ))

    return AnalyticsTaskOutcomes(
        by_tier=by_tier,
        verification_by_tier=verification_by_tier,
    )


# ---------------------------------------------------------------------------
# Efficiency
# ---------------------------------------------------------------------------

@router.get("/efficiency")
@inject
async def efficiency(
    _admin: dict = Depends(require_admin),
    db: Database = Depends(Provide[Container.db]),
) -> AnalyticsEfficiency:
    """Retry rates, checkpoint counts, wave throughput, cost efficiency."""

    # Retries by tier
    retry_rows = await db.fetchall(
        "SELECT model_tier, COUNT(*) as total, "
        "SUM(CASE WHEN retry_count > 0 THEN 1 ELSE 0 END) as with_retries, "
        "SUM(retry_count) as total_retries "
        "FROM tasks GROUP BY model_tier"
    )
    retries_by_tier = [
        RetryByTier(
            model_tier=r["model_tier"],
            total_tasks=r["total"],
            tasks_with_retries=r["with_retries"],
            total_retries=r["total_retries"],
            retry_rate=round(r["with_retries"] / r["total"], 4) if r["total"] else 0,
        )
        for r in retry_rows
    ]

    # Checkpoint counts
    cp_row = await db.fetchone(
        "SELECT COUNT(*) as total, "
        "SUM(CASE WHEN resolved_at IS NULL THEN 1 ELSE 0 END) as unresolved "
        "FROM checkpoints"
    )
    checkpoint_count = cp_row["total"] if cp_row else 0
    unresolved = (cp_row["unresolved"] or 0) if cp_row else 0

    # Wave throughput
    wave_rows = await db.fetchall(
        "SELECT wave, COUNT(*) as task_count, "
        "AVG(completed_at - started_at) as avg_duration "
        "FROM tasks WHERE completed_at IS NOT NULL AND started_at IS NOT NULL "
        "GROUP BY wave ORDER BY wave"
    )
    wave_throughput = [
        WaveThroughput(
            wave=r["wave"],
            task_count=r["task_count"],
            avg_duration_seconds=round(r["avg_duration"], 2) if r["avg_duration"] else None,
        )
        for r in wave_rows
    ]

    # Cost efficiency by tier
    eff_rows = await db.fetchall(
        "SELECT model_tier, SUM(cost_usd) as cost, "
        "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed, "
        "SUM(CASE WHEN verification_status = 'passed' THEN 1 ELSE 0 END) as passed "
        "FROM tasks GROUP BY model_tier"
    )
    cost_efficiency = [
        CostEfficiencyItem(
            model_tier=r["model_tier"],
            cost_usd=round(r["cost"], 6),
            tasks_completed=r["completed"],
            verification_pass_count=r["passed"],
            cost_per_pass=round(r["cost"] / r["passed"], 6) if r["passed"] else None,
        )
        for r in eff_rows
    ]

    return AnalyticsEfficiency(
        retries_by_tier=retries_by_tier,
        checkpoint_count=checkpoint_count,
        unresolved_checkpoint_count=unresolved,
        wave_throughput=wave_throughput,
        cost_efficiency=cost_efficiency,
    )
