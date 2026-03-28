import base64
import io

from flask import Blueprint, abort, send_file, session

from auth import login_required
from db import (
    AvatarBatch,
    AvatarResult,
    CreativeBatch,
    CreativeInspiration,
    CreativeResult,
    ImageGeneration,
    ImagePrompt,
    Product,
    ProductImage,
)
from media_storage import read_storage_bytes


media_bp = Blueprint("media", __name__)


def _send_image_payload(
    *,
    storage_path: str | None,
    fallback_b64: str | None,
    mime_type: str,
    download_name: str,
):
    storage_bytes = read_storage_bytes(storage_path)
    if storage_bytes is not None:
        return send_file(
            io.BytesIO(storage_bytes),
            mimetype=mime_type,
            as_attachment=False,
            download_name=download_name,
        )

    if fallback_b64:
        try:
            decoded = base64.b64decode(fallback_b64, validate=False)
        except Exception:
            decoded = b""
        if decoded:
            return send_file(
                io.BytesIO(decoded),
                mimetype=mime_type,
                as_attachment=False,
                download_name=download_name,
            )

    abort(404)


@media_bp.route("/media/product-images/<int:image_id>")
@login_required
def product_image(image_id: int):
    image = (
        ProductImage.query
        .join(Product, Product.id == ProductImage.product_id)
        .filter(
            ProductImage.id == image_id,
            Product.user_id == session["user_id"],
        )
        .first()
    )
    if not image:
        abort(404)

    return _send_image_payload(
        storage_path=image.storage_path,
        fallback_b64=image.image_data,
        mime_type=image.mime_type,
        download_name=image.filename or f"product_image_{image.id}",
    )


@media_bp.route("/media/inspirations/<int:inspiration_id>")
@login_required
def inspiration_image(inspiration_id: int):
    inspiration = CreativeInspiration.query.filter_by(
        id=inspiration_id,
        user_id=session["user_id"],
    ).first()
    if not inspiration:
        abort(404)

    return _send_image_payload(
        storage_path=inspiration.storage_path,
        fallback_b64=inspiration.image_data,
        mime_type=inspiration.mime_type,
        download_name=inspiration.name or f"inspiration_{inspiration.id}",
    )


@media_bp.route("/media/ai-image-generations/<int:generation_id>")
@login_required
def ai_image_generation_image(generation_id: int):
    generation = (
        ImageGeneration.query
        .join(ImagePrompt, ImagePrompt.id == ImageGeneration.prompt_id)
        .filter(
            ImageGeneration.id == generation_id,
            ImagePrompt.user_id == session["user_id"],
        )
        .first()
    )
    if not generation:
        abort(404)
    return _send_image_payload(
        storage_path=generation.storage_path,
        fallback_b64=generation.image_data,
        mime_type="image/png",
        download_name=f"ai_image_generation_{generation.id}.png",
    )


@media_bp.route("/media/avatar-results/<int:result_id>/before")
@login_required
def avatar_result_before_image(result_id: int):
    result = (
        AvatarResult.query
        .join(AvatarBatch, AvatarBatch.id == AvatarResult.batch_id)
        .filter(
            AvatarResult.id == result_id,
            AvatarBatch.user_id == session["user_id"],
        )
        .first()
    )
    if not result:
        abort(404)
    return _send_image_payload(
        storage_path=result.before_storage_path,
        fallback_b64=result.before_image,
        mime_type="image/png",
        download_name=f"avatar_before_{result.id}.png",
    )


@media_bp.route("/media/avatar-results/<int:result_id>/after")
@login_required
def avatar_result_after_image(result_id: int):
    result = (
        AvatarResult.query
        .join(AvatarBatch, AvatarBatch.id == AvatarResult.batch_id)
        .filter(
            AvatarResult.id == result_id,
            AvatarBatch.user_id == session["user_id"],
        )
        .first()
    )
    if not result:
        abort(404)
    return _send_image_payload(
        storage_path=result.after_storage_path,
        fallback_b64=result.after_image,
        mime_type="image/png",
        download_name=f"avatar_after_{result.id}.png",
    )


@media_bp.route("/media/creative-results/<int:result_id>/generated")
@login_required
def creative_result_generated_image(result_id: int):
    result = (
        CreativeResult.query
        .join(CreativeBatch, CreativeBatch.id == CreativeResult.batch_id)
        .filter(
            CreativeResult.id == result_id,
            CreativeBatch.user_id == session["user_id"],
        )
        .first()
    )
    if not result:
        abort(404)
    return _send_image_payload(
        storage_path=result.generated_storage_path,
        fallback_b64=result.generated_image,
        mime_type="image/png",
        download_name=f"creative_generated_{result.id}.png",
    )
