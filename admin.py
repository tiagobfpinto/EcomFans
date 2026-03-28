"""Admin blueprint — internal cost/usage dashboard.

Access is restricted to user IDs listed in the ADMIN_USER_IDS env var
(comma-separated integers, e.g. "1,2"). Defaults to user ID 1 only.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, abort, render_template, session
from sqlalchemy import func, text

from db import ApiRequestEvent, User, db

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def _get_admin_ids() -> set[int]:
    raw = os.environ.get("ADMIN_USER_IDS", "1")
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id or user_id not in _get_admin_ids():
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@admin_bp.route("/costs")
@admin_required
def costs():
    now = datetime.now(timezone.utc)
    days = 30
    cutoff = now - timedelta(days=days)

    # -- Summary cards: total cost + calls in period -----------------------
    summary_row = db.session.execute(
        text("""
            SELECT
                COUNT(*) AS total_calls,
                COALESCE(SUM(estimated_cost_usd), 0) AS total_usd
            FROM api_request_events
            WHERE created_at >= :cutoff
              AND status = 'completed'
        """),
        {"cutoff": cutoff},
    ).fetchone()
    total_calls = summary_row.total_calls
    total_usd = float(summary_row.total_usd or 0)

    # -- Cost by feature + provider ----------------------------------------
    by_feature = db.session.execute(
        text("""
            SELECT
                COALESCE(feature, 'unknown') AS feature,
                provider,
                COUNT(*) AS calls,
                COALESCE(SUM(estimated_cost_usd), 0) AS total_usd,
                COALESCE(AVG(estimated_cost_usd), 0) AS avg_usd
            FROM api_request_events
            WHERE created_at >= :cutoff
            GROUP BY feature, provider
            ORDER BY total_usd DESC
        """),
        {"cutoff": cutoff},
    ).fetchall()

    # -- Cost by operation (detailed breakdown) ----------------------------
    by_operation = db.session.execute(
        text("""
            SELECT
                COALESCE(feature, 'unknown') AS feature,
                COALESCE(operation, 'unknown') AS operation,
                provider,
                COALESCE(model, '') AS model,
                COUNT(*) AS calls,
                COALESCE(SUM(estimated_cost_usd), 0) AS total_usd,
                COALESCE(AVG(total_tokens), 0) AS avg_tokens,
                COALESCE(MAX(total_tokens), 0) AS max_tokens
            FROM api_request_events
            WHERE created_at >= :cutoff
            GROUP BY feature, operation, provider, model
            ORDER BY total_usd DESC
        """),
        {"cutoff": cutoff},
    ).fetchall()

    # -- Top users by cost --------------------------------------------------
    top_users = db.session.execute(
        text("""
            SELECT
                u.username,
                u.email,
                u.plan_tier,
                COUNT(e.id) AS calls,
                COALESCE(SUM(e.estimated_cost_usd), 0) AS total_usd
            FROM api_request_events e
            JOIN users u ON u.id = e.user_id
            WHERE e.created_at >= :cutoff
            GROUP BY u.id, u.username, u.email, u.plan_tier
            ORDER BY total_usd DESC
            LIMIT 20
        """),
        {"cutoff": cutoff},
    ).fetchall()

    # -- Daily trend (last 30 days) ----------------------------------------
    daily_trend = db.session.execute(
        text("""
            SELECT
                DATE(created_at) AS day,
                COALESCE(SUM(estimated_cost_usd), 0) AS total_usd,
                COUNT(*) AS calls
            FROM api_request_events
            WHERE created_at >= :cutoff
            GROUP BY day
            ORDER BY day DESC
        """),
        {"cutoff": cutoff},
    ).fetchall()

    # -- Failed calls (errors burning retries) -----------------------------
    failed_calls = db.session.execute(
        text("""
            SELECT
                COALESCE(feature, 'unknown') AS feature,
                COALESCE(operation, 'unknown') AS operation,
                provider,
                COUNT(*) AS error_count,
                MAX(created_at) AS last_error
            FROM api_request_events
            WHERE created_at >= :cutoff
              AND status = 'failed'
            GROUP BY feature, operation, provider
            ORDER BY error_count DESC
        """),
        {"cutoff": cutoff},
    ).fetchall()

    # -- Expensive individual calls (token overruns) ----------------------
    heavy_calls = db.session.execute(
        text("""
            SELECT
                COALESCE(feature, 'unknown') AS feature,
                COALESCE(operation, 'unknown') AS operation,
                provider,
                model,
                total_tokens,
                estimated_cost_usd,
                e.created_at,
                u.username
            FROM api_request_events e
            LEFT JOIN users u ON u.id = e.user_id
            WHERE e.created_at >= :cutoff
              AND e.total_tokens IS NOT NULL
            ORDER BY e.estimated_cost_usd DESC NULLS LAST
            LIMIT 20
        """),
        {"cutoff": cutoff},
    ).fetchall()

    # -- Cache hit stats for inspiration analysis --------------------------
    cache_stats = db.session.execute(
        text("""
            SELECT
                COUNT(*) AS total_analysis_calls,
                COUNT(*) FILTER (WHERE operation = 'inspiration_analysis') AS analysis_calls
            FROM api_request_events
            WHERE created_at >= :cutoff
              AND feature = 'ai_creatives'
        """),
        {"cutoff": cutoff},
    ).fetchone()

    # -- AI Creatives prompt vs image cost split ---------------------------
    # Splits inspiration_analysis cost by whether it led to an image or was
    # prompt-only (metadata_json::jsonb->>'prompt_only' = 'true').
    creatives_split = db.session.execute(
        text("""
            SELECT
                CASE
                    WHEN metadata_json::jsonb->>'prompt_only' = 'true' THEN 'prompt_only'
                    ELSE 'with_image'
                END AS mode,
                provider,
                COUNT(*) AS calls,
                COALESCE(SUM(estimated_cost_usd), 0) AS total_usd,
                COALESCE(AVG(total_tokens), 0) AS avg_tokens
            FROM api_request_events
            WHERE created_at >= :cutoff
              AND feature = 'ai_creatives'
              AND operation = 'inspiration_analysis'
              AND metadata_json IS NOT NULL
            GROUP BY mode, provider
            ORDER BY total_usd DESC
        """),
        {"cutoff": cutoff},
    ).fetchall()

    return render_template(
        "admin_costs.html",
        days=days,
        total_calls=total_calls,
        total_usd=total_usd,
        by_feature=by_feature,
        by_operation=by_operation,
        top_users=top_users,
        daily_trend=daily_trend,
        failed_calls=failed_calls,
        heavy_calls=heavy_calls,
        cache_stats=cache_stats,
        creatives_split=creatives_split,
        now=now,
    )
