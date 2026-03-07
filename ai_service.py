"""
Shared AI provider service.

All raw API calls to Gemini and OpenAI live here so every blueprint
uses the same code path, model IDs, and error handling.
"""

import base64
import os

import requests

# ── Model IDs ─────────────────────────────────────────────────────────
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"   # image generation
GEMINI_VISION_MODEL = "gemini-2.5-flash"         # text / multimodal vision

OPENAI_CHAT_MODEL = "gpt-4o-mini"
OPENAI_IMAGE_MODEL = "gpt-image-1"
OPENAI_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"


# ── Gemini ────────────────────────────────────────────────────────────

def gemini_generate_image(api_key: str, prompt_text: str, images: list | None = None) -> str:
    """Generate an image with Gemini and return a base64 string.

    Args:
        api_key: User's Gemini API key.
        prompt_text: Text description / generation prompt.
        images: Optional list of {"mime_type": str, "data": str (base64)}.
                Sent before the text prompt as visual reference.
    Returns:
        Base64-encoded PNG/JPEG string.
    Raises:
        RuntimeError on API or parsing errors.
    """
    url = f"{GEMINI_API_BASE}/{GEMINI_IMAGE_MODEL}:generateContent"

    parts = []
    if images:
        for img in images:
            parts.append({
                "inlineData": {"mimeType": img["mime_type"], "data": img["data"]}
            })
    parts.append({"text": f"Generate an image based on this description: {prompt_text}"})

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    resp = requests.post(
        url,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json=payload,
        timeout=120,
    )
    _raise_for_gemini(resp, "Gemini image generation")

    for candidate in resp.json().get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if "inlineData" in part:
                return part["inlineData"]["data"]

    raise RuntimeError("No image returned by Gemini. Try rephrasing the prompt.")


def gemini_vision(
    api_key: str,
    system_prompt: str,
    parts: list,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """Call Gemini with vision/text input and return the text response.

    Args:
        api_key: User's Gemini API key.
        system_prompt: System instruction string.
        parts: List of Gemini content parts — text dicts or inlineData dicts.
               e.g. [{"text": "..."}, {"inlineData": {"mimeType": ..., "data": ...}}]
        max_tokens: Max tokens in the response.
        temperature: Sampling temperature.
    Returns:
        Stripped text from the first text part of the response.
    Raises:
        RuntimeError on API or parsing errors.
    """
    url = f"{GEMINI_API_BASE}/{GEMINI_VISION_MODEL}:generateContent"

    token_budget = max_tokens
    accumulated_text = ""
    for attempt in range(3):
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
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": request_parts}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": token_budget},
        }
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json=payload,
            timeout=60,
        )
        _raise_for_gemini(resp, "Gemini vision")

        candidates = resp.json().get("candidates", [])
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
                return accumulated_text

        token_budget *= 2

    if accumulated_text:
        return accumulated_text

    raise RuntimeError("Gemini vision output was truncated before any text was returned.")


# ── OpenAI ────────────────────────────────────────────────────────────

def openai_generate_image(api_key: str, prompt_text: str) -> str:
    """Generate an image with OpenAI gpt-image-1 and return a base64 string.

    Args:
        api_key: User's OpenAI API key.
        prompt_text: Full text generation prompt.
    Returns:
        Base64-encoded image string.
    Raises:
        RuntimeError on API or parsing errors.
    """
    resp = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": OPENAI_IMAGE_MODEL,
            "prompt": prompt_text,
            "n": 1,
            "size": "1024x1024",
            "quality": "standard",
        },
        timeout=120,
    )
    _raise_for_openai(resp, "OpenAI image generation")

    data = resp.json()["data"][0]
    if data.get("b64_json"):
        return data["b64_json"]

    # Fallback: download from URL
    url = data.get("url")
    if url:
        img_resp = requests.get(url, timeout=60)
        img_resp.raise_for_status()
        return base64.b64encode(img_resp.content).decode()

    raise RuntimeError("OpenAI returned no image data.")


def openai_chat(
    api_key: str,
    system_prompt: str,
    user_content,
    model: str = OPENAI_CHAT_MODEL,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """Call OpenAI chat completions and return the assistant text response.

    Args:
        api_key: User's OpenAI API key.
        system_prompt: System message string.
        user_content: Either a plain string or a list of content parts
                      (multimodal — text / image_url dicts).
        model: Chat model to use.
        max_tokens: Max tokens in the response.
        temperature: Sampling temperature.
    Returns:
        Stripped text of the assistant message.
    Raises:
        RuntimeError on API errors.
    """
    token_budget = max_tokens
    accumulated_text = ""
    for attempt in range(3):
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
        _raise_for_openai(resp, "OpenAI chat")
        data = resp.json()
        choice = data["choices"][0]
        content = choice["message"]["content"].strip()
        accumulated_text = f"{accumulated_text}{content}".strip()
        if choice.get("finish_reason") != "length":
            return accumulated_text
        token_budget *= 2

    if accumulated_text:
        return accumulated_text

    raise RuntimeError("OpenAI chat output was truncated before any text was returned.")


def openai_transcribe_file(
    api_key: str,
    file_path: str,
    mime_type: str | None = None,
    model: str = OPENAI_TRANSCRIBE_MODEL,
) -> str:
    """Transcribe an audio/video file with OpenAI and return plain text."""
    with open(file_path, "rb") as media_file:
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
    _raise_for_openai(resp, "OpenAI transcription")

    payload = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    text = (payload.get("text") or "").strip() if isinstance(payload, dict) else ""
    if not text:
        raise RuntimeError("OpenAI transcription returned no text.")
    return text


# ── Internal helpers ──────────────────────────────────────────────────

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
