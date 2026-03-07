import mimetypes
import os
import tempfile

from flask import Blueprint, jsonify, render_template, request, session

from ai_service import openai_transcribe_file
from auth import login_required
from billing_service import get_effective_api_key
from db import User, db

script_optimizer_bp = Blueprint("script_optimizer", __name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _get_user():
    return db.session.get(User, session["user_id"])


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

    suffix = os.path.splitext(original_name)[1] or ".mp4"
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(prefix="script_optimizer_", suffix=suffix, delete=False) as tmp:
            temp_path = tmp.name
        video_file.save(temp_path)

        file_size = os.path.getsize(temp_path)
        if file_size <= 0:
            return jsonify({"error": "Uploaded file is empty."}), 400
        if file_size > MAX_UPLOAD_BYTES:
            return jsonify(
                {
                    "error": (
                        f"Video is too large. Maximum supported size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
                    )
                }
            ), 400

        transcript = openai_transcribe_file(
            api_key=openai_key,
            file_path=temp_path,
            mime_type=detected_mime,
        )

        return jsonify({"text": transcript})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
