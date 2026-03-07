import base64

from flask import Blueprint, jsonify, render_template, request, session, url_for

from auth import login_required
from billing_service import (
    consume_credits,
    ensure_credits_or_error,
    get_available_credits,
    get_billing_summary,
    get_effective_api_key,
    quote_ai_creatives,
    refresh_cycle_if_needed,
)
from db import (
    CreativeBatch, CreativeInspiration, CreativeResult,
    Product, User, db,
)
from ai_service import (
    gemini_generate_image,
    gemini_vision,
    openai_generate_image,
    openai_chat,
)
from media_storage import delete_storage_file, get_image_payload, save_inspiration_image

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

    saved = []
    written_paths = []
    for img in images:
        mime = img.get("mime_type", "image/jpeg")
        b64 = img.get("data", "")
        name = img.get("name", "")
        if not b64:
            continue
        if not mime.startswith("image/"):
            return jsonify({"error": "Only image inspirations are supported."}), 400
        try:
            image_bytes = base64.b64decode(b64, validate=True)
        except Exception:
            return jsonify({"error": "Invalid base64 image payload."}), 400
        if not image_bytes:
            continue

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


# ── Generate batch ────────────────────────────────────────────────────
@ai_creatives_bp.route("/ai-creatives/generate", methods=["POST"])
@login_required
def generate_creatives():
    user = _get_user()
    refresh_cycle_if_needed(user)
    data = request.get_json() or {}

    product_id = data.get("product_id")
    inspiration_ids = data.get("inspiration_ids", [])
    provider = data.get("provider", "gemini")  # "gemini" | "openai" | "both"
    base_prompt = (data.get("base_prompt") or DEFAULT_BASE_PROMPT).strip()
    analysis_prompt = (data.get("analysis_prompt") or DEFAULT_ANALYSIS_PROMPT).strip()
    product_info_source = (data.get("product_info_source") or "product_page").strip()
    prompt_only = data.get("prompt_only", False)

    if provider not in ("gemini", "openai", "both"):
        return jsonify({"error": "Invalid provider."}), 400
    if not product_id:
        return jsonify({"error": "Product is required."}), 400
    if not inspiration_ids:
        return jsonify({"error": "Select at least one inspiration."}), 400
    if not base_prompt:
        return jsonify({"error": "Base prompt is required."}), 400
    if not analysis_prompt:
        return jsonify({"error": "Analysis prompt is required."}), 400
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

    credit_quote = quote_ai_creatives(len(inspirations), provider, bool(prompt_only))
    credit_error = ensure_credits_or_error(
        user, credit_quote["credits"], feature="ai_creatives"
    )
    if credit_error:
        status_code, payload = credit_error
        db.session.commit()
        return jsonify(payload), status_code

    # All product images — sent alongside inspiration image in every generation call
    product_images = []
    for img in product.images:
        payload = get_image_payload(img.storage_path, img.mime_type, img.image_data)
        if payload:
            product_images.append(payload)

    batch = CreativeBatch(
        user_id=user.id,
        product_id=product.id,
        status="pending",
    )
    db.session.add(batch)
    db.session.flush()

    results = []
    latest_user = user
    per_inspiration_credits = credit_quote["per_inspiration_credits"]
    per_inspiration_cost = (
        credit_quote["estimated_cost_usd"] / credit_quote["units"]
        if credit_quote["units"]
        else 0
    )
    for insp in inspirations:
        result = CreativeResult(
            batch_id=batch.id,
            inspiration_id=insp.id,
            status="pending",
        )
        db.session.add(result)
        db.session.flush()

        try:
            insp_payload = get_image_payload(
                insp.storage_path,
                insp.mime_type,
                insp.image_data,
            )
            if not insp_payload:
                raise RuntimeError("Inspiration image data is missing.")
            # ── Step 1: Analyze inspiration image → regen_prompt ──────────────
            # Only the analysis_prompt + inspiration image are sent here.
            # We now inject the product info (name + context + requested additional info)
            # into the analysis step so that the generated prompt is already tailored
            # to the specific product rather than appending it at the end.
            product_info = f"Product Name: {product.name}\nProduct Context: {product.context}"
            product_page_info = product.build_product_info()
            if product_page_info:
                product_info += f"\n{product_page_info}"

            full_analysis_prompt = (
                f"{analysis_prompt}\n\n"
                f"Make sure the prompt you generate is specifically for the following product:\n"
                f"{product_info}"
            )

            # Provider choice determines which vision model is used:
            # - gemini-only → Gemini vision (no OpenAI key needed)
            # - openai or both → GPT-4o-mini vision
            if provider == "gemini":
                regen_prompt = _analyze_with_gemini(
                    gemini_key,
                    full_analysis_prompt,
                    insp_payload["data"],
                    insp_payload["mime_type"],
                )
            else:
                regen_prompt = _analyze_with_openai(
                    openai_key,
                    full_analysis_prompt,
                    insp_payload["data"],
                    insp_payload["mime_type"],
                )

            # ── Step 2: Build final generation prompt ─────────────────────────
            # final_prompt = base_prompt + regen_prompt
            final_prompt = base_prompt + "\n\n" + regen_prompt

            # Store the full final prompt in the DB
            result.generated_prompt = final_prompt

            # ── Step 3: Generate image(s) ─────────────────────────────────────
            # Images sent to generation: [inspiration image] + [all product images]
            # The inspiration image is the visual target to recreate.
            # Product images are the visual reference for what should appear in the output.
            generation_images = [
                {
                    "mime_type": insp_payload["mime_type"],
                    "data": insp_payload["data"],
                },
                *product_images,
            ]

            openai_image = None
            gemini_image = None

            if not prompt_only:
                if provider in ("openai", "both"):
                    # OpenAI: text prompt only (gpt-image-1 standard endpoint)
                    openai_image = openai_generate_image(openai_key, final_prompt)

                if provider in ("gemini", "both"):
                    # Gemini: full prompt + inspiration image + all product images as visual context
                    gemini_image = gemini_generate_image(gemini_key, final_prompt, generation_images)

            charged, charged_user, payload = consume_credits(
                user.id,
                per_inspiration_credits,
                feature="ai_creatives",
                provider=provider,
                units=1,
                estimated_cost_usd=per_inspiration_cost,
                metadata={
                    "inspiration_id": insp.id,
                    "prompt_only": bool(prompt_only),
                    "product_id": product.id,
                },
            )
            if not charged:
                result.status = "failed"
                result.error_message = payload["error"]
                results.append({
                    "id": result.id,
                    "inspiration_id": insp.id,
                    "inspiration_url": url_for("media.inspiration_image", inspiration_id=insp.id),
                    "provider": provider,
                    "analysis_prompt": analysis_prompt,
                    "regen_prompt": regen_prompt,
                    "base_prompt": base_prompt,
                    "product_info": product.build_product_info(),
                    "final_prompt": result.generated_prompt,
                    "product_image_count": len(product_images),
                    "openai_image": None,
                    "gemini_image": None,
                    "generated_image": None,
                    "status": "failed",
                    "error": payload["error"],
                    "reason": payload.get("reason"),
                    "redirect_url": payload.get("redirect_url"),
                })
                continue

            latest_user = charged_user

            result.generated_image = gemini_image or openai_image
            result.status = "completed"

            results.append({
                "id": result.id,
                "inspiration_id": insp.id,
                "inspiration_url": url_for("media.inspiration_image", inspiration_id=insp.id),
                "provider": provider,
                # Full prompt breakdown for transparency
                "analysis_prompt": analysis_prompt,
                "regen_prompt": regen_prompt,
                "base_prompt": base_prompt,
                "product_info": product_page_info,
                "final_prompt": final_prompt,
                "product_image_count": len(product_images),
                # Generated images
                "openai_image": openai_image,
                "gemini_image": gemini_image,
                "generated_image": gemini_image or openai_image,
                "status": "completed",
            })

        except Exception as e:
            result.status = "failed"
            result.error_message = str(e)
            results.append({
                "id": result.id,
                "inspiration_id": insp.id,
                "inspiration_url": url_for("media.inspiration_image", inspiration_id=insp.id),
                "provider": provider,
                "analysis_prompt": analysis_prompt,
                "regen_prompt": None,
                "base_prompt": base_prompt,
                "product_info": product.build_product_info(),
                "final_prompt": result.generated_prompt,
                "product_image_count": len(product_images),
                "openai_image": None,
                "gemini_image": None,
                "generated_image": None,
                "status": "failed",
                "error": str(e),
            })

    batch.status = "completed"
    db.session.commit()
    billing_summary = get_billing_summary(latest_user)

    return jsonify({
        "batch_id": batch.id,
        "batch_created_at": batch.created_at.strftime("%b %d, %Y at %H:%M"),
        "product_name": product.name,
        "provider": provider,
        "results": results,
        "credits_remaining": get_available_credits(latest_user),
        "monthly_credits": billing_summary["monthly_credits"],
        "extra_credits": billing_summary["extra_credits"],
    })


# ── Delete batch ──────────────────────────────────────────────────────
@ai_creatives_bp.route("/ai-creatives/batch/<int:batch_id>", methods=["DELETE"])
@login_required
def delete_batch(batch_id):
    batch = CreativeBatch.query.filter_by(
        id=batch_id, user_id=session["user_id"]
    ).first()
    if not batch:
        return jsonify({"error": "Not found."}), 404
    db.session.delete(batch)
    db.session.commit()
    return jsonify({"success": True})


# ── Analysis helpers ──────────────────────────────────────────────────

def _analyze_with_openai(api_key, analysis_prompt, insp_b64, insp_mime):
    """Send analysis_prompt + inspiration image to GPT-4o-mini, return regen_prompt text."""
    user_content = [
        {"type": "text", "text": analysis_prompt},
        {"type": "image_url", "image_url": {"url": f"data:{insp_mime};base64,{insp_b64}"}},
    ]
    return openai_chat(
        api_key,
        _ANALYSIS_SYSTEM_PROMPT,
        user_content,
        max_tokens=1200,
        temperature=0.7,
    )


def _analyze_with_gemini(api_key, analysis_prompt, insp_b64, insp_mime):
    """Send analysis_prompt + inspiration image to Gemini vision, return regen_prompt text."""
    parts = [
        {"text": analysis_prompt},
        {"inlineData": {"mimeType": insp_mime, "data": insp_b64}},
    ]
    return gemini_vision(
        api_key,
        _ANALYSIS_SYSTEM_PROMPT,
        parts,
        max_tokens=1200,
        temperature=0.7,
    )
