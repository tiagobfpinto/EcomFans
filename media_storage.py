import base64
import io
import mimetypes
import os
import secrets
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


def resolve_storage_path(storage_path: str) -> Path:
    return _resolve_under_root(storage_path)


def prepare_social_download_directory(user_id: int, download_id: int) -> Path:
    relative_dir = (
        Path("users")
        / str(user_id)
        / "social_downloads"
        / str(download_id)
    )
    directory = _resolve_under_root(relative_dir.as_posix())
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_competitor_ad_video(
    user_id: int,
    competitor_id: int,
    ad_id: int,
    extension: str,
    file_storage,
) -> tuple[str, int]:
    """Stream an uploaded competitor ad video into media storage.

    Returns (relative storage path, file size in bytes).
    """
    safe_ext = extension if extension.startswith(".") else f".{extension}"
    safe_ext = "".join(
        ch for ch in safe_ext if ch.isalnum() or ch in (".", "-", "_")
    ) or ".mp4"
    relative_path = (
        Path("users")
        / str(user_id)
        / "competitors"
        / str(competitor_id)
        / f"{ad_id}{safe_ext}"
    )
    absolute_path = _resolve_under_root(relative_path.as_posix())
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = absolute_path.with_name(f"{absolute_path.name}.{os.getpid()}.tmp")
    try:
        file_storage.save(tmp_path)
        os.replace(tmp_path, absolute_path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return relative_path.as_posix(), os.path.getsize(absolute_path)


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


def save_prompt_library_thumbnail(
    user_id: int,
    prompt_id: int,
    thumbnail_id: int,
    mime_type: str,
    data: bytes,
) -> str:
    return _save_image(
        Path("users") / str(user_id) / "prompts" / str(prompt_id),
        thumbnail_id,
        mime_type,
        data,
    )


def save_prompt_library_target_image(
    user_id: int,
    target_id: int,
    image_id: int,
    mime_type: str,
    data: bytes,
) -> str:
    return _save_image(
        Path("users") / str(user_id) / "prompt_targets" / str(target_id),
        image_id,
        mime_type,
        data,
    )


def save_storyboard_thumbnail(
    user_id: int,
    project_id: int,
    frame_id: int,
    mime_type: str,
    data: bytes,
) -> str:
    """Save a versioned frame thumbnail so database replacement stays atomic."""
    extension = _extension_from_mime(mime_type)
    filename = f"{frame_id}-{secrets.token_hex(8)}{extension}"
    relative_path = (
        Path("users")
        / str(user_id)
        / "storyboards"
        / str(project_id)
        / filename
    )
    absolute_path = _resolve_under_root(relative_path.as_posix())
    _write_bytes_atomic(absolute_path, data)
    return relative_path.as_posix()


def save_ai_generation_image(
    user_id: int,
    prompt_id: int,
    generation_id: int,
    mime_type: str,
    data: bytes,
) -> str:
    return _save_image(
        Path("users") / str(user_id) / "ai_image" / str(prompt_id),
        generation_id,
        mime_type,
        data,
    )


def save_saved_script_thumbnail(
    user_id: int,
    script_id: int,
    mime_type: str,
    data: bytes,
) -> str:
    return _save_image(
        Path("users") / str(user_id) / "scripts",
        script_id,
        mime_type,
        data,
    )


def save_avatar_result_before_image(
    user_id: int,
    batch_id: int,
    result_id: int,
    mime_type: str,
    data: bytes,
) -> str:
    return _save_image(
        Path("users") / str(user_id) / "avatars" / str(batch_id) / "before",
        result_id,
        mime_type,
        data,
    )


def save_avatar_result_after_image(
    user_id: int,
    batch_id: int,
    result_id: int,
    mime_type: str,
    data: bytes,
) -> str:
    return _save_image(
        Path("users") / str(user_id) / "avatars" / str(batch_id) / "after",
        result_id,
        mime_type,
        data,
    )


def save_creative_result_image(
    user_id: int,
    batch_id: int,
    result_id: int,
    mime_type: str,
    data: bytes,
) -> str:
    return _save_image(
        Path("users") / str(user_id) / "creatives" / str(batch_id),
        result_id,
        mime_type,
        data,
    )


def save_lacy_result_image(
    user_id: int,
    job_id: int,
    index: int,
    mime_type: str,
    data: bytes,
) -> str:
    return _save_image(
        Path("users") / str(user_id) / "lacy" / str(job_id),
        index,
        mime_type,
        data,
    )


def prepare_prompt_thumbnail_image(
    data: bytes,
    *,
    max_edge: int = 720,
    quality: int = 82,
) -> tuple[bytes, str, int, int]:
    """Downsample an uploaded prompt thumbnail to a compact 720p image."""
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:
        raise RuntimeError("Pillow is required to process prompt thumbnails.") from exc

    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            image.thumbnail((max_edge, max_edge), resampling)

            if image.mode not in ("RGB", "RGBA"):
                has_alpha = image.mode in ("LA", "PA") or "transparency" in image.info
                image = image.convert("RGBA" if has_alpha else "RGB")

            output = io.BytesIO()
            try:
                image.save(output, format="WEBP", quality=quality, method=6)
                mime_type = "image/webp"
            except Exception:
                output = io.BytesIO()
                if image.mode == "RGBA":
                    flattened = Image.new("RGB", image.size, (13, 13, 26))
                    flattened.paste(image, mask=image.getchannel("A"))
                    image = flattened
                else:
                    image = image.convert("RGB")
                image.save(
                    output,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                    progressive=True,
                )
                mime_type = "image/jpeg"

            return output.getvalue(), mime_type, image.width, image.height
    except UnidentifiedImageError as exc:
        raise ValueError("Uploaded file is not a valid image.") from exc


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
