import json

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from ai_service import GEMINI_IMAGE_MODEL
from auth import login_required
from billing_service import (
    build_plan_required_payload,
    ensure_credits_or_error,
    get_billing_summary,
    get_effective_api_key,
    has_required_plan,
    quote_avatars,
    refresh_cycle_if_needed,
)
from db import AvatarBatch, AvatarResult, Product, User, db
from media_storage import delete_storage_file
from usage_pricing import normalize_model_name
from worker_queue import enqueue_worker_job, get_worker_job_for_user, job_payload, serialize_worker_job
from worker_tasks import JOB_TYPE_AVATARS_GENERATE

avatars_bp = Blueprint("avatars", __name__)
MAX_PERSONAS_PER_REQUEST = 24
MAX_PERSONA_LENGTH = 120
MAX_CHARACTERISTIC_LENGTH = 200

PRESET_PERSONAS = [
    "White American mom",
    "Black American mom",
    "Hispanic mom",
    "Asian middle-aged woman",
    "White American woman",
    "Black American woman",
    "Asian young woman",
    "Hispanic young woman",
    "White American dad",
    "Black American dad",
    "Hispanic dad",
    "Asian middle-aged man",
    "White American man",
    "Black American man",
    "Asian young man",
    "Hispanic young man",
]


def _get_user():
    return db.session.get(User, session["user_id"])


# ── Page ────────────────────────────────────────────────────────────
@avatars_bp.route("/avatars")
@login_required
def avatars_page():
    user = _get_user()
    if not has_required_plan(user, "pro"):
        return redirect(url_for("billing.upgrade_page", feature="avatars"))
    changed = refresh_cycle_if_needed(user)
    if changed:
        db.session.commit()
    billing_summary = get_billing_summary(user)
    products = (
        Product.query.filter_by(user_id=user.id)
        .order_by(Product.created_at.desc())
        .all()
    )
    batches = (
        AvatarBatch.query.filter_by(user_id=user.id)
        .order_by(AvatarBatch.created_at.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "avatars.html",
        has_api_key=bool(get_effective_api_key(user.id, "gemini")),
        credits=billing_summary["available_credits"],
        monthly_credits=billing_summary["monthly_credits"],
        extra_credits=billing_summary["extra_credits"],
        plan_tier=billing_summary["plan_tier"],
        products=products,
        batches=batches,
        preset_personas=PRESET_PERSONAS,
        default_gemini_image_model=user.default_gemini_image_model or GEMINI_IMAGE_MODEL,
        batch_api_for_queued_jobs=bool(user.batch_api_for_queued_jobs),
    )


# ── Generate avatars (async) ────────────────────────────────────────
@avatars_bp.route("/avatars/generate", methods=["POST"])
@login_required
def generate_avatars():
    user = _get_user()
    refresh_cycle_if_needed(user)
    if not has_required_plan(user, "pro"):
        status_code, payload = build_plan_required_payload("pro", feature="avatars")
        return jsonify(payload), status_code
    data = request.get_json() or {}

    product_id = data.get("product_id")
    characteristic = data.get("characteristic", "").strip()
    personas = data.get("personas", [])
    if isinstance(personas, list):
        personas = [persona.strip() for persona in personas if isinstance(persona, str) and persona.strip()]
    else:
        personas = []
    count_per_persona = int(data.get("count_per_persona", 1))
    count_per_persona = max(1, min(3, count_per_persona))
    requested_model = normalize_model_name(data.get("gemini_model"))
    use_batch_api = bool(data.get("use_batch_api", user.batch_api_for_queued_jobs))
    traffic_type = "batch" if use_batch_api else "standard"
    gemini_model = requested_model or normalize_model_name(user.default_gemini_image_model)

    if not product_id:
        return jsonify({"error": "Please select a product."}), 400
    if not characteristic:
        return jsonify({"error": "Physical characteristic is required."}), 400
    if len(characteristic) > MAX_CHARACTERISTIC_LENGTH:
        return jsonify({"error": f"Characteristic must be <= {MAX_CHARACTERISTIC_LENGTH} characters."}), 400
    if not personas or len(personas) == 0:
        return jsonify({"error": "Select at least one persona."}), 400
    if len(personas) > MAX_PERSONAS_PER_REQUEST:
        return jsonify({"error": f"Select at most {MAX_PERSONAS_PER_REQUEST} personas per run."}), 400
    if any(
        (not isinstance(persona, str) or not persona.strip() or len(persona.strip()) > MAX_PERSONA_LENGTH)
        for persona in personas
    ):
        return jsonify({"error": f"Each persona must be a non-empty string <= {MAX_PERSONA_LENGTH} chars."}), 400
    if gemini_model and "image" not in gemini_model.lower():
        return jsonify({"error": "Invalid Gemini image model."}), 400

    # Validate product belongs to user
    product = Product.query.filter_by(id=product_id, user_id=user.id).first()
    if not product:
        return jsonify({"error": "Product not found."}), 404

    gemini_key = get_effective_api_key(user.id, "gemini")
    if not gemini_key:
        return jsonify({"error": "no_api_key"}), 400

    total_pairs = len(personas) * count_per_persona
    credit_quote = quote_avatars(total_pairs, traffic_type=traffic_type)
    credit_error = ensure_credits_or_error(user, credit_quote["credits"], feature="avatars")
    if credit_error:
        status_code, payload = credit_error
        db.session.commit()
        return jsonify(payload), status_code

    # Create batch
    batch = AvatarBatch(
        user_id=user.id,
        product_id=product.id,
        characteristic=characteristic,
        personas=json.dumps(personas),
        count_per_persona=count_per_persona,
        status="queued",
    )
    db.session.add(batch)
    db.session.flush()

    charge_per_pair = max(1, int(credit_quote["credits"] // max(1, total_pairs)))
    estimated_per_pair = float(
        credit_quote["estimated_cost_usd"] / total_pairs if total_pairs else 0
    )

    result_records = []
    for persona in personas:
        for _ in range(count_per_persona):
            result = AvatarResult(
                batch_id=batch.id,
                persona=persona,
                status="pending",
            )
            db.session.add(result)
            result_records.append(result)
    db.session.flush()

    job = enqueue_worker_job(
        user_id=user.id,
        job_type=JOB_TYPE_AVATARS_GENERATE,
        queue_name="default",
        max_attempts=2,
        payload={
            "batch_id": batch.id,
            "product_id": product.id,
            "result_ids_with_personas": [
                {"result_id": r.id, "persona": r.persona}
                for r in result_records
            ],
            "gemini_model": gemini_model,
            "traffic_type": traffic_type,
            "charge_per_pair": charge_per_pair,
            "estimated_per_pair": estimated_per_pair,
        },
    )
    db.session.commit()

    return jsonify({
        "job_id": job.id,
        "batch_id": batch.id,
        "status": "queued",
        "status_url": f"/avatars/jobs/{job.id}",
    }), 202


# ── Job status ──────────────────────────────────────────────────────
@avatars_bp.route("/avatars/jobs/<int:job_id>", methods=["GET"])
@login_required
def avatar_job_status(job_id):
    job = get_worker_job_for_user(job_id, session["user_id"])
    if not job or job.job_type != JOB_TYPE_AVATARS_GENERATE:
        return jsonify({"error": "Job not found."}), 404

    serialized = serialize_worker_job(job)
    payload = job_payload(job)
    batch_id = payload.get("batch_id")

    response = {
        "job_id": job.id,
        "status": job.status,
        "batch_id": batch_id,
        "error": serialized["error"],
    }

    if job.status == "completed":
        results = AvatarResult.query.filter_by(batch_id=batch_id).order_by(AvatarResult.id).all()
        response["results"] = [
            {
                "id": r.id,
                "persona": r.persona,
                "status": r.status,
                "before_image_url": url_for("media.avatar_result_before_image", result_id=r.id) if r.status == "completed" else None,
                "after_image_url": url_for("media.avatar_result_after_image", result_id=r.id) if r.status == "completed" else None,
                "error": r.error_message,
            }
            for r in results
        ]

    return jsonify(response)


# ── Delete batch ───────────────────────────────────────────────────
@avatars_bp.route("/avatars/batch/<int:batch_id>", methods=["DELETE"])
@login_required
def delete_batch(batch_id):
    batch = AvatarBatch.query.filter_by(
        id=batch_id, user_id=session["user_id"]
    ).first()
    if not batch:
        return jsonify({"error": "Batch not found."}), 404

    for result in batch.results:
        delete_storage_file(result.before_storage_path)
        delete_storage_file(result.after_storage_path)
    db.session.delete(batch)
    db.session.commit()
    return jsonify({"success": True})


# ── Gemini API call ────────────────────────────────────────────────
