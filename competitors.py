import base64
import io
import json
import mimetypes
import os
import re
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    render_template,
    request,
    session,
    stream_with_context,
)
from werkzeug.utils import secure_filename

from auth import login_required
from billing_service import get_effective_api_key
from db import Competitor, CompetitorAd, Product, User, WorkerJob, db
from image_harvest import (
    MAX_HTML_BYTES,
    MAX_IMAGE_BYTES,
    MAX_ZIP_IMAGES,
    ImageFetchError,
    extract_media_candidates,
    fetch_image,
    stream_media,
)
from media_storage import delete_storage_file, save_competitor_ad_video
from worker_queue import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    enqueue_worker_job,
)
from worker_tasks import (
    JOB_TYPE_COMPETITOR_AD_ANALYZE,
    JOB_TYPE_COMPETITOR_AD_TRANSCRIBE,
)

competitors_bp = Blueprint("competitors", __name__)

# OpenAI transcription rejects files above 25 MB.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
JOB_QUEUE = "default"
JOB_MAX_ATTEMPTS = 2
MAX_NAME_LENGTH = 160


def _get_user() -> User:
    return db.session.get(User, session["user_id"])


def _get_competitor(competitor_id: int) -> Competitor | None:
    return Competitor.query.filter_by(
        id=competitor_id, user_id=session["user_id"]
    ).first()


def _get_ad(ad_id: int) -> CompetitorAd | None:
    return (
        CompetitorAd.query
        .join(Competitor, Competitor.id == CompetitorAd.competitor_id)
        .filter(
            CompetitorAd.id == ad_id,
            Competitor.user_id == session["user_id"],
        )
        .first()
    )


def _get_product(product_id) -> Product | None:
    if not product_id:
        return None
    return Product.query.filter_by(
        id=int(product_id), user_id=session["user_id"]
    ).first()


def _serialize_competitor(competitor: Competitor, ad_count: int | None = None) -> dict:
    product = competitor.product
    return {
        "id": competitor.id,
        "name": competitor.name,
        "product": {"id": product.id, "name": product.name} if product else None,
        "ad_count": ad_count if ad_count is not None else len(competitor.ads),
        "created_at": (
            competitor.created_at.isoformat() if competitor.created_at else None
        ),
    }


def _parse_analysis(ad: CompetitorAd) -> dict | None:
    if not ad.analysis_json:
        return None
    try:
        parsed = json.loads(ad.analysis_json)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _serialize_ad(ad: CompetitorAd) -> dict:
    return {
        "id": ad.id,
        "competitor_id": ad.competitor_id,
        "original_filename": ad.original_filename,
        "mime_type": ad.mime_type,
        "file_size_bytes": ad.file_size_bytes,
        "video_url": f"/media/competitor-ads/{ad.id}" if ad.storage_path else None,
        "transcript": ad.transcript,
        "transcript_status": ad.transcript_status,
        "transcript_error": ad.transcript_error,
        "analysis": _parse_analysis(ad),
        "analysis_status": ad.analysis_status,
        "analysis_error": ad.analysis_error,
        "created_at": ad.created_at.isoformat() if ad.created_at else None,
        "updated_at": ad.updated_at.isoformat() if ad.updated_at else None,
    }


def _mark_worker_job_cancelled(job_id: int | None, reason: str) -> None:
    if not job_id:
        return
    job = db.session.get(WorkerJob, job_id)
    if not job or job.status in (JOB_STATUS_COMPLETED, JOB_STATUS_FAILED):
        return
    job.status = JOB_STATUS_FAILED
    job.error_message = reason
    job.locked_at = None
    job.lock_token = None
    job.worker_name = None
    job.finished_at = datetime.now(timezone.utc)


def _request_payload() -> dict:
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


# ── Pages ────────────────────────────────────────────────────────────────────


@competitors_bp.route("/competitors")
@login_required
def competitors_page():
    user = _get_user()
    competitors = (
        Competitor.query.filter_by(user_id=user.id)
        .order_by(Competitor.created_at.desc())
        .all()
    )
    products = (
        Product.query.filter_by(user_id=user.id)
        .order_by(Product.name.asc())
        .all()
    )
    return render_template(
        "competitors.html",
        competitors=[_serialize_competitor(c) for c in competitors],
        products=[{"id": p.id, "name": p.name} for p in products],
    )


@competitors_bp.route("/competitors/<int:competitor_id>")
@login_required
def competitor_detail_page(competitor_id: int):
    competitor = _get_competitor(competitor_id)
    if not competitor:
        abort(404)

    user = _get_user()
    products = (
        Product.query.filter_by(user_id=user.id)
        .order_by(Product.name.asc())
        .all()
    )
    openai_key = get_effective_api_key(user.id, "openai")
    return render_template(
        "competitor_detail.html",
        competitor=_serialize_competitor(competitor),
        ads=[_serialize_ad(ad) for ad in competitor.ads],
        products=[{"id": p.id, "name": p.name} for p in products],
        has_openai_key=bool(openai_key),
        max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
    )


# ── Competitor CRUD ──────────────────────────────────────────────────────────


@competitors_bp.route("/competitors", methods=["POST"])
@login_required
def create_competitor():
    payload = _request_payload()
    name = (payload.get("name") or "").strip()[:MAX_NAME_LENGTH]
    if not name:
        return jsonify({"error": "Competitor name is required."}), 400

    product = None
    product_id = payload.get("product_id")
    if product_id not in (None, "", "none"):
        product = _get_product(product_id)
        if not product:
            return jsonify({"error": "Product not found."}), 400

    competitor = Competitor(
        user_id=session["user_id"],
        name=name,
        product_id=product.id if product else None,
    )
    db.session.add(competitor)
    db.session.commit()
    return jsonify({"competitor": _serialize_competitor(competitor, ad_count=0)}), 201


@competitors_bp.route("/competitors/<int:competitor_id>", methods=["PATCH"])
@login_required
def update_competitor(competitor_id: int):
    competitor = _get_competitor(competitor_id)
    if not competitor:
        return jsonify({"error": "Competitor not found."}), 404

    payload = _request_payload()
    if "name" in payload:
        name = (payload.get("name") or "").strip()[:MAX_NAME_LENGTH]
        if not name:
            return jsonify({"error": "Competitor name is required."}), 400
        competitor.name = name

    if "product_id" in payload:
        product_id = payload.get("product_id")
        if product_id in (None, "", "none"):
            competitor.product_id = None
        else:
            product = _get_product(product_id)
            if not product:
                return jsonify({"error": "Product not found."}), 400
            competitor.product_id = product.id

    db.session.commit()
    return jsonify({"competitor": _serialize_competitor(competitor)})


@competitors_bp.route("/competitors/<int:competitor_id>", methods=["DELETE"])
@login_required
def delete_competitor(competitor_id: int):
    competitor = _get_competitor(competitor_id)
    if not competitor:
        return jsonify({"error": "Competitor not found."}), 404

    storage_paths = []
    for ad in competitor.ads:
        if ad.storage_path:
            storage_paths.append(ad.storage_path)
        _mark_worker_job_cancelled(ad.transcribe_job_id, "Competitor removed by user.")
        _mark_worker_job_cancelled(ad.analysis_job_id, "Competitor removed by user.")

    db.session.delete(competitor)
    db.session.commit()

    for storage_path in storage_paths:
        delete_storage_file(storage_path)
    return jsonify({"ok": True})


# ── Ads ──────────────────────────────────────────────────────────────────────


@competitors_bp.route("/competitors/<int:competitor_id>/ads", methods=["GET"])
@login_required
def list_competitor_ads(competitor_id: int):
    competitor = _get_competitor(competitor_id)
    if not competitor:
        return jsonify({"error": "Competitor not found."}), 404
    return jsonify({"ads": [_serialize_ad(ad) for ad in competitor.ads]})


@competitors_bp.route("/competitors/<int:competitor_id>/ads", methods=["POST"])
@login_required
def upload_competitor_ad(competitor_id: int):
    competitor = _get_competitor(competitor_id)
    if not competitor:
        return jsonify({"error": "Competitor not found."}), 404

    user = _get_user()
    openai_key = get_effective_api_key(user.id, "openai")
    if not openai_key:
        return jsonify({"error": "no_openai_key"}), 400

    video_file = request.files.get("video")
    if not video_file or not video_file.filename:
        return jsonify({"error": "Please upload a video file."}), 400

    original_name = video_file.filename
    detected_mime = (
        video_file.content_type
        or mimetypes.guess_type(original_name)[0]
        or "application/octet-stream"
    )
    if not (detected_mime.startswith("video/") or detected_mime.startswith("audio/")):
        return jsonify({"error": "Only video files are supported."}), 400

    ad = CompetitorAd(
        competitor_id=competitor.id,
        original_filename=original_name[:255],
        mime_type=detected_mime[:100],
        transcript_status="queued",
    )
    db.session.add(ad)
    db.session.flush()  # assign ad.id before writing the file to storage

    extension = os.path.splitext(original_name)[1] or (
        mimetypes.guess_extension(detected_mime) or ".mp4"
    )
    try:
        storage_path, file_size = save_competitor_ad_video(
            user.id, competitor.id, ad.id, extension, video_file
        )
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Could not store the uploaded video: {exc}"}), 500

    if file_size <= 0:
        db.session.rollback()
        delete_storage_file(storage_path)
        return jsonify({"error": "Uploaded file is empty."}), 400
    if file_size > MAX_UPLOAD_BYTES:
        db.session.rollback()
        delete_storage_file(storage_path)
        return jsonify(
            {
                "error": (
                    f"Video is too large. Maximum supported size is "
                    f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
                )
            }
        ), 400

    ad.storage_path = storage_path
    ad.file_size_bytes = file_size

    job = enqueue_worker_job(
        user_id=user.id,
        job_type=JOB_TYPE_COMPETITOR_AD_TRANSCRIBE,
        queue_name=JOB_QUEUE,
        max_attempts=JOB_MAX_ATTEMPTS,
        payload={"ad_id": ad.id},
        commit=False,
    )
    ad.transcribe_job_id = job.id
    db.session.commit()

    return jsonify({"ad": _serialize_ad(ad)}), 202


@competitors_bp.route("/competitors/ads/<int:ad_id>", methods=["GET"])
@login_required
def get_competitor_ad(ad_id: int):
    ad = _get_ad(ad_id)
    if not ad:
        return jsonify({"error": "Ad not found."}), 404
    return jsonify({"ad": _serialize_ad(ad)})


@competitors_bp.route("/competitors/ads/<int:ad_id>/transcribe", methods=["POST"])
@login_required
def retry_competitor_ad_transcription(ad_id: int):
    ad = _get_ad(ad_id)
    if not ad:
        return jsonify({"error": "Ad not found."}), 404
    if ad.transcript_status in ("queued", "processing"):
        return jsonify({"error": "Transcription is already in progress."}), 409
    if not ad.storage_path:
        return jsonify({"error": "This ad has no stored video file."}), 400

    user = _get_user()
    openai_key = get_effective_api_key(user.id, "openai")
    if not openai_key:
        return jsonify({"error": "no_openai_key"}), 400

    job = enqueue_worker_job(
        user_id=user.id,
        job_type=JOB_TYPE_COMPETITOR_AD_TRANSCRIBE,
        queue_name=JOB_QUEUE,
        max_attempts=JOB_MAX_ATTEMPTS,
        payload={"ad_id": ad.id},
        commit=False,
    )
    ad.transcribe_job_id = job.id
    ad.transcript_status = "queued"
    ad.transcript_error = None
    db.session.commit()
    return jsonify({"ad": _serialize_ad(ad)}), 202


@competitors_bp.route("/competitors/ads/<int:ad_id>/analyze", methods=["POST"])
@login_required
def analyze_competitor_ad(ad_id: int):
    ad = _get_ad(ad_id)
    if not ad:
        return jsonify({"error": "Ad not found."}), 404
    if not (ad.transcript or "").strip():
        return jsonify(
            {"error": "This ad has no transcript yet. Wait for transcription to finish."}
        ), 400
    if ad.analysis_status in ("queued", "processing"):
        return jsonify({"error": "Analysis is already in progress."}), 409

    user = _get_user()
    openai_key = get_effective_api_key(user.id, "openai")
    if not openai_key:
        return jsonify({"error": "no_openai_key"}), 400

    job = enqueue_worker_job(
        user_id=user.id,
        job_type=JOB_TYPE_COMPETITOR_AD_ANALYZE,
        queue_name=JOB_QUEUE,
        max_attempts=JOB_MAX_ATTEMPTS,
        payload={"ad_id": ad.id},
        commit=False,
    )
    ad.analysis_job_id = job.id
    ad.analysis_status = "queued"
    ad.analysis_error = None
    db.session.commit()
    return jsonify({"ad": _serialize_ad(ad)}), 202


@competitors_bp.route("/competitors/ads/<int:ad_id>", methods=["DELETE"])
@login_required
def delete_competitor_ad(ad_id: int):
    ad = _get_ad(ad_id)
    if not ad:
        return jsonify({"error": "Ad not found."}), 404

    storage_path = ad.storage_path
    _mark_worker_job_cancelled(ad.transcribe_job_id, "Ad removed by user.")
    _mark_worker_job_cancelled(ad.analysis_job_id, "Ad removed by user.")
    db.session.delete(ad)
    db.session.commit()

    delete_storage_file(storage_path)
    return jsonify({"ok": True})


# ── Page media extractor ───────────────────────────────────────────────────────
#
# Paste a page's HTML source → every image and video is pulled out, upgraded to
# the best available quality, and served back for preview + download. Stateless:
# nothing is persisted, so there is no DB model or worker job involved.

_DATA_URI_RE = re.compile(
    r"^data:((?:image|video)/[\w.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL
)


@competitors_bp.route("/competitors/image-extractor")
@login_required
def image_extractor_page():
    return render_template(
        "image_extractor.html",
        max_html_mb=MAX_HTML_BYTES // (1024 * 1024),
        max_zip_images=MAX_ZIP_IMAGES,
    )


def _clean_base_url(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return urlunparse(parsed)


@competitors_bp.route("/competitors/extract-images", methods=["POST"])
@login_required
def extract_images():
    payload = request.get_json(silent=True) or {}
    html = payload.get("html")
    if not isinstance(html, str) or not html.strip():
        return jsonify({"error": "Paste a page's source code first."}), 400
    if len(html.encode("utf-8", "ignore")) > MAX_HTML_BYTES:
        return jsonify(
            {
                "error": (
                    "The pasted source is too large. Max "
                    f"{MAX_HTML_BYTES // (1024 * 1024)} MB."
                )
            }
        ), 400

    base_url = None
    if payload.get("base_url"):
        base_url = _clean_base_url(payload["base_url"])
        if base_url is None:
            return jsonify({"error": "The optional page URL is not valid."}), 400

    media = extract_media_candidates(html, base_url)
    return jsonify(
        {
            "images": media["images"],
            "videos": media["videos"],
            "count": len(media["images"]) + len(media["videos"]),
        }
    )


def _decode_data_uri(url: str) -> tuple[bytes, str] | None:
    match = _DATA_URI_RE.match((url or "").strip())
    if not match:
        return None
    try:
        return base64.b64decode(match.group(2), validate=False), match.group(1).lower()
    except Exception:
        return None


def _download_name(raw: str | None, content_type: str, fallback: str) -> str:
    name = secure_filename(raw or "") or fallback
    if "." not in name:
        ext = mimetypes.guess_extension((content_type or "").split(";")[0]) or ".jpg"
        name += ext
    return name[:150]


@competitors_bp.route("/competitors/image-proxy")
@login_required
def image_proxy():
    """Serve a single media file back to the browser (bypasses CORS / hotlinking).

    Used both as an ``<img>`` src fallback (inline) and to force a download with
    the right filename (``download=1``). Videos (``kind=video``) are streamed so
    large files are not buffered in memory.
    """
    url = (request.args.get("url") or "").strip()
    if not url:
        abort(400)
    as_download = request.args.get("download") == "1"
    is_video = request.args.get("kind") == "video"

    if url.startswith("data:"):
        decoded = _decode_data_uri(url)
        if not decoded:
            abort(400)
        data, content_type = decoded
        response = Response(data, mimetype=content_type or "application/octet-stream")
    elif is_video:
        try:
            stream, content_type = stream_media(url)
        except Exception:
            abort(502)
        response = Response(
            stream_with_context(stream),
            mimetype=content_type or "application/octet-stream",
        )
    else:
        try:
            data, content_type = fetch_image(url)
        except Exception:
            abort(502)
        response = Response(data, mimetype=content_type or "application/octet-stream")

    response.headers["Cache-Control"] = "private, max-age=300"
    if as_download:
        fallback = "video.mp4" if is_video else "image.jpg"
        name = _download_name(request.args.get("name"), content_type, fallback)
        response.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@competitors_bp.route("/competitors/download-images-zip", methods=["POST"])
@login_required
def download_images_zip():
    payload = request.get_json(silent=True) or {}
    images = payload.get("images")
    if not isinstance(images, list) or not images:
        return jsonify({"error": "No images to download."}), 400
    images = images[:MAX_ZIP_IMAGES]

    buffer = io.BytesIO()
    used_names: set[str] = set()
    added = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, item in enumerate(images, start=1):
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or "").strip()
            if not url:
                continue
            try:
                if url.startswith("data:"):
                    decoded = _decode_data_uri(url)
                    if not decoded:
                        continue
                    data, content_type = decoded
                    if len(data) > MAX_IMAGE_BYTES:
                        continue
                else:
                    data, content_type = fetch_image(url)
            except Exception:
                continue

            name = _download_name(
                item.get("filename"), content_type, f"image-{index}.jpg"
            )
            stem, dot, ext = name.rpartition(".")
            stem = stem if dot else name
            suffix = f".{ext}" if dot else ""
            unique = name
            counter = 2
            while unique.lower() in used_names:
                unique = f"{stem}-{counter}{suffix}"
                counter += 1
            used_names.add(unique.lower())

            archive.writestr(unique, data)
            added += 1

    if added == 0:
        return jsonify({"error": "None of the images could be downloaded."}), 502

    buffer.seek(0)
    response = Response(buffer.getvalue(), mimetype="application/zip")
    response.headers["Content-Disposition"] = (
        'attachment; filename="competitor-images.zip"'
    )
    response.headers["Cache-Control"] = "no-store"
    return response
