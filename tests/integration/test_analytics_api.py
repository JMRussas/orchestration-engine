#  Orchestration Engine - Analytics API Integration Tests
#
#  Tests for admin-only analytics endpoints: cost breakdown, task outcomes, efficiency.
#
#  Depends on: backend/routes/analytics.py, tests/conftest.py
#  Used by:    pytest

import time


async def _get_admin_client(app_client, tmp_db):
    """Register an admin user (first user) and return the authed client."""
    resp = await app_client.post("/api/auth/register", json={
        "email": "admin@example.com",
        "password": "adminpass123",
        "display_name": "Admin",
    })
    assert resp.status_code == 201

    resp = await app_client.post("/api/auth/login", json={
        "email": "admin@example.com",
        "password": "adminpass123",
    })
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    app_client.headers["Authorization"] = f"Bearer {token}"
    return app_client


async def _seed_analytics_data(tmp_db):
    """Insert sample projects, tasks, usage_log, budget_periods, and checkpoints."""
    now = time.time()
    project_id = "proj_analytics"

    await tmp_db.execute_write(
        "INSERT INTO projects (id, name, requirements, status, created_at, updated_at) "
        "VALUES (?, 'Analytics Test', 'test reqs', 'executing', ?, ?)",
        (project_id, now, now),
    )
    await tmp_db.execute_write(
        "INSERT INTO plans (id, project_id, version, model_used, plan_json, status, created_at) "
        "VALUES (?, ?, 1, 'test', '{}', 'approved', ?)",
        (f"plan_{project_id}", project_id, now),
    )

    # Tasks with different tiers and statuses
    tasks = [
        ("t1", "haiku", "completed", 0.005, 0, "passed", 0, now - 100, now - 50),
        ("t2", "haiku", "completed", 0.003, 1, "passed", 0, now - 90, now - 40),
        ("t3", "haiku", "failed", 0.002, 2, None, 0, now - 80, now - 30),
        ("t4", "sonnet", "completed", 0.05, 0, "gaps_found", 0, now - 70, now - 20),
        ("t5", "sonnet", "needs_review", 0.04, 0, "human_needed", 1, now - 60, None),
        ("t6", "sonnet", "completed", 0.06, 1, "passed", 0, now - 55, now - 10),
    ]
    for tid, tier, status, cost, wave, verif, retries, started, completed in tasks:
        await tmp_db.execute_write(
            "INSERT INTO tasks (id, project_id, plan_id, title, description, "
            "task_type, priority, status, model_tier, wave, retry_count, max_retries, "
            "cost_usd, verification_status, started_at, completed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'test', 'code', 0, ?, ?, ?, ?, 5, ?, ?, ?, ?, ?, ?)",
            (tid, project_id, f"plan_{project_id}", f"Task {tid}",
             status, tier, wave, retries, cost, verif, started, completed, now, now),
        )

    # Usage log entries (id is AUTOINCREMENT, column is `timestamp` not `created_at`)
    for tid, cost in [("t1", 0.005), ("t2", 0.003), ("t4", 0.05), ("t6", 0.06)]:
        await tmp_db.execute_write(
            "INSERT INTO usage_log (project_id, task_id, provider, model, "
            "prompt_tokens, completion_tokens, cost_usd, purpose, timestamp) "
            "VALUES (?, ?, 'anthropic', 'claude-3-haiku', 100, 50, ?, 'execution', ?)",
            (project_id, tid, cost, now),
        )

    # Budget periods (period_key is PK, no id/created_at/updated_at columns)
    await tmp_db.execute_write(
        "INSERT INTO budget_periods (period_key, period_type, total_cost_usd, "
        "api_call_count) "
        "VALUES ('2025-01-15', 'daily', 0.118, 4)"
    )

    # Checkpoints
    await tmp_db.execute_write(
        "INSERT INTO checkpoints (id, project_id, task_id, checkpoint_type, summary, "
        "attempts_json, question, created_at) "
        "VALUES ('cp1', ?, 't3', 'retry_exhausted', 'Task failed', '[]', 'What now?', ?)",
        (project_id, now),
    )
    await tmp_db.execute_write(
        "INSERT INTO checkpoints (id, project_id, task_id, checkpoint_type, summary, "
        "attempts_json, question, resolved_at, created_at) "
        "VALUES ('cp2', ?, 't3', 'retry_exhausted', 'Resolved', '[]', 'Fixed', ?, ?)",
        (project_id, now, now),
    )

    return project_id


# ---------------------------------------------------------------------------
# Auth / Access Control
# ---------------------------------------------------------------------------

class TestAnalyticsAuth:
    async def test_cost_breakdown_requires_auth(self, app_client, tmp_db):
        resp = await app_client.get("/api/admin/analytics/cost-breakdown")
        assert resp.status_code == 401

    async def test_task_outcomes_requires_auth(self, app_client, tmp_db):
        resp = await app_client.get("/api/admin/analytics/task-outcomes")
        assert resp.status_code == 401

    async def test_efficiency_requires_auth(self, app_client, tmp_db):
        resp = await app_client.get("/api/admin/analytics/efficiency")
        assert resp.status_code == 401

    async def test_cost_breakdown_requires_admin(self, app_client, tmp_db):
        # Register admin first, then non-admin
        client = await _get_admin_client(app_client, tmp_db)
        resp = await client.post("/api/auth/register", json={
            "email": "user@example.com",
            "password": "userpass123",
            "display_name": "User",
        })
        assert resp.status_code == 201
        resp = await client.post("/api/auth/login", json={
            "email": "user@example.com",
            "password": "userpass123",
        })
        token = resp.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        resp = await client.get("/api/admin/analytics/cost-breakdown")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Cost Breakdown
# ---------------------------------------------------------------------------

class TestCostBreakdown:
    async def test_empty_db_returns_zeros(self, app_client, tmp_db):
        client = await _get_admin_client(app_client, tmp_db)
        resp = await client.get("/api/admin/analytics/cost-breakdown")
        assert resp.status_code == 200
        data = resp.json()
        assert data["by_project"] == []
        assert data["by_model_tier"] == []
        assert data["daily_trend"] == []
        assert data["total_cost_usd"] == 0

    async def test_with_data(self, app_client, tmp_db):
        client = await _get_admin_client(app_client, tmp_db)
        await _seed_analytics_data(tmp_db)

        resp = await client.get("/api/admin/analytics/cost-breakdown")
        assert resp.status_code == 200
        data = resp.json()

        # By project
        assert len(data["by_project"]) == 1
        assert data["by_project"][0]["project_name"] == "Analytics Test"
        assert data["by_project"][0]["task_count"] == 4  # 4 usage_log entries

        # By model tier
        tiers = {t["model_tier"]: t for t in data["by_model_tier"]}
        assert "haiku" in tiers
        assert "sonnet" in tiers
        assert tiers["haiku"]["task_count"] == 3  # 3 haiku tasks in terminal statuses
        assert tiers["sonnet"]["task_count"] == 3  # 3 sonnet tasks in terminal statuses

        # Daily trend
        assert len(data["daily_trend"]) == 1
        assert data["daily_trend"][0]["date"] == "2025-01-15"

        # Total cost
        assert data["total_cost_usd"] > 0

    async def test_days_param(self, app_client, tmp_db):
        client = await _get_admin_client(app_client, tmp_db)
        resp = await client.get("/api/admin/analytics/cost-breakdown?days=7")
        assert resp.status_code == 200

    async def test_days_param_validation(self, app_client, tmp_db):
        client = await _get_admin_client(app_client, tmp_db)
        resp = await client.get("/api/admin/analytics/cost-breakdown?days=0")
        assert resp.status_code == 422  # ge=1


# ---------------------------------------------------------------------------
# Task Outcomes
# ---------------------------------------------------------------------------

class TestTaskOutcomes:
    async def test_empty_db(self, app_client, tmp_db):
        client = await _get_admin_client(app_client, tmp_db)
        resp = await client.get("/api/admin/analytics/task-outcomes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["by_tier"] == []
        assert data["verification_by_tier"] == []

    async def test_with_data(self, app_client, tmp_db):
        client = await _get_admin_client(app_client, tmp_db)
        await _seed_analytics_data(tmp_db)

        resp = await client.get("/api/admin/analytics/task-outcomes")
        assert resp.status_code == 200
        data = resp.json()

        # By tier
        tiers = {t["model_tier"]: t for t in data["by_tier"]}
        assert "haiku" in tiers
        assert tiers["haiku"]["completed"] == 2
        assert tiers["haiku"]["failed"] == 1
        assert tiers["haiku"]["total"] == 3
        assert 0 < tiers["haiku"]["success_rate"] < 1

        assert "sonnet" in tiers
        assert tiers["sonnet"]["completed"] == 2
        assert tiers["sonnet"]["needs_review"] == 1

        # Verification by tier
        verif = {v["model_tier"]: v for v in data["verification_by_tier"]}
        assert "haiku" in verif
        assert verif["haiku"]["passed"] == 2
        assert "sonnet" in verif
        assert verif["sonnet"]["human_needed"] == 1


# ---------------------------------------------------------------------------
# Efficiency
# ---------------------------------------------------------------------------

class TestEfficiency:
    async def test_empty_db(self, app_client, tmp_db):
        client = await _get_admin_client(app_client, tmp_db)
        resp = await client.get("/api/admin/analytics/efficiency")
        assert resp.status_code == 200
        data = resp.json()
        assert data["retries_by_tier"] == []
        assert data["checkpoint_count"] == 0
        assert data["unresolved_checkpoint_count"] == 0
        assert data["wave_throughput"] == []
        assert data["cost_efficiency"] == []

    async def test_with_data(self, app_client, tmp_db):
        client = await _get_admin_client(app_client, tmp_db)
        await _seed_analytics_data(tmp_db)

        resp = await client.get("/api/admin/analytics/efficiency")
        assert resp.status_code == 200
        data = resp.json()

        # Retries
        retries = {r["model_tier"]: r for r in data["retries_by_tier"]}
        assert "sonnet" in retries
        assert retries["sonnet"]["tasks_with_retries"] >= 1  # t5 has retry_count=1

        # Checkpoints
        assert data["checkpoint_count"] == 2
        assert data["unresolved_checkpoint_count"] == 1

        # Wave throughput (only tasks with both started_at and completed_at)
        waves = {w["wave"]: w for w in data["wave_throughput"]}
        assert len(waves) >= 1

        # Cost efficiency
        eff = {e["model_tier"]: e for e in data["cost_efficiency"]}
        assert "haiku" in eff
        assert eff["haiku"]["tasks_completed"] == 2
        assert eff["haiku"]["verification_pass_count"] == 2
        assert eff["haiku"]["cost_per_pass"] is not None
