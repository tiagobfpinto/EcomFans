import json

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from auth import login_required
from billing_config import CREDIT_COSTS, ESTIMATED_USAGE_COST_USD
from billing_service import (
    build_plan_required_payload,
    consume_credits,
    ensure_credits_or_error,
    get_available_credits,
    get_billing_summary,
    get_effective_api_key,
    has_required_plan,
    quote_avatars,
    refresh_cycle_if_needed,
)
from db import AvatarBatch, AvatarResult, Product, User, db
from ai_service import gemini_generate_image
from media_storage import get_image_payload

avatars_bp = Blueprint("avatars", __name__)

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
    )


# ── Generate avatars ───────────────────────────────────────────────
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
    count_per_persona = int(data.get("count_per_persona", 1))
    count_per_persona = max(1, min(3, count_per_persona))

    if not product_id:
        return jsonify({"error": "Please select a product."}), 400
    if not characteristic:
        return jsonify({"error": "Physical characteristic is required."}), 400
    if not personas or len(personas) == 0:
        return jsonify({"error": "Select at least one persona."}), 400

    # Validate product belongs to user
    product = Product.query.filter_by(id=product_id, user_id=user.id).first()
    if not product:
        return jsonify({"error": "Product not found."}), 404

    gemini_key = get_effective_api_key(user.id, "gemini")
    if not gemini_key:
        return jsonify({"error": "no_api_key"}), 400

    total_pairs = len(personas) * count_per_persona
    credit_quote = quote_avatars(total_pairs)
    credit_error = ensure_credits_or_error(user, credit_quote["credits"], feature="avatars")
    if credit_error:
        status_code, payload = credit_error
        db.session.commit()
        return jsonify(payload), status_code

    # Prepare product images for context
    product_images = []
    for img in product.images[:3]:  # max 3 product images as context
        payload = get_image_payload(img.storage_path, img.mime_type, img.image_data)
        if payload:
            product_images.append(payload)

    # Create batch
    batch = AvatarBatch(
        user_id=user.id,
        product_id=product.id,
        characteristic=characteristic,
        personas=json.dumps(personas),
        count_per_persona=count_per_persona,
        status="processing",
    )
    db.session.add(batch)
    db.session.flush()

    results = []
    latest_user = user

    for persona in personas:
        for i in range(count_per_persona):
            result = AvatarResult(
                batch_id=batch.id,
                persona=persona,
                status="pending",
            )
            db.session.add(result)
            db.session.flush()

            try:
                # Generate BEFORE image
                before_prompt = (
                    f"A realistic full body photo portrait of a {persona} "
                    f"who is {characteristic}. Standing pose, neutral "
                    f"background, casual everyday clothing. Natural lighting, "
                    f"photorealistic style. No text in image."
                )
                before_b64 = gemini_generate_image(gemini_key, before_prompt)

                # Generate AFTER image – feed the before image + product images
                # so Gemini keeps the same person identity
                after_context = [
                    {"mime_type": "image/png", "data": before_b64},
                ] + product_images

                after_prompt = (
                    f"This is a photo of a {persona}. Generate a new "
                    f"realistic photo of this EXACT SAME person from the "
                    f"first reference photo, but now they look fit, confident, "
                    f"and transformed after using the product "
                    f"'{product.name}'. {product.context}. "
                    f"The other reference photos are of the product itself. "
                    f"Please feature this exact product naturally in the new photo. "
                    f"Same person, same face, same identity. "
                    f"Bright, positive lighting, photorealistic style. "
                    f"No text in image."
                )
                after_b64 = gemini_generate_image(
                    gemini_key, after_prompt, after_context
                )

                charged, charged_user, payload = consume_credits(
                    user.id,
                    CREDIT_COSTS["avatars_pair"],
                    feature="avatars",
                    provider="gemini",
                    units=1,
                    estimated_cost_usd=ESTIMATED_USAGE_COST_USD["avatars_pair"],
                    metadata={"persona": persona, "product_id": product.id},
                )
                if not charged:
                    result.status = "failed"
                    result.error_message = payload["error"]
                    results.append({
                        "id": result.id,
                        "persona": persona,
                        "before_image": None,
                        "after_image": None,
                        "status": "failed",
                        "error": payload["error"],
                        "reason": payload.get("reason"),
                        "redirect_url": payload.get("redirect_url"),
                    })
                    continue

                latest_user = charged_user
                result.before_image = before_b64
                result.after_image = after_b64
                result.status = "completed"

                results.append({
                    "id": result.id,
                    "persona": persona,
                    "before_image": before_b64,
                    "after_image": after_b64,
                    "status": "completed",
                })
            except Exception as e:
                result.status = "failed"
                result.error_message = str(e)
                results.append({
                    "id": result.id,
                    "persona": persona,
                    "before_image": None,
                    "after_image": None,
                    "status": "failed",
                    "error": str(e),
                })

    batch.status = "completed"
    db.session.commit()
    billing_summary = get_billing_summary(latest_user)

    return jsonify({
        "batch_id": batch.id,
        "results": results,
        "credits_remaining": get_available_credits(latest_user),
        "monthly_credits": billing_summary["monthly_credits"],
        "extra_credits": billing_summary["extra_credits"],
    })


# ── Delete batch ───────────────────────────────────────────────────
@avatars_bp.route("/avatars/batch/<int:batch_id>", methods=["DELETE"])
@login_required
def delete_batch(batch_id):
    batch = AvatarBatch.query.filter_by(
        id=batch_id, user_id=session["user_id"]
    ).first()
    if not batch:
        return jsonify({"error": "Batch not found."}), 404

    db.session.delete(batch)
    db.session.commit()
    return jsonify({"success": True})


# ── Gemini API call ────────────────────────────────────────────────
