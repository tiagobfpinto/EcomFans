import base64
import json
import mimetypes

from flask import Blueprint, jsonify, render_template, request, session

from auth import login_required
from db import ApiKey, ImageGeneration, ImagePrompt, User, db
from ai_service import gemini_generate_image, openai_chat

ai_image_bp = Blueprint("ai_image", __name__)

DEFAULT_REMIX_SYSTEM_PROMPT = (
    "You are a creative prompt engineer. Given an image generation prompt, "
    "rewrite it with creative variations while keeping the same core concept "
    "and intent. Make meaningful changes to style, composition, lighting, "
    "colors, or perspective. Return ONLY the rewritten prompt, nothing else."
)


def _get_user():
    """Return the current logged-in User object."""
    return db.session.get(User, session["user_id"])


def _get_api_key(user_id, service="gemini"):
    """Return an API key for a user and service, or None."""
    key_row = ApiKey.query.filter_by(user_id=user_id, service=service).first()
    return key_row.api_key if key_row else None


# ── Page ────────────────────────────────────────────────────────────
@ai_image_bp.route("/ai-image")
@login_required
def ai_image_page():
    user = _get_user()
    gemini_key = _get_api_key(user.id, "gemini")
    openai_key = _get_api_key(user.id, "openai")
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
        credits=user.credits,
        prompts=prompts,
        default_remix_prompt=DEFAULT_REMIX_SYSTEM_PROMPT,
    )


# ── API Key management ─────────────────────────────────────────────
@ai_image_bp.route("/ai-image/api-key", methods=["POST"])
@login_required
def save_api_key():
    data = request.get_json() or {}
    key = data.get("api_key", "").strip()
    service = data.get("service", "gemini").strip()

    if service not in ("gemini", "openai"):
        return jsonify({"error": "Invalid service."}), 400
    if not key:
        return jsonify({"error": "API key is required."}), 400

    user_id = session["user_id"]
    existing = ApiKey.query.filter_by(user_id=user_id, service=service).first()

    if existing:
        existing.api_key = key
    else:
        new_key = ApiKey(user_id=user_id, service=service, api_key=key)
        db.session.add(new_key)

    db.session.commit()
    return jsonify({"success": True})


@ai_image_bp.route("/ai-image/api-key", methods=["DELETE"])
@login_required
def delete_api_key():
    data = request.get_json() or {}
    service = data.get("service", "gemini")
    user_id = session["user_id"]
    ApiKey.query.filter_by(user_id=user_id, service=service).delete()
    db.session.commit()
    return jsonify({"success": True})


# ── Generate images ────────────────────────────────────────────────
@ai_image_bp.route("/ai-image/generate", methods=["POST"])
@login_required
def generate_images():
    user = _get_user()

    # Accept multipart/form-data for file uploads
    prompt_text = request.form.get("prompt", "").strip()
    variations = int(request.form.get("variations", 1))
    variations = max(1, min(3, variations))
    remix_enabled = request.form.get("remix", "false") == "true"
    remix_system_prompt = request.form.get(
        "remix_system_prompt", DEFAULT_REMIX_SYSTEM_PROMPT
    ).strip()

    if not prompt_text:
        return jsonify({"error": "Prompt is required."}), 400

    gemini_key = _get_api_key(user.id, "gemini")
    if not gemini_key:
        return jsonify({"error": "no_api_key"}), 400

    if user.credits < variations:
        return jsonify({
            "error": f"Not enough credits. You need {variations} but have {user.credits}."
        }), 400

    # Check OpenAI key if remix is enabled
    openai_key = None
    if remix_enabled and variations > 1:
        openai_key = _get_api_key(user.id, "openai")
        if not openai_key:
            return jsonify({"error": "no_openai_key"}), 400

    # Collect uploaded images (up to 3)
    uploaded_images = []
    for key in ["image_0", "image_1", "image_2"]:
        f = request.files.get(key)
        if f and f.filename:
            img_bytes = f.read()
            mime = f.content_type or mimetypes.guess_type(f.filename)[0] or "image/png"
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            uploaded_images.append({"mime_type": mime, "data": img_b64})

    # Build per-variation prompts
    variation_prompts = []
    for i in range(variations):
        if remix_enabled and openai_key and i > 0:
            try:
                remixed = _remix_prompt(
                    openai_key, prompt_text, remix_system_prompt, i
                )
                variation_prompts.append(remixed)
            except Exception:
                variation_prompts.append(prompt_text)
        else:
            variation_prompts.append(prompt_text)

    # Save prompt
    prompt = ImagePrompt(user_id=user.id, prompt_text=prompt_text)
    db.session.add(prompt)
    db.session.flush()

    results = []

    for i in range(variations):
        generation = ImageGeneration(
            prompt_id=prompt.id,
            variation_index=i + 1,
            status="pending",
        )
        db.session.add(generation)
        db.session.flush()

        try:
            image_b64 = gemini_generate_image(
                gemini_key, variation_prompts[i], uploaded_images
            )
            generation.image_data = image_b64
            generation.status = "completed"
            user.credits -= 1
            results.append({
                "id": generation.id,
                "variation": i + 1,
                "prompt_used": variation_prompts[i],
                "image_data": image_b64,
                "status": "completed",
            })
        except Exception as e:
            generation.status = "failed"
            generation.error_message = str(e)
            results.append({
                "id": generation.id,
                "variation": i + 1,
                "prompt_used": variation_prompts[i],
                "image_data": None,
                "status": "failed",
                "error": str(e),
            })

    db.session.commit()

    return jsonify({
        "prompt_id": prompt.id,
        "prompt_text": prompt_text,
        "results": results,
        "credits_remaining": user.credits,
    })


# ── Delete a prompt ────────────────────────────────────────────────
@ai_image_bp.route("/ai-image/prompt/<int:prompt_id>", methods=["DELETE"])
@login_required
def delete_prompt(prompt_id):
    prompt = ImagePrompt.query.filter_by(
        id=prompt_id, user_id=session["user_id"]
    ).first()
    if not prompt:
        return jsonify({"error": "Prompt not found."}), 404

    db.session.delete(prompt)
    db.session.commit()
    return jsonify({"success": True})


# ── Prompt remix (delegates to ai_service) ─────────────────────────
def _remix_prompt(api_key, original_prompt, system_prompt, variation_num):
    """Use OpenAI to create a creative variation of an image prompt."""
    user_content = (
        f"Here is the original prompt:\n\n"
        f"{original_prompt}\n\n"
        f"Create variation #{variation_num + 1}. "
        f"Keep the same subject/concept but make it distinctly different."
    )
    return openai_chat(
        api_key, system_prompt, user_content,
        max_tokens=300, temperature=0.9,
    )
