from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from dlqueue import download as dlqueue_download

from ai_service import (
    gemini_generate_image_with_meta,
    gemini_vision_with_meta,
    openai_chat_with_meta,
    openai_generate_image_with_meta,
    openai_transcribe_file_with_meta,
)
from billing_service import (
    consume_credits,
    get_effective_api_key,
    record_api_request_event,
)
from db import (
    ApiRequestEvent,
    AvatarBatch,
    AvatarResult,
    BrandDNAAnalysis,
    Competitor,
    CompetitorAd,
    CreativeBatch,
    CreativeInspiration,
    CreativeInspirationAnalysis,
    CreativeResult,
    ImageGeneration,
    Product,
    ProductImage,
    SocialDownload,
    User,
    WorkerJob,
    db,
)
from media_storage import (
    get_media_root,
    get_image_payload,
    prepare_social_download_directory,
    resolve_storage_path,
    save_ai_generation_image,
    save_avatar_result_after_image,
    save_avatar_result_before_image,
    save_creative_result_image,
    save_lacy_result_image,
    save_product_image,
)
from worker_queue import (
    JOB_STATUS_FAILED,
    claim_next_worker_job,
    complete_worker_job,
    fail_worker_job,
    job_payload,
    requeue_stale_running_jobs,
)
from worker_tasks import (
    JOB_TYPE_AI_IMAGE_GENERATE,
    JOB_TYPE_AVATARS_GENERATE,
    JOB_TYPE_BRAND_DNA_ANALYZE,
    JOB_TYPE_COMPETITOR_AD_ANALYZE,
    JOB_TYPE_COMPETITOR_AD_TRANSCRIBE,
    JOB_TYPE_CREATIVES_GENERATE,
    JOB_TYPE_LACY_GENERATE,
    JOB_TYPE_SOCIAL_DOWNLOAD,
    JOB_TYPE_SCRIPT_TRANSCRIBE,
    JOB_TYPE_VOICEOVER_TIGHTEN,
)
from voiceover_worker import run_voiceover_tightening_job


# OpenAI accepts transcription uploads up to 25 MB. Keep a little headroom for
# provider-side size accounting and turn larger/unsupported media into compact,
# speech-optimised MP3 chunks before uploading them one at a time.
_OPENAI_TRANSCRIPTION_DIRECT_MAX_BYTES = 24_000_000
_TRANSCRIPTION_CHUNK_SECONDS = 20 * 60
_TRANSCRIPTION_AUDIO_BITRATE = "64k"
_DIRECT_TRANSCRIPTION_EXTENSIONS = {
    ".m4a",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".wav",
    ".webm",
}

# Internal system prompt used for inspiration analysis in ai_creatives worker
_CREATIVES_ANALYSIS_SYSTEM_PROMPT = (
    "You are an expert ad creative director. "
    "Output ONLY the requested text, no commentary, no preamble, no markdown."
)
_CREATIVES_ANALYSIS_MEDIA_RESOLUTION = "MEDIUM"

# Lazy-load competitor prompts to avoid circular import at module level
def _competitor_prompts():
    from prompts_config import (
        PROMPT_COMPETITOR_ANALYSIS,
        PROMPT_COMPETITOR_GENERATION,
        SYSTEM_PROMPT_COMPETITOR_MODE,
    )
    return PROMPT_COMPETITOR_ANALYSIS, PROMPT_COMPETITOR_GENERATION, SYSTEM_PROMPT_COMPETITOR_MODE


def _worker_upload_root(app) -> str:
    configured = (os.getenv("WORKER_UPLOAD_ROOT") or "").strip()
    if configured:
        return os.path.abspath(configured)
    return os.path.abspath(os.path.join(app.instance_path, "worker_uploads"))


def _is_subpath(path: str, parent: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(parent)]) == os.path.abspath(parent)
    except ValueError:
        return False


def _cleanup_job_artifacts(app, job_type: str, payload: dict[str, Any]) -> None:
    if job_type != JOB_TYPE_SCRIPT_TRANSCRIBE:
        return
    file_path = (payload.get("file_path") or "").strip()
    if not file_path:
        return

    root = _worker_upload_root(app)
    if not _is_subpath(file_path, root):
        return
    if not os.path.exists(file_path):
        return
    try:
        os.remove(file_path)
    except OSError:
        pass


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _resolve_ffmpeg_executable() -> str:
    configured = (os.getenv("FFMPEG_BINARY") or "").strip()
    if configured:
        if os.path.isfile(configured):
            return os.path.abspath(configured)
        discovered = shutil.which(configured)
        if discovered:
            return discovered
        raise RuntimeError("FFMPEG_BINARY does not point to an available executable.")

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError) as exc:
        discovered = shutil.which("ffmpeg")
        if discovered:
            return discovered
        raise RuntimeError(
            "Large-file transcription requires FFmpeg. Install the application "
            "requirements or configure FFMPEG_BINARY."
        ) from exc


def _split_media_for_transcription(file_path: str, output_dir: str) -> list[str]:
    """Extract mono speech audio into provider-safe, fixed-duration MP3 files."""
    chunk_seconds = _positive_int_env(
        "OPENAI_TRANSCRIPTION_CHUNK_SECONDS", _TRANSCRIPTION_CHUNK_SECONDS
    )
    output_pattern = os.path.join(output_dir, "chunk_%04d.mp3")
    command = [
        _resolve_ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        file_path,
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        _TRANSCRIPTION_AUDIO_BITRATE,
        "-f",
        "segment",
        "-segment_time",
        str(chunk_seconds),
        "-reset_timestamps",
        "1",
        output_pattern,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_positive_int_env("TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS", 3600),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Preparing the audio for transcription timed out.") from exc
    except OSError as exc:
        raise RuntimeError("FFmpeg could not be started for large-file transcription.") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        message = detail[-1] if detail else "The media file may not contain a readable audio track."
        raise RuntimeError(f"Could not prepare audio for transcription: {message[:500]}")

    chunks = sorted(
        os.path.join(output_dir, name)
        for name in os.listdir(output_dir)
        if name.startswith("chunk_") and name.endswith(".mp3")
    )
    if not chunks:
        raise RuntimeError("The uploaded media does not contain a readable audio track.")

    for chunk_path in chunks:
        chunk_size = os.path.getsize(chunk_path)
        if chunk_size <= 0:
            raise RuntimeError("Audio preparation produced an empty transcription chunk.")
        if chunk_size > _OPENAI_TRANSCRIPTION_DIRECT_MAX_BYTES:
            raise RuntimeError(
                "An audio chunk is still too large for transcription. Reduce "
                "OPENAI_TRANSCRIPTION_CHUNK_SECONDS and try again."
            )
    return chunks


def _sum_meta_values(items: list[dict], key: str) -> int | float | None:
    values = [item.get(key) for item in items if item.get(key) is not None]
    if not values:
        return None
    return sum(values)


def _aggregate_transcription_meta(items: list[dict], *, transcoded: bool) -> dict:
    base = dict(items[0]) if items else {}
    for key in (
        "latency_ms",
        "input_tokens",
        "cached_tokens",
        "output_tokens",
        "total_tokens",
        "estimated_cost_usd",
        "estimated_cost_eur",
    ):
        base[key] = _sum_meta_values(items, key)
    base["transcription_chunk_count"] = len(items)
    base["source_transcoded"] = transcoded
    return base


def _transcribe_media_file_with_meta(
    *, api_key: str, file_path: str, mime_type: str | None = None
) -> tuple[str, dict]:
    """Transcribe directly when safe, otherwise transcode and send chunks serially."""
    extension = os.path.splitext(file_path)[1].lower()
    file_size = os.path.getsize(file_path)
    can_send_directly = (
        file_size <= _OPENAI_TRANSCRIPTION_DIRECT_MAX_BYTES
        and extension in _DIRECT_TRANSCRIPTION_EXTENSIONS
    )
    if can_send_directly:
        text, meta = openai_transcribe_file_with_meta(
            api_key=api_key,
            file_path=file_path,
            mime_type=mime_type,
        )
        return text, _aggregate_transcription_meta([meta], transcoded=False)

    source_dir = os.path.dirname(os.path.abspath(file_path))
    with tempfile.TemporaryDirectory(prefix="transcription_chunks_", dir=source_dir) as chunk_dir:
        chunk_paths = _split_media_for_transcription(file_path, chunk_dir)
        transcripts: list[str] = []
        metadata_items: list[dict] = []
        for index, chunk_path in enumerate(chunk_paths, start=1):
            try:
                text, meta = openai_transcribe_file_with_meta(
                    api_key=api_key,
                    file_path=chunk_path,
                    mime_type="audio/mpeg",
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Transcription failed on audio chunk {index} of {len(chunk_paths)}: {exc}"
                ) from exc
            transcripts.append(text.strip())
            metadata_items.append(meta)

    transcript = "\n\n".join(text for text in transcripts if text)
    if not transcript:
        raise RuntimeError("OpenAI transcription returned no text.")
    return transcript, _aggregate_transcription_meta(metadata_items, transcoded=True)


def _sha256_file(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as media_file:
        for block in iter(lambda: media_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_script_transcription(job_id: int, user_id: int | None, payload: dict[str, Any]) -> dict[str, Any]:
    file_path = (payload.get("file_path") or "").strip()
    mime_type = (payload.get("mime_type") or "").strip() or "application/octet-stream"
    original_name = (payload.get("original_name") or "").strip()
    file_size = int(payload.get("file_size_bytes") or 0)

    if not user_id:
        raise RuntimeError("Job has no user association.")
    if not file_path:
        raise RuntimeError("Transcription job payload is missing file_path.")
    if not os.path.exists(file_path):
        raise RuntimeError("Uploaded media file is no longer available for processing.")

    user = db.session.get(User, user_id)
    if not user:
        raise RuntimeError("User not found for transcription job.")
    openai_key = get_effective_api_key(user.id, "openai")
    if not openai_key:
        raise RuntimeError("OpenAI API key is not configured on the platform.")

    # Compute file hash for deduplication — avoid re-transcribing the same file.
    file_sha256 = _sha256_file(file_path)

    dedup_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent_events = ApiRequestEvent.query.filter(
        ApiRequestEvent.user_id == user_id,
        ApiRequestEvent.operation == "transcription",
        ApiRequestEvent.status == "completed",
        ApiRequestEvent.created_at >= dedup_cutoff,
    ).all()
    for prev_event in recent_events:
        prev_meta = json.loads(prev_event.metadata_json or "{}")
        if prev_meta.get("file_sha256") == file_sha256:
            prev_job_id = prev_meta.get("job_id")
            if prev_job_id:
                prev_job = db.session.get(WorkerJob, prev_job_id)
                if prev_job and prev_job.result_json:
                    cached_result = json.loads(prev_job.result_json)
                    if cached_result.get("text"):
                        return cached_result

    try:
        transcript, meta = _transcribe_media_file_with_meta(
            api_key=openai_key,
            file_path=file_path,
            mime_type=mime_type,
        )
        record_api_request_event(
            user_id=user.id,
            feature="script_optimizer",
            provider="openai",
            operation="transcription",
            meta=meta,
            metadata={
                "job_id": job_id,
                "file_size_bytes": file_size,
                "mime_type": mime_type,
                "original_name": original_name,
                "file_sha256": file_sha256,
                "transcription_chunk_count": meta.get("transcription_chunk_count", 1),
                "source_transcoded": bool(meta.get("source_transcoded")),
            },
        )
        return {"text": transcript}
    except Exception as exc:
        record_api_request_event(
            user_id=user.id,
            feature="script_optimizer",
            provider="openai",
            operation="transcription",
            status="failed",
            error_message=str(exc),
            metadata={
                "job_id": job_id,
                "file_size_bytes": file_size,
                "mime_type": mime_type,
                "original_name": original_name,
                "file_sha256": file_sha256,
            },
        )
        raise


def _run_ai_image_generate(job_id: int, user_id: int | None, payload: dict[str, Any]) -> dict[str, Any]:
    if not user_id:
        raise RuntimeError("Job has no user association.")

    user = db.session.get(User, user_id)
    if not user:
        raise RuntimeError("User not found for ai_image job.")

    gemini_key = get_effective_api_key(user.id, "gemini")
    if not gemini_key:
        raise RuntimeError("Gemini API key is not configured on the platform.")

    prompt_id = payload.get("prompt_id")
    generation_ids = payload.get("generation_ids", [])
    gemini_model = payload.get("gemini_model")
    traffic_type = payload.get("traffic_type", "standard")
    uploaded_images = payload.get("uploaded_images", [])
    variation_prompts = payload.get("variation_prompts", [])
    credit_quote = payload.get("credit_quote", {})

    total_credits = int(credit_quote.get("credits", 0))
    total_units = max(1, len(generation_ids))
    base_variation_credits = total_credits // total_units
    extra_variation_slots = total_credits % total_units
    variation_estimated_cost = float(credit_quote.get("estimated_cost_usd", 0)) / total_units if total_units else 0

    completed_ids = []
    failed_ids = []

    for i, generation_id in enumerate(generation_ids):
        generation = db.session.get(ImageGeneration, generation_id)
        if not generation:
            failed_ids.append(generation_id)
            continue

        variation_prompt = variation_prompts[i] if i < len(variation_prompts) else ""
        variation_credit_cost = base_variation_credits + (1 if i < extra_variation_slots else 0)

        try:
            image_b64, gemini_meta = gemini_generate_image_with_meta(
                gemini_key,
                variation_prompt,
                uploaded_images,
                model=gemini_model,
                traffic_type=traffic_type,
            )
            record_api_request_event(
                user_id=user_id,
                feature="ai_image",
                provider="gemini",
                operation="image_generation",
                meta=gemini_meta,
                metadata={"variation_index": generation.variation_index, "prompt_id": prompt_id, "job_id": job_id},
            )

            charged, _charged_user, charge_payload = consume_credits(
                user_id,
                variation_credit_cost,
                feature="ai_image",
                provider="gemini",
                units=1,
                estimated_cost_usd=variation_estimated_cost,
                metadata={
                    "variation_index": generation.variation_index,
                    "traffic_type": traffic_type,
                    "gemini_model": gemini_model,
                    "job_id": job_id,
                },
            )
            if not charged:
                generation.status = "failed"
                generation.error_message = charge_payload["error"]
                db.session.commit()
                failed_ids.append(generation_id)
                continue

            try:
                image_bytes = base64.b64decode(image_b64, validate=True)
                generation.storage_path = save_ai_generation_image(
                    user_id,
                    prompt_id,
                    generation_id,
                    "image/png",
                    image_bytes,
                )
                generation.image_data = None
            except Exception:
                generation.storage_path = None
                generation.image_data = image_b64

            generation.status = "completed"
            db.session.commit()
            completed_ids.append(generation_id)

        except Exception as exc:
            generation = db.session.get(ImageGeneration, generation_id)
            if generation:
                generation.status = "failed"
                generation.error_message = str(exc)
                db.session.commit()
            record_api_request_event(
                user_id=user_id,
                feature="ai_image",
                provider="gemini",
                operation="image_generation",
                status="failed",
                error_message=str(exc),
                metadata={"variation_index": i + 1, "prompt_id": prompt_id, "job_id": job_id},
            )
            db.session.commit()
            failed_ids.append(generation_id)

    return {"prompt_id": prompt_id, "generation_ids": generation_ids}


def _run_avatars_generate(job_id: int, user_id: int | None, payload: dict[str, Any]) -> dict[str, Any]:
    if not user_id:
        raise RuntimeError("Job has no user association.")

    user = db.session.get(User, user_id)
    if not user:
        raise RuntimeError("User not found for avatars job.")

    gemini_key = get_effective_api_key(user.id, "gemini")
    if not gemini_key:
        raise RuntimeError("Gemini API key is not configured on the platform.")

    batch_id = payload.get("batch_id")
    product_id = payload.get("product_id")
    result_ids_with_personas = payload.get("result_ids_with_personas", [])
    gemini_model = payload.get("gemini_model")
    traffic_type = payload.get("traffic_type", "standard")
    charge_per_pair = int(payload.get("charge_per_pair", 1))
    estimated_per_pair = float(payload.get("estimated_per_pair", 0))

    batch = db.session.get(AvatarBatch, batch_id)
    if not batch:
        raise RuntimeError(f"AvatarBatch {batch_id} not found.")

    product = db.session.get(Product, product_id)
    if not product:
        batch.status = "failed"
        db.session.commit()
        raise RuntimeError(f"Product {product_id} not found.")

    product_images = []
    for img in product.images[:3]:
        img_payload = get_image_payload(img.storage_path, img.mime_type, img.image_data)
        if img_payload:
            product_images.append(img_payload)

    for item in result_ids_with_personas:
        result_id = item.get("result_id")
        persona = item.get("persona", "")

        result = db.session.get(AvatarResult, result_id)
        if not result:
            continue

        try:
            before_prompt = (
                f"A realistic full body photo portrait of a {persona} "
                f"who is {batch.characteristic}. Standing pose, neutral "
                f"background, casual everyday clothing. Natural lighting, "
                f"photorealistic style. No text in image."
            )
            before_b64, before_meta = gemini_generate_image_with_meta(
                gemini_key,
                before_prompt,
                model=gemini_model,
                traffic_type=traffic_type,
            )
            record_api_request_event(
                user_id=user_id,
                feature="avatars",
                provider="gemini",
                operation="before_image_generation",
                meta=before_meta,
                metadata={"persona": persona, "batch_id": batch_id, "job_id": job_id},
            )

            after_context = [
                {"mime_type": "image/png", "data": before_b64},
            ] + product_images

            after_prompt = (
                f"This is a photo of a {persona}. Generate a new "
                f"realistic photo of this EXACT SAME person from the "
                f"first reference photo, but now they look fit, confident, "
                f"and transformed after using the product "
                f"'{product.name}'. {product.context}. "
                f"The other reference photos are of the product itself. "
                f"Please feature this exact product naturally in the new photo. "
                f"Same person, same face, same identity. "
                f"Bright, positive lighting, photorealistic style. "
                f"No text in image."
            )
            after_b64, after_meta = gemini_generate_image_with_meta(
                gemini_key,
                after_prompt,
                after_context,
                model=gemini_model,
                traffic_type=traffic_type,
            )
            record_api_request_event(
                user_id=user_id,
                feature="avatars",
                provider="gemini",
                operation="after_image_generation",
                meta=after_meta,
                metadata={"persona": persona, "batch_id": batch_id, "job_id": job_id},
            )

            charged, _charged_user, charge_payload = consume_credits(
                user_id,
                charge_per_pair,
                feature="avatars",
                provider="gemini",
                units=1,
                estimated_cost_usd=estimated_per_pair,
                metadata={
                    "persona": persona,
                    "product_id": product_id,
                    "traffic_type": traffic_type,
                    "gemini_model": gemini_model,
                    "job_id": job_id,
                },
            )
            if not charged:
                result = db.session.get(AvatarResult, result_id)
                if result:
                    result.status = "failed"
                    result.error_message = charge_payload["error"]
                    db.session.commit()
                continue

            result = db.session.get(AvatarResult, result_id)
            if not result:
                continue

            try:
                before_bytes = base64.b64decode(before_b64, validate=True)
                after_bytes = base64.b64decode(after_b64, validate=True)
                result.before_storage_path = save_avatar_result_before_image(
                    user_id,
                    batch_id,
                    result_id,
                    "image/png",
                    before_bytes,
                )
                result.after_storage_path = save_avatar_result_after_image(
                    user_id,
                    batch_id,
                    result_id,
                    "image/png",
                    after_bytes,
                )
                result.before_image = None
                result.after_image = None
            except Exception:
                result.before_storage_path = None
                result.after_storage_path = None
                result.before_image = before_b64
                result.after_image = after_b64

            result.status = "completed"
            db.session.commit()

        except Exception as exc:
            result = db.session.get(AvatarResult, result_id)
            if result:
                result.status = "failed"
                result.error_message = str(exc)
                db.session.commit()
            record_api_request_event(
                user_id=user_id,
                feature="avatars",
                provider="gemini",
                operation="avatar_generation_pipeline",
                status="failed",
                error_message=str(exc),
                metadata={"persona": persona, "batch_id": batch_id, "job_id": job_id},
            )
            db.session.commit()

    batch = db.session.get(AvatarBatch, batch_id)
    if batch:
        batch.status = "completed"
        db.session.commit()

    return {"batch_id": batch_id}


def _run_creatives_generate(job_id: int, user_id: int | None, payload: dict[str, Any]) -> dict[str, Any]:
    if not user_id:
        raise RuntimeError("Job has no user association.")

    user = db.session.get(User, user_id)
    if not user:
        raise RuntimeError("User not found for ai_creatives job.")

    gemini_key = get_effective_api_key(user.id, "gemini")
    openai_key = get_effective_api_key(user.id, "openai")

    batch_id = payload.get("batch_id")
    product_id = payload.get("product_id")
    provider = payload.get("provider", "gemini")
    base_prompt = payload.get("base_prompt", "")
    analysis_prompt = payload.get("analysis_prompt", "")
    gemini_model = payload.get("gemini_model")
    traffic_type = payload.get("traffic_type", "standard")
    prompt_only = bool(payload.get("prompt_only", False))
    result_ids_with_inspiration_ids = payload.get("result_ids_with_inspiration_ids", [])
    credits_per_inspiration = int(payload.get("credits_per_inspiration", 1))
    estimated_per_inspiration = float(payload.get("estimated_per_inspiration", 0))
    generation_mode = payload.get("generation_mode", "standard")
    awareness_level = payload.get("awareness_level", "solution_aware")
    platform = payload.get("platform", "meta_feed")

    if provider in ("gemini", "both") and not gemini_key:
        raise RuntimeError("Gemini API key is not configured on the platform.")
    if provider in ("openai", "both") and not openai_key:
        raise RuntimeError("OpenAI API key is not configured on the platform.")

    batch = db.session.get(CreativeBatch, batch_id)
    if not batch:
        raise RuntimeError(f"CreativeBatch {batch_id} not found.")

    product = db.session.get(Product, product_id)
    if not product:
        batch.status = "failed"
        db.session.commit()
        raise RuntimeError(f"Product {product_id} not found.")

    product_images = []
    for img in product.images[:3]:
        img_payload = get_image_payload(img.storage_path, img.mime_type, img.image_data)
        if img_payload:
            product_images.append(img_payload)

    for idx, item in enumerate(result_ids_with_inspiration_ids):
        result_id = item.get("result_id")
        inspiration_id = item.get("inspiration_id")

        result = db.session.get(CreativeResult, result_id)
        if not result:
            continue

        insp = db.session.get(CreativeInspiration, inspiration_id)
        if not insp:
            result.status = "failed"
            result.error_message = "Inspiration not found."
            db.session.commit()
            continue

        try:
            insp_payload = get_image_payload(
                insp.storage_path,
                insp.mime_type,
                insp.image_data,
            )
            if not insp_payload:
                raise RuntimeError("Inspiration image data is missing.")

            product_info = f"Product Name: {product.name}\nProduct Context: {product.context}"
            product_page_info = product.build_product_info()
            if product_page_info:
                product_info += f"\n{product_page_info}"

            # ── Competitor Inspiration: two-phase pipeline ────────────
            if generation_mode == "competitor_inspiration":
                P1_PROMPT, P2_PROMPT_TMPL, SYS_PROMPT = _competitor_prompts()

                # Phase 1 — analyse competitor ad image (image only, no product info)
                effective_analysis_provider = "gemini" if provider == "gemini" else "openai"
                if provider == "gemini":
                    phase1_parts = [
                        {"text": P1_PROMPT},
                        {"inlineData": {"mimeType": insp_payload["mime_type"], "data": insp_payload["data"]}},
                    ]
                    analysis_text, p1_meta = gemini_vision_with_meta(
                        gemini_key,
                        SYS_PROMPT,
                        phase1_parts,
                        max_tokens=1200,
                        temperature=0.7,
                        traffic_type=traffic_type,
                        media_resolution=_CREATIVES_ANALYSIS_MEDIA_RESOLUTION,
                    )
                else:
                    phase1_content = [
                        {"type": "text", "text": P1_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:{insp_payload['mime_type']};base64,{insp_payload['data']}"}},
                    ]
                    analysis_text, p1_meta = openai_chat_with_meta(
                        openai_key,
                        SYS_PROMPT,
                        phase1_content,
                        max_tokens=1200,
                        temperature=0.7,
                    )

                if p1_meta:
                    record_api_request_event(
                        user_id=user_id,
                        feature="ai_creatives",
                        provider=effective_analysis_provider,
                        operation="competitor_analysis",
                        meta=p1_meta,
                        metadata={"inspiration_id": inspiration_id, "batch_id": batch_id, "job_id": job_id},
                    )

                # Phase 2 — generate image prompt from analysis + product info + targeting
                p2_prompt = (
                    P2_PROMPT_TMPL
                    .replace("{{PHASE_1_ANALYSIS_OUTPUT}}", analysis_text)
                    .replace("{{USER_PRODUCT_INFO}}", product_page_info or product_info)
                    .replace("{{AWARENESS_LEVEL}}", awareness_level)
                    .replace("{{PLATFORM}}", platform)
                )

                if provider == "gemini":
                    final_prompt, p2_meta = gemini_vision_with_meta(
                        gemini_key,
                        SYS_PROMPT,
                        [{"text": p2_prompt}],
                        max_tokens=1500,
                        temperature=0.8,
                        traffic_type=traffic_type,
                    )
                else:
                    final_prompt, p2_meta = openai_chat_with_meta(
                        openai_key,
                        SYS_PROMPT,
                        p2_prompt,
                        max_tokens=1500,
                        temperature=0.8,
                    )

                if p2_meta:
                    record_api_request_event(
                        user_id=user_id,
                        feature="ai_creatives",
                        provider=effective_analysis_provider,
                        operation="competitor_concept",
                        meta=p2_meta,
                        metadata={"inspiration_id": inspiration_id, "batch_id": batch_id, "job_id": job_id},
                    )

                final_prompt = final_prompt.strip()
                result.generated_prompt = final_prompt

            else:
                # ── Standard / Winner Ad Variation ───────────────────
                full_analysis_prompt = (
                    f"{analysis_prompt}\n\n"
                    f"Make sure the prompt you generate is specifically for the following product:\n"
                    f"{product_info}"
                )

                effective_analysis_provider = "gemini" if provider == "gemini" else "openai"
                analysis_prompt_hash = hashlib.sha256(full_analysis_prompt.encode()).hexdigest()
                cache_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
                cached_analysis = CreativeInspirationAnalysis.query.filter_by(
                    inspiration_id=inspiration_id,
                    product_id=product_id,
                    provider=effective_analysis_provider,
                    prompt_hash=analysis_prompt_hash,
                ).filter(
                    CreativeInspirationAnalysis.created_at >= cache_cutoff
                ).first()

                if cached_analysis:
                    regen_prompt = cached_analysis.result_text
                    analysis_meta = {}
                elif provider == "gemini":
                    parts = [
                        {"text": full_analysis_prompt},
                        {"inlineData": {"mimeType": insp_payload["mime_type"], "data": insp_payload["data"]}},
                    ]
                    regen_prompt, analysis_meta = gemini_vision_with_meta(
                        gemini_key,
                        _CREATIVES_ANALYSIS_SYSTEM_PROMPT,
                        parts,
                        max_tokens=1200,
                        temperature=0.7,
                        traffic_type=traffic_type,
                        media_resolution=_CREATIVES_ANALYSIS_MEDIA_RESOLUTION,
                        max_token_budget=2400,
                    )
                    db.session.add(CreativeInspirationAnalysis(
                        inspiration_id=inspiration_id,
                        product_id=product_id,
                        provider=effective_analysis_provider,
                        prompt_hash=analysis_prompt_hash,
                        result_text=regen_prompt,
                    ))
                    db.session.flush()
                else:
                    user_content = [
                        {"type": "text", "text": full_analysis_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{insp_payload['mime_type']};base64,{insp_payload['data']}"}},
                    ]
                    regen_prompt, analysis_meta = openai_chat_with_meta(
                        openai_key,
                        _CREATIVES_ANALYSIS_SYSTEM_PROMPT,
                        user_content,
                        max_tokens=1200,
                        temperature=0.7,
                    )
                    db.session.add(CreativeInspirationAnalysis(
                        inspiration_id=inspiration_id,
                        product_id=product_id,
                        provider=effective_analysis_provider,
                        prompt_hash=analysis_prompt_hash,
                        result_text=regen_prompt,
                    ))
                    db.session.flush()

                if analysis_meta:
                    record_api_request_event(
                        user_id=user_id,
                        feature="ai_creatives",
                        provider=effective_analysis_provider,
                        operation="inspiration_analysis",
                        meta=analysis_meta,
                        metadata={
                            "inspiration_id": inspiration_id,
                            "batch_id": batch_id,
                            "job_id": job_id,
                            "prompt_only": bool(prompt_only),
                        },
                    )

                final_prompt = base_prompt + "\n\n" + regen_prompt
                result.generated_prompt = final_prompt

            generation_images = [
                {
                    "mime_type": insp_payload["mime_type"],
                    "data": insp_payload["data"],
                },
                *product_images,
            ]

            openai_image = None
            gemini_image = None

            if not prompt_only:
                if provider in ("openai", "both"):
                    openai_image, openai_meta = openai_generate_image_with_meta(
                        openai_key, final_prompt
                    )
                    record_api_request_event(
                        user_id=user_id,
                        feature="ai_creatives",
                        provider="openai",
                        operation="image_generation",
                        meta=openai_meta,
                        metadata={"inspiration_id": inspiration_id, "batch_id": batch_id, "job_id": job_id},
                    )

                if provider in ("gemini", "both"):
                    gemini_image, gemini_meta = gemini_generate_image_with_meta(
                        gemini_key,
                        final_prompt,
                        generation_images,
                        model=gemini_model,
                        traffic_type=traffic_type,
                    )
                    record_api_request_event(
                        user_id=user_id,
                        feature="ai_creatives",
                        provider="gemini",
                        operation="image_generation",
                        meta=gemini_meta,
                        metadata={"inspiration_id": inspiration_id, "batch_id": batch_id, "job_id": job_id},
                    )

            charged, _charged_user, charge_payload = consume_credits(
                user_id,
                credits_per_inspiration,
                feature="ai_creatives",
                provider=provider,
                units=1,
                estimated_cost_usd=estimated_per_inspiration,
                metadata={
                    "inspiration_id": inspiration_id,
                    "prompt_only": bool(prompt_only),
                    "product_id": product_id,
                    "credits_charged": credits_per_inspiration,
                    "traffic_type": traffic_type,
                    "gemini_model": gemini_model,
                    "job_id": job_id,
                },
            )
            if not charged:
                result = db.session.get(CreativeResult, result_id)
                if result:
                    result.status = "failed"
                    result.error_message = charge_payload["error"]
                    db.session.commit()
                continue

            result = db.session.get(CreativeResult, result_id)
            if not result:
                continue

            generated_image = gemini_image or openai_image
            if generated_image:
                try:
                    generated_bytes = base64.b64decode(generated_image, validate=True)
                    result.generated_storage_path = save_creative_result_image(
                        user_id,
                        batch_id,
                        result_id,
                        "image/png",
                        generated_bytes,
                    )
                    result.generated_image = None
                except Exception:
                    result.generated_storage_path = None
                    result.generated_image = generated_image
            else:
                result.generated_storage_path = None
                result.generated_image = None

            result.status = "completed"
            db.session.commit()

        except Exception as exc:
            result = db.session.get(CreativeResult, result_id)
            if result:
                result.status = "failed"
                result.error_message = str(exc)
                db.session.commit()
            record_api_request_event(
                user_id=user_id,
                feature="ai_creatives",
                provider="gemini" if provider == "gemini" else provider,
                operation="generation_pipeline",
                status="failed",
                error_message=str(exc),
                metadata={"inspiration_id": inspiration_id, "batch_id": batch_id, "job_id": job_id},
            )
            db.session.commit()

    batch = db.session.get(CreativeBatch, batch_id)
    if batch:
        batch.status = "completed"
        db.session.commit()

    return {"batch_id": batch_id}


def _run_brand_dna_analyze(
    job_id: int,
    user_id: int | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    1. Fetch brand homepage HTML
    2. Extract real CSS colors from <style> tags + linked stylesheets
    3. Collect candidate image URLs (no extension filter — handles CDN/Shopify URLs)
    4. Download up to 8 images; skip icons (<3 KB)
    5. Call GPT-4o vision with full page text + all images (indexed)
       — GPT returns brand info + image_index per product
    6. Merge CSS palette (authoritative) with AI palette (fallback)
    7. Create Product records and attach ProductImage from identified index
    8. Charge credits and persist
    """
    import re
    from collections import Counter
    from pathlib import Path as _Path
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    from scraper import _safe_get_bytes, _ensure_public_url

    if not user_id:
        raise RuntimeError("Job has no user association.")

    user = db.session.get(User, user_id)
    if not user:
        raise RuntimeError("User not found.")

    openai_key = get_effective_api_key(user.id, "openai")
    if not openai_key:
        raise RuntimeError("OpenAI API key is not configured on the platform.")

    analysis_id = payload.get("analysis_id")
    url = (payload.get("url") or "").strip()
    credit_cost = int(payload.get("credit_cost") or 5)

    analysis = db.session.get(BrandDNAAnalysis, analysis_id)
    if not analysis:
        raise RuntimeError(f"BrandDNAAnalysis {analysis_id} not found.")

    analysis.status = "running"
    db.session.commit()

    BROWSER_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

    try:
        # --- 1. Fetch HTML ---
        html_bytes, _, final_url = _safe_get_bytes(
            url,
            headers=BROWSER_HEADERS,
            timeout=20,
            max_bytes=3 * 1024 * 1024,
        )

        soup = BeautifulSoup(html_bytes.decode("utf-8", errors="ignore"), "html.parser")

        # --- 2. Extract CSS colors from inline <style> + linked stylesheets ---
        css_text = ""
        for style_tag in soup.find_all("style"):
            css_text += style_tag.get_text() + "\n"

        stylesheet_count = 0
        for link_tag in soup.find_all("link"):
            rel = link_tag.get("rel") or []
            if isinstance(rel, list):
                rel = " ".join(rel)
            if "stylesheet" not in rel.lower():
                continue
            href = link_tag.get("href")
            if not href or href.startswith("data:"):
                continue
            abs_href = urljoin(final_url, href)
            try:
                _ensure_public_url(abs_href)
                css_bytes, _, _ = _safe_get_bytes(
                    abs_href,
                    headers=BROWSER_HEADERS,
                    timeout=8,
                    max_bytes=500 * 1024,
                )
                css_text += css_bytes.decode("utf-8", errors="ignore") + "\n"
                stylesheet_count += 1
                if stylesheet_count >= 3:
                    break
            except Exception:
                continue

        # Parse 6-digit and 3-digit hex colors from CSS
        hex6 = re.findall(r"#([0-9a-fA-F]{6})\b", css_text)
        hex3 = re.findall(r"#([0-9a-fA-F]{3})\b", css_text)
        expanded3 = [c[0] * 2 + c[1] * 2 + c[2] * 2 for c in hex3]
        color_freq = Counter(h.upper() for h in hex6 + expanded3)

        css_palette: list[str] = []
        for hex_val, _ in color_freq.most_common(60):
            r, g, b = int(hex_val[0:2], 16), int(hex_val[2:4], 16), int(hex_val[4:6], 16)
            if r < 25 and g < 25 and b < 25:
                continue  # near black
            if r > 235 and g > 235 and b > 235:
                continue  # near white
            if max(abs(r - g), abs(g - b), abs(r - b)) < 15:
                continue  # pure gray
            css_palette.append(f"#{hex_val}")
            if len(css_palette) >= 7:
                break

        # --- 3. Collect candidate image URLs (no extension pre-filter) ---
        seen_imgs: set[str] = set()
        candidate_imgs: list[str] = []

        def _add_img(src: str | None) -> None:
            if not src or src.startswith("data:"):
                return
            abs_src = urljoin(final_url, src.strip())
            if abs_src not in seen_imgs:
                seen_imgs.add(abs_src)
                candidate_imgs.append(abs_src)

        for img_tag in soup.find_all("img"):
            for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                _add_img(img_tag.get(attr))
            # srcset: take the last (highest res) URL
            srcset = img_tag.get("srcset") or ""
            if srcset:
                parts = [p.strip().split(" ")[0] for p in srcset.split(",") if p.strip()]
                if parts:
                    _add_img(parts[-1])

        for source_tag in soup.find_all("source"):
            srcset = source_tag.get("srcset") or ""
            if srcset:
                parts = [p.strip().split(" ")[0] for p in srcset.split(",") if p.strip()]
                if parts:
                    _add_img(parts[-1])

        # --- 4. Download up to 8 images for vision (skip tiny icons) ---
        MIN_IMG_BYTES = 4 * 1024       # 4 KB minimum — skip tracking pixels / icons
        MAX_IMG_BYTES = 1500 * 1024    # 1.5 MB per image
        MAX_VISION_IMAGES = 8

        # downloaded_images: list of dicts with keys bytes, mime, url
        downloaded_images: list[dict] = []

        for img_url in candidate_imgs:
            if len(downloaded_images) >= MAX_VISION_IMAGES:
                break
            try:
                _ensure_public_url(img_url)
                img_bytes, content_type, _ = _safe_get_bytes(
                    img_url,
                    headers=BROWSER_HEADERS,
                    timeout=10,
                    max_bytes=MAX_IMG_BYTES,
                )
                if not img_bytes or len(img_bytes) < MIN_IMG_BYTES:
                    continue
                mime = content_type.split(";")[0].strip().lower()
                if not mime.startswith("image/"):
                    continue
                if mime not in ALLOWED_IMAGE_MIMES:
                    mime = "image/jpeg"
                downloaded_images.append({"bytes": img_bytes, "mime": mime, "url": img_url})
            except Exception:
                continue

        # --- 5. Call GPT-4o vision ---
        SYSTEM_PROMPT = (
            "You are a brand analyst. Extract structured brand information from the "
            "website content provided. Output ONLY a valid JSON object — no markdown, "
            "no commentary, no preamble."
        )

        # Build index summary for the prompt
        img_index_summary = ""
        for i, img in enumerate(downloaded_images):
            img_index_summary += f"  Image {i}: {img['url']}\n"

        USER_TEXT = (
            f"Website URL: {final_url}\n\n"
            f"Website text content:\n"
            + soup.get_text(separator=" ", strip=True)[:12000]
            + "\n\n"
            + (
                f"I'm also providing {len(downloaded_images)} images from the site, indexed 0–{len(downloaded_images) - 1}:\n"
                f"{img_index_summary}\n"
                if downloaded_images else ""
            )
            + "Extract the following and output ONLY a valid JSON object:\n"
            "1. brand_name (string)\n"
            "2. brand_description (1–2 sentences, max 300 chars)\n"
            "3. color_palette (array of 4–6 dominant brand HEX color codes — accent colours, "
            "   button colours, brand colours; avoid pure black/white/grays)\n"
            "4. products (array of up to 5 products found on the page, each with: "
            "   name (string), description (max 200 chars), price (string or null), "
            "   image_index (integer — index of the image above that best shows this product, or null))\n\n"
            "Output ONLY this JSON:\n"
            '{"brand_name":"...","brand_description":"...","color_palette":["#hex",...],'
            '"products":[{"name":"...","description":"...","price":null,"image_index":0}]}'
        )

        user_content: list[dict] = [{"type": "text", "text": USER_TEXT}]
        for img in downloaded_images:
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{img['mime']};base64,{base64.b64encode(img['bytes']).decode()}",
                    "detail": "low",
                },
            })

        raw_text, analysis_meta = openai_chat_with_meta(
            openai_key,
            SYSTEM_PROMPT,
            user_content,
            model="gpt-4o",
            max_tokens=1500,
            temperature=0.3,
        )

        record_api_request_event(
            user_id=user_id,
            feature="brand_dna",
            provider="openai",
            operation="brand_analysis",
            meta=analysis_meta,
            metadata={"analysis_id": analysis_id, "job_id": job_id, "url": url},
        )

        # --- 6. Parse AI JSON response ---
        clean = raw_text.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```[a-z]*\n?", "", clean)
            clean = re.sub(r"```$", "", clean).strip()

        brand_data = json.loads(clean)

        brand_name = (brand_data.get("brand_name") or "").strip()[:255]
        brand_description = (brand_data.get("brand_description") or "").strip()[:500]

        # --- 6b. Merge CSS palette (authoritative) + AI palette (supplement) ---
        ai_palette_raw = brand_data.get("color_palette") or []
        ai_palette = [
            c for c in ai_palette_raw
            if isinstance(c, str) and re.match(r"^#[0-9a-fA-F]{3,8}$", c)
        ]
        if len(css_palette) >= 3:
            # CSS gives us the real brand colors — trust it
            color_palette = css_palette[:7]
        else:
            # Not enough CSS colors; merge with AI suggestions
            merged: list[str] = list(css_palette)
            for c in ai_palette:
                if c.upper() not in {x.upper() for x in merged}:
                    merged.append(c)
            color_palette = merged[:7]

        raw_products = brand_data.get("products") or []

        # --- 7. Create Product records with images ---
        products_created = 0
        for p in raw_products[:5]:
            pname = (p.get("name") or "").strip()[:160]
            pdesc = (p.get("description") or "").strip()
            pprice = str(p.get("price") or "").strip()[:100] or None
            img_idx = p.get("image_index")
            if not pname or not pdesc:
                continue

            product = Product(user_id=user_id, name=pname, context=pdesc, price=pprice)
            db.session.add(product)
            db.session.flush()

            # Attach product image if GPT-4o identified one
            if (
                img_idx is not None
                and isinstance(img_idx, int)
                and 0 <= img_idx < len(downloaded_images)
            ):
                img_data = downloaded_images[img_idx]
                ext_map = {
                    "image/jpeg": ".jpg",
                    "image/png": ".png",
                    "image/webp": ".webp",
                    "image/gif": ".gif",
                }
                ext = ext_map.get(img_data["mime"], ".jpg")
                raw_filename = _Path(img_data["url"].split("?")[0]).name or f"product{ext}"
                filename = (raw_filename[:200] if raw_filename else f"product{ext}")

                product_image = ProductImage(
                    product_id=product.id,
                    sort_order=1,
                    filename=filename,
                    mime_type=img_data["mime"],
                    image_data=None,
                )
                db.session.add(product_image)
                db.session.flush()
                try:
                    product_image.storage_path = save_product_image(
                        user_id,
                        product.id,
                        product_image.id,
                        img_data["mime"],
                        img_data["bytes"],
                    )
                except Exception:
                    product_image.storage_path = None

            products_created += 1

        # --- 8. Charge credits ---
        from decimal import Decimal
        estimated_cost = Decimal(str(analysis_meta.get("estimated_cost_usd") or "0"))
        charged, _, charge_payload = consume_credits(
            user_id,
            credit_cost,
            feature="brand_dna",
            provider="openai",
            units=1,
            estimated_cost_usd=estimated_cost,
            metadata={"analysis_id": analysis_id, "job_id": job_id},
        )
        if not charged:
            db.session.rollback()
            analysis = db.session.get(BrandDNAAnalysis, analysis_id)
            if analysis:
                analysis.status = "failed"
                analysis.error = (charge_payload or {}).get("error", "Insufficient credits.")
                db.session.commit()
            raise RuntimeError((charge_payload or {}).get("error", "Credit charge failed."))

        # --- 9. Persist results ---
        analysis = db.session.get(BrandDNAAnalysis, analysis_id)
        if analysis:
            analysis.status = "completed"
            analysis.brand_name = brand_name
            analysis.brand_description = brand_description
            analysis.color_palette = json.dumps(color_palette)
            analysis.products_created = products_created
            analysis.completed_at = datetime.now(timezone.utc)

        db.session.commit()
        return {"analysis_id": analysis_id}

    except Exception as exc:
        db.session.rollback()
        _analysis = db.session.get(BrandDNAAnalysis, analysis_id)
        if _analysis and _analysis.status not in ("completed",):
            _analysis.status = "failed"
            _analysis.error = str(exc)[:2000]
            db.session.commit()
        raise


def _run_lacy_generate(job_id: int, user_id: int | None, payload: dict[str, Any]) -> dict[str, Any]:
    if not user_id:
        raise RuntimeError("Job has no user association.")

    user = db.session.get(User, user_id)
    if not user:
        raise RuntimeError("User not found for lacy job.")

    gemini_key = get_effective_api_key(user.id, "gemini")
    if not gemini_key:
        raise RuntimeError("Gemini API key is not configured on the platform.")

    base_image_id = payload.get("base_image_id")
    source_ids = payload.get("source_ids", [])
    prompt = payload.get("prompt", "")
    gemini_model = payload.get("gemini_model")
    credits_per_source = int(payload.get("credits_per_source", 2))

    base_insp = db.session.get(CreativeInspiration, base_image_id)
    if not base_insp:
        raise RuntimeError(f"Base image inspiration {base_image_id} not found.")

    base_payload = get_image_payload(base_insp.storage_path, base_insp.mime_type, base_insp.image_data)
    if not base_payload:
        raise RuntimeError("Base image data is missing from storage.")

    results = []
    for index, source_id in enumerate(source_ids):
        source_insp = db.session.get(CreativeInspiration, source_id)
        if not source_insp:
            results.append({"index": index, "source_id": source_id, "storage_path": None, "error": "Source image not found."})
            continue
        try:
            source_payload = get_image_payload(source_insp.storage_path, source_insp.mime_type, source_insp.image_data)
            if not source_payload:
                raise RuntimeError("Source image data is missing.")

            image_b64, gemini_meta = gemini_generate_image_with_meta(
                gemini_key,
                prompt,
                [source_payload, base_payload],
                model=gemini_model,
            )
            record_api_request_event(
                user_id=user_id,
                feature="ai_creatives",
                provider="gemini",
                operation="image_generation",
                meta=gemini_meta,
                metadata={"lacy_job_id": job_id, "source_id": source_id, "index": index},
            )

            image_bytes = base64.b64decode(image_b64)
            storage_path = save_lacy_result_image(user_id, job_id, index, "image/png", image_bytes)

            consume_credits(user_id, credits_per_source, feature="ai_creatives")

            results.append({"index": index, "source_id": source_id, "storage_path": storage_path, "error": None})
        except Exception as exc:
            results.append({"index": index, "source_id": source_id, "storage_path": None, "error": str(exc)})

    return {"results": results}


def _run_social_download(
    job_id: int,
    user_id: int | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not user_id:
        raise RuntimeError("Download job has no user association.")

    download_id = int(payload.get("download_id") or 0)
    item = SocialDownload.query.filter_by(
        id=download_id, user_id=user_id
    ).first()
    if not item:
        raise RuntimeError("Social download record was not found.")
    if item.worker_job_id != job_id:
        raise RuntimeError("Social download job is no longer current.")

    source_url = (payload.get("source_url") or item.source_url or "").strip()
    if not source_url:
        raise RuntimeError("Download URL is missing.")

    item.status = "downloading"
    item.error = None
    item.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    download_dir = prepare_social_download_directory(user_id, item.id)
    for existing_file in download_dir.iterdir():
        if existing_file.is_file():
            try:
                existing_file.unlink()
            except OSError:
                pass

    extra_opts = {}
    cookie_file = (os.getenv("SOCIAL_DOWNLOAD_COOKIE_FILE") or "").strip()
    if cookie_file:
        cookie_path = os.path.abspath(cookie_file)
        if os.path.isfile(cookie_path):
            extra_opts["cookiefile"] = cookie_path

    try:
        filename, title = dlqueue_download(
            source_url,
            str(download_dir),
            out_id="video",
            extra_opts=extra_opts or None,
        )
        file_path = os.path.abspath(filename)
        if not os.path.isfile(file_path):
            candidates = [
                path for path in download_dir.glob("video.*")
                if path.is_file() and not path.name.endswith(".part")
            ]
            if not candidates:
                raise RuntimeError("The downloader did not produce a media file.")
            file_path = str(max(candidates, key=lambda path: path.stat().st_mtime))

        resolved_file = os.path.realpath(file_path)
        resolved_dir = os.path.realpath(str(download_dir))
        if os.path.commonpath([resolved_file, resolved_dir]) != resolved_dir:
            raise RuntimeError("Downloaded file escaped the configured storage directory.")

        media_root = get_media_root().resolve()
        storage_path = (
            os.path.relpath(resolved_file, str(media_root))
            .replace(os.sep, "/")
        )
        mime_type = mimetypes.guess_type(resolved_file)[0] or "video/mp4"
        file_size = os.path.getsize(resolved_file)

        item = db.session.get(SocialDownload, download_id)
        if not item:
            raise RuntimeError("Social download record disappeared.")
        item.status = "success"
        item.title = (title or os.path.basename(resolved_file))[:500]
        item.storage_path = storage_path
        item.mime_type = mime_type
        item.file_size_bytes = file_size
        item.error = None
        item.finished_at = datetime.now(timezone.utc)
        item.updated_at = item.finished_at
        db.session.commit()
        return {
            "download_id": item.id,
            "storage_path": storage_path,
            "file_size_bytes": file_size,
            "mime_type": mime_type,
        }
    except Exception as exc:
        db.session.rollback()
        try:
            shutil.rmtree(download_dir)
        except OSError:
            pass
        item = db.session.get(SocialDownload, download_id)
        if item:
            item.status = "failed"
            item.error = (str(exc).strip() or "Download failed.")[:2000]
            item.finished_at = datetime.now(timezone.utc)
            item.updated_at = item.finished_at
            db.session.commit()
        raise


_COMPETITOR_AD_ANALYSIS_SYSTEM_PROMPT = (
    "You are a senior direct-response advertising strategist who evaluates "
    "competitor video ad scripts for e-commerce brands. "
    "Respond ONLY with a single valid JSON object. No markdown fences, no commentary."
)


def _get_competitor_ad_for_user(ad_id: int, user_id: int) -> CompetitorAd | None:
    return (
        CompetitorAd.query
        .join(Competitor, Competitor.id == CompetitorAd.competitor_id)
        .filter(CompetitorAd.id == ad_id, Competitor.user_id == user_id)
        .first()
    )


def _run_competitor_ad_transcribe(
    job_id: int, user_id: int | None, payload: dict[str, Any]
) -> dict[str, Any]:
    ad_id = int(payload.get("ad_id") or 0)
    if not user_id:
        raise RuntimeError("Job has no user association.")

    ad = _get_competitor_ad_for_user(ad_id, user_id)
    if not ad:
        raise RuntimeError("Competitor ad not found.")

    try:
        openai_key = get_effective_api_key(user_id, "openai")
        if not openai_key:
            raise RuntimeError("OpenAI API key is not configured on the platform.")
        if not ad.storage_path:
            raise RuntimeError("Competitor ad has no stored video file.")
        file_path = resolve_storage_path(ad.storage_path)
        if not file_path.is_file():
            raise RuntimeError("Competitor ad video file is no longer available.")

        ad.transcript_status = "processing"
        ad.transcript_error = None
        db.session.commit()

        transcript, meta = _transcribe_media_file_with_meta(
            api_key=openai_key,
            file_path=str(file_path),
            mime_type=ad.mime_type,
        )

        ad = _get_competitor_ad_for_user(ad_id, user_id)
        if not ad:
            raise RuntimeError("Competitor ad was removed during transcription.")
        ad.transcript = transcript
        ad.transcript_status = "completed"
        ad.transcript_error = None
        db.session.commit()

        record_api_request_event(
            user_id=user_id,
            feature="competitors",
            provider="openai",
            operation="transcription",
            meta=meta,
            metadata={
                "job_id": job_id,
                "ad_id": ad_id,
                "file_size_bytes": ad.file_size_bytes,
                "mime_type": ad.mime_type,
                "original_name": ad.original_filename,
                "transcription_chunk_count": meta.get("transcription_chunk_count", 1),
                "source_transcoded": bool(meta.get("source_transcoded")),
            },
        )
        return {"ad_id": ad_id, "text": transcript}
    except Exception as exc:
        db.session.rollback()
        ad = _get_competitor_ad_for_user(ad_id, user_id)
        if ad:
            ad.transcript_status = "failed"
            ad.transcript_error = (str(exc).strip() or "Transcription failed.")[:2000]
            db.session.commit()
        record_api_request_event(
            user_id=user_id,
            feature="competitors",
            provider="openai",
            operation="transcription",
            status="failed",
            error_message=str(exc),
            metadata={"job_id": job_id, "ad_id": ad_id},
        )
        raise


def _parse_competitor_analysis(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("AI analysis did not return valid JSON.")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise RuntimeError("AI analysis did not return a JSON object.")

    try:
        rating = int(round(float(parsed.get("rating"))))
    except (TypeError, ValueError):
        rating = 0
    rating = max(1, min(10, rating)) if rating else None

    def _str_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()][:4]

    return {
        "rating": rating,
        "overview": str(parsed.get("overview") or "").strip(),
        "strengths": _str_list(parsed.get("strengths")),
        "weaknesses": _str_list(parsed.get("weaknesses")),
    }


def _run_competitor_ad_analyze(
    job_id: int, user_id: int | None, payload: dict[str, Any]
) -> dict[str, Any]:
    ad_id = int(payload.get("ad_id") or 0)
    if not user_id:
        raise RuntimeError("Job has no user association.")

    ad = _get_competitor_ad_for_user(ad_id, user_id)
    if not ad:
        raise RuntimeError("Competitor ad not found.")

    try:
        openai_key = get_effective_api_key(user_id, "openai")
        if not openai_key:
            raise RuntimeError("OpenAI API key is not configured on the platform.")
        transcript = (ad.transcript or "").strip()
        if not transcript:
            raise RuntimeError("This ad has no transcript yet. Wait for transcription to finish.")

        ad.analysis_status = "processing"
        ad.analysis_error = None
        db.session.commit()

        competitor = ad.competitor
        product = competitor.product if competitor else None
        context_lines = [f"Competitor name: {competitor.name}" if competitor else ""]
        if product:
            context_lines.append(f"This competitor competes with my product: {product.name}")
            product_context = (product.context or "").strip()
            if product_context:
                context_lines.append(f"My product context: {product_context[:1500]}")
        user_content = (
            "Analyze this competitor's video ad script.\n"
            + "\n".join(line for line in context_lines if line)
            + "\n\nAd script (transcript):\n\"\"\"\n"
            + transcript[:12000]
            + "\n\"\"\"\n\n"
            "Return a JSON object with exactly these keys:\n"
            '{"rating": <integer 1-10 scoring how strong this ad is as a direct-response ad>, '
            '"overview": "<3-4 sentence overview of the ad: its angle, hook, structure and why it works or not>", '
            '"strengths": ["<up to 3 short bullet strengths>"], '
            '"weaknesses": ["<up to 3 short bullet weaknesses>"]}'
        )

        raw_text, meta = openai_chat_with_meta(
            api_key=openai_key,
            system_prompt=_COMPETITOR_AD_ANALYSIS_SYSTEM_PROMPT,
            user_content=user_content,
            max_tokens=900,
            temperature=0.4,
        )
        analysis = _parse_competitor_analysis(raw_text)

        ad = _get_competitor_ad_for_user(ad_id, user_id)
        if not ad:
            raise RuntimeError("Competitor ad was removed during analysis.")
        ad.analysis_json = json.dumps(analysis, ensure_ascii=False)
        ad.analysis_status = "completed"
        ad.analysis_error = None
        db.session.commit()

        record_api_request_event(
            user_id=user_id,
            feature="competitors",
            provider="openai",
            operation="competitor_ad_analysis",
            meta=meta,
            metadata={"job_id": job_id, "ad_id": ad_id},
        )
        return {"ad_id": ad_id, "analysis": analysis}
    except Exception as exc:
        db.session.rollback()
        ad = _get_competitor_ad_for_user(ad_id, user_id)
        if ad:
            ad.analysis_status = "failed"
            ad.analysis_error = (str(exc).strip() or "Analysis failed.")[:2000]
            db.session.commit()
        record_api_request_event(
            user_id=user_id,
            feature="competitors",
            provider="openai",
            operation="competitor_ad_analysis",
            status="failed",
            error_message=str(exc),
            metadata={"job_id": job_id, "ad_id": ad_id},
        )
        raise


def _run_job(job_id: int, user_id: int | None, job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if job_type == JOB_TYPE_SCRIPT_TRANSCRIBE:
        return _run_script_transcription(job_id, user_id, payload)
    if job_type == JOB_TYPE_AI_IMAGE_GENERATE:
        return _run_ai_image_generate(job_id, user_id, payload)
    if job_type == JOB_TYPE_AVATARS_GENERATE:
        return _run_avatars_generate(job_id, user_id, payload)
    if job_type == JOB_TYPE_CREATIVES_GENERATE:
        return _run_creatives_generate(job_id, user_id, payload)
    if job_type == JOB_TYPE_LACY_GENERATE:
        return _run_lacy_generate(job_id, user_id, payload)
    if job_type == JOB_TYPE_SOCIAL_DOWNLOAD:
        return _run_social_download(job_id, user_id, payload)
    if job_type == JOB_TYPE_BRAND_DNA_ANALYZE:
        return _run_brand_dna_analyze(job_id, user_id, payload)
    if job_type == JOB_TYPE_COMPETITOR_AD_TRANSCRIBE:
        return _run_competitor_ad_transcribe(job_id, user_id, payload)
    if job_type == JOB_TYPE_COMPETITOR_AD_ANALYZE:
        return _run_competitor_ad_analyze(job_id, user_id, payload)
    if job_type == JOB_TYPE_VOICEOVER_TIGHTEN:
        return run_voiceover_tightening_job(job_id, user_id, payload)
    raise RuntimeError(f"Unsupported worker job type: {job_type}")


def _worker_loop(
    app,
    *,
    stop_event: threading.Event,
    worker_name: str,
    queue_name: str,
    poll_interval: float,
    stale_timeout_seconds: int,
    run_stale_recovery: bool,
) -> None:
    last_stale_check_at = 0.0
    with app.app_context():
        while not stop_event.is_set():
            try:
                now = time.time()
                if run_stale_recovery and (now - last_stale_check_at) >= 30:
                    requeue_stale_running_jobs(stale_after_seconds=stale_timeout_seconds)
                    last_stale_check_at = now

                claimed = claim_next_worker_job(
                    worker_name=worker_name,
                    queue_name=queue_name,
                    job_types={
                        JOB_TYPE_SCRIPT_TRANSCRIBE,
                        JOB_TYPE_AI_IMAGE_GENERATE,
                        JOB_TYPE_AVATARS_GENERATE,
                        JOB_TYPE_CREATIVES_GENERATE,
                        JOB_TYPE_BRAND_DNA_ANALYZE,
                        JOB_TYPE_LACY_GENERATE,
                        JOB_TYPE_SOCIAL_DOWNLOAD,
                        JOB_TYPE_COMPETITOR_AD_TRANSCRIBE,
                        JOB_TYPE_COMPETITOR_AD_ANALYZE,
                        JOB_TYPE_VOICEOVER_TIGHTEN,
                    },
                )
                if not claimed:
                    db.session.remove()
                    stop_event.wait(max(0.1, float(poll_interval)))
                    continue

                job, lock_token = claimed
                payload = job_payload(job)

                try:
                    result = _run_job(job.id, job.user_id, job.job_type, payload)
                    complete_worker_job(job.id, lock_token, result)
                    _cleanup_job_artifacts(app, job.job_type, payload)
                except Exception as exc:
                    next_status = fail_worker_job(
                        job.id,
                        lock_token,
                        str(exc),
                        retry_delay_seconds=min(120, max(10, int(job.attempts or 1) * 15)),
                    )
                    if next_status == JOB_STATUS_FAILED:
                        _cleanup_job_artifacts(app, job.job_type, payload)
            finally:
                db.session.remove()


def run_worker_pool(
    app,
    *,
    queue_name: str = "default",
    concurrency: int = 2,
    poll_interval: float = 1.5,
    stale_timeout_seconds: int = 3600,
) -> None:
    concurrency = max(1, int(concurrency))
    stop_event = threading.Event()

    os.makedirs(_worker_upload_root(app), exist_ok=True)

    hostname = socket.gethostname() or "worker"
    pid = os.getpid()
    threads = []
    for idx in range(concurrency):
        name = f"{hostname}-{pid}-w{idx + 1}"
        thread = threading.Thread(
            target=_worker_loop,
            kwargs={
                "app": app,
                "stop_event": stop_event,
                "worker_name": name,
                "queue_name": queue_name,
                "poll_interval": poll_interval,
                "stale_timeout_seconds": stale_timeout_seconds,
                "run_stale_recovery": idx == 0,
            },
            daemon=False,
            name=name,
        )
        thread.start()
        threads.append(thread)

    try:
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(timeout=0.5)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=2.0)
