import io
import json
import mimetypes
import os
from urllib.parse import urlparse

import requests
from flask import Blueprint, abort, jsonify, render_template, request, send_file, session, url_for

from ai_service import GEMINI_IMAGE_MODEL, normalize_model_name
from auth import login_required
from billing_service import (
    ensure_credits_or_error,
    get_billing_summary,
    get_effective_api_key,
    quote_ai_creatives,
    refresh_cycle_if_needed,
)
from db import CreativeInspiration, User, db
from media_storage import delete_storage_file, read_storage_bytes, save_inspiration_image
from scraper import _ensure_public_url, _safe_get_bytes, extract_images
from worker_queue import enqueue_worker_job, get_worker_job_for_user, job_payload
from worker_tasks import JOB_TYPE_LACY_GENERATE
from security import is_allowed_upload_image

product_images_bp = Blueprint("product_images", __name__)

MAX_LACY_SOURCE_IMAGES = 10
MAX_LACY_IMAGE_BYTES = 8 * 1024 * 1024
MAX_LACY_TOTAL_BYTES = 40 * 1024 * 1024

LACY_BASE_PROMPT = (
    "Generate a clean, high-quality ecommerce product image for the selected product. "
    "Use the competitor image only as layout and mockup inspiration. Replace every "
    "visible competitor product, package, screen mockup, logo, label, and branded "
    "element with the selected product from the product reference images. Preserve "
    "the useful camera angle, composition, lighting, background, prop style, and "
    "mockup placement. Do not copy competitor branding, text, watermarks, or logos. "
    "Keep the selected product accurate, prominent, and commercially polished."
)

LACY_ANALYSIS_PROMPT = (
    "Analyze this competitor product/mockup image. Identify the composition, product "
    "placement, camera angle, lighting, background, props, styling, and mockup layout. "
    "Then write a concrete image generation prompt to recreate this type of product "
    "image for a different product. The prompt must instruct the image model to "
    "replace all competitor products, labels, logos, screens, and mockups with the "
    "user's selected product, while preserving the useful visual structure. Output "
    "only the image generation prompt."
)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

ALLOWED_LACY_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}


def _get_user():
    return db.session.get(User, session["user_id"])


def _coerce_limit(raw_limit) -> int:
    try:
        limit = int(raw_limit)
    except Exception:
        limit = MAX_LACY_SOURCE_IMAGES
    return max(1, min(MAX_LACY_SOURCE_IMAGES, limit))


def _normalize_competitor_url(raw_url: str | None) -> str:
    url = (raw_url or "").strip()
    if not url:
        raise ValueError("Competitor URL is required.")

    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    elif parsed.scheme not in ("http", "https"):
        raise ValueError("Invalid URL scheme. Use http or https.")

    return _ensure_public_url(url)


def _normalize_image_mime(content_type: str | None, source_url: str) -> str:
    mime_type = (content_type or "").split(";")[0].strip().lower()
    if mime_type == "image/jpg":
        mime_type = "image/jpeg"
    if not is_allowed_upload_image(mime_type):
        # Falling back to the URL's extension must not reintroduce a type we
        # just rejected (a ".svg" path would guess right back to image/svg+xml).
        guessed = mimetypes.guess_type(urlparse(source_url).path)[0] or ""
        mime_type = guessed if is_allowed_upload_image(guessed) else "image/jpeg"
    return mime_type


def _source_filename(source_url: str, index: int, mime_type: str) -> str:
    filename = os.path.basename(urlparse(source_url).path).strip()
    if not filename or "." not in filename:
        extension = mimetypes.guess_extension(mime_type) or ".jpg"
        filename = f"lacy_source_{index}{extension}"
    return filename[:255]


def _download_remote_image(source_url: str) -> dict:
    _ensure_public_url(source_url)
    image_bytes, content_type, final_url = _safe_get_bytes(
        source_url,
        headers=BROWSER_HEADERS,
        timeout=12,
        max_bytes=MAX_LACY_IMAGE_BYTES,
        expected_prefix="image/",
    )
    mime_type = _normalize_image_mime(content_type, final_url)
    if mime_type not in ALLOWED_LACY_IMAGE_MIME_TYPES:
        raise ValueError(f"Unsupported image type: {mime_type}")
    return {
        "url": final_url,
        "mime_type": mime_type,
        "image_bytes": image_bytes,
    }


def _prepare_remote_images(source_urls: list[str], limit: int) -> tuple[list[dict], list[dict]]:
    prepared = []
    skipped = []
    total_bytes = 0
    seen = set()

    for raw_url in source_urls:
        if len(prepared) >= limit:
            break
        source_url = str(raw_url or "").strip()
        if not source_url or source_url in seen:
            continue
        seen.add(source_url)

        try:
            image = _download_remote_image(source_url)
        except Exception as exc:
            skipped.append({"url": source_url, "error": str(exc)})
            continue

        next_total = total_bytes + len(image["image_bytes"])
        if next_total > MAX_LACY_TOTAL_BYTES:
            skipped.append({
                "url": source_url,
                "error": (
                    f"Total imported source images must be <= "
                    f"{MAX_LACY_TOTAL_BYTES // (1024 * 1024)} MB."
                ),
            })
            continue

        total_bytes = next_total
        prepared.append(image)

    return prepared, skipped


def _save_inspiration_images(user_id: int, images: list[dict]) -> list[dict]:
    saved = []
    written_paths = []

    try:
        for index, image in enumerate(images, start=1):
            insp = CreativeInspiration(
                user_id=user_id,
                name=_source_filename(image["url"], index, image["mime_type"]),
                image_data=None,
                mime_type=image["mime_type"],
            )
            db.session.add(insp)
            db.session.flush()
            insp.storage_path = save_inspiration_image(
                user_id,
                insp.id,
                image["mime_type"],
                image["image_bytes"],
            )
            written_paths.append(insp.storage_path)
            saved.append({
                "id": insp.id,
                "name": insp.name,
                "mime_type": insp.mime_type,
                "source_url": image["url"],
                "image_url": url_for("media.inspiration_image", inspiration_id=insp.id),
            })

        db.session.commit()
        return saved
    except Exception:
        db.session.rollback()
        for relative_path in written_paths:
            delete_storage_file(relative_path)
        raise


@product_images_bp.route("/product-images")
@login_required
def product_images_page():
    user = _get_user()
    changed = refresh_cycle_if_needed(user)
    if changed:
        db.session.commit()

    billing_summary = get_billing_summary(user)
    gemini_key = get_effective_api_key(user.id, "gemini")

    return render_template(
        "product_images.html",
        has_gemini_key=bool(gemini_key),
        credits=billing_summary["available_credits"],
        monthly_credits=billing_summary["monthly_credits"],
        extra_credits=billing_summary["extra_credits"],
        plan_tier=billing_summary["plan_tier"],
        default_gemini_image_model=user.default_gemini_image_model or GEMINI_IMAGE_MODEL,
        default_lacy_prompt=LACY_BASE_PROMPT,
        lacy_analysis_prompt=LACY_ANALYSIS_PROMPT,
    )


@product_images_bp.route("/product-images/lacy/generate", methods=["POST"])
@login_required
def lacy_generate():
    user = _get_user()
    data = request.get_json() or {}

    base_image_id = data.get("base_image_id")
    source_ids = data.get("source_ids", [])
    prompt = (data.get("prompt") or "").strip()
    gemini_model = normalize_model_name(data.get("gemini_model")) or user.default_gemini_image_model or GEMINI_IMAGE_MODEL

    if not base_image_id:
        return jsonify({"error": "base_image_id is required."}), 400
    if not isinstance(source_ids, list) or not (1 <= len(source_ids) <= MAX_LACY_SOURCE_IMAGES):
        return jsonify({"error": f"Provide 1 to {MAX_LACY_SOURCE_IMAGES} source image IDs."}), 400
    if not prompt:
        return jsonify({"error": "prompt is required."}), 400
    if len(prompt) > 5000:
        return jsonify({"error": "Prompt must be 5000 characters or fewer."}), 400

    base_insp = CreativeInspiration.query.filter_by(id=base_image_id, user_id=user.id).first()
    if not base_insp:
        return jsonify({"error": "Base image not found."}), 404

    source_ids = [int(sid) for sid in source_ids]
    sources = CreativeInspiration.query.filter(
        CreativeInspiration.id.in_(source_ids),
        CreativeInspiration.user_id == user.id,
    ).all()
    if len(sources) != len(source_ids):
        return jsonify({"error": "One or more source images not found."}), 404

    gemini_key = get_effective_api_key(user.id, "gemini")
    if not gemini_key:
        return jsonify({"error": "no_gemini_key"}), 400

    quote = quote_ai_creatives(len(source_ids), "gemini", False)
    credits_per_source = quote["credits"] // len(source_ids)
    credit_error = ensure_credits_or_error(user, quote["credits"], feature="ai_creatives")
    if credit_error:
        return jsonify(credit_error), 402

    job = enqueue_worker_job(
        user_id=user.id,
        job_type=JOB_TYPE_LACY_GENERATE,
        payload={
            "base_image_id": base_insp.id,
            "source_ids": source_ids,
            "prompt": prompt,
            "gemini_model": gemini_model,
            "credits_per_source": credits_per_source,
        },
    )

    return jsonify({
        "job_id": job.id,
        "status": "queued",
        "status_url": f"/product-images/lacy/jobs/{job.id}",
    }), 202


@product_images_bp.route("/product-images/lacy/jobs/<int:job_id>")
@login_required
def lacy_job_status(job_id):
    user = _get_user()
    job = get_worker_job_for_user(job_id, user.id)
    if not job or job.job_type != JOB_TYPE_LACY_GENERATE:
        return jsonify({"error": "Job not found."}), 404

    response = {"job_id": job.id, "status": job.status, "error": job.error_message}
    if job.status == "completed":
        raw = job_payload(job) if hasattr(job, "result_json") else {}
        # result_json is stored separately from payload_json
        try:
            result_data = json.loads(job.result_json or "{}") if job.result_json else {}
        except Exception:
            result_data = {}
        items = result_data.get("results", [])
        response["results"] = [
            {
                "index": item.get("index"),
                "source_id": item.get("source_id"),
                "source_url": f"/media/inspirations/{item['source_id']}" if item.get("source_id") else None,
                "generated_url": f"/product-images/lacy/results/{job_id}/{item['index']}" if item.get("storage_path") else None,
                "error": item.get("error"),
            }
            for item in items
        ]
    return jsonify(response)


@product_images_bp.route("/product-images/lacy/results/<int:job_id>/<int:index>")
@login_required
def lacy_result_image(job_id, index):
    user = _get_user()
    job = get_worker_job_for_user(job_id, user.id)
    if not job or job.job_type != JOB_TYPE_LACY_GENERATE:
        abort(404)
    try:
        result_data = json.loads(job.result_json or "{}") if job.result_json else {}
    except Exception:
        abort(404)
    items = result_data.get("results", [])
    item = next((r for r in items if r.get("index") == index), None)
    if not item or not item.get("storage_path"):
        abort(404)
    storage_bytes = read_storage_bytes(item["storage_path"])
    if not storage_bytes:
        abort(404)
    return send_file(
        io.BytesIO(storage_bytes),
        mimetype="image/png",
        download_name=f"lacy_{job_id}_{index}.png",
    )


@product_images_bp.route("/product-images/lacy/auto-scrape", methods=["POST"])
@login_required
def lacy_auto_scrape():
    data = request.get_json() or {}
    limit = _coerce_limit(data.get("limit"))

    try:
        competitor_url = _normalize_competitor_url(data.get("url"))
        image_urls = extract_images(competitor_url)
        prepared_images, skipped = _prepare_remote_images(image_urls, limit)
        if not prepared_images:
            return jsonify({
                "error": "No importable public product images were found on that page.",
                "scraped_count": len(image_urls),
                "skipped": skipped[:10],
            }), 400

        saved = _save_inspiration_images(session["user_id"], prepared_images)
        return jsonify({
            "success": True,
            "url": competitor_url,
            "scraped_count": len(image_urls),
            "imported_count": len(saved),
            "saved": saved,
            "skipped_count": len(skipped),
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out. The site may be slow or unavailable."}), 408
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Could not connect to the site. Check the URL."}), 502
    except requests.exceptions.RequestException as exc:
        return jsonify({"error": f"Failed to fetch the page: {exc}"}), 500
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Failed to auto scrape product images: {exc}"}), 500
