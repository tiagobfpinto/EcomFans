import base64

from flask import Blueprint, jsonify, render_template, request, session, url_for

from ai_service import (
    gemini_vision_with_meta,
    openai_chat_with_meta,
)
from auth import login_required
from billing_service import (
    ensure_credits_or_error,
    get_billing_summary,
    get_effective_api_key,
    quote_ai_creatives,
    refresh_cycle_if_needed,
)
from db import (
    CreativeBatch, CreativeInspiration, CreativeResult,
    Product, User, db,
)
from media_storage import (
    delete_storage_file,
    save_inspiration_image,
)
from usage_pricing import normalize_model_name
from worker_queue import enqueue_worker_job, get_worker_job_for_user, job_payload, serialize_worker_job
from worker_tasks import JOB_TYPE_CREATIVES_GENERATE
from security import is_allowed_upload_image, upload_image_type_error

ai_creatives_bp = Blueprint("ai_creatives", __name__)

# Internal system prompt — enforces clean output format from the analysis step.
# The user controls the actual analysis instruction via the "analysis prompt" field.
_ANALYSIS_SYSTEM_PROMPT = (
    "You are an expert ad creative director. "
    "Output ONLY the requested text, no commentary, no preamble, no markdown."
)

DEFAULT_BASE_PROMPT = (
    "Create a high-quality advertising creative featuring the product prominently."
)

DEFAULT_ANALYSIS_PROMPT = (
    "Analyze this ad image in detail. Describe: the visual style, layout, color palette, "
    "mood, composition, lighting, text placement, and overall aesthetic. "
    "Then write a concrete image generation prompt to recreate this ad style for a different product. "
    "Output only the image generation prompt — no explanation."
)


MAX_PRODUCT_CONTEXT_IMAGES = 3
ANALYSIS_MEDIA_RESOLUTION = "MEDIUM"
MAX_INSPIRATION_UPLOADS_PER_REQUEST = 20
MAX_INSPIRATION_IMAGE_BYTES = 8 * 1024 * 1024
MAX_INSPIRATION_TOTAL_BYTES = 40 * 1024 * 1024
MAX_INSPIRATIONS_PER_RUN = 24
MAX_PROMPT_CHARS = 5000

# Competitor inspiration two-phase pipeline
VALID_AWARENESS_LEVELS = frozenset([
    "unaware", "problem_aware", "solution_aware", "product_aware", "most_aware",
])
VALID_PLATFORMS = frozenset([
    "meta_feed", "meta_stories", "google_display", "tiktok_feed", "email_banner", "other",
])


def _get_user():
    return db.session.get(User, session["user_id"])


# ── Page ─────────────────────────────────────────────────────────────
@ai_creatives_bp.route("/ai-creatives")
@login_required
def ai_creatives_page():
    user = _get_user()
    changed = refresh_cycle_if_needed(user)
    if changed:
        db.session.commit()
    billing_summary = get_billing_summary(user)
    gemini_key = get_effective_api_key(user.id, "gemini")
    openai_key = get_effective_api_key(user.id, "openai")
    products = Product.query.filter_by(user_id=user.id).order_by(Product.created_at.desc()).all()
    inspirations = (
        CreativeInspiration.query
        .filter_by(user_id=user.id)
        .order_by(CreativeInspiration.created_at.desc())
        .all()
    )
    batches = (
        CreativeBatch.query
        .filter_by(user_id=user.id)
        .order_by(CreativeBatch.created_at.desc())
        .all()
    )
    return render_template(
        "ai_creatives.html",
        has_gemini_key=bool(gemini_key),
        has_openai_key=bool(openai_key),
        credits=billing_summary["available_credits"],
        monthly_credits=billing_summary["monthly_credits"],
        extra_credits=billing_summary["extra_credits"],
        plan_tier=billing_summary["plan_tier"],
        products=products,
        inspirations=inspirations,
        batches=batches,
        default_base_prompt=user.default_base_prompt or DEFAULT_BASE_PROMPT,
        default_analysis_prompt=user.default_analysis_prompt or DEFAULT_ANALYSIS_PROMPT,
        default_gemini_image_model=user.default_gemini_image_model,
        batch_api_for_queued_jobs=bool(user.batch_api_for_queued_jobs),
    )


@ai_creatives_bp.route("/ai-creatives/save-default", methods=["POST"])
@login_required
def save_default_prompt():
    user = _get_user()
    data = request.get_json() or {}
    field = data.get("field")
    value = data.get("value", "").strip()

    if field == "base_prompt":
        user.default_base_prompt = value if value else None
    elif field == "analysis_prompt":
        user.default_analysis_prompt = value if value else None
    else:
        return jsonify({"error": "Invalid field."}), 400

    db.session.commit()
    return jsonify({"success": True, "field": field, "value": value})


# ── Inspirations library ──────────────────────────────────────────────
@ai_creatives_bp.route("/ai-creatives/inspirations", methods=["POST"])
@login_required
def upload_inspirations():
    user_id = session["user_id"]
    data = request.get_json() or {}
    images = data.get("images", [])

    if not images:
        return jsonify({"error": "No images provided."}), 400
    if not isinstance(images, list):
        return jsonify({"error": "Invalid images payload."}), 400
    if len(images) > MAX_INSPIRATION_UPLOADS_PER_REQUEST:
        return jsonify(
            {
                "error": (
                    f"Upload at most {MAX_INSPIRATION_UPLOADS_PER_REQUEST} inspiration images per request."
                )
            }
        ), 400

    saved = []
    written_paths = []
    total_bytes = 0
    for img in images:
        mime = img.get("mime_type", "image/jpeg")
        b64 = img.get("data", "")
        name = img.get("name", "")
        if not b64:
            continue
        if not is_allowed_upload_image(mime):
            return jsonify({"error": upload_image_type_error()}), 400
        try:
            image_bytes = base64.b64decode(b64, validate=True)
        except Exception:
            return jsonify({"error": "Invalid base64 image payload."}), 400
        if not image_bytes:
            continue
        if len(image_bytes) > MAX_INSPIRATION_IMAGE_BYTES:
            return jsonify(
                {
                    "error": (
                        f"Each inspiration must be <= {MAX_INSPIRATION_IMAGE_BYTES // (1024 * 1024)} MB."
                    )
                }
            ), 413
        total_bytes += len(image_bytes)
        if total_bytes > MAX_INSPIRATION_TOTAL_BYTES:
            return jsonify(
                {
                    "error": (
                        f"Total inspiration upload must be <= {MAX_INSPIRATION_TOTAL_BYTES // (1024 * 1024)} MB."
                    )
                }
            ), 413

        try:
            insp = CreativeInspiration(
                user_id=user_id,
                name=name,
                image_data=None,
                mime_type=mime,
            )
            db.session.add(insp)
            db.session.flush()
            insp.storage_path = save_inspiration_image(
                user_id,
                insp.id,
                mime,
                image_bytes,
            )
            written_paths.append(insp.storage_path)
            saved.append({"id": insp.id, "name": insp.name, "mime_type": insp.mime_type})
        except Exception:
            db.session.rollback()
            for relative_path in written_paths:
                delete_storage_file(relative_path)
            return jsonify({"error": "Failed to save one or more inspirations."}), 500

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        for relative_path in written_paths:
            delete_storage_file(relative_path)
        return jsonify({"error": "Failed to persist inspirations."}), 500
    return jsonify({"success": True, "saved": saved})


@ai_creatives_bp.route("/ai-creatives/inspirations/<int:inspiration_id>", methods=["DELETE"])
@login_required
def delete_inspiration(inspiration_id):
    insp = CreativeInspiration.query.filter_by(
        id=inspiration_id, user_id=session["user_id"]
    ).first()
    if not insp:
        return jsonify({"error": "Not found."}), 404
    storage_path = insp.storage_path
    db.session.delete(insp)
    db.session.commit()
    delete_storage_file(storage_path)
    return jsonify({"success": True})


# ── Generate batch (async) ────────────────────────────────────────────
@ai_creatives_bp.route("/ai-creatives/generate", methods=["POST"])
@login_required
def generate_creatives():
    user = _get_user()
    refresh_cycle_if_needed(user)
    data = request.get_json() or {}

    product_id = data.get("product_id")
    inspiration_ids = data.get("inspiration_ids", [])
    if not isinstance(inspiration_ids, list):
        return jsonify({"error": "Invalid inspirations payload."}), 400
    try:
        inspiration_ids = list(dict.fromkeys(int(insp_id) for insp_id in inspiration_ids))
    except Exception:
        return jsonify({"error": "Invalid inspiration IDs."}), 400
    provider = data.get("provider", "gemini")  # "gemini" | "openai" | "both"
    base_prompt = (data.get("base_prompt") or DEFAULT_BASE_PROMPT).strip()
    analysis_prompt = (data.get("analysis_prompt") or DEFAULT_ANALYSIS_PROMPT).strip()
    product_info_source = (data.get("product_info_source") or "product_page").strip()
    prompt_only = data.get("prompt_only", False)
    generation_mode = (data.get("generation_mode") or "standard").strip()
    awareness_level = (data.get("awareness_level") or "solution_aware").strip()
    platform = (data.get("platform") or "meta_feed").strip()
    requested_model = normalize_model_name(data.get("gemini_model"))
    use_batch_api = bool(data.get("use_batch_api", user.batch_api_for_queued_jobs))
    if provider != "gemini":
        use_batch_api = False
    traffic_type = "batch" if use_batch_api else "standard"
    gemini_model = requested_model or normalize_model_name(user.default_gemini_image_model)

    if provider not in ("gemini", "openai", "both"):
        return jsonify({"error": "Invalid provider."}), 400
    if gemini_model and "image" not in gemini_model.lower():
        return jsonify({"error": "Invalid Gemini image model."}), 400
    if not product_id:
        return jsonify({"error": "Product is required."}), 400
    if not inspiration_ids:
        return jsonify({"error": "Select at least one inspiration."}), 400
    if len(inspiration_ids) > MAX_INSPIRATIONS_PER_RUN:
        return jsonify(
            {"error": f"Select at most {MAX_INSPIRATIONS_PER_RUN} inspirations per run."}
        ), 400
    if generation_mode not in ("standard", "competitor_inspiration"):
        return jsonify({"error": "Invalid generation_mode."}), 400
    if generation_mode != "competitor_inspiration":
        if not base_prompt:
            return jsonify({"error": "Base prompt is required."}), 400
        if not analysis_prompt:
            return jsonify({"error": "Analysis prompt is required."}), 400
        if len(base_prompt) > MAX_PROMPT_CHARS:
            return jsonify({"error": f"Base prompt must be <= {MAX_PROMPT_CHARS} characters."}), 400
        if len(analysis_prompt) > MAX_PROMPT_CHARS:
            return jsonify({"error": f"Analysis prompt must be <= {MAX_PROMPT_CHARS} characters."}), 400
    if generation_mode == "competitor_inspiration":
        if awareness_level not in VALID_AWARENESS_LEVELS:
            return jsonify({"error": "Invalid awareness_level."}), 400
        if platform not in VALID_PLATFORMS:
            return jsonify({"error": "Invalid platform."}), 400
    if product_info_source != "product_page":
        return jsonify({"error": "Invalid product info source."}), 400

    product = Product.query.filter_by(id=product_id, user_id=user.id).first()
    if not product:
        return jsonify({"error": "Product not found."}), 404

    inspirations = CreativeInspiration.query.filter(
        CreativeInspiration.id.in_(inspiration_ids),
        CreativeInspiration.user_id == user.id,
    ).all()
    if not inspirations:
        return jsonify({"error": "No valid inspirations found."}), 404

    gemini_key = get_effective_api_key(user.id, "gemini")
    openai_key = get_effective_api_key(user.id, "openai")

    if provider in ("gemini", "both") and not gemini_key:
        return jsonify({"error": "no_gemini_key"}), 400
    if provider in ("openai", "both") and not openai_key:
        return jsonify({"error": "no_openai_key"}), 400

    credit_quote = quote_ai_creatives(
        len(inspirations),
        provider,
        bool(prompt_only),
        traffic_type=traffic_type,
    )
    credit_error = ensure_credits_or_error(
        user, credit_quote["credits"], feature="ai_creatives"
    )
    if credit_error:
        status_code, payload = credit_error
        db.session.commit()
        return jsonify(payload), status_code

    total_credits_to_charge = int(credit_quote["credits"])
    total_units = int(credit_quote["units"] or 1)
    base_credits_per_inspiration = total_credits_to_charge // total_units
    per_inspiration_cost = (
        credit_quote["estimated_cost_usd"] / credit_quote["units"]
        if credit_quote["units"]
        else 0
    )

    batch = CreativeBatch(
        user_id=user.id,
        product_id=product.id,
        status="queued",
    )
    db.session.add(batch)
    db.session.flush()

    result_records = []
    for insp in inspirations:
        result = CreativeResult(
            batch_id=batch.id,
            inspiration_id=insp.id,
            status="pending",
        )
        db.session.add(result)
        result_records.append(result)
    db.session.flush()

    job = enqueue_worker_job(
        user_id=user.id,
        job_type=JOB_TYPE_CREATIVES_GENERATE,
        queue_name="default",
        max_attempts=2,
        payload={
            "batch_id": batch.id,
            "product_id": product.id,
            "inspiration_ids": [insp.id for insp in inspirations],
            "result_ids_with_inspiration_ids": [
                {"result_id": r.id, "inspiration_id": r.inspiration_id}
                for r in result_records
            ],
            "provider": provider,
            "base_prompt": base_prompt,
            "analysis_prompt": analysis_prompt,
            "gemini_model": gemini_model,
            "traffic_type": traffic_type,
            "prompt_only": prompt_only,
            "generation_mode": generation_mode,
            "awareness_level": awareness_level,
            "platform": platform,
            "credits_per_inspiration": base_credits_per_inspiration,
            "estimated_per_inspiration": float(per_inspiration_cost),
        },
    )
    db.session.commit()

    return jsonify({
        "job_id": job.id,
        "batch_id": batch.id,
        "status": "queued",
        "status_url": f"/ai-creatives/jobs/{job.id}",
    }), 202


# ── Job status ──────────────────────────────────────────────────────
@ai_creatives_bp.route("/ai-creatives/jobs/<int:job_id>", methods=["GET"])
@login_required
def creatives_job_status(job_id):
    job = get_worker_job_for_user(job_id, session["user_id"])
    if not job or job.job_type != JOB_TYPE_CREATIVES_GENERATE:
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
        results = CreativeResult.query.filter_by(batch_id=batch_id).order_by(CreativeResult.id).all()
        response["results"] = [
            {
                "id": r.id,
                "inspiration_id": r.inspiration_id,
                "status": r.status,
                "inspiration_url": url_for("media.inspiration_image", inspiration_id=r.inspiration_id) if r.inspiration_id else None,
                "generated_image_url": url_for("media.creative_result_generated_image", result_id=r.id) if r.status == "completed" and (r.generated_storage_path or r.generated_image) else None,
                "generated_prompt": r.generated_prompt,
                "error": r.error_message,
            }
            for r in results
        ]

    return jsonify(response)


# ── Delete batch ──────────────────────────────────────────────────────
@ai_creatives_bp.route("/ai-creatives/batch/<int:batch_id>", methods=["DELETE"])
@login_required
def delete_batch(batch_id):
    batch = CreativeBatch.query.filter_by(
        id=batch_id, user_id=session["user_id"]
    ).first()
    if not batch:
        return jsonify({"error": "Not found."}), 404
    for result in batch.results:
        delete_storage_file(result.generated_storage_path)
    db.session.delete(batch)
    db.session.commit()
    return jsonify({"success": True})


# ── Analysis helpers ──────────────────────────────────────────────────

def _analyze_with_openai(api_key, analysis_prompt, insp_b64, insp_mime):
    """Send analysis prompt + inspiration image to OpenAI, return (regen_prompt, meta)."""
    user_content = [
        {"type": "text", "text": analysis_prompt},
        {"type": "image_url", "image_url": {"url": f"data:{insp_mime};base64,{insp_b64}"}},
    ]
    return openai_chat_with_meta(
        api_key,
        _ANALYSIS_SYSTEM_PROMPT,
        user_content,
        max_tokens=1200,
        temperature=0.7,
    )


# Competitor Inspiration two-phase pipeline is handled entirely in the worker.
# See worker_runtime.py (_run_creatives_generate, generation_mode == "competitor_inspiration").




def _analyze_with_gemini(
    api_key,
    analysis_prompt,
    insp_b64,
    insp_mime,
    traffic_type: str = "standard",
):
    """Send analysis prompt + inspiration image to Gemini vision, return (regen_prompt, meta)."""
    parts = [
        {"text": analysis_prompt},
        {"inlineData": {"mimeType": insp_mime, "data": insp_b64}},
    ]
    return gemini_vision_with_meta(
        api_key,
        _ANALYSIS_SYSTEM_PROMPT,
        parts,
        max_tokens=1200,
        temperature=0.7,
        traffic_type=traffic_type,
        media_resolution=ANALYSIS_MEDIA_RESOLUTION,
    )
