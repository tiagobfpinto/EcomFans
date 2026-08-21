from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename

from db import VoiceoverTightening, db
from media_storage import (
    get_media_root,
    prepare_voiceover_tightening_directory,
    resolve_storage_path,
)
from voiceover_processing import process_voiceover


def _positive_config(name: str, default: int) -> int:
    try:
        return max(1, int(current_app.config.get(name, default)))
    except (TypeError, ValueError):
        return default


def run_voiceover_tightening_job(
    job_id: int, user_id: int | None, payload: dict
) -> dict:
    tightening_id = int(payload.get("tightening_id") or 0)
    if not user_id or not tightening_id:
        raise RuntimeError("Voiceover job payload is incomplete.")
    item = VoiceoverTightening.query.filter_by(
        id=tightening_id, user_id=user_id
    ).first()
    if not item:
        raise RuntimeError("Voiceover tightening record no longer exists.")
    item.status = "processing"
    item.error = None
    db.session.commit()

    temp_path = None
    output_path = None
    completed = False
    try:
        source_path = resolve_storage_path(item.original_storage_path)
        if not source_path.is_file():
            raise RuntimeError("The original MP3 is no longer available.")
        settings = json.loads(item.settings_json)
        output_dir = prepare_voiceover_tightening_directory(user_id, item.id)
        safe_stem = (
            secure_filename(Path(item.original_filename).stem)
            or f"voiceover_{item.id}"
        )
        output_path = output_dir / f"{safe_stem}_tightened.mp3"
        temp_path = output_dir / (
            f".{safe_stem}.{os.getpid()}.{secrets.token_hex(4)}.tmp.mp3"
        )
        result = process_voiceover(
            str(source_path),
            str(temp_path),
            item.preset,
            settings,
            max_duration_seconds=_positive_config(
                "VOICEOVER_TIGHTENER_MAX_DURATION_SECONDS", 3600
            ),
            timeout_seconds=_positive_config(
                "VOICEOVER_TIGHTENER_FFMPEG_TIMEOUT_SECONDS", 1800
            ),
        )
        current = VoiceoverTightening.query.filter_by(
            id=tightening_id, user_id=user_id
        ).first()
        if not current:
            raise RuntimeError("Voiceover was removed during processing.")
        os.replace(temp_path, output_path)
        relative_path = output_path.resolve().relative_to(
            get_media_root().resolve()
        ).as_posix()
        current.status = "completed"
        current.output_storage_path = relative_path
        current.output_file_size_bytes = output_path.stat().st_size
        current.original_duration_ms = result["original_duration_ms"]
        current.output_duration_ms = result["output_duration_ms"]
        current.removed_duration_ms = result["removed_duration_ms"]
        current.pauses_shortened = result["pauses_shortened"]
        current.overlaps_applied = result["overlaps_applied"]
        current.warnings_json = json.dumps(result["warnings"])
        current.error = None
        current.finished_at = datetime.now(timezone.utc)
        db.session.commit()
        completed = True
        return {"tightening_id": current.id, **result}
    except Exception as exc:
        db.session.rollback()
        if output_path and not completed:
            try:
                output_path.unlink()
            except FileNotFoundError:
                pass
        current = VoiceoverTightening.query.filter_by(
            id=tightening_id, user_id=user_id
        ).first()
        if current:
            current.status = "failed"
            current.error = (str(exc).strip() or "Voiceover processing failed.")[:4000]
            current.finished_at = datetime.now(timezone.utc)
            db.session.commit()
        raise
    finally:
        if temp_path:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
