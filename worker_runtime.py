from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

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
    CreativeBatch,
    CreativeInspiration,
    CreativeInspirationAnalysis,
    CreativeResult,
    ImageGeneration,
    Product,
    ProductImage,
    User,
    WorkerJob,
    db,
)
from media_storage import (
    get_image_payload,
    save_ai_generation_image,
    save_avatar_result_after_image,
    save_avatar_result_before_image,
    save_creative_result_image,
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
    JOB_TYPE_CREATIVES_GENERATE,
    JOB_TYPE_SCRIPT_TRANSCRIBE,
)

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
    with open(file_path, "rb") as _f:
        file_sha256 = hashlib.sha256(_f.read()).hexdigest()

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
        transcript, meta = openai_transcribe_file_with_meta(
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


def _run_job(job_id: int, user_id: int | None, job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if job_type == JOB_TYPE_SCRIPT_TRANSCRIBE:
        return _run_script_transcription(job_id, user_id, payload)
    if job_type == JOB_TYPE_AI_IMAGE_GENERATE:
        return _run_ai_image_generate(job_id, user_id, payload)
    if job_type == JOB_TYPE_AVATARS_GENERATE:
        return _run_avatars_generate(job_id, user_id, payload)
    if job_type == JOB_TYPE_CREATIVES_GENERATE:
        return _run_creatives_generate(job_id, user_id, payload)
    if job_type == JOB_TYPE_BRAND_DNA_ANALYZE:
        return _run_brand_dna_analyze(job_id, user_id, payload)
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
