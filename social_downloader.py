from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from dlqueue import UnsupportedURLError, detect_platform
from flask import (
    Blueprint,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from auth import login_required
from billing_service import (
    build_plan_required_payload,
    has_required_plan,
)
from db import SocialDownload, User, WorkerJob, db
from media_storage import delete_storage_file, resolve_storage_path
from worker_queue import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    enqueue_worker_job,
)
from worker_tasks import JOB_TYPE_SOCIAL_DOWNLOAD


social_downloader_bp = Blueprint("social_downloader", __name__)

REQUIRED_PLAN = "pro"
FEATURE_NAME = "social_downloader"
DOWNLOAD_QUEUE_NAME = "default"
MAX_URLS_PER_REQUEST = 10
MAX_ACTIVE_DOWNLOADS = 20
MAX_HISTORY_ITEMS = 100
ACTIVE_STATUSES = {"queued", "downloading"}
SUPPORTED_PLATFORMS = {"tiktok", "instagram", "facebook"}
SUPPORTED_URL_ERROR = (
    "Only TikTok, Instagram, and Facebook links are supported."
)


def _get_user() -> User:
    return db.session.get(User, session["user_id"])


def _plan_error(user: User):
    if has_required_plan(user, REQUIRED_PLAN):
        return None
    status, payload = build_plan_required_payload(
        REQUIRED_PLAN, feature=FEATURE_NAME
    )
    return jsonify(payload), status


def _normalize_url(raw_url: str) -> tuple[str, str]:
    url = (raw_url or "").strip()
    if not url:
        raise UnsupportedURLError("URL is empty.")
    if len(url) > 2048:
        raise UnsupportedURLError("URL is too long.")

    parsed = urlparse(url)
    if not parsed.scheme:
        url = f"https://{url}"
        parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsupportedURLError("Only http and https URLs are supported.")

    platform = detect_platform(url)
    if platform not in SUPPORTED_PLATFORMS:
        raise UnsupportedURLError(SUPPORTED_URL_ERROR)
    return url, platform


def _serialize_download(item: SocialDownload) -> dict:
    return {
        "id": item.id,
        "url": item.source_url,
        "platform": item.platform,
        "status": item.status,
        "title": item.title,
        "filename": (
            Path(item.storage_path).name if item.storage_path else None
        ),
        "file_size_bytes": item.file_size_bytes,
        "error": item.error,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "finished_at": (
            item.finished_at.isoformat() if item.finished_at else None
        ),
        "downloadable": item.status == "success" and bool(item.storage_path),
        "retryable": item.status == "failed",
        "removable": True,
        "download_url": (
            url_for("social_downloader.download_file", download_id=item.id)
            if item.status == "success" and item.storage_path
            else None
        ),
    }


def _mark_worker_job_removed(job_id: int | None) -> None:
    if not job_id:
        return
    job = db.session.get(WorkerJob, job_id)
    if not job or job.status == JOB_STATUS_COMPLETED:
        return
    job.status = JOB_STATUS_FAILED
    job.error_message = "Download removed by user."
    job.locked_at = None
    job.lock_token = None
    job.worker_name = None
    job.finished_at = datetime.now(timezone.utc)


def _delete_download_record(item: SocialDownload) -> str | None:
    storage_path = item.storage_path
    _mark_worker_job_removed(item.worker_job_id)
    db.session.delete(item)
    return storage_path


@social_downloader_bp.route("/social-downloader")
@login_required
def social_downloader_page():
    user = _get_user()
    has_access = has_required_plan(user, REQUIRED_PLAN)
    return render_template(
        "social_downloader.html",
        has_access=has_access,
        required_plan=REQUIRED_PLAN,
        max_urls=MAX_URLS_PER_REQUEST,
    )


@social_downloader_bp.route("/social-downloader/items")
@login_required
def list_downloads():
    user = _get_user()
    plan_error = _plan_error(user)
    if plan_error:
        return plan_error

    items = (
        SocialDownload.query
        .filter_by(user_id=user.id)
        .order_by(SocialDownload.created_at.desc(), SocialDownload.id.desc())
        .limit(MAX_HISTORY_ITEMS)
        .all()
    )
    active_count = sum(
        1 for item in items if item.status in ACTIVE_STATUSES
    )
    return jsonify(
        {
            "items": [_serialize_download(item) for item in items],
            "active_count": active_count,
            "max_active": MAX_ACTIVE_DOWNLOADS,
        }
    )


@social_downloader_bp.route(
    "/social-downloader/items", methods=["POST"]
)
@login_required
def create_downloads():
    user = _get_user()
    plan_error = _plan_error(user)
    if plan_error:
        return plan_error

    data = request.get_json(silent=True) or {}
    raw_urls = data.get("urls")
    if isinstance(raw_urls, str):
        raw_urls = raw_urls.splitlines()
    if not isinstance(raw_urls, list):
        return jsonify(
            {"error": "Provide a list of TikTok, Instagram, or Facebook URLs."}
        ), 400

    raw_urls = [str(value).strip() for value in raw_urls if str(value).strip()]
    if not raw_urls:
        return jsonify({"error": "Add at least one URL."}), 400
    if len(raw_urls) > MAX_URLS_PER_REQUEST:
        return jsonify(
            {
                "error": (
                    f"You can add up to {MAX_URLS_PER_REQUEST} links "
                    "at a time."
                )
            }
        ), 400

    normalized = []
    errors = []
    for index, raw_url in enumerate(raw_urls, start=1):
        try:
            normalized.append(_normalize_url(raw_url))
        except UnsupportedURLError as exc:
            errors.append(f"Link {index}: {exc}")
    if errors:
        return jsonify(
            {"error": "Some links are not supported.", "details": errors}
        ), 400

    active_count = SocialDownload.query.filter(
        SocialDownload.user_id == user.id,
        SocialDownload.status.in_(ACTIVE_STATUSES),
    ).count()
    if active_count + len(normalized) > MAX_ACTIVE_DOWNLOADS:
        available = max(0, MAX_ACTIVE_DOWNLOADS - active_count)
        return jsonify(
            {
                "error": (
                    "Your download queue is full. "
                    f"You can add {available} more link(s) right now."
                ),
                "active_count": active_count,
                "max_active": MAX_ACTIVE_DOWNLOADS,
            }
        ), 409

    created = []
    try:
        for url, platform in normalized:
            item = SocialDownload(
                user_id=user.id,
                source_url=url,
                platform=platform,
                status="queued",
            )
            db.session.add(item)
            db.session.flush()

            job = enqueue_worker_job(
                user_id=user.id,
                job_type=JOB_TYPE_SOCIAL_DOWNLOAD,
                queue_name=DOWNLOAD_QUEUE_NAME,
                max_attempts=1,
                commit=False,
                payload={
                    "download_id": item.id,
                    "source_url": url,
                },
            )
            item.worker_job_id = job.id
            created.append(item)

        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Failed to add links to the queue."}), 500

    return jsonify(
        {"items": [_serialize_download(item) for item in created]}
    ), 202


@social_downloader_bp.route(
    "/social-downloader/items", methods=["DELETE"]
)
@login_required
def clear_downloads():
    user = _get_user()
    plan_error = _plan_error(user)
    if plan_error:
        return plan_error

    items = SocialDownload.query.filter_by(user_id=user.id).all()
    storage_paths = []
    try:
        for item in items:
            storage_path = _delete_download_record(item)
            if storage_path:
                storage_paths.append(storage_path)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Failed to clear the queue."}), 500

    for storage_path in storage_paths:
        delete_storage_file(storage_path)
    return jsonify({"ok": True, "deleted": len(items)})


@social_downloader_bp.route(
    "/social-downloader/items/<int:download_id>/retry",
    methods=["POST"],
)
@login_required
def retry_download(download_id: int):
    user = _get_user()
    plan_error = _plan_error(user)
    if plan_error:
        return plan_error

    item = SocialDownload.query.filter_by(
        id=download_id, user_id=user.id
    ).first()
    if not item:
        return jsonify({"error": "Download not found."}), 404
    if item.status != "failed":
        return jsonify({"error": "Only failed downloads can be retried."}), 409

    old_storage_path = item.storage_path
    try:
        item.status = "queued"
        item.error = None
        item.title = None
        item.storage_path = None
        item.mime_type = None
        item.file_size_bytes = None
        item.finished_at = None
        item.updated_at = datetime.now(timezone.utc)

        job = enqueue_worker_job(
            user_id=user.id,
            job_type=JOB_TYPE_SOCIAL_DOWNLOAD,
            queue_name=DOWNLOAD_QUEUE_NAME,
            max_attempts=1,
            commit=False,
            payload={
                "download_id": item.id,
                "source_url": item.source_url,
            },
        )
        item.worker_job_id = job.id
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Failed to retry the download."}), 500

    if old_storage_path:
        delete_storage_file(old_storage_path)
    return jsonify({"item": _serialize_download(item)}), 202


@social_downloader_bp.route(
    "/social-downloader/items/<int:download_id>", methods=["DELETE"]
)
@login_required
def delete_download(download_id: int):
    user = _get_user()
    plan_error = _plan_error(user)
    if plan_error:
        return plan_error

    item = SocialDownload.query.filter_by(
        id=download_id, user_id=user.id
    ).first()
    if not item:
        return jsonify({"error": "Download not found."}), 404
    storage_path = None
    try:
        storage_path = _delete_download_record(item)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Failed to remove the download."}), 500

    if storage_path:
        delete_storage_file(storage_path)
    return jsonify({"ok": True})


@social_downloader_bp.route(
    "/social-downloader/items/<int:download_id>/download"
)
@login_required
def download_file(download_id: int):
    user = _get_user()
    if not has_required_plan(user, REQUIRED_PLAN):
        abort(403)

    item = SocialDownload.query.filter_by(
        id=download_id,
        user_id=user.id,
        status="success",
    ).first()
    if not item or not item.storage_path:
        abort(404)

    try:
        file_path = resolve_storage_path(item.storage_path)
    except ValueError:
        abort(404)
    if not file_path.is_file():
        abort(404)

    extension = file_path.suffix or ".mp4"
    title = secure_filename(item.title or f"{item.platform}_{item.id}")
    title_path = Path(title)
    if title_path.suffix.lower() == extension.lower():
        download_name = title
    else:
        download_name = f"{title or f'download_{item.id}'}{extension}"
    return send_file(
        file_path,
        mimetype=item.mime_type or "video/mp4",
        as_attachment=True,
        download_name=download_name,
        conditional=True,
    )
