import base64
import mimetypes
import os
from pathlib import Path

from flask import current_app


MIME_EXTENSION_MAP = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
}


def get_media_root() -> Path:
    configured = current_app.config.get("MEDIA_ROOT")
    if configured:
        root = Path(configured)
    else:
        root = Path(current_app.instance_path) / "media"

    if not root.is_absolute():
        root = Path(current_app.root_path) / root
    return root


def _extension_from_mime(mime_type: str) -> str:
    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError("Only image MIME types are supported.")

    normalized = mime_type.split(";")[0].strip().lower()
    mapped = MIME_EXTENSION_MAP.get(normalized)
    if mapped:
        return mapped

    guessed = mimetypes.guess_extension(normalized) or ""
    if guessed == ".jpe":
        return ".jpg"
    if guessed:
        return guessed

    subtype = normalized.split("/", 1)[1]
    safe_subtype = "".join(ch for ch in subtype if ch.isalnum() or ch in ("+", "-", "_"))
    if not safe_subtype:
        raise ValueError("Invalid MIME subtype for extension mapping.")
    return f".{safe_subtype}"


def _resolve_under_root(storage_path: str) -> Path:
    root = get_media_root().resolve()
    candidate = (root / storage_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Resolved path escapes media root.") from exc
    return candidate


def _write_bytes_atomic(target_path: Path, data: bytes) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name(f"{target_path.name}.{os.getpid()}.tmp")
    with open(tmp_path, "wb") as handle:
        handle.write(data)
    os.replace(tmp_path, target_path)


def _save_image(relative_dir: Path, image_id: int, mime_type: str, data: bytes) -> str:
    extension = _extension_from_mime(mime_type)
    relative_path = relative_dir / f"{image_id}{extension}"
    absolute_path = _resolve_under_root(relative_path.as_posix())
    _write_bytes_atomic(absolute_path, data)
    return relative_path.as_posix()


def save_product_image(
    user_id: int,
    product_id: int,
    image_id: int,
    mime_type: str,
    data: bytes,
) -> str:
    return _save_image(
        Path("users") / str(user_id) / "products" / str(product_id),
        image_id,
        mime_type,
        data,
    )


def save_inspiration_image(
    user_id: int,
    inspiration_id: int,
    mime_type: str,
    data: bytes,
) -> str:
    return _save_image(
        Path("users") / str(user_id) / "inspirations",
        inspiration_id,
        mime_type,
        data,
    )


def read_storage_bytes(storage_path: str | None) -> bytes | None:
    if not storage_path:
        return None
    try:
        path = _resolve_under_root(storage_path)
    except ValueError:
        return None
    if not path.exists() or not path.is_file():
        return None
    return path.read_bytes()


def read_storage_base64(storage_path: str | None) -> str | None:
    image_bytes = read_storage_bytes(storage_path)
    if image_bytes is None:
        return None
    return base64.b64encode(image_bytes).decode("utf-8")


def get_image_payload(
    storage_path: str | None,
    mime_type: str,
    fallback_b64: str | None = None,
) -> dict | None:
    storage_b64 = read_storage_base64(storage_path)
    if storage_b64:
        return {"mime_type": mime_type, "data": storage_b64}
    if fallback_b64:
        return {"mime_type": mime_type, "data": fallback_b64}
    return None


def delete_storage_file(storage_path: str | None) -> None:
    if not storage_path:
        return
    try:
        path = _resolve_under_root(storage_path)
    except ValueError:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return
