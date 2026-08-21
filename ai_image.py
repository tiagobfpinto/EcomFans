import base64
import mimetypes

from flask import Blueprint, jsonify, render_template, request, session, url_for

from ai_service import (
    GEMINI_IMAGE_MODEL,
    gemini_list_image_models,
    openai_chat_with_meta,
)
from auth import login_required
from billing_service import (
    allow_user_api_keys,
    ensure_credits_or_error,
    get_billing_summary,
    get_effective_api_key,
    quote_ai_image,
    record_api_request_event,
    refresh_cycle_if_needed,
)
from db import ApiKey, ImageGeneration, ImagePrompt, User, db
from media_storage import delete_storage_file
from usage_pricing import normalize_model_name
from utils_crypto import encrypt_api_key
from worker_queue import enqueue_worker_job, get_worker_job_for_user, job_payload, serialize_worker_job
from worker_tasks import JOB_TYPE_AI_IMAGE_GENERATE
from security import is_allowed_upload_image, upload_image_type_error

ai_image_bp = Blueprint("ai_image", __name__)

DEFAULT_REMIX_SYSTEM_PROMPT = (
    "You are a creative prompt engineer. Given an image generation prompt, "
    "rewrite it with creative variations while keeping the same core concept "
    "and intent. Make meaningful changes to style, composition, lighting, "
    "colors, or perspective. Return ONLY the rewritten prompt, nothing else."
)
MAX_REFERENCE_IMAGES = 3
MAX_REFERENCE_IMAGE_BYTES = 8 * 1024 * 1024
MAX_REFERENCE_TOTAL_BYTES = 20 * 1024 * 1024


def _get_user():
    """Return the current logged-in User object."""
    return db.session.get(User, session["user_id"])


def _resolve_gemini_image_model(user: User, requested_model: str | None = None) -> str:
    chosen = normalize_model_name(requested_model) or normalize_model_name(
        user.default_gemini_image_model
    )
    if not chosen:
        return GEMINI_IMAGE_MODEL
    return chosen


def _is_likely_image_model(model_name: str) -> bool:
    return "image" in model_name.lower()


# -- Page -------------------------------------------------------------------
@ai_image_bp.route("/ai-image")
@login_required
def ai_image_page():
    user = _get_user()
    changed = refresh_cycle_if_needed(user)
    if changed:
        db.session.commit()
    billing_summary = get_billing_summary(user)
    gemini_key = get_effective_api_key(user.id, "gemini")
    openai_key = get_effective_api_key(user.id, "openai")
    prompts = (
        ImagePrompt.query
        .filter_by(user_id=user.id)
        .order_by(ImagePrompt.created_at.desc())
        .all()
    )
    return render_template(
        "ai_image.html",
        has_api_key=bool(gemini_key),
        has_openai_key=bool(openai_key),
        credits=billing_summary["available_credits"],
        monthly_credits=billing_summary["monthly_credits"],
        extra_credits=billing_summary["extra_credits"],
        plan_tier=billing_summary["plan_tier"],
        prompts=prompts,
        default_remix_prompt=DEFAULT_REMIX_SYSTEM_PROMPT,
        default_gemini_image_model=user.default_gemini_image_model or GEMINI_IMAGE_MODEL,
        batch_api_for_queued_jobs=bool(user.batch_api_for_queued_jobs),
    )


# -- Settings and model discovery -------------------------------------------
@ai_image_bp.route("/ai-image/gemini-models", methods=["GET"])
@login_required
def get_gemini_image_models():
    user = _get_user()
    gemini_key = get_effective_api_key(user.id, "gemini")
    if not gemini_key:
        return jsonify({"error": "no_api_key"}), 400
    try:
        models = gemini_list_image_models(gemini_key)
        return jsonify({
            "models": models,
            "default_model": user.default_gemini_image_model or GEMINI_IMAGE_MODEL,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ai_image_bp.route("/ai-image/preferences", methods=["POST"])
@login_required
def save_ai_preferences():
    user = _get_user()
    data = request.get_json() or {}

    model_name = normalize_model_name(data.get("default_gemini_image_model"))
    if model_name is not None:
        if not _is_likely_image_model(model_name):
            return jsonify({"error": "Invalid Gemini image model."}), 400
        user.default_gemini_image_model = model_name

    if "batch_api_for_queued_jobs" in data:
        user.batch_api_for_queued_jobs = bool(data.get("batch_api_for_queued_jobs"))

    db.session.commit()
    return jsonify({
        "success": True,
        "default_gemini_image_model": user.default_gemini_image_model,
        "batch_api_for_queued_jobs": bool(user.batch_api_for_queued_jobs),
    })


# -- API Key management ------------------------------------------------------
@ai_image_bp.route("/ai-image/api-key", methods=["POST"])
@login_required
def save_api_key():
    if not allow_user_api_keys():
        return jsonify({"error": "User API keys are disabled in platform mode."}), 403

    data = request.get_json() or {}
    key = data.get("api_key", "").strip()
    service = data.get("service", "gemini").strip()

    if service not in ("gemini", "openai"):
        return jsonify({"error": "Invalid service."}), 400
    if not key:
        return jsonify({"error": "API key is required."}), 400

    user_id = session["user_id"]
    existing = ApiKey.query.filter_by(user_id=user_id, service=service).first()

    encrypted_key = encrypt_api_key(key)
    
    if existing:
        existing.api_key = encrypted_key
    else:
        new_key = ApiKey(user_id=user_id, service=service, api_key=encrypted_key)
        db.session.add(new_key)

    db.session.commit()
    return jsonify({"success": True})


@ai_image_bp.route("/ai-image/api-key", methods=["DELETE"])
@login_required
def delete_api_key():
    if not allow_user_api_keys():
        return jsonify({"error": "User API keys are disabled in platform mode."}), 403

    data = request.get_json() or {}
    service = data.get("service", "gemini")
    user_id = session["user_id"]
    ApiKey.query.filter_by(user_id=user_id, service=service).delete()
    db.session.commit()
    return jsonify({"success": True})


# -- Generate images (async) -------------------------------------------------
@ai_image_bp.route("/ai-image/generate", methods=["POST"])
@login_required
def generate_images():
    user = _get_user()
    refresh_cycle_if_needed(user)

    # Accept multipart/form-data for file uploads
    prompt_text = request.form.get("prompt", "").strip()
    variations = int(request.form.get("variations", 1))
    variations = max(1, min(3, variations))
    remix_enabled = request.form.get("remix", "false") == "true"
    remix_system_prompt = request.form.get(
        "remix_system_prompt", DEFAULT_REMIX_SYSTEM_PROMPT
    ).strip()
    requested_model = request.form.get("gemini_model", "").strip()
    use_batch_api = request.form.get("use_batch_api", "").lower() == "true"
    if request.form.get("use_batch_api") is None:
        use_batch_api = bool(user.batch_api_for_queued_jobs)
    traffic_type = "batch" if use_batch_api else "standard"

    if not prompt_text:
        return jsonify({"error": "Prompt is required."}), 400

    gemini_key = get_effective_api_key(user.id, "gemini")
    if not gemini_key:
        return jsonify({"error": "no_api_key"}), 400

    gemini_model = _resolve_gemini_image_model(user, requested_model)
    if not _is_likely_image_model(gemini_model):
        return jsonify({"error": "Invalid Gemini image model."}), 400

    credit_quote = quote_ai_image(variations, traffic_type=traffic_type)
    credit_error = ensure_credits_or_error(
        user, credit_quote["credits"], feature="ai_image"
    )
    if credit_error:
        status_code, payload = credit_error
        db.session.commit()
        return jsonify(payload), status_code

    # Check OpenAI key if remix is enabled
    openai_key = None
    if remix_enabled and variations > 1:
        openai_key = get_effective_api_key(user.id, "openai")
        if not openai_key:
            return jsonify({"error": "no_openai_key"}), 400

    # Collect uploaded images with strict limits.
    uploaded_images = []
    total_uploaded_bytes = 0
    for key in [f"image_{idx}" for idx in range(MAX_REFERENCE_IMAGES)]:
        file_obj = request.files.get(key)
        if file_obj and file_obj.filename:
            img_bytes = file_obj.read()
            if not img_bytes:
                continue
            mime = file_obj.content_type or mimetypes.guess_type(file_obj.filename)[0] or "image/png"
            if not is_allowed_upload_image(mime):
                return jsonify({"error": upload_image_type_error()}), 400
            if len(img_bytes) > MAX_REFERENCE_IMAGE_BYTES:
                return jsonify(
                    {
                        "error": (
                            f"Each reference image must be <= {MAX_REFERENCE_IMAGE_BYTES // (1024 * 1024)} MB."
                        )
                    }
                ), 413
            total_uploaded_bytes += len(img_bytes)
            if total_uploaded_bytes > MAX_REFERENCE_TOTAL_BYTES:
                return jsonify(
                    {
                        "error": (
                            f"Total reference upload must be <= {MAX_REFERENCE_TOTAL_BYTES // (1024 * 1024)} MB."
                        )
                    }
                ), 413
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            uploaded_images.append({"mime_type": mime, "data": img_b64})

    # Build per-variation prompts (remix happens synchronously before enqueue)
    variation_prompts = []
    for i in range(variations):
        if remix_enabled and openai_key and i > 0:
            try:
                remixed, remix_meta = _remix_prompt(
                    openai_key, prompt_text, remix_system_prompt, i
                )
                record_api_request_event(
                    user_id=user.id,
                    feature="ai_image",
                    provider="openai",
                    operation="prompt_remix",
                    meta=remix_meta,
                    metadata={"variation_index": i + 1},
                )
                variation_prompts.append(remixed)
            except Exception as remix_error:
                record_api_request_event(
                    user_id=user.id,
                    feature="ai_image",
                    provider="openai",
                    operation="prompt_remix",
                    status="failed",
                    error_message=str(remix_error),
                    metadata={"variation_index": i + 1},
                )
                variation_prompts.append(prompt_text)
        else:
            variation_prompts.append(prompt_text)

    # Save prompt
    prompt = ImagePrompt(user_id=user.id, prompt_text=prompt_text)
    db.session.add(prompt)
    db.session.flush()

    # Create queued generation records
    generations = []
    for i in range(variations):
        generation = ImageGeneration(
            prompt_id=prompt.id,
            variation_index=i + 1,
            status="queued",
        )
        db.session.add(generation)
        generations.append(generation)
    db.session.flush()

    job = enqueue_worker_job(
        user_id=user.id,
        job_type=JOB_TYPE_AI_IMAGE_GENERATE,
        queue_name="default",
        max_attempts=2,
        payload={
            "prompt_id": prompt.id,
            "prompt_text": prompt_text,
            "variation_prompts": variation_prompts,
            "generation_ids": [g.id for g in generations],
            "gemini_model": gemini_model,
            "traffic_type": traffic_type,
            "uploaded_images": uploaded_images,
            "credit_quote": {
                "credits": int(credit_quote["credits"]),
                "estimated_cost_usd": str(credit_quote["estimated_cost_usd"]),
                "units": int(credit_quote["units"]),
            },
        },
    )
    db.session.commit()

    return jsonify({
        "job_id": job.id,
        "prompt_id": prompt.id,
        "status": "queued",
        "status_url": f"/ai-image/jobs/{job.id}",
    }), 202


# -- Job status --------------------------------------------------------------
@ai_image_bp.route("/ai-image/jobs/<int:job_id>", methods=["GET"])
@login_required
def ai_image_job_status(job_id):
    job = get_worker_job_for_user(job_id, session["user_id"])
    if not job or job.job_type != JOB_TYPE_AI_IMAGE_GENERATE:
        return jsonify({"error": "Job not found."}), 404

    serialized = serialize_worker_job(job)
    payload = job_payload(job)
    prompt_id = payload.get("prompt_id")

    response = {
        "job_id": job.id,
        "status": job.status,
        "prompt_id": prompt_id,
        "error": serialized["error"],
    }

    if job.status == "completed":
        gens = ImageGeneration.query.filter_by(prompt_id=prompt_id).order_by(ImageGeneration.variation_index).all()
        response["results"] = [
            {
                "id": g.id,
                "variation": g.variation_index,
                "status": g.status,
                "image_url": url_for("media.ai_image_generation_image", generation_id=g.id) if g.status == "completed" else None,
                "error": g.error_message,
            }
            for g in gens
        ]

    return jsonify(response)


# -- Delete -----------------------------------------------------------------
@ai_image_bp.route("/ai-image/prompt/<int:prompt_id>", methods=["DELETE"])
@login_required
def delete_prompt(prompt_id):
    prompt = ImagePrompt.query.filter_by(
        id=prompt_id, user_id=session["user_id"]
    ).first()
    if not prompt:
        return jsonify({"error": "Prompt not found."}), 404

    for generation in prompt.generations:
        delete_storage_file(generation.storage_path)
    db.session.delete(prompt)
    db.session.commit()
    return jsonify({"success": True})


# -- Prompt remix ------------------------------------------------------------
def _remix_prompt(api_key, original_prompt, system_prompt, variation_num):
    """Use OpenAI to create a creative variation of an image prompt."""
    user_content = (
        f"Here is the original prompt:\n\n"
        f"{original_prompt}\n\n"
        f"Create variation #{variation_num + 1}. "
        f"Keep the same subject/concept but make it distinctly different."
    )
    return openai_chat_with_meta(
        api_key, system_prompt, user_content,
        max_tokens=300, temperature=0.9,
    )
