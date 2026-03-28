from __future__ import annotations

import json
from urllib.parse import urlparse

from flask import Blueprint, jsonify, render_template, request, session

from auth import login_required
from billing_service import get_available_credits, get_effective_api_key
from db import BrandDNAAnalysis, User, db
from scraper import _ensure_public_url
from worker_queue import (
    enqueue_worker_job,
    get_worker_job_for_user,
    serialize_worker_job,
)
from worker_tasks import JOB_TYPE_BRAND_DNA_ANALYZE

brand_dna_bp = Blueprint("brand_dna", __name__)

BRAND_DNA_CREDIT_COST = 5
BRAND_DNA_JOB_QUEUE = "default"


def _get_user() -> User:
    return db.session.get(User, session["user_id"])


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@brand_dna_bp.route("/brand-dna")
@login_required
def brand_dna_page():
    user = _get_user()
    openai_key = get_effective_api_key(user.id, "openai")
    recent_analyses = (
        BrandDNAAnalysis.query
        .filter_by(user_id=user.id)
        .order_by(BrandDNAAnalysis.created_at.desc())
        .limit(10)
        .all()
    )
    palette_data = {}
    for a in recent_analyses:
        if a.color_palette:
            try:
                palette_data[a.id] = json.loads(a.color_palette)
            except Exception:
                palette_data[a.id] = []
    return render_template(
        "brand_dna.html",
        has_openai_key=bool(openai_key),
        recent_analyses=recent_analyses,
        palette_data=palette_data,
        credit_cost=BRAND_DNA_CREDIT_COST,
    )


# ---------------------------------------------------------------------------
# Submit analysis
# ---------------------------------------------------------------------------

@brand_dna_bp.route("/brand-dna/analyze", methods=["POST"])
@login_required
def analyze_brand():
    user = _get_user()
    openai_key = get_effective_api_key(user.id, "openai")
    if not openai_key:
        return jsonify({"error": "OpenAI API key is not configured on the platform."}), 400

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required."}), 400

    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    elif parsed.scheme not in ("http", "https"):
        return jsonify({"error": "Only http and https URLs are supported."}), 400

    try:
        _ensure_public_url(url)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if get_available_credits(user) < BRAND_DNA_CREDIT_COST:
        return jsonify({
            "error": f"You need at least {BRAND_DNA_CREDIT_COST} credits to run a Brand DNA analysis.",
            "reason": "insufficient_credits",
        }), 402

    analysis = BrandDNAAnalysis(user_id=user.id, url=url, status="queued")
    db.session.add(analysis)
    db.session.flush()

    job = enqueue_worker_job(
        user_id=user.id,
        job_type=JOB_TYPE_BRAND_DNA_ANALYZE,
        queue_name=BRAND_DNA_JOB_QUEUE,
        max_attempts=1,
        payload={
            "analysis_id": analysis.id,
            "url": url,
            "credit_cost": BRAND_DNA_CREDIT_COST,
        },
    )
    analysis.worker_job_id = job.id
    db.session.commit()

    return jsonify({
        "job_id": job.id,
        "analysis_id": analysis.id,
        "status": "queued",
        "status_url": f"/brand-dna/jobs/{job.id}",
    }), 202


# ---------------------------------------------------------------------------
# Job status (polling)
# ---------------------------------------------------------------------------

@brand_dna_bp.route("/brand-dna/jobs/<int:job_id>")
@login_required
def brand_dna_job_status(job_id: int):
    job = get_worker_job_for_user(job_id, session["user_id"])
    if not job or job.job_type != JOB_TYPE_BRAND_DNA_ANALYZE:
        return jsonify({"error": "Job not found."}), 404

    serialized = serialize_worker_job(job)

    response = {
        "job_id": job.id,
        "status": serialized["status"],
        "error": serialized["error"],
        "started_at": serialized["started_at"],
        "finished_at": serialized["finished_at"],
    }

    if serialized["status"] == "completed":
        try:
            result = json.loads(job.result_json or "{}")
            analysis_id = result.get("analysis_id")
        except Exception:
            analysis_id = None

        if not analysis_id:
            try:
                payload = json.loads(job.payload_json or "{}")
                analysis_id = payload.get("analysis_id")
            except Exception:
                analysis_id = None

        if analysis_id:
            analysis = db.session.get(BrandDNAAnalysis, analysis_id)
            if analysis:
                response["analysis"] = {
                    "id": analysis.id,
                    "brand_name": analysis.brand_name,
                    "brand_description": analysis.brand_description,
                    "color_palette": json.loads(analysis.color_palette or "[]"),
                    "products_created": analysis.products_created,
                    "url": analysis.url,
                }

    return jsonify(response)
