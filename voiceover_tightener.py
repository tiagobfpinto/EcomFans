from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from auth import login_required
from db import User, VoiceoverTightening, db
from media_storage import (
    delete_storage_file,
    resolve_storage_path,
    save_voiceover_upload,
)
from voiceover_processing import (
    PRESET_DEFAULTS,
    VoiceoverProcessingError,
    normalize_settings,
    probe_mp3,
)
from worker_queue import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    enqueue_worker_job,
)
from worker_tasks import JOB_TYPE_VOICEOVER_TIGHTEN


voiceover_tightener_bp = Blueprint("voiceover_tightener", __name__)

ACTIVE_STATUSES = {"queued", "processing"}
TERMINAL_STATUSES = {"completed", "failed"}
HISTORY_PAGE_SIZE = 20
MAX_HISTORY_PAGE_SIZE = 50
QUEUE_NAME = "default"


def _get_user() -> User:
    return db.session.get(User, session["user_id"])


def _max_upload_bytes() -> int:
    return int(
        current_app.config.get(
            "VOICEOVER_TIGHTENER_MAX_UPLOAD_BYTES", 100 * 1024 * 1024
        )
    )


def _max_duration_seconds() -> int:
    return int(
        current_app.config.get("VOICEOVER_TIGHTENER_MAX_DURATION_SECONDS", 3600)
    )


def _json_object(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Settings must contain valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Settings must be a JSON object.")
    return parsed


def _load_json(raw: str | None, fallback):
    if not raw:
        return fallback
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _serialize_item(item: VoiceoverTightening) -> dict:
    completed = item.status == "completed" and bool(item.output_storage_path)
    return {
        "id": item.id,
        "status": item.status,
        "original_filename": item.original_filename,
        "preset": item.preset,
        "settings": _load_json(item.settings_json, {}),
        "original_file_size_bytes": item.original_file_size_bytes,
        "output_file_size_bytes": item.output_file_size_bytes,
        "original_duration_ms": item.original_duration_ms,
        "output_duration_ms": item.output_duration_ms,
        "removed_duration_ms": item.removed_duration_ms,
        "pauses_shortened": item.pauses_shortened,
        "overlaps_applied": item.overlaps_applied,
        "warnings": _load_json(item.warnings_json, []),
        "error": item.error,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "finished_at": item.finished_at.isoformat() if item.finished_at else None,
        "original_audio_url": url_for(
            "voiceover_tightener.original_audio", tightening_id=item.id
        ),
        "output_audio_url": (
            url_for("voiceover_tightener.output_audio", tightening_id=item.id)
            if completed
            else None
        ),
        "download_url": (
            url_for("voiceover_tightener.download_output", tightening_id=item.id)
            if completed
            else None
        ),
        "retryable": item.status == "failed",
        "deletable": item.status in TERMINAL_STATUSES,
    }


def _item_for_user(tightening_id: int) -> VoiceoverTightening | None:
    return VoiceoverTightening.query.filter_by(
        id=tightening_id, user_id=session["user_id"]
    ).first()


def _reconcile_active_items(user_id: int) -> None:
    changed = False
    active_items = VoiceoverTightening.query.filter(
        VoiceoverTightening.user_id == user_id,
        VoiceoverTightening.status.in_(ACTIVE_STATUSES),
    ).all()
    for item in active_items:
        job = item.worker_job
        if not job or job.status not in {JOB_STATUS_FAILED, JOB_STATUS_COMPLETED}:
            continue
        item.status = "failed"
        item.error = (
            job.error_message
            or "The processing worker stopped before producing a result. Please retry."
        )[:4000]
        item.finished_at = job.finished_at or datetime.now(timezone.utc)
        changed = True
    if changed:
        db.session.commit()


def _send_stored_audio(item: VoiceoverTightening, storage_path: str | None):
    if not storage_path:
        abort(404)
    try:
        file_path = resolve_storage_path(storage_path)
    except ValueError:
        abort(404)
    if not file_path.is_file():
        abort(404)
    return send_file(
        file_path,
        mimetype="audio/mpeg",
        conditional=True,
        max_age=0,
    )


@voiceover_tightener_bp.route("/voiceover-tightener")
@login_required
def voiceover_tightener_page():
    return render_template(
        "voiceover_tightener.html",
        preset_defaults=PRESET_DEFAULTS,
        max_upload_mb=_max_upload_bytes() // (1024 * 1024),
        max_duration_minutes=_max_duration_seconds() // 60,
    )


@voiceover_tightener_bp.route("/voiceover-tightener/items")
@login_required
def list_items():
    _reconcile_active_items(session["user_id"])
    try:
        limit = min(
            MAX_HISTORY_PAGE_SIZE,
            max(1, int(request.args.get("limit", HISTORY_PAGE_SIZE))),
        )
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid history limit."}), 400
    query = VoiceoverTightening.query.filter_by(user_id=session["user_id"])
    cursor = request.args.get("cursor", "").strip()
    if cursor:
        try:
            query = query.filter(VoiceoverTightening.id < int(cursor))
        except ValueError:
            return jsonify({"error": "Invalid history cursor."}), 400
    items = query.order_by(VoiceoverTightening.id.desc()).limit(limit + 1).all()
    has_more = len(items) > limit
    page = items[:limit]
    active_count = VoiceoverTightening.query.filter(
        VoiceoverTightening.user_id == session["user_id"],
        VoiceoverTightening.status.in_(ACTIVE_STATUSES),
    ).count()
    return jsonify(
        {
            "items": [_serialize_item(item) for item in page],
            "active_count": active_count,
            "next_cursor": str(page[-1].id) if has_more and page else None,
        }
    )


@voiceover_tightener_bp.route(
    "/voiceover-tightener/items", methods=["POST"]
)
@login_required
def create_item():
    user = _get_user()
    _reconcile_active_items(user.id)
    active_count = VoiceoverTightening.query.filter(
        VoiceoverTightening.user_id == user.id,
        VoiceoverTightening.status.in_(ACTIVE_STATUSES),
    ).count()
    if active_count:
        return jsonify(
            {"error": "Wait for the current voiceover to finish before adding another."}
        ), 409

    audio = request.files.get("audio")
    original_name = (audio.filename or "").strip() if audio else ""
    if not audio or not original_name:
        return jsonify({"error": "Choose an MP3 voiceover to process."}), 400
    if Path(original_name).suffix.lower() != ".mp3":
        return jsonify({"error": "Only MP3 files are supported."}), 400
    try:
        preset, settings = normalize_settings(
            request.form.get("preset", "dynamic"),
            _json_object(request.form.get("settings")),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    safe_name = secure_filename(Path(original_name).name) or "voiceover.mp3"
    item = VoiceoverTightening(
        user_id=user.id,
        status="queued",
        original_filename=safe_name[:255],
        original_storage_path="pending",
        original_file_size_bytes=0,
        preset=preset,
        settings_json=json.dumps(settings, separators=(",", ":")),
    )
    storage_path = None
    try:
        db.session.add(item)
        db.session.flush()
        storage_path, file_size = save_voiceover_upload(user.id, item.id, audio)
        if file_size <= 0:
            raise ValueError("The selected MP3 is empty.")
        if file_size > _max_upload_bytes():
            max_mb = _max_upload_bytes() // (1024 * 1024)
            raise ValueError(f"The MP3 exceeds the {max_mb} MB limit.")
        absolute_path = resolve_storage_path(storage_path)
        probe = probe_mp3(str(absolute_path))
        if probe.duration_ms > _max_duration_seconds() * 1000:
            max_minutes = _max_duration_seconds() // 60
            raise ValueError(
                f"Voiceovers must be {max_minutes} minutes or shorter."
            )
        item.original_storage_path = storage_path
        item.original_file_size_bytes = file_size
        item.original_duration_ms = probe.duration_ms
        job = enqueue_worker_job(
            user_id=user.id,
            job_type=JOB_TYPE_VOICEOVER_TIGHTEN,
            queue_name=QUEUE_NAME,
            max_attempts=1,
            commit=False,
            payload={"tightening_id": item.id},
        )
        item.worker_job_id = job.id
        db.session.commit()
    except (ValueError, VoiceoverProcessingError) as exc:
        db.session.rollback()
        if storage_path:
            delete_storage_file(storage_path)
        return jsonify({"error": str(exc)}), 400
    except Exception:
        db.session.rollback()
        if storage_path:
            delete_storage_file(storage_path)
        current_app.logger.exception("Failed to queue voiceover tightening")
        return jsonify({"error": "Could not queue this voiceover."}), 500
    return jsonify({"item": _serialize_item(item)}), 202


@voiceover_tightener_bp.route(
    "/voiceover-tightener/items/<int:tightening_id>/retry", methods=["POST"]
)
@login_required
def retry_item(tightening_id: int):
    _reconcile_active_items(session["user_id"])
    item = _item_for_user(tightening_id)
    if not item:
        return jsonify({"error": "Voiceover not found."}), 404
    if item.status != "failed":
        return jsonify({"error": "Only failed voiceovers can be retried."}), 409
    active_count = VoiceoverTightening.query.filter(
        VoiceoverTightening.user_id == session["user_id"],
        VoiceoverTightening.status.in_(ACTIVE_STATUSES),
    ).count()
    if active_count:
        return jsonify({"error": "Another voiceover is already processing."}), 409
    try:
        old_output = item.output_storage_path
        item.status = "queued"
        item.output_storage_path = None
        item.output_file_size_bytes = None
        item.output_duration_ms = None
        item.removed_duration_ms = None
        item.pauses_shortened = None
        item.overlaps_applied = None
        item.warnings_json = None
        item.error = None
        item.finished_at = None
        job = enqueue_worker_job(
            user_id=item.user_id,
            job_type=JOB_TYPE_VOICEOVER_TIGHTEN,
            queue_name=QUEUE_NAME,
            max_attempts=1,
            commit=False,
            payload={"tightening_id": item.id},
        )
        item.worker_job_id = job.id
        db.session.commit()
        if old_output:
            delete_storage_file(old_output)
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Could not retry this voiceover."}), 500
    return jsonify({"item": _serialize_item(item)}), 202


@voiceover_tightener_bp.route(
    "/voiceover-tightener/items/<int:tightening_id>", methods=["DELETE"]
)
@login_required
def delete_item(tightening_id: int):
    item = _item_for_user(tightening_id)
    if not item:
        return jsonify({"error": "Voiceover not found."}), 404
    if item.status not in TERMINAL_STATUSES:
        return jsonify({"error": "Wait for processing to finish before deleting."}), 409
    paths = [item.original_storage_path, item.output_storage_path]
    try:
        db.session.delete(item)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Could not delete this voiceover."}), 500
    for storage_path in paths:
        delete_storage_file(storage_path)
    return jsonify({"ok": True})


@voiceover_tightener_bp.route(
    "/voiceover-tightener/items/<int:tightening_id>/original"
)
@login_required
def original_audio(tightening_id: int):
    item = _item_for_user(tightening_id)
    if not item:
        abort(404)
    return _send_stored_audio(item, item.original_storage_path)


@voiceover_tightener_bp.route(
    "/voiceover-tightener/items/<int:tightening_id>/output"
)
@login_required
def output_audio(tightening_id: int):
    item = _item_for_user(tightening_id)
    if not item or item.status != "completed":
        abort(404)
    return _send_stored_audio(item, item.output_storage_path)


@voiceover_tightener_bp.route(
    "/voiceover-tightener/items/<int:tightening_id>/download"
)
@login_required
def download_output(tightening_id: int):
    item = _item_for_user(tightening_id)
    if not item or item.status != "completed" or not item.output_storage_path:
        abort(404)
    try:
        file_path = resolve_storage_path(item.output_storage_path)
    except ValueError:
        abort(404)
    if not file_path.is_file():
        abort(404)
    stem = secure_filename(Path(item.original_filename).stem) or f"voiceover_{item.id}"
    return send_file(
        file_path,
        mimetype="audio/mpeg",
        as_attachment=True,
        download_name=f"{stem}_tightened.mp3",
        conditional=True,
    )
