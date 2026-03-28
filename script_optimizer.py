import mimetypes
import os
import tempfile
import uuid

from flask import Blueprint, current_app, jsonify, render_template, request, session

from auth import login_required
from billing_service import get_effective_api_key
from db import User, db
from worker_queue import enqueue_worker_job, get_worker_job_for_user, serialize_worker_job
from worker_tasks import JOB_TYPE_SCRIPT_TRANSCRIBE

script_optimizer_bp = Blueprint("script_optimizer", __name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
WORKER_UPLOAD_SUBDIR = "worker_uploads"
TRANSCRIBE_JOB_QUEUE = "default"
TRANSCRIBE_JOB_MAX_ATTEMPTS = 2


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
    return render_template(
        "script_optimizer.html",
        has_openai_key=bool(openai_key),
        max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
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
        if file_size > MAX_UPLOAD_BYTES:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            return jsonify(
                {
                    "error": (
                        f"Video is too large. Maximum supported size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
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
