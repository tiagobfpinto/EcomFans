import mimetypes
import os
import tempfile
import uuid

from flask import Blueprint, current_app, jsonify, render_template, request, session

from auth import login_required
from billing_service import get_effective_api_key
from db import SavedScript, User, db
from media_storage import (
    delete_storage_file,
    prepare_prompt_thumbnail_image,
    save_saved_script_thumbnail,
)
from worker_queue import enqueue_worker_job, get_worker_job_for_user, serialize_worker_job
from worker_tasks import JOB_TYPE_SCRIPT_TRANSCRIBE

script_optimizer_bp = Blueprint("script_optimizer", __name__)

DEFAULT_MAX_UPLOAD_MB = 500
MAX_UPLOAD_BYTES = DEFAULT_MAX_UPLOAD_MB * 1024 * 1024
MAX_THUMBNAIL_BYTES = 8 * 1024 * 1024
MAX_TRANSCRIPT_CHARS = 100_000
WORKER_UPLOAD_SUBDIR = "worker_uploads"
TRANSCRIBE_JOB_QUEUE = "default"
TRANSCRIBE_JOB_MAX_ATTEMPTS = 2


def _max_upload_bytes() -> int:
    return int(current_app.config.get("SCRIPT_TRANSCRIBE_MAX_UPLOAD_BYTES", MAX_UPLOAD_BYTES))


def _serialize_saved_script(script: SavedScript) -> dict:
    return {
        "id": script.id,
        "title": script.title,
        "transcript": script.transcript,
        "source_filename": script.source_filename,
        "has_thumbnail": bool(script.thumbnail_storage_path),
        "thumbnail_url": (
            f"/media/script-thumbnails/{script.id}"
            if script.thumbnail_storage_path
            else None
        ),
        "created_at": script.created_at.isoformat() if script.created_at else None,
    }


def _get_user():
    return db.session.get(User, session["user_id"])


def _worker_upload_root() -> str:
    configured = (os.getenv("WORKER_UPLOAD_ROOT") or "").strip()
    if configured:
        return os.path.abspath(configured)
    return os.path.abspath(os.path.join(current_app.instance_path, WORKER_UPLOAD_SUBDIR))


def _persist_upload_for_worker(video_file, original_name: str) -> tuple[str, int]:
    suffix = os.path.splitext(original_name)[1] or ".mp4"
    upload_root = _worker_upload_root()
    os.makedirs(upload_root, exist_ok=True)

    temp_path = None
    with tempfile.NamedTemporaryFile(
        prefix=f"script_optimizer_{uuid.uuid4().hex[:8]}_",
        suffix=suffix,
        dir=upload_root,
        delete=False,
    ) as tmp:
        temp_path = tmp.name

    try:
        video_file.save(temp_path)
        file_size = os.path.getsize(temp_path)
    except Exception:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise

    return temp_path, file_size


@script_optimizer_bp.route("/script-optimizer")
@login_required
def script_optimizer_page():
    user = _get_user()
    openai_key = get_effective_api_key(user.id, "openai")
    saved_scripts = (
        SavedScript.query.filter_by(user_id=user.id)
        .order_by(SavedScript.created_at.desc())
        .all()
    )
    return render_template(
        "script_optimizer.html",
        has_openai_key=bool(openai_key),
        max_upload_mb=_max_upload_bytes() // (1024 * 1024),
        saved_scripts=[_serialize_saved_script(s) for s in saved_scripts],
    )


@script_optimizer_bp.route("/script-optimizer/transcribe", methods=["POST"])
@login_required
def transcribe_video():
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

    try:
        temp_path, file_size = _persist_upload_for_worker(video_file, original_name)
        if file_size <= 0:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            return jsonify({"error": "Uploaded file is empty."}), 400
        max_upload_bytes = _max_upload_bytes()
        if file_size > max_upload_bytes:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            return jsonify(
                {
                    "error": (
                        f"Video is too large. Maximum supported size is {max_upload_bytes // (1024 * 1024)} MB."
                    )
                }
            ), 400

        job = enqueue_worker_job(
            user_id=user.id,
            job_type=JOB_TYPE_SCRIPT_TRANSCRIBE,
            queue_name=TRANSCRIBE_JOB_QUEUE,
            max_attempts=TRANSCRIBE_JOB_MAX_ATTEMPTS,
            payload={
                "file_path": temp_path,
                "mime_type": detected_mime,
                "original_name": original_name,
                "file_size_bytes": file_size,
            },
        )
        return jsonify(
            {
                "job_id": job.id,
                "status": job.status,
                "status_url": f"/script-optimizer/transcribe/jobs/{job.id}",
                "job": serialize_worker_job(job),
            }
        ), 202
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@script_optimizer_bp.route("/script-optimizer/transcribe/jobs/<int:job_id>", methods=["GET"])
@login_required
def transcribe_video_job_status(job_id: int):
    job = get_worker_job_for_user(job_id, session["user_id"])
    if not job or job.job_type != JOB_TYPE_SCRIPT_TRANSCRIBE:
        return jsonify({"error": "Job not found."}), 404

    serialized = serialize_worker_job(job)
    response = {
        "job_id": serialized["id"],
        "status": serialized["status"],
        "attempts": serialized["attempts"],
        "max_attempts": serialized["max_attempts"],
        "error": serialized["error"],
        "created_at": serialized["created_at"],
        "started_at": serialized["started_at"],
        "finished_at": serialized["finished_at"],
    }

    if serialized["status"] == "completed":
        response["text"] = (serialized.get("result") or {}).get("text", "")
    elif serialized["status"] == "queued":
        response["message"] = "Queued for background transcription."
    elif serialized["status"] == "running":
        response["message"] = "Transcribing in background."

    return jsonify(response)


@script_optimizer_bp.route("/script-optimizer/scripts", methods=["GET"])
@login_required
def list_saved_scripts():
    scripts = (
        SavedScript.query.filter_by(user_id=session["user_id"])
        .order_by(SavedScript.created_at.desc())
        .all()
    )
    return jsonify({"scripts": [_serialize_saved_script(s) for s in scripts]})


@script_optimizer_bp.route("/script-optimizer/scripts", methods=["POST"])
@login_required
def save_script():
    user = _get_user()

    transcript = (request.form.get("transcript") or "").strip()
    if not transcript:
        return jsonify({"error": "Cannot save an empty script."}), 400
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        return jsonify({"error": "Script is too long to save."}), 400

    title = (request.form.get("title") or "").strip()
    if not title:
        # Derive a friendly title from the first line of the transcript.
        first_line = transcript.splitlines()[0].strip() if transcript else ""
        title = (first_line[:80] or "Untitled script")
    title = title[:200]

    source_filename = (request.form.get("source_filename") or "").strip()[:255] or None

    script = SavedScript(
        user_id=user.id,
        title=title,
        transcript=transcript,
        source_filename=source_filename,
    )
    db.session.add(script)
    db.session.flush()  # assign script.id before storing the thumbnail

    thumbnail_file = request.files.get("thumbnail")
    if thumbnail_file and thumbnail_file.filename:
        raw = thumbnail_file.read(MAX_THUMBNAIL_BYTES + 1)
        if len(raw) > MAX_THUMBNAIL_BYTES:
            db.session.rollback()
            return jsonify({"error": "Thumbnail image is too large."}), 400
        if raw:
            try:
                processed, mime_type, _w, _h = prepare_prompt_thumbnail_image(raw)
                storage_path = save_saved_script_thumbnail(
                    user.id, script.id, mime_type, processed
                )
                script.thumbnail_storage_path = storage_path
                script.thumbnail_mime_type = mime_type
            except (ValueError, RuntimeError):
                # A bad/undecodable frame shouldn't block saving the script itself.
                script.thumbnail_storage_path = None
                script.thumbnail_mime_type = None

    db.session.commit()
    return jsonify({"script": _serialize_saved_script(script)}), 201


@script_optimizer_bp.route("/script-optimizer/scripts/<int:script_id>", methods=["DELETE"])
@login_required
def delete_saved_script(script_id: int):
    script = SavedScript.query.filter_by(
        id=script_id, user_id=session["user_id"]
    ).first()
    if not script:
        return jsonify({"error": "Script not found."}), 404

    delete_storage_file(script.thumbnail_storage_path)
    db.session.delete(script)
    db.session.commit()
    return jsonify({"ok": True})
