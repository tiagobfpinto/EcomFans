"""Shared browser-facing security helpers.

Two jobs live here:

* deciding which MIME types are safe to hand a browser *inline* from our own
  origin, and
* building the Content-Security-Policy the app sends on every response.

The MIME rules matter more than they look. Anything the browser will parse as
markup — SVG, HTML, XML — executes script in *our* origin when it is served
inline, which turns a file upload or a media proxy into stored XSS against a
logged-in user. So inline serving is allowlist-only, and everything else is
forced to download as an opaque byte stream.
"""

from __future__ import annotations

import secrets

from flask import g


# Raster formats a browser renders as a picture and never as markup.
INLINE_SAFE_IMAGE_TYPES = frozenset(
    {
        "image/jpeg",
        "image/pjpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/avif",
        "image/bmp",
        "image/x-ms-bmp",
        "image/tiff",
        "image/heic",
        "image/heif",
    }
)

INLINE_SAFE_VIDEO_TYPES = frozenset(
    {
        "video/mp4",
        "video/webm",
        "video/ogg",
        "video/quicktime",
        "video/x-m4v",
    }
)

INLINE_SAFE_AUDIO_TYPES = frozenset(
    {
        "audio/mpeg",
        "audio/mp4",
        "audio/aac",
        "audio/ogg",
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
    }
)

# Uploads are held to the image allowlist. SVG is absent on purpose: it is a
# script-bearing document format, not a picture.
UPLOAD_ALLOWED_IMAGE_TYPES = INLINE_SAFE_IMAGE_TYPES

OPAQUE_TYPE = "application/octet-stream"


def normalize_mime(raw: object) -> str:
    """Lowercase a Content-Type and drop its parameters."""
    text = str(raw or "").split(";", 1)[0].strip().lower()
    # A header is not allowed to carry control characters into our response.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return ""
    return text


def is_inline_safe(mime: str, *, allow_video: bool = False, allow_audio: bool = False) -> bool:
    normalized = normalize_mime(mime)
    if normalized in INLINE_SAFE_IMAGE_TYPES:
        return True
    if allow_video and normalized in INLINE_SAFE_VIDEO_TYPES:
        return True
    if allow_audio and normalized in INLINE_SAFE_AUDIO_TYPES:
        return True
    return False


def safe_media_mime(
    raw: object,
    *,
    allow_video: bool = False,
    allow_audio: bool = False,
) -> tuple[str, bool]:
    """Return ``(mime_to_send, may_render_inline)`` for a media response.

    Unrecognised or markup-bearing types collapse to ``application/octet-stream``
    so the browser downloads the bytes instead of parsing them.
    """
    normalized = normalize_mime(raw)
    if is_inline_safe(normalized, allow_video=allow_video, allow_audio=allow_audio):
        return normalized, True
    return OPAQUE_TYPE, False


def is_allowed_upload_image(raw: object) -> bool:
    """True when an uploaded file may be stored as an image."""
    return normalize_mime(raw) in UPLOAD_ALLOWED_IMAGE_TYPES


def upload_image_type_error() -> str:
    return (
        "Only JPEG, PNG, GIF, WebP, AVIF, BMP, TIFF or HEIC images are accepted. "
        "SVG files are not allowed because they can carry scripts."
    )


def harden_media_response(response, *, inline: bool, download_name: str | None = None):
    """Apply the headers a user-supplied media response must always carry."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Belt and braces: even if a type slipped through, this policy stops the
    # document from loading scripts, styles or subresources of its own.
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; sandbox; base-uri 'none'; form-action 'none'"
    )
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if not inline:
        safe_name = _sanitize_download_name(download_name)
        response.headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    return response


def _sanitize_download_name(name: str | None) -> str:
    from werkzeug.utils import secure_filename

    cleaned = secure_filename(name or "") or "download"
    return cleaned[:150]


# ---------------------------------------------------------------------------
# Content-Security-Policy
# ---------------------------------------------------------------------------

_NONCE_KEY = "_csp_nonce"


def csp_nonce() -> str:
    """Per-response nonce for the inline <script> blocks our templates ship."""
    nonce = getattr(g, _NONCE_KEY, None)
    if not nonce:
        nonce = secrets.token_urlsafe(16)
        setattr(g, _NONCE_KEY, nonce)
    return nonce


def build_content_security_policy(nonce: str, *, allow_inline_scripts: bool = False) -> str:
    """Assemble the app's CSP.

    ``allow_inline_scripts`` exists for exactly one page — the funnel editor.
    It previews a page template in a ``srcdoc`` iframe, and a srcdoc document
    inherits its parent's policy, so a nonce-only script-src would stop the
    template's own bundled script from running. That page shows the signed-in
    user nothing but their own content, so the weaker policy is contained.
    """
    script_src = ["'self'", f"'nonce-{nonce}'"]
    if allow_inline_scripts:
        script_src.append("'unsafe-inline'")

    directives = [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'self'",
        "form-action 'self'",
        f"script-src {' '.join(script_src)}",
        # Inline style attributes are used widely in the templates and cannot
        # execute script on their own.
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        "img-src 'self' data: blob: https:",
        "media-src 'self' data: blob: https:",
        "connect-src 'self'",
        "frame-src 'self' blob:",
        "worker-src 'self' blob:",
        "manifest-src 'self'",
        "upgrade-insecure-requests",
    ]
    return "; ".join(directives)


PERMISSIONS_POLICY = (
    "accelerometer=(), autoplay=(self), camera=(), display-capture=(), "
    "encrypted-media=(), fullscreen=(self), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), midi=(), payment=(), usb=(), "
    "interest-cohort=()"
)
