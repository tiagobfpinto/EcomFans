import json
import math
import re

from flask import Blueprint, abort, jsonify, render_template, request, session

from auth import login_required
from db import NoteBoard, db


notes_bp = Blueprint("notes", __name__)

MAX_BOARD_NAME_CHARS = 160
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_OBJECTS = 1000
MAX_TEXT_CHARS = 20000
MAX_COORDINATE = 1_000_000
MAX_DIMENSION = 100_000
ALLOWED_TYPES = {"text", "sticky", "rectangle", "ellipse", "line", "arrow"}
ALLOWED_COLORS = {"transparent"}
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")

COMMON_FIELDS = {
    "id",
    "type",
    "x",
    "y",
    "width",
    "height",
    "rotation",
    "fill",
    "stroke",
    "stroke_width",
    "opacity",
}
TEXT_FIELDS = {"text", "font_size", "font_weight", "text_align", "text_color"}


def empty_document() -> dict:
    return {
        "schema_version": 1,
        "viewport": {"x": 0, "y": 0, "zoom": 1},
        "objects": [],
    }


def _compact_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _is_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _number(value, field: str, *, minimum: float, maximum: float) -> float:
    if not _is_number(value) or value < minimum or value > maximum:
        raise ValueError(f"{field} must be a finite number between {minimum:g} and {maximum:g}.")
    return float(value)


def _color(value, field: str) -> str:
    if not isinstance(value, str) or (
        value not in ALLOWED_COLORS and not COLOR_PATTERN.fullmatch(value)
    ):
        raise ValueError(f"{field} must be a six-digit hex color or transparent.")
    return value.lower()


def validate_document(value) -> tuple[dict, str]:
    if not isinstance(value, dict):
        raise ValueError("Document must be a JSON object.")
    if set(value) != {"schema_version", "viewport", "objects"}:
        raise ValueError("Document must contain only schema_version, viewport, and objects.")
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported board document version.")

    viewport = value.get("viewport")
    if not isinstance(viewport, dict) or set(viewport) != {"x", "y", "zoom"}:
        raise ValueError("Viewport must contain x, y, and zoom.")
    clean_viewport = {
        "x": _number(
            viewport.get("x"), "viewport.x", minimum=-MAX_COORDINATE, maximum=MAX_COORDINATE
        ),
        "y": _number(
            viewport.get("y"), "viewport.y", minimum=-MAX_COORDINATE, maximum=MAX_COORDINATE
        ),
        "zoom": _number(viewport.get("zoom"), "viewport.zoom", minimum=0.1, maximum=4),
    }

    objects = value.get("objects")
    if not isinstance(objects, list):
        raise ValueError("Document objects must be an array.")
    if len(objects) > MAX_OBJECTS:
        raise ValueError(f"A board can contain at most {MAX_OBJECTS} objects.")

    ids = set()
    clean_objects = []
    for index, item in enumerate(objects):
        label = f"objects[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{label} must be an object.")
        item_type = item.get("type")
        if item_type not in ALLOWED_TYPES:
            raise ValueError(f"{label}.type is not supported.")
        allowed = COMMON_FIELDS | (TEXT_FIELDS if item_type in {"text", "sticky"} else set())
        if set(item) != allowed:
            raise ValueError(f"{label} contains missing or unsupported fields.")

        object_id = item.get("id")
        if not isinstance(object_id, str) or not object_id or len(object_id) > 80:
            raise ValueError(f"{label}.id must be a non-empty string up to 80 characters.")
        if object_id in ids:
            raise ValueError(f"{label}.id must be unique.")
        ids.add(object_id)

        width_minimum = -MAX_DIMENSION if item_type in {"line", "arrow"} else 1
        height_minimum = -MAX_DIMENSION if item_type in {"line", "arrow"} else 1
        width = _number(
            item.get("width"), f"{label}.width", minimum=width_minimum, maximum=MAX_DIMENSION
        )
        height = _number(
            item.get("height"), f"{label}.height", minimum=height_minimum, maximum=MAX_DIMENSION
        )
        if item_type in {"line", "arrow"} and width == 0 and height == 0:
            raise ValueError(f"{label} must have a visible length.")

        clean = {
            "id": object_id,
            "type": item_type,
            "x": _number(
                item.get("x"), f"{label}.x", minimum=-MAX_COORDINATE, maximum=MAX_COORDINATE
            ),
            "y": _number(
                item.get("y"), f"{label}.y", minimum=-MAX_COORDINATE, maximum=MAX_COORDINATE
            ),
            "width": width,
            "height": height,
            "rotation": _number(
                item.get("rotation"), f"{label}.rotation", minimum=-36000, maximum=36000
            ),
            "fill": _color(item.get("fill"), f"{label}.fill"),
            "stroke": _color(item.get("stroke"), f"{label}.stroke"),
            "stroke_width": _number(
                item.get("stroke_width"), f"{label}.stroke_width", minimum=0, maximum=24
            ),
            "opacity": _number(
                item.get("opacity"), f"{label}.opacity", minimum=0.05, maximum=1
            ),
        }
        if item_type in {"text", "sticky"}:
            text = item.get("text")
            if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
                raise ValueError(f"{label}.text must be text up to {MAX_TEXT_CHARS} characters.")
            font_weight = item.get("font_weight")
            if font_weight not in {400, 500, 600, 700}:
                raise ValueError(f"{label}.font_weight is not supported.")
            text_align = item.get("text_align")
            if text_align not in {"left", "center", "right"}:
                raise ValueError(f"{label}.text_align is not supported.")
            clean.update(
                {
                    "text": text,
                    "font_size": _number(
                        item.get("font_size"), f"{label}.font_size", minimum=8, maximum=240
                    ),
                    "font_weight": font_weight,
                    "text_align": text_align,
                    "text_color": _color(item.get("text_color"), f"{label}.text_color"),
                }
            )
        clean_objects.append(clean)

    clean_document = {
        "schema_version": 1,
        "viewport": clean_viewport,
        "objects": clean_objects,
    }
    serialized = _compact_json(clean_document)
    if len(serialized.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError("Board document must be 2 MB or smaller.")
    return clean_document, serialized


def _load_document(board: NoteBoard) -> dict:
    try:
        value = json.loads(board.document_json)
        clean, _serialized = validate_document(value)
        return clean
    except (TypeError, ValueError, json.JSONDecodeError):
        return empty_document()


def _get_board(board_id: int) -> NoteBoard | None:
    return NoteBoard.query.filter_by(id=board_id, user_id=session["user_id"]).first()


def _serialize_board(board: NoteBoard, *, include_document: bool = False) -> dict:
    payload = {
        "id": board.id,
        "name": board.name,
        "object_count": board.object_count,
        "revision": board.revision,
        "created_at": board.created_at.isoformat() if board.created_at else None,
        "updated_at": board.updated_at.isoformat() if board.updated_at else None,
    }
    if include_document:
        payload["document"] = _load_document(board)
    return payload


def _request_payload() -> dict:
    if not request.is_json:
        return {}
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


@notes_bp.route("/notes")
@login_required
def notes_page():
    boards = (
        NoteBoard.query.filter_by(user_id=session["user_id"])
        .order_by(NoteBoard.updated_at.desc(), NoteBoard.id.desc())
        .all()
    )
    return render_template(
        "notes.html", boards=[_serialize_board(board) for board in boards]
    )


@notes_bp.route("/notes/boards", methods=["POST"])
@login_required
def create_board():
    payload = _request_payload()
    if set(payload) - {"name"}:
        return jsonify({"error": "Unknown board field."}), 400
    name = str(payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Board name is required."}), 400
    name = name[:MAX_BOARD_NAME_CHARS].strip()

    document = empty_document()
    board = NoteBoard(
        user_id=session["user_id"],
        name=name,
        document_json=_compact_json(document),
        object_count=0,
        revision=1,
    )
    db.session.add(board)
    db.session.commit()
    return jsonify({"board": _serialize_board(board, include_document=True)}), 201


@notes_bp.route("/notes/boards/<int:board_id>")
@login_required
def board_page(board_id: int):
    board = _get_board(board_id)
    if not board:
        abort(404)
    return render_template(
        "notes_board.html", board=_serialize_board(board, include_document=True)
    )


@notes_bp.route("/notes/boards/<int:board_id>", methods=["PATCH"])
@login_required
def update_board(board_id: int):
    board = _get_board(board_id)
    if not board:
        return jsonify({"error": "Board not found."}), 404

    payload = _request_payload()
    if set(payload) - {"name", "document", "revision"}:
        return jsonify({"error": "Unknown board field."}), 400
    revision = payload.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        return jsonify({"error": "A valid board revision is required."}), 400
    if "name" not in payload and "document" not in payload:
        return jsonify({"error": "No board changes were provided."}), 400

    values = {"revision": NoteBoard.revision + 1, "updated_at": db.func.now()}
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Board name is required."}), 400
        values["name"] = name[:MAX_BOARD_NAME_CHARS].strip()
    if "document" in payload:
        try:
            clean_document, serialized = validate_document(payload.get("document"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        values["document_json"] = serialized
        values["object_count"] = len(clean_document["objects"])

    result = db.session.execute(
        db.update(NoteBoard)
        .where(
            NoteBoard.id == board_id,
            NoteBoard.user_id == session["user_id"],
            NoteBoard.revision == revision,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        db.session.rollback()
        latest = _get_board(board_id)
        return (
            jsonify(
                {
                    "error": "This board was changed in another tab.",
                    "conflict": True,
                    "board": _serialize_board(latest, include_document=True) if latest else None,
                }
            ),
            409,
        )

    db.session.commit()
    updated = _get_board(board_id)
    return jsonify({"board": _serialize_board(updated, include_document=True)})


@notes_bp.route("/notes/boards/<int:board_id>/duplicate", methods=["POST"])
@login_required
def duplicate_board(board_id: int):
    board = _get_board(board_id)
    if not board:
        return jsonify({"error": "Board not found."}), 404
    suffix = " copy"
    name = f"{board.name[: MAX_BOARD_NAME_CHARS - len(suffix)]}{suffix}"
    duplicate = NoteBoard(
        user_id=session["user_id"],
        name=name,
        document_json=board.document_json,
        object_count=board.object_count,
        revision=1,
    )
    db.session.add(duplicate)
    db.session.commit()
    return jsonify({"board": _serialize_board(duplicate, include_document=True)}), 201


@notes_bp.route("/notes/boards/<int:board_id>", methods=["DELETE"])
@login_required
def delete_board(board_id: int):
    board = _get_board(board_id)
    if not board:
        return jsonify({"error": "Board not found."}), 404
    db.session.delete(board)
    db.session.commit()
    return jsonify({"ok": True})
