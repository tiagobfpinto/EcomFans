"""
Shared AI provider service.

All raw API calls to Gemini and OpenAI live here so every blueprint
uses the same code path, model IDs, and error handling.
"""

from __future__ import annotations

import base64
import os
import time

import requests

from usage_pricing import estimate_gemini_cost_usd, normalize_model_name, usd_to_eur

# -- Model IDs ---------------------------------------------------------------
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"  # image generation
GEMINI_VISION_MODEL = "gemini-2.5-flash-lite"  # text / multimodal vision

OPENAI_CHAT_MODEL = "gpt-4o-mini"
OPENAI_IMAGE_MODEL = "gpt-image-1"
OPENAI_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"

MEDIA_RESOLUTIONS = {"LOW", "MEDIUM", "HIGH"}

# In-process cache for Gemini model list (free API but slow; 1-hour TTL).
_gemini_models_cache: dict[str, tuple[list, float]] = {}
GEMINI_MODELS_CACHE_TTL = 3600


def _build_gemini_meta(
    payload: dict,
    model: str,
    traffic_type: str,
    latency_ms: int,
    input_image_count: int = 0,
    images_generated: int = 0,
) -> dict:
    usage = payload.get("usageMetadata", {}) if isinstance(payload, dict) else {}
    input_tokens = _int_or_none(usage.get("promptTokenCount"))
    output_tokens = _int_or_none(usage.get("candidatesTokenCount"))
    total_tokens = _int_or_none(usage.get("totalTokenCount"))
    cached_tokens = _int_or_none(usage.get("cachedContentTokenCount"))

    cost_usd = estimate_gemini_cost_usd(
        model=model,
        traffic_type=traffic_type,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        images_generated=images_generated,
    )
    cost_eur = usd_to_eur(cost_usd)

    return {
        "provider": "gemini",
        "model": normalize_model_name(model),
        "traffic_type": traffic_type,
        "latency_ms": latency_ms,
        "http_status": 200,
        "request_id": payload.get("responseId"),
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "input_image_count": max(0, int(input_image_count or 0)),
        "images_generated": max(0, int(images_generated or 0)),
        "estimated_cost_usd": cost_usd,
        "estimated_cost_eur": cost_eur,
        "model_version": payload.get("modelVersion"),
    }


def _build_openai_meta(
    payload: dict | None,
    model: str,
    latency_ms: int,
    images_generated: int = 0,
) -> dict:
    usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
    input_tokens = _int_or_none(usage.get("prompt_tokens"))
    output_tokens = _int_or_none(usage.get("completion_tokens"))
    total_tokens = _int_or_none(usage.get("total_tokens"))
    return {
        "provider": "openai",
        "model": model,
        "traffic_type": "standard",
        "latency_ms": latency_ms,
        "http_status": 200,
        "request_id": None,
        "input_tokens": input_tokens,
        "cached_tokens": None,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "input_image_count": 0,
        "images_generated": max(0, int(images_generated or 0)),
        "estimated_cost_usd": None,
        "estimated_cost_eur": None,
        "model_version": None,
    }


# -- Gemini -----------------------------------------------------------------

def gemini_list_models(api_key: str) -> list[dict]:
    """Return raw Gemini models list from Models API (cached 1 hour per API key)."""
    now = time.time()
    if api_key in _gemini_models_cache:
        cached, ts = _gemini_models_cache[api_key]
        if now - ts < GEMINI_MODELS_CACHE_TTL:
            return cached

    next_page_token = None
    models: list[dict] = []

    while True:
        params = {"pageSize": 1000}
        if next_page_token:
            params["pageToken"] = next_page_token

        resp = requests.get(
            GEMINI_API_BASE,
            params=params,
            headers={"x-goog-api-key": api_key},
            timeout=30,
        )
        _raise_for_gemini(resp, "Gemini list models")
        payload = resp.json()
        models.extend(payload.get("models", []))
        next_page_token = payload.get("nextPageToken")
        if not next_page_token:
            break

    _gemini_models_cache[api_key] = (models, time.time())
    return models


def gemini_list_image_models(api_key: str) -> list[dict]:
    """Return available Gemini image-capable models."""
    image_models = []
    for model in gemini_list_models(api_key):
        name = normalize_model_name(model.get("name"))
        if not name:
            continue
        methods = model.get("supportedGenerationMethods", []) or []
        if "generateContent" not in methods:
            continue
        lower_name = name.lower()
        lower_display = (model.get("displayName") or "").lower()
        if "image" not in lower_name and "image" not in lower_display:
            continue
        image_models.append({
            "name": name,
            "display_name": model.get("displayName") or name,
            "description": model.get("description") or "",
            "input_token_limit": model.get("inputTokenLimit"),
            "output_token_limit": model.get("outputTokenLimit"),
            "supported_generation_methods": methods,
        })

    # Keep consistent order by model name for stable frontend rendering.
    image_models.sort(key=lambda m: m["name"])
    return image_models


def gemini_generate_image_with_meta(
    api_key: str,
    prompt_text: str,
    images: list | None = None,
    model: str | None = None,
    traffic_type: str = "standard",
) -> tuple[str, dict]:
    """Generate an image with Gemini and return (base64, request metadata)."""
    selected_model = normalize_model_name(model) or GEMINI_IMAGE_MODEL
    url = f"{GEMINI_API_BASE}/{selected_model}:generateContent"

    parts = []
    if images:
        for img in images:
            if img.get("file_uri"):
                parts.append({
                    "fileData": {"mimeType": img["mime_type"], "fileUri": img["file_uri"]}
                })
            else:
                parts.append({
                    "inlineData": {"mimeType": img["mime_type"], "data": img["data"]}
                })
    parts.append({"text": f"Generate an image based on this description: {prompt_text}"})

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    started_at = time.perf_counter()
    resp = requests.post(
        url,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json=payload,
        timeout=120,
    )
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    # Some Gemini versions require TEXT in responseModalities; fall back if rejected.
    if resp.status_code != 200:
        payload["generationConfig"]["responseModalities"] = ["TEXT", "IMAGE"]
        started_at = time.perf_counter()
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json=payload,
            timeout=120,
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    _raise_for_gemini(resp, "Gemini image generation")
    json_payload = resp.json()

    for candidate in json_payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if "inlineData" in part:
                meta = _build_gemini_meta(
                    payload=json_payload,
                    model=selected_model,
                    traffic_type=traffic_type,
                    latency_ms=elapsed_ms,
                    input_image_count=len(images or []),
                    images_generated=1,
                )
                return part["inlineData"]["data"], meta

    raise RuntimeError("No image returned by Gemini. Try rephrasing the prompt.")


def gemini_generate_image(
    api_key: str,
    prompt_text: str,
    images: list | None = None,
    model: str | None = None,
    traffic_type: str = "standard",
) -> str:
    """Generate an image with Gemini and return only base64 image data."""
    image_data, _ = gemini_generate_image_with_meta(
        api_key=api_key,
        prompt_text=prompt_text,
        images=images,
        model=model,
        traffic_type=traffic_type,
    )
    return image_data


def gemini_vision_with_meta(
    api_key: str,
    system_prompt: str,
    parts: list,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    model: str | None = None,
    traffic_type: str = "standard",
    media_resolution: str | None = None,
    max_token_budget: int | None = None,
) -> tuple[str, dict]:
    """Call Gemini with vision/text input and return (response_text, metadata)."""
    selected_model = normalize_model_name(model) or GEMINI_VISION_MODEL
    url = f"{GEMINI_API_BASE}/{selected_model}:generateContent"

    normalized_resolution = (media_resolution or "").strip().upper()
    use_resolution = normalized_resolution if normalized_resolution in MEDIA_RESOLUTIONS else None

    token_budget = max_tokens
    accumulated_text = ""
    total_input_tokens = 0
    total_cached_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    total_latency_ms = 0
    last_payload: dict = {}
    last_request_id = None
    input_image_count = sum(
        1 for part in parts
        if isinstance(part, dict) and ("inlineData" in part or "fileData" in part)
    )

    for _ in range(3):
        request_parts = list(parts)
        if accumulated_text:
            request_parts = [
                *request_parts,
                {
                    "text": (
                        "Continue exactly where your previous response stopped. "
                        "Do not restart, summarize, or add commentary. "
                        f"Previous response:\n{accumulated_text}"
                    )
                },
            ]

        generation_config: dict[str, object] = {
            "temperature": temperature,
            "maxOutputTokens": token_budget,
        }
        if use_resolution:
            generation_config["mediaResolution"] = use_resolution

        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": request_parts}],
            "generationConfig": generation_config,
        }

        started_at = time.perf_counter()
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json=payload,
            timeout=60,
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)

        # Fallback for projects pinned to older API behavior without mediaResolution.
        if resp.status_code != 200 and use_resolution:
            msg = ""
            try:
                msg = resp.json().get("error", {}).get("message", "")
            except Exception:
                msg = resp.text
            if "mediaResolution" in msg or "media_resolution" in msg:
                generation_config.pop("mediaResolution", None)
                started_at = time.perf_counter()
                resp = requests.post(
                    url,
                    headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                    json=payload,
                    timeout=60,
                )
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)

        _raise_for_gemini(resp, "Gemini vision")
        json_payload = resp.json()
        last_payload = json_payload
        total_latency_ms += elapsed_ms
        last_request_id = json_payload.get("responseId")

        usage = json_payload.get("usageMetadata", {}) if isinstance(json_payload, dict) else {}
        total_input_tokens += int(usage.get("promptTokenCount") or 0)
        total_cached_tokens += int(usage.get("cachedContentTokenCount") or 0)
        total_output_tokens += int(usage.get("candidatesTokenCount") or 0)
        total_tokens += int(usage.get("totalTokenCount") or 0)

        candidates = json_payload.get("candidates", [])
        for candidate in candidates:
            finish_reason = candidate.get("finishReason")
            text_parts = [
                part["text"].strip()
                for part in candidate.get("content", {}).get("parts", [])
                if "text" in part and part["text"].strip()
            ]
            if not text_parts:
                continue
            chunk_text = "\n".join(text_parts).strip()
            accumulated_text = f"{accumulated_text}{chunk_text}".strip()
            if finish_reason != "MAX_TOKENS":
                meta = _build_gemini_meta(
                    payload=last_payload,
                    model=selected_model,
                    traffic_type=traffic_type,
                    latency_ms=total_latency_ms,
                    input_image_count=input_image_count,
                    images_generated=0,
                )
                meta.update({
                    "request_id": last_request_id,
                    "input_tokens": total_input_tokens or None,
                    "cached_tokens": total_cached_tokens or None,
                    "output_tokens": total_output_tokens or None,
                    "total_tokens": total_tokens or None,
                })
                return accumulated_text, meta

        token_budget *= 2
        if max_token_budget is not None:
            token_budget = min(token_budget, max_token_budget)

    if accumulated_text:
        meta = _build_gemini_meta(
            payload=last_payload,
            model=selected_model,
            traffic_type=traffic_type,
            latency_ms=total_latency_ms,
            input_image_count=input_image_count,
            images_generated=0,
        )
        meta.update({
            "request_id": last_request_id,
            "input_tokens": total_input_tokens or None,
            "cached_tokens": total_cached_tokens or None,
            "output_tokens": total_output_tokens or None,
            "total_tokens": total_tokens or None,
        })
        return accumulated_text, meta

    raise RuntimeError("Gemini vision output was truncated before any text was returned.")


def gemini_vision(
    api_key: str,
    system_prompt: str,
    parts: list,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    model: str | None = None,
    traffic_type: str = "standard",
    media_resolution: str | None = None,
    max_token_budget: int | None = None,
) -> str:
    """Call Gemini with vision/text input and return the text response."""
    text, _ = gemini_vision_with_meta(
        api_key=api_key,
        system_prompt=system_prompt,
        parts=parts,
        max_tokens=max_tokens,
        temperature=temperature,
        model=model,
        traffic_type=traffic_type,
        media_resolution=media_resolution,
        max_token_budget=max_token_budget,
    )
    return text


# -- OpenAI -----------------------------------------------------------------

def openai_generate_image_with_meta(
    api_key: str,
    prompt_text: str,
    model: str = OPENAI_IMAGE_MODEL,
) -> tuple[str, dict]:
    """Generate an image with OpenAI gpt-image-1 and return (base64, metadata)."""
    started_at = time.perf_counter()
    resp = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "prompt": prompt_text,
            "n": 1,
            "size": "1024x1024",
            "quality": "standard",
        },
        timeout=120,
    )
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    _raise_for_openai(resp, "OpenAI image generation")

    payload = resp.json()
    data = payload["data"][0]
    image_b64 = None
    if data.get("b64_json"):
        image_b64 = data["b64_json"]

    if not image_b64:
        # Fallback: download from URL
        url = data.get("url")
        if url:
            img_resp = requests.get(url, timeout=60)
            img_resp.raise_for_status()
            image_b64 = base64.b64encode(img_resp.content).decode()

    if not image_b64:
        raise RuntimeError("OpenAI returned no image data.")

    meta = _build_openai_meta(payload=payload, model=model, latency_ms=elapsed_ms, images_generated=1)
    return image_b64, meta


def openai_generate_image(api_key: str, prompt_text: str) -> str:
    """Generate an image with OpenAI and return only base64 image data."""
    image_data, _ = openai_generate_image_with_meta(api_key=api_key, prompt_text=prompt_text)
    return image_data


def remove_background_local(image_bytes: bytes) -> bytes:
    """Remove image background locally (no external API key required)."""
    if not image_bytes:
        raise RuntimeError("Image payload is empty.")

    try:
        from rembg import remove
    except Exception as exc:
        raise RuntimeError(
            "Local background removal is unavailable. Install rembg dependencies."
        ) from exc

    try:
        output = remove(image_bytes)
    except Exception as exc:
        raise RuntimeError(f"Local background removal failed: {exc}") from exc

    if not output:
        raise RuntimeError("Local background removal returned empty output.")
    return output


def openai_remove_background_with_meta(
    api_key: str,
    image_bytes: bytes,
    filename: str | None = None,
    mime_type: str | None = None,
    model: str = OPENAI_IMAGE_MODEL,
) -> tuple[bytes, dict]:
    """Remove image background with OpenAI and return (png_bytes, metadata)."""
    if not image_bytes:
        raise RuntimeError("Image payload is empty.")

    prompt = (
        "Remove the full background from this image and keep only the main subject. "
        "Preserve original colors and details. Return a transparent PNG."
    )
    safe_filename = filename or "upload.png"
    safe_mime_type = mime_type or "image/png"

    started_at = time.perf_counter()
    resp = requests.post(
        "https://api.openai.com/v1/images/edits",
        headers={"Authorization": f"Bearer {api_key}"},
        data={
            "model": model,
            "prompt": prompt,
        },
        files={
            "image": (
                safe_filename,
                image_bytes,
                safe_mime_type,
            ),
        },
        timeout=180,
    )
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    _raise_for_openai(resp, "OpenAI background removal")

    payload = resp.json()
    data = (payload.get("data") or [{}])[0]
    edited_b64 = data.get("b64_json")
    edited_bytes = b""
    if edited_b64:
        try:
            edited_bytes = base64.b64decode(edited_b64, validate=True)
        except Exception:
            edited_bytes = b""

    if not edited_bytes:
        url = data.get("url")
        if url:
            img_resp = requests.get(url, timeout=60)
            img_resp.raise_for_status()
            edited_bytes = img_resp.content

    if not edited_bytes:
        raise RuntimeError("OpenAI returned no edited image data.")

    meta = _build_openai_meta(payload=payload, model=model, latency_ms=elapsed_ms, images_generated=1)
    return edited_bytes, meta


def openai_remove_background(
    api_key: str,
    image_bytes: bytes,
    filename: str | None = None,
    mime_type: str | None = None,
) -> bytes:
    """Remove image background with OpenAI and return image bytes."""
    result, _ = openai_remove_background_with_meta(
        api_key=api_key,
        image_bytes=image_bytes,
        filename=filename,
        mime_type=mime_type,
    )
    return result


def openai_chat_with_meta(
    api_key: str,
    system_prompt: str,
    user_content,
    model: str = OPENAI_CHAT_MODEL,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> tuple[str, dict]:
    """Call OpenAI chat completions and return (assistant_text, metadata)."""
    token_budget = max_tokens
    accumulated_text = ""
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    total_latency_ms = 0
    last_payload: dict = {}

    for _ in range(3):
        effective_user_content = user_content
        if accumulated_text:
            if isinstance(user_content, list):
                effective_user_content = [
                    *user_content,
                    {
                        "type": "text",
                        "text": (
                            "Continue exactly where your previous response stopped. "
                            "Do not restart, summarize, or add commentary. "
                            f"Previous response:\n{accumulated_text}"
                        ),
                    },
                ]
            else:
                effective_user_content = (
                    f"{user_content}\n\n"
                    "Continue exactly where your previous response stopped. "
                    "Do not restart, summarize, or add commentary.\n"
                    f"Previous response:\n{accumulated_text}"
                )
        started_at = time.perf_counter()
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": effective_user_content},
                ],
                "max_tokens": token_budget,
                "temperature": temperature,
            },
            timeout=60,
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        _raise_for_openai(resp, "OpenAI chat")
        payload = resp.json()
        last_payload = payload
        total_latency_ms += elapsed_ms

        usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
        total_input_tokens += int(usage.get("prompt_tokens") or 0)
        total_output_tokens += int(usage.get("completion_tokens") or 0)
        total_tokens += int(usage.get("total_tokens") or 0)

        choice = payload["choices"][0]
        content = (choice.get("message", {}).get("content") or "").strip()
        accumulated_text = f"{accumulated_text}{content}".strip()
        if choice.get("finish_reason") != "length":
            meta = _build_openai_meta(payload=last_payload, model=model, latency_ms=total_latency_ms)
            meta.update({
                "input_tokens": total_input_tokens or None,
                "output_tokens": total_output_tokens or None,
                "total_tokens": total_tokens or None,
            })
            return accumulated_text, meta
        token_budget *= 2

    if accumulated_text:
        meta = _build_openai_meta(payload=last_payload, model=model, latency_ms=total_latency_ms)
        meta.update({
            "input_tokens": total_input_tokens or None,
            "output_tokens": total_output_tokens or None,
            "total_tokens": total_tokens or None,
        })
        return accumulated_text, meta

    raise RuntimeError("OpenAI chat output was truncated before any text was returned.")


def openai_chat(
    api_key: str,
    system_prompt: str,
    user_content,
    model: str = OPENAI_CHAT_MODEL,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """Call OpenAI chat completions and return only assistant text."""
    text, _ = openai_chat_with_meta(
        api_key=api_key,
        system_prompt=system_prompt,
        user_content=user_content,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return text


def openai_transcribe_file_with_meta(
    api_key: str,
    file_path: str,
    mime_type: str | None = None,
    model: str = OPENAI_TRANSCRIBE_MODEL,
) -> tuple[str, dict]:
    """Transcribe an audio/video file with OpenAI and return (text, metadata)."""
    with open(file_path, "rb") as media_file:
        started_at = time.perf_counter()
        resp = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data={
                "model": model,
                "response_format": "json",
            },
            files={
                "file": (
                    os.path.basename(file_path) or "upload.bin",
                    media_file,
                    mime_type or "application/octet-stream",
                ),
            },
            timeout=300,
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)

    _raise_for_openai(resp, "OpenAI transcription")

    payload = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    text = (payload.get("text") or "").strip() if isinstance(payload, dict) else ""
    if not text:
        raise RuntimeError("OpenAI transcription returned no text.")
    meta = _build_openai_meta(payload=payload, model=model, latency_ms=elapsed_ms)
    return text, meta


def openai_transcribe_file(
    api_key: str,
    file_path: str,
    mime_type: str | None = None,
    model: str = OPENAI_TRANSCRIBE_MODEL,
) -> str:
    """Transcribe an audio/video file with OpenAI and return plain text."""
    text, _ = openai_transcribe_file_with_meta(
        api_key=api_key,
        file_path=file_path,
        mime_type=mime_type,
        model=model,
    )
    return text


# -- Internal helpers --------------------------------------------------------

def _int_or_none(value):
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _raise_for_gemini(resp: requests.Response, context: str) -> None:
    if resp.status_code == 200:
        return
    try:
        msg = resp.json().get("error", {}).get("message", resp.text)
    except Exception:
        msg = resp.text
    raise RuntimeError(f"{context} error ({resp.status_code}): {msg}")


def _raise_for_openai(resp: requests.Response, context: str) -> None:
    if resp.status_code == 200:
        return
    try:
        msg = resp.json().get("error", {}).get("message", resp.text)
    except Exception:
        msg = resp.text
    raise RuntimeError(f"{context} error ({resp.status_code}): {msg}")
