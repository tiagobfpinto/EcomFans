import base64
import io

from flask import Blueprint, abort, send_file, session

from auth import login_required
from db import (
    AvatarBatch,
    AvatarResult,
    Competitor,
    CompetitorAd,
    CreativeBatch,
    CreativeInspiration,
    CreativeResult,
    ImageGeneration,
    ImagePrompt,
    PromptLibraryItem,
    PromptLibraryTarget,
    PromptLibraryTargetImage,
    PromptLibraryThumbnail,
    Product,
    ProductImage,
    SavedScript,
    StoryboardFrame,
    StoryboardProject,
)
from media_storage import read_storage_bytes, resolve_storage_path
from security import harden_media_response, safe_media_mime


media_bp = Blueprint("media", __name__)


def _send_image_payload(
    *,
    storage_path: str | None,
    fallback_b64: str | None,
    mime_type: str,
    download_name: str,
):
    # mime_type comes from whatever the uploader declared, so it is never
    # trusted for inline rendering. Anything outside the raster allowlist (an
    # SVG, say) is downgraded to an opaque download so the browser cannot parse
    # it as a script-bearing document on our own origin.
    safe_mime, inline = safe_media_mime(mime_type)

    payload = read_storage_bytes(storage_path)
    if payload is None and fallback_b64:
        try:
            payload = base64.b64decode(fallback_b64, validate=False)
        except Exception:
            payload = None

    if not payload:
        abort(404)

    response = send_file(
        io.BytesIO(payload),
        mimetype=safe_mime,
        as_attachment=not inline,
        download_name=download_name,
    )
    return harden_media_response(response, inline=inline, download_name=download_name)


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


@media_bp.route("/media/prompt-thumbnails/<int:thumbnail_id>")
@login_required
def prompt_thumbnail(thumbnail_id: int):
    thumbnail = (
        PromptLibraryThumbnail.query
        .join(PromptLibraryItem, PromptLibraryItem.id == PromptLibraryThumbnail.prompt_id)
        .filter(
            PromptLibraryThumbnail.id == thumbnail_id,
            PromptLibraryItem.user_id == session["user_id"],
        )
        .first()
    )
    if not thumbnail:
        abort(404)

    return _send_image_payload(
        storage_path=thumbnail.storage_path,
        fallback_b64=None,
        mime_type=thumbnail.mime_type,
        download_name=thumbnail.filename or f"prompt_thumbnail_{thumbnail.id}",
    )


@media_bp.route("/media/prompt-target-images/<int:image_id>")
@login_required
def prompt_target_image(image_id: int):
    image = (
        PromptLibraryTargetImage.query
        .join(PromptLibraryTarget, PromptLibraryTarget.id == PromptLibraryTargetImage.target_id)
        .filter(
            PromptLibraryTargetImage.id == image_id,
            PromptLibraryTarget.user_id == session["user_id"],
        )
        .first()
    )
    if not image:
        abort(404)

    return _send_image_payload(
        storage_path=image.storage_path,
        fallback_b64=None,
        mime_type=image.mime_type,
        download_name=image.filename or f"prompt_target_image_{image.id}",
    )


@media_bp.route("/media/storyboard-thumbnails/<int:frame_id>")
@login_required
def storyboard_thumbnail(frame_id: int):
    frame = (
        StoryboardFrame.query
        .join(StoryboardProject, StoryboardProject.id == StoryboardFrame.project_id)
        .filter(
            StoryboardFrame.id == frame_id,
            StoryboardProject.user_id == session["user_id"],
        )
        .first()
    )
    if not frame or not frame.thumbnail_storage_path:
        abort(404)

    return _send_image_payload(
        storage_path=frame.thumbnail_storage_path,
        fallback_b64=None,
        mime_type=frame.thumbnail_mime_type or "image/webp",
        download_name=frame.thumbnail_filename or f"storyboard_{frame.id}",
    )


@media_bp.route("/media/script-thumbnails/<int:script_id>")
@login_required
def script_thumbnail(script_id: int):
    script = SavedScript.query.filter_by(
        id=script_id,
        user_id=session["user_id"],
    ).first()
    if not script or not script.thumbnail_storage_path:
        abort(404)

    return _send_image_payload(
        storage_path=script.thumbnail_storage_path,
        fallback_b64=None,
        mime_type=script.thumbnail_mime_type or "image/jpeg",
        download_name=f"script_thumbnail_{script.id}",
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


@media_bp.route("/media/competitor-ads/<int:ad_id>")
@login_required
def competitor_ad_video(ad_id: int):
    ad = (
        CompetitorAd.query
        .join(Competitor, Competitor.id == CompetitorAd.competitor_id)
        .filter(
            CompetitorAd.id == ad_id,
            Competitor.user_id == session["user_id"],
        )
        .first()
    )
    if not ad or not ad.storage_path:
        abort(404)

    try:
        file_path = resolve_storage_path(ad.storage_path)
    except ValueError:
        abort(404)
    if not file_path.is_file():
        abort(404)

    download_name = ad.original_filename or f"competitor_ad_{ad.id}.mp4"
    safe_mime, inline = safe_media_mime(ad.mime_type or "video/mp4", allow_video=True)
    response = send_file(
        file_path,
        mimetype=safe_mime,
        as_attachment=not inline,
        download_name=download_name,
        conditional=True,
    )
    return harden_media_response(response, inline=inline, download_name=download_name)


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
