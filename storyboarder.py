import logging
import mimetypes
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request, session
from werkzeug.utils import secure_filename

from auth import login_required
from db import Product, StoryboardFrame, StoryboardProject, db
from media_storage import (
    delete_storage_file,
    prepare_prompt_thumbnail_image,
    save_storyboard_thumbnail,
)


storyboarder_bp = Blueprint("storyboarder", __name__)
logger = logging.getLogger(__name__)

MAX_PROJECT_NAME_CHARS = 160
MAX_MARKDOWN_BYTES = 2 * 1024 * 1024
MAX_STORYBOARD_TEXT_BYTES = MAX_MARKDOWN_BYTES
MAX_THUMBNAIL_BYTES = 8 * 1024 * 1024
THUMBNAIL_MAX_EDGE = 720

FIELD_MAP = {
    "LABEL": "label",
    "TYPE": "clip_type",
    "TIMESTAMP": "timestamp",
    "PHOTO": "photo",
    "TRANSFORM": "transform_prompt",
    "VOICEOVER": "voiceover",
    "VIDEO": "video_prompt",
}
SECTION_RE = re.compile(
    r"^\s*\[\s*(?:(BASE_PROMPT)|CLIP_(\d+))\s*\]\s*$",
    re.IGNORECASE,
)
ANY_SECTION_RE = re.compile(r"^\s*\[[^\]]+\]\s*$")
FIELD_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*:\s?(.*)$")
CLIP_HEADER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?CLIP[\s_-]+(\d+)\s*(?:(?:—|–|-|:)\s*(.+?))?\s*$",
    re.IGNORECASE,
)
CLIP_FIELD_RE = re.compile(
    r"^\s*(Dialogue\b[^:]*|Top|Bottom)\s*:\s?(.*)$",
    re.IGNORECASE,
)
GENERATION_PROMPT_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?"
    r"(Image[\s_-]+to[\s_-]+video(?:[\s_-]+prompt)?|"
    r"Text[\s_-]+to[\s_-]+image(?:[\s_-]+prompt)?)"
    r"\s*(?::\s*)?(?:\*\*)?\s*(.*)$",
    re.IGNORECASE,
)
REFERENCE_RE = re.compile(r"^\s*@\S+")
NAMED_BLOCK_HEADER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?"
    r"([A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ0-9]*(?:[ _-][A-ZÀ-ÖØ-Þ0-9]+)*)"
    r"\s+(?:—|–|-)\s+(.+?)\s*$"
)
BRACKET_BLOCK_HEADER_RE = re.compile(
    r"^\s*\[\s*([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ0-9 _-]{0,79})\s*\]\s*$"
)


class StoryboardParseError(ValueError):
    def __init__(self, message: str, *, line: int | None = None, clip: int | None = None):
        self.line = line
        self.clip = clip
        context = []
        if line is not None:
            context.append(f"line {line}")
        if clip is not None:
            context.append(f"CLIP_{clip}")
        prefix = f"{', '.join(context)}: " if context else ""
        super().__init__(prefix + message)


@dataclass(frozen=True)
class ParsedStoryboardFrame:
    sort_order: int
    label: str
    clip_type: str
    timestamp: str
    photo: str
    transform_prompt: str
    voiceover: str
    video_prompt: str


@dataclass(frozen=True)
class ParsedStoryboard:
    base_prompt: str
    frames: list[ParsedStoryboardFrame]
    prompt_blocks: dict[str, str] = field(default_factory=dict)


def _trim_block(lines: list[str]) -> str:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end]).strip()


def _decode_storyboard_text(value: str | bytes, *, field_name: str) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise StoryboardParseError(
                f"{field_name} must be UTF-8 encoded."
            ) from exc
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str or bytes")
    return value.removeprefix("\ufeff")


def _spoken_text(value: str) -> str:
    value = value.strip()
    quote_pairs = (("\"", "\""), ("“", "”"), ("'", "'"))
    for opening, closing in quote_pairs:
        if value.startswith(opening) and value.endswith(closing) and len(value) >= 2:
            return value[len(opening) : -len(closing)].strip()
    return value


def _clip_label(voiceover: str, clip_number: int) -> str:
    compact = re.sub(r"\s+", " ", voiceover).strip()
    if not compact:
        return f"Clip {clip_number}"
    first_sentence = re.split(r"(?<=[.!?])\s+", compact, maxsplit=1)[0]
    if len(first_sentence) <= 90:
        return first_sentence
    return first_sentence[:89].rstrip() + "…"


def _clip_field_key(raw_key: str) -> str:
    normalized = re.sub(r"[\s_-]+", " ", raw_key).strip().casefold()
    if normalized.startswith("dialogue"):
        return "dialogue"
    return {
        "top": "top",
        "bottom": "bottom",
        "image to video": "image_to_video",
        "image to video prompt": "image_to_video",
        "text to image": "text_to_image",
        "text to image prompt": "text_to_image",
    }[normalized]


def _clip_field_label(key: str) -> str:
    return {
        "dialogue": "Dialogue",
        "top": "Top",
        "bottom": "Bottom",
        "image_to_video": "Image to video prompt",
        "text_to_image": "Text to image prompt",
    }.get(key, key.replace("_", " ").title())


def _normalize_prompt_block_name(name: str) -> str:
    return re.sub(r"[\s_-]+", " ", name).strip().upper()


def parse_storyboard_clips(
    base_prompt: str | bytes, clips_text: str | bytes
) -> ParsedStoryboard:
    """Parse pasted CLIP blocks such as those in ``example.md``."""
    base_prompt = _decode_storyboard_text(base_prompt, field_name="Base prompt").strip()
    clips_text = _decode_storyboard_text(clips_text, field_name="Clip text")
    if not base_prompt:
        raise StoryboardParseError("Base prompt cannot be empty.")
    if not clips_text.strip():
        raise StoryboardParseError("Clip text cannot be empty.")

    lines = clips_text.splitlines()
    frames: list[ParsedStoryboardFrame] = []
    prompt_blocks: dict[str, str] = {}
    current_clip: int | None = None
    current_timestamp = ""
    current_lines: list[str] = []
    current_start_line = 1
    current_block: str | None = None
    current_block_lines: list[str] = []
    current_block_start_line = 1

    def finish_block(finish_line: int) -> None:
        nonlocal current_block, current_block_lines
        if current_block is None:
            return
        value = _trim_block(current_block_lines)
        if not value:
            raise StoryboardParseError(
                f"[{current_block}] cannot be empty.", line=current_block_start_line
            )
        if current_block in prompt_blocks:
            raise StoryboardParseError(
                f"[{current_block}] may only be declared once.",
                line=current_block_start_line,
            )
        prompt_blocks[current_block] = value
        current_block = None
        current_block_lines = []

    def finish_clip(finish_line: int) -> None:
        nonlocal current_clip, current_timestamp, current_lines
        if current_clip is None:
            return

        fields: dict[str, list[str]] = {}
        field_lines: dict[str, int] = {}
        references: list[str] = []
        current_field: str | None = None

        for offset, line in enumerate(current_lines, start=current_start_line + 1):
            field_match = GENERATION_PROMPT_RE.match(line) or CLIP_FIELD_RE.match(
                line
            )
            if field_match:
                key = _clip_field_key(field_match.group(1))
                if key in fields:
                    raise StoryboardParseError(
                        f"{_clip_field_label(key)} may only appear once.",
                        line=offset,
                        clip=current_clip,
                    )
                fields[key] = [field_match.group(2)]
                field_lines[key] = offset
                current_field = key
                continue
            if REFERENCE_RE.match(line):
                references.append(line.strip())
                current_field = None
                continue
            if current_field is not None:
                fields[current_field].append(line)
            elif line.strip():
                raise StoryboardParseError(
                    "Expected a Dialogue, Top, Bottom, Text to image prompt, or Image to video prompt field.",
                    line=offset,
                    clip=current_clip,
                )

        if not fields:
            raise StoryboardParseError(
                "The clip must contain at least one supported field.",
                line=finish_line,
                clip=current_clip,
            )
        if not current_timestamp:
            raise StoryboardParseError(
                "The clip heading must include a timestamp.",
                line=current_start_line,
                clip=current_clip,
            )

        values = {key: _trim_block(value) for key, value in fields.items()}
        for key, value in values.items():
            if not value:
                raise StoryboardParseError(
                    f"{_clip_field_label(key)} cannot be empty.",
                    line=field_lines[key],
                    clip=current_clip,
                )

        voiceover = _spoken_text(values.get("dialogue", ""))
        visual_parts = []
        if values.get("top"):
            visual_parts.append(f"Top: {values['top']}")
        if values.get("bottom"):
            visual_parts.append(f"Bottom: {values['bottom']}")
        visual_prompt = "\n\n".join(visual_parts)
        text_to_image_prompt = values.get("text_to_image") or visual_prompt
        image_to_video_prompt = values.get("image_to_video") or _trim_block(
            current_lines
        )
        frames.append(
            ParsedStoryboardFrame(
                sort_order=current_clip,
                label=_clip_label(voiceover, current_clip),
                clip_type=(
                    "split_screen"
                    if values.get("top") and values.get("bottom")
                    else "custom"
                ),
                timestamp=current_timestamp,
                photo="\n".join(references),
                transform_prompt=text_to_image_prompt,
                voiceover=voiceover,
                video_prompt=image_to_video_prompt,
            )
        )
        current_clip = None
        current_timestamp = ""
        current_lines = []

    for line_number, line in enumerate(lines, start=1):
        header = CLIP_HEADER_RE.fullmatch(line)
        if header:
            finish_block(line_number)
            finish_clip(line_number)
            clip_number = int(header.group(1))
            expected = len(frames) + 1
            if clip_number <= 0:
                raise StoryboardParseError(
                    "Clip numbers must be positive.", line=line_number, clip=clip_number
                )
            if clip_number != expected:
                raise StoryboardParseError(
                    f"Expected CLIP {expected} next.", line=line_number, clip=clip_number
                )
            current_clip = clip_number
            current_timestamp = (header.group(2) or "").strip()
            current_start_line = line_number
            current_lines = []
            continue

        named_block = NAMED_BLOCK_HEADER_RE.fullmatch(line)
        bracket_block = BRACKET_BLOCK_HEADER_RE.fullmatch(line)
        if named_block or (bracket_block and current_clip is None):
            finish_clip(line_number)
            finish_block(line_number)
            raw_name = (named_block or bracket_block).group(1)
            current_block = _normalize_prompt_block_name(raw_name)
            current_block_start_line = line_number
            current_block_lines = []
            continue

        if current_block is not None:
            current_block_lines.append(line)
        elif current_clip is not None:
            current_lines.append(line)
        elif line.strip():
            raise StoryboardParseError(
                "Content must belong to a named block or CLIP section.",
                line=line_number,
            )

    finish_block(len(lines) or 1)
    if current_clip is None and not frames:
        raise StoryboardParseError("At least one CLIP section is required.")
    finish_clip(len(lines) or 1)
    return ParsedStoryboard(
        base_prompt=base_prompt,
        frames=frames,
        prompt_blocks=prompt_blocks,
    )


def parse_storyboard_markdown(markdown: str | bytes) -> ParsedStoryboard:
    """Parse the strict, line-oriented Storyboarder Markdown format."""
    if isinstance(markdown, bytes):
        try:
            markdown = markdown.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise StoryboardParseError("The file must be UTF-8 encoded.") from exc
    if not isinstance(markdown, str):
        raise TypeError("markdown must be str or bytes")
    markdown = markdown.removeprefix("\ufeff")

    lines = markdown.splitlines()
    base_seen = False
    base_lines: list[str] = []
    base_prompt = ""
    frames: list[ParsedStoryboardFrame] = []
    seen_clip_numbers: set[int] = set()

    current_clip: int | None = None
    current_fields: dict[str, list[str]] = {}
    current_field: str | None = None
    current_field_lines: dict[str, int] = {}

    def finish_clip(finish_line: int) -> None:
        nonlocal current_clip, current_fields, current_field, current_field_lines
        if current_clip is None:
            return

        missing = [key for key in FIELD_MAP if key not in current_fields]
        if missing:
            raise StoryboardParseError(
                f"Missing required field(s): {', '.join(missing)}.",
                line=finish_line,
                clip=current_clip,
            )

        values = {}
        for source_key, target_key in FIELD_MAP.items():
            value = _trim_block(current_fields[source_key])
            if not value:
                raise StoryboardParseError(
                    f"{source_key} cannot be empty.",
                    line=current_field_lines[source_key],
                    clip=current_clip,
                )
            values[target_key] = value

        frames.append(ParsedStoryboardFrame(sort_order=current_clip, **values))
        current_clip = None
        current_fields = {}
        current_field = None
        current_field_lines = {}

    for line_number, line in enumerate(lines, start=1):
        section = SECTION_RE.fullmatch(line)
        if section:
            is_base = bool(section.group(1))
            if is_base:
                if base_seen:
                    raise StoryboardParseError(
                        "[BASE_PROMPT] may only appear once.", line=line_number
                    )
                if current_clip is not None or frames:
                    raise StoryboardParseError(
                        "[BASE_PROMPT] must appear before every clip.",
                        line=line_number,
                    )
                base_seen = True
                continue

            clip_number = int(section.group(2))
            if not base_seen:
                raise StoryboardParseError(
                    "[BASE_PROMPT] must appear before the first clip.",
                    line=line_number,
                    clip=clip_number,
                )
            if current_clip is None and not frames:
                base_prompt = _trim_block(base_lines)
                if not base_prompt:
                    raise StoryboardParseError(
                        "[BASE_PROMPT] cannot be empty.", line=line_number
                    )
            else:
                finish_clip(line_number)

            if clip_number <= 0:
                raise StoryboardParseError(
                    "Clip numbers must be positive.",
                    line=line_number,
                    clip=clip_number,
                )
            if clip_number in seen_clip_numbers:
                raise StoryboardParseError(
                    "Clip section is duplicated.",
                    line=line_number,
                    clip=clip_number,
                )
            expected = len(seen_clip_numbers) + 1
            if clip_number != expected:
                raise StoryboardParseError(
                    f"Expected [CLIP_{expected}] next.",
                    line=line_number,
                    clip=clip_number,
                )

            seen_clip_numbers.add(clip_number)
            current_clip = clip_number
            current_fields = {}
            current_field = None
            current_field_lines = {}
            continue

        if ANY_SECTION_RE.fullmatch(line):
            raise StoryboardParseError("Unknown section heading.", line=line_number)

        if current_clip is not None:
            field_match = FIELD_RE.match(line)
            if field_match:
                key = field_match.group(1).upper()
                if key not in FIELD_MAP:
                    raise StoryboardParseError(
                        f"Unknown field {key}.",
                        line=line_number,
                        clip=current_clip,
                    )
                if key in current_fields:
                    raise StoryboardParseError(
                        f"{key} may only appear once.",
                        line=line_number,
                        clip=current_clip,
                    )
                current_fields[key] = [field_match.group(2)]
                current_field_lines[key] = line_number
                current_field = key
            elif current_field is not None:
                current_fields[current_field].append(line)
            elif line.strip():
                raise StoryboardParseError(
                    "Content must belong to a named field.",
                    line=line_number,
                    clip=current_clip,
                )
        elif base_seen:
            base_lines.append(line)
        elif line.strip():
            raise StoryboardParseError(
                "Content found before [BASE_PROMPT].", line=line_number
            )

    if not base_seen:
        raise StoryboardParseError("Missing [BASE_PROMPT] section.")
    if current_clip is None and not frames:
        base_prompt = _trim_block(base_lines)
        if not base_prompt:
            raise StoryboardParseError("[BASE_PROMPT] cannot be empty.")
        raise StoryboardParseError("At least one [CLIP_N] section is required.")

    finish_clip(len(lines) or 1)
    return ParsedStoryboard(base_prompt=base_prompt, frames=frames)


def _get_project(project_id: int) -> StoryboardProject | None:
    return StoryboardProject.query.filter_by(
        id=project_id, user_id=session["user_id"]
    ).first()


def _get_frame(frame_id: int) -> StoryboardFrame | None:
    return (
        StoryboardFrame.query.join(
            StoryboardProject,
            StoryboardProject.id == StoryboardFrame.project_id,
        )
        .filter(
            StoryboardFrame.id == frame_id,
            StoryboardProject.user_id == session["user_id"],
        )
        .first()
    )


def _get_product(product_id) -> Product | None:
    try:
        parsed_id = int(product_id)
    except (TypeError, ValueError):
        return None
    return Product.query.filter_by(
        id=parsed_id, user_id=session["user_id"]
    ).first()


def _serialize_frame(frame: StoryboardFrame) -> dict:
    return {
        "id": frame.id,
        "project_id": frame.project_id,
        "sort_order": frame.sort_order,
        "label": frame.label,
        "clip_type": frame.clip_type,
        "timestamp": frame.timestamp,
        "photo": frame.photo,
        "transform_prompt": frame.transform_prompt,
        "voiceover": frame.voiceover,
        "video_prompt": frame.video_prompt,
        "thumbnail_url": (
            f"/media/storyboard-thumbnails/{frame.id}"
            if frame.thumbnail_storage_path
            else None
        ),
        "updated_at": frame.updated_at.isoformat() if frame.updated_at else None,
    }


def _deserialize_prompt_blocks(project: StoryboardProject) -> dict[str, str]:
    try:
        value = json.loads(project.prompt_blocks_json or "{}")
    except (TypeError, ValueError):
        logger.warning("Invalid prompt blocks JSON for storyboard project id=%s", project.id)
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        _normalize_prompt_block_name(str(name)): str(content)
        for name, content in value.items()
        if str(name).strip() and isinstance(content, str) and content.strip()
    }


def _serialize_project(project: StoryboardProject, *, include_frames=False) -> dict:
    product = project.product
    frames = list(project.frames)
    payload = {
        "id": project.id,
        "name": project.name,
        "base_prompt": project.base_prompt or "",
        "prompt_blocks": _deserialize_prompt_blocks(project),
        "product": {"id": product.id, "name": product.name} if product else None,
        "frame_count": len(frames),
        "thumbnail_url": next(
            (
                f"/media/storyboard-thumbnails/{frame.id}"
                for frame in frames
                if frame.thumbnail_storage_path
            ),
            None,
        ),
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }
    if include_frames:
        payload["frames"] = [_serialize_frame(frame) for frame in frames]
    return payload


def _request_payload() -> dict:
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


def _truthy(value) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


@storyboarder_bp.route("/storyboarder")
@login_required
def storyboarder_page():
    projects = (
        StoryboardProject.query.filter_by(user_id=session["user_id"])
        .order_by(StoryboardProject.created_at.desc())
        .all()
    )
    products = (
        Product.query.filter_by(user_id=session["user_id"])
        .order_by(Product.name.asc())
        .all()
    )
    return render_template(
        "storyboarder.html",
        projects=[_serialize_project(project) for project in projects],
        products=[{"id": product.id, "name": product.name} for product in products],
    )


@storyboarder_bp.route("/storyboarder/projects", methods=["POST"])
@login_required
def create_storyboard_project():
    payload = _request_payload()
    name = (payload.get("name") or "").strip()[:MAX_PROJECT_NAME_CHARS].strip()
    if not name:
        return jsonify({"error": "Project name is required."}), 400

    product = _get_product(payload.get("product_id"))
    if not product:
        return jsonify({"error": "Choose one of your products."}), 400

    project = StoryboardProject(
        user_id=session["user_id"],
        product_id=product.id,
        name=name,
        base_prompt="",
        prompt_blocks_json="{}",
    )
    db.session.add(project)
    db.session.commit()
    return jsonify({"project": _serialize_project(project)}), 201


@storyboarder_bp.route("/storyboarder/projects/<int:project_id>")
@login_required
def storyboard_project_page(project_id: int):
    project = _get_project(project_id)
    if not project:
        abort(404)
    products = (
        Product.query.filter_by(user_id=session["user_id"])
        .order_by(Product.name.asc())
        .all()
    )
    return render_template(
        "storyboarder_detail.html",
        project=_serialize_project(project, include_frames=True),
        products=[{"id": product.id, "name": product.name} for product in products],
        max_markdown_mb=MAX_MARKDOWN_BYTES // (1024 * 1024),
        max_thumbnail_mb=MAX_THUMBNAIL_BYTES // (1024 * 1024),
    )


@storyboarder_bp.route(
    "/storyboarder/projects/<int:project_id>", methods=["PATCH"]
)
@login_required
def update_storyboard_project(project_id: int):
    project = _get_project(project_id)
    if not project:
        return jsonify({"error": "Project not found."}), 404

    payload = _request_payload()
    allowed = {"name", "product_id", "base_prompt"}
    unknown = set(payload) - allowed
    if unknown:
        return jsonify({"error": f"Unknown field: {sorted(unknown)[0]}."}), 400

    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Project name is required."}), 400
        project.name = name[:MAX_PROJECT_NAME_CHARS].strip()
    if "product_id" in payload:
        product = _get_product(payload.get("product_id"))
        if not product:
            return jsonify({"error": "Choose one of your products."}), 400
        project.product_id = product.id
    if "base_prompt" in payload:
        value = payload.get("base_prompt")
        if not isinstance(value, str):
            return jsonify({"error": "Base prompt must be text."}), 400
        project.base_prompt = value

    db.session.commit()
    return jsonify({"project": _serialize_project(project)})


@storyboarder_bp.route(
    "/storyboarder/projects/<int:project_id>", methods=["DELETE"]
)
@login_required
def delete_storyboard_project(project_id: int):
    project = _get_project(project_id)
    if not project:
        return jsonify({"error": "Project not found."}), 404
    thumbnail_paths = [
        frame.thumbnail_storage_path
        for frame in project.frames
        if frame.thumbnail_storage_path
    ]
    try:
        db.session.delete(project)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to delete storyboard project id=%s", project_id)
        return jsonify({"error": "Failed to delete the project."}), 500
    for storage_path in thumbnail_paths:
        delete_storage_file(storage_path)
    return jsonify({"ok": True})


@storyboarder_bp.route(
    "/storyboarder/projects/<int:project_id>/clips", methods=["POST"]
)
@storyboarder_bp.route(
    "/storyboarder/projects/<int:project_id>/import", methods=["POST"]
)
@login_required
def create_storyboard_frames(project_id: int):
    project = _get_project(project_id)
    if not project:
        return jsonify({"error": "Project not found."}), 404

    try:
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            base_prompt = payload.get("base_prompt")
            clips_text = payload.get("clips_text")
            if not isinstance(base_prompt, str):
                return jsonify({"error": "Base prompt must be text."}), 400
            if not isinstance(clips_text, str):
                return jsonify({"error": "Clip text must be text."}), 400
            if len(clips_text.encode("utf-8")) > MAX_STORYBOARD_TEXT_BYTES:
                return jsonify({"error": "Clip text must be 2 MB or smaller."}), 413
            parsed = parse_storyboard_clips(base_prompt, clips_text)
            replace_existing = _truthy(payload.get("replace_existing"))
        else:
            # Keep the former file endpoint working for existing integrations. The
            # product UI now sends pasted text to the JSON endpoint above.
            upload = request.files.get("file")
            if not upload or not upload.filename:
                return jsonify({"error": "Add a base prompt and clip text."}), 400
            if Path(upload.filename).suffix.casefold() != ".md":
                return jsonify({"error": "Only .md files are supported."}), 400
            raw_bytes = upload.stream.read(MAX_MARKDOWN_BYTES + 1)
            if len(raw_bytes) > MAX_MARKDOWN_BYTES:
                return jsonify({"error": "The file must be 2 MB or smaller."}), 413
            if not raw_bytes:
                return jsonify({"error": "The uploaded file is empty."}), 400
            decoded = _decode_storyboard_text(raw_bytes, field_name="The file")
            if SECTION_RE.match(decoded.lstrip().splitlines()[0] if decoded.strip() else ""):
                parsed = parse_storyboard_markdown(decoded)
            else:
                parsed = parse_storyboard_clips(project.base_prompt, decoded)
            replace_existing = _truthy(request.form.get("replace_existing"))
    except StoryboardParseError as exc:
        return jsonify(
            {
                "error": str(exc),
                "line": exc.line,
                "clip": exc.clip,
            }
        ), 400

    if project.frames and not replace_existing:
        return jsonify(
            {
                "error": "Creating these clips will replace every existing storyboard image and thumbnail.",
                "conflict": True,
                "frame_count": len(project.frames),
            }
        ), 409

    old_thumbnail_paths = [
        frame.thumbnail_storage_path
        for frame in project.frames
        if frame.thumbnail_storage_path
    ]
    try:
        project.base_prompt = parsed.base_prompt
        project.prompt_blocks_json = json.dumps(
            parsed.prompt_blocks,
            ensure_ascii=False,
            sort_keys=True,
        )
        project.frames.clear()
        db.session.flush()
        for parsed_frame in parsed.frames:
            project.frames.append(
                StoryboardFrame(
                    sort_order=parsed_frame.sort_order,
                    label=parsed_frame.label,
                    clip_type=parsed_frame.clip_type,
                    timestamp=parsed_frame.timestamp,
                    photo=parsed_frame.photo,
                    transform_prompt=parsed_frame.transform_prompt,
                    voiceover=parsed_frame.voiceover,
                    video_prompt=parsed_frame.video_prompt,
                )
            )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to create storyboard frames project id=%s", project_id)
        return jsonify({"error": "Failed to save the storyboard clips."}), 500

    for storage_path in old_thumbnail_paths:
        delete_storage_file(storage_path)
    return jsonify(
        {
            "project": _serialize_project(project, include_frames=True),
            "created_frame_count": len(parsed.frames),
            "imported_frame_count": len(parsed.frames),
        }
    )


@storyboarder_bp.route("/storyboarder/frames/<int:frame_id>", methods=["PATCH"])
@login_required
def update_storyboard_frame(frame_id: int):
    frame = _get_frame(frame_id)
    if not frame:
        return jsonify({"error": "Storyboard image not found."}), 404

    payload = _request_payload()
    allowed = set(FIELD_MAP.values())
    unknown = set(payload) - allowed
    if unknown:
        return jsonify({"error": f"Unknown field: {sorted(unknown)[0]}."}), 400

    for field_name, value in payload.items():
        if not isinstance(value, str):
            return jsonify({"error": f"{field_name} must be text."}), 400
        setattr(frame, field_name, value)
    db.session.commit()
    return jsonify({"frame": _serialize_frame(frame)})


@storyboarder_bp.route(
    "/storyboarder/frames/<int:frame_id>/thumbnail", methods=["POST"]
)
@login_required
def upload_storyboard_thumbnail(frame_id: int):
    frame = _get_frame(frame_id)
    if not frame:
        return jsonify({"error": "Storyboard image not found."}), 404

    upload = request.files.get("thumbnail")
    if not upload or not upload.filename:
        return jsonify({"error": "Choose an image to upload."}), 400
    mime_type = (
        upload.content_type
        or mimetypes.guess_type(upload.filename)[0]
        or "application/octet-stream"
    ).split(";", 1)[0].strip().lower()
    if not mime_type.startswith("image/"):
        return jsonify({"error": "Only image files are supported."}), 400

    raw_bytes = upload.stream.read(MAX_THUMBNAIL_BYTES + 1)
    if len(raw_bytes) > MAX_THUMBNAIL_BYTES:
        return jsonify({"error": "The image must be 8 MB or smaller."}), 413
    if not raw_bytes:
        return jsonify({"error": "The uploaded image is empty."}), 400

    try:
        processed, processed_mime, width, height = prepare_prompt_thumbnail_image(
            raw_bytes, max_edge=THUMBNAIL_MAX_EDGE
        )
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    project = frame.project
    old_storage_path = frame.thumbnail_storage_path
    new_storage_path = None
    try:
        new_storage_path = save_storyboard_thumbnail(
            session["user_id"],
            project.id,
            frame.id,
            processed_mime,
            processed,
        )
        frame.thumbnail_filename = (
            secure_filename(upload.filename) or f"storyboard_{frame.id}"
        )[:255]
        frame.thumbnail_mime_type = processed_mime
        frame.thumbnail_storage_path = new_storage_path
        frame.thumbnail_width = width
        frame.thumbnail_height = height
        frame.thumbnail_file_size_bytes = len(processed)
        db.session.commit()
    except Exception:
        db.session.rollback()
        delete_storage_file(new_storage_path)
        logger.exception("Failed to save storyboard thumbnail frame_id=%s", frame_id)
        return jsonify({"error": "Failed to save the thumbnail."}), 500

    if old_storage_path and old_storage_path != new_storage_path:
        delete_storage_file(old_storage_path)
    return jsonify({"frame": _serialize_frame(frame)})
