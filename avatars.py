import json

from flask import Blueprint, jsonify, render_template, request, session

from auth import login_required
from db import ApiKey, AvatarBatch, AvatarResult, Product, User, db
from ai_service import gemini_generate_image

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


def _get_api_key(user_id, service="gemini"):
    key_row = ApiKey.query.filter_by(user_id=user_id, service=service).first()
    return key_row.api_key if key_row else None


# ── Page ────────────────────────────────────────────────────────────
@avatars_bp.route("/avatars")
@login_required
def avatars_page():
    user = _get_user()
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
        has_api_key=bool(_get_api_key(user.id, "gemini")),
        credits=user.credits,
        products=products,
        batches=batches,
        preset_personas=PRESET_PERSONAS,
    )


# ── Generate avatars ───────────────────────────────────────────────
@avatars_bp.route("/avatars/generate", methods=["POST"])
@login_required
def generate_avatars():
    user = _get_user()
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

    gemini_key = _get_api_key(user.id, "gemini")
    if not gemini_key:
        return jsonify({"error": "no_api_key"}), 400

    total_pairs = len(personas) * count_per_persona
    if user.credits < total_pairs:
        return jsonify({
            "error": (
                f"Not enough credits. You need {total_pairs} "
                f"but have {user.credits}."
            )
        }), 400

    # Prepare product images for context
    product_images = []
    for img in product.images[:3]:  # max 3 product images as context
        product_images.append({
            "mime_type": img.mime_type,
            "data": img.image_data,
        })

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

                result.before_image = before_b64
                result.after_image = after_b64
                result.status = "completed"
                user.credits -= 1

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

    return jsonify({
        "batch_id": batch.id,
        "results": results,
        "credits_remaining": user.credits,
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
