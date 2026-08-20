from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy.exc import IntegrityError

from auth import login_required
from db import Funnel, FunnelPage, db


funnels_bp = Blueprint("funnels", __name__)

DEFAULT_TEMPLATE_PATH = Path(__file__).with_name("ex.html")
MAX_FUNNEL_NAME_CHARS = 160
MAX_DESCRIPTION_CHARS = 2000
MAX_PAGE_TITLE_CHARS = 200
MAX_SLUG_CHARS = 180
MAX_HTML_BYTES = 2 * 1024 * 1024

PAGE_TYPES = {
    "advertorial": "Advertorial",
    "listicle": "Listicle",
    "landing": "Landing page",
    "product": "Product page",
    "quiz": "Quiz",
    "custom": "Custom",
}
PAGE_STATUSES = {"draft", "published"}

# A published page lives directly at /<slug>. These prefixes belong to the app
# and cannot be claimed by a funnel page.
RESERVED_SLUG_PREFIXES = {
    "admin",
    "ai-creatives",
    "ai-image",
    "app",
    "avatars",
    "billing",
    "brand-dna",
    "competitors",
    "credits-store",
    "dashboard",
    "download",
    "forgot-password",
    "funnels",
    "health",
    "landing-builder",
    "login",
    "logout",
    "media",
    "metrics",
    "notes",
    "privacy",
    "product-images",
    "products",
    "prompts",
    "register",
    "reset-password",
    "scrape",
    "script-optimizer",
    "social-downloader",
    "static",
    "storyboarder",
    "terms",
    "upgrade",
}


def default_template_html() -> str:
    try:
        return DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("The default funnel template ex.html is unavailable.") from exc


def normalize_slug(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/").strip("/")
    ascii_value = (
        unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    )
    segments = []
    for segment in ascii_value.lower().split("/"):
        cleaned = re.sub(r"[^a-z0-9_-]+", "-", segment)
        cleaned = re.sub(r"[-_]{2,}", "-", cleaned).strip("-_")
        if cleaned:
            segments.append(cleaned)
    slug = "/".join(segments)
    if not slug:
        raise ValueError("Slug is required.")
    if len(slug) > MAX_SLUG_CHARS:
        raise ValueError(f"Slug must be {MAX_SLUG_CHARS} characters or fewer.")
    if slug.split("/", 1)[0] in RESERVED_SLUG_PREFIXES:
        raise ValueError("That slug is reserved by the application. Choose another one.")
    return slug


def _request_payload() -> dict:
    if not request.is_json:
        return {}
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _get_funnel(funnel_id: int) -> Funnel | None:
    return Funnel.query.filter_by(id=funnel_id, user_id=session["user_id"]).first()


def _get_page(funnel_id: int, page_id: int) -> FunnelPage | None:
    return (
        FunnelPage.query.join(Funnel)
        .filter(
            FunnelPage.id == page_id,
            FunnelPage.funnel_id == funnel_id,
            Funnel.user_id == session["user_id"],
        )
        .first()
    )


def _serialize_page(page: FunnelPage, *, include_html: bool = False) -> dict:
    payload = {
        "id": page.id,
        "funnel_id": page.funnel_id,
        "title": page.title,
        "page_type": page.page_type,
        "page_type_label": PAGE_TYPES.get(page.page_type, "Custom"),
        "slug": page.slug,
        "path": f"/{page.slug}",
        "status": page.status,
        "sort_order": page.sort_order,
        "revision": page.revision,
        "created_at": page.created_at.isoformat() if page.created_at else None,
        "updated_at": page.updated_at.isoformat() if page.updated_at else None,
    }
    if include_html:
        payload["html_content"] = page.html_content
    return payload


def _serialize_funnel(funnel: Funnel, *, include_pages: bool = False) -> dict:
    pages = list(funnel.pages)
    payload = {
        "id": funnel.id,
        "name": funnel.name,
        "description": funnel.description or "",
        "page_count": len(pages),
        "published_count": sum(page.status == "published" for page in pages),
        "created_at": funnel.created_at.isoformat() if funnel.created_at else None,
        "updated_at": funnel.updated_at.isoformat() if funnel.updated_at else None,
    }
    if include_pages:
        payload["pages"] = [_serialize_page(page) for page in pages]
    return payload


def _validate_title(value: object) -> str:
    title = str(value or "").strip()
    if not title:
        raise ValueError("Page title is required.")
    return title[:MAX_PAGE_TITLE_CHARS].strip()


def _validate_page_type(value: object) -> str:
    page_type = str(value or "").strip().lower()
    if page_type not in PAGE_TYPES:
        raise ValueError("Select a valid page type.")
    return page_type


def _validate_html(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("HTML content must be text.")
    if len(value.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("HTML content must be 2 MB or smaller.")
    return value


def _slug_in_use(slug: str, *, excluding_page_id: int | None = None) -> bool:
    query = FunnelPage.query.filter_by(slug=slug)
    if excluding_page_id is not None:
        query = query.filter(FunnelPage.id != excluding_page_id)
    return db.session.query(query.exists()).scalar()


def _available_copy_slug(source_slug: str) -> str:
    suffix = "-copy"
    base = source_slug[: MAX_SLUG_CHARS - len(suffix)].rstrip("-_")
    candidate = f"{base}{suffix}"
    counter = 2
    while _slug_in_use(candidate):
        numbered_suffix = f"-copy-{counter}"
        trimmed = source_slug[: MAX_SLUG_CHARS - len(numbered_suffix)].rstrip("-_")
        candidate = f"{trimmed}{numbered_suffix}"
        counter += 1
    return candidate


@funnels_bp.route("/funnels")
@login_required
def funnels_page():
    funnels = (
        Funnel.query.filter_by(user_id=session["user_id"])
        .order_by(Funnel.updated_at.desc(), Funnel.id.desc())
        .all()
    )
    items = [_serialize_funnel(funnel) for funnel in funnels]
    return render_template(
        "funnels.html",
        funnels=items,
        total_pages=sum(item["page_count"] for item in items),
        total_published=sum(item["published_count"] for item in items),
    )


@funnels_bp.route("/funnels", methods=["POST"])
@login_required
def create_funnel():
    payload = _request_payload()
    if set(payload) - {"name", "description"}:
        return jsonify({"error": "Unknown funnel field."}), 400
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    if not name:
        return jsonify({"error": "Funnel name is required."}), 400
    if len(description) > MAX_DESCRIPTION_CHARS:
        return jsonify({"error": "Description is too long."}), 400

    funnel = Funnel(
        user_id=session["user_id"],
        name=name[:MAX_FUNNEL_NAME_CHARS].strip(),
        description=description or None,
    )
    db.session.add(funnel)
    db.session.commit()
    return jsonify({"funnel": _serialize_funnel(funnel)}), 201


@funnels_bp.route("/funnels/<int:funnel_id>")
@login_required
def funnel_detail_page(funnel_id: int):
    funnel = _get_funnel(funnel_id)
    if not funnel:
        abort(404)
    return render_template(
        "funnel_detail.html",
        funnel=_serialize_funnel(funnel, include_pages=True),
        page_types=PAGE_TYPES,
    )


@funnels_bp.route("/funnels/<int:funnel_id>", methods=["PATCH"])
@login_required
def update_funnel(funnel_id: int):
    funnel = _get_funnel(funnel_id)
    if not funnel:
        return jsonify({"error": "Funnel not found."}), 404
    payload = _request_payload()
    if set(payload) - {"name", "description"}:
        return jsonify({"error": "Unknown funnel field."}), 400
    if not payload:
        return jsonify({"error": "No funnel changes were provided."}), 400

    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Funnel name is required."}), 400
        funnel.name = name[:MAX_FUNNEL_NAME_CHARS].strip()
    if "description" in payload:
        description = str(payload.get("description") or "").strip()
        if len(description) > MAX_DESCRIPTION_CHARS:
            return jsonify({"error": "Description is too long."}), 400
        funnel.description = description or None
    funnel.updated_at = db.func.now()
    db.session.commit()
    return jsonify({"funnel": _serialize_funnel(funnel, include_pages=True)})


@funnels_bp.route("/funnels/<int:funnel_id>", methods=["DELETE"])
@login_required
def delete_funnel(funnel_id: int):
    funnel = _get_funnel(funnel_id)
    if not funnel:
        return jsonify({"error": "Funnel not found."}), 404
    db.session.delete(funnel)
    db.session.commit()
    return jsonify({"ok": True})


@funnels_bp.route("/funnels/<int:funnel_id>/pages", methods=["POST"])
@login_required
def create_page(funnel_id: int):
    funnel = _get_funnel(funnel_id)
    if not funnel:
        return jsonify({"error": "Funnel not found."}), 404
    payload = _request_payload()
    if set(payload) - {"title", "page_type", "slug"}:
        return jsonify({"error": "Unknown page field."}), 400
    try:
        title = _validate_title(payload.get("title"))
        page_type = _validate_page_type(payload.get("page_type") or "advertorial")
        slug = normalize_slug(payload.get("slug"))
        html_content = _validate_html(default_template_html())
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    if _slug_in_use(slug):
        return jsonify({"error": "That slug is already in use."}), 409

    max_order = (
        db.session.query(db.func.max(FunnelPage.sort_order))
        .filter_by(funnel_id=funnel.id)
        .scalar()
        or 0
    )
    page = FunnelPage(
        funnel_id=funnel.id,
        title=title,
        page_type=page_type,
        slug=slug,
        html_content=html_content,
        status="draft",
        sort_order=max_order + 1,
        revision=1,
    )
    db.session.add(page)
    funnel.updated_at = db.func.now()
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "That slug is already in use."}), 409
    return jsonify({"page": _serialize_page(page, include_html=True)}), 201


@funnels_bp.route("/funnels/<int:funnel_id>/pages/<int:page_id>")
@login_required
def page_editor(funnel_id: int, page_id: int):
    funnel = _get_funnel(funnel_id)
    page = _get_page(funnel_id, page_id) if funnel else None
    if not funnel or not page:
        abort(404)
    return render_template(
        "funnel_editor.html",
        funnel=_serialize_funnel(funnel),
        page=_serialize_page(page, include_html=True),
        page_types=PAGE_TYPES,
        default_template=default_template_html(),
    )


@funnels_bp.route("/funnels/<int:funnel_id>/pages/<int:page_id>", methods=["PATCH"])
@login_required
def update_page(funnel_id: int, page_id: int):
    page = _get_page(funnel_id, page_id)
    funnel = _get_funnel(funnel_id)
    if not page or not funnel:
        return jsonify({"error": "Page not found."}), 404
    payload = _request_payload()
    allowed = {"title", "page_type", "slug", "html_content", "status", "revision"}
    if set(payload) - allowed:
        return jsonify({"error": "Unknown page field."}), 400
    revision = payload.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        return jsonify({"error": "A valid page revision is required."}), 400
    if revision != page.revision:
        return (
            jsonify(
                {
                    "error": "This page was changed in another tab.",
                    "conflict": True,
                    "page": _serialize_page(page, include_html=True),
                }
            ),
            409,
        )
    if not (set(payload) - {"revision"}):
        return jsonify({"error": "No page changes were provided."}), 400

    try:
        if "title" in payload:
            page.title = _validate_title(payload.get("title"))
        if "page_type" in payload:
            page.page_type = _validate_page_type(payload.get("page_type"))
        if "slug" in payload:
            slug = normalize_slug(payload.get("slug"))
            if _slug_in_use(slug, excluding_page_id=page.id):
                return jsonify({"error": "That slug is already in use."}), 409
            page.slug = slug
        if "html_content" in payload:
            page.html_content = _validate_html(payload.get("html_content"))
        if "status" in payload:
            status = str(payload.get("status") or "").strip().lower()
            if status not in PAGE_STATUSES:
                raise ValueError("Select a valid page status.")
            page.status = status
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    page.revision += 1
    page.updated_at = db.func.now()
    funnel.updated_at = db.func.now()
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "That slug is already in use."}), 409
    return jsonify({"page": _serialize_page(page, include_html=True)})


@funnels_bp.route(
    "/funnels/<int:funnel_id>/pages/<int:page_id>/duplicate", methods=["POST"]
)
@login_required
def duplicate_page(funnel_id: int, page_id: int):
    source = _get_page(funnel_id, page_id)
    funnel = _get_funnel(funnel_id)
    if not source or not funnel:
        return jsonify({"error": "Page not found."}), 404
    max_order = (
        db.session.query(db.func.max(FunnelPage.sort_order))
        .filter_by(funnel_id=funnel.id)
        .scalar()
        or 0
    )
    suffix = " copy"
    duplicate = FunnelPage(
        funnel_id=funnel.id,
        title=f"{source.title[: MAX_PAGE_TITLE_CHARS - len(suffix)]}{suffix}",
        page_type=source.page_type,
        slug=_available_copy_slug(source.slug),
        html_content=source.html_content,
        status="draft",
        sort_order=max_order + 1,
        revision=1,
    )
    db.session.add(duplicate)
    funnel.updated_at = db.func.now()
    db.session.commit()
    return jsonify({"page": _serialize_page(duplicate, include_html=True)}), 201


@funnels_bp.route("/funnels/<int:funnel_id>/pages/<int:page_id>", methods=["DELETE"])
@login_required
def delete_page(funnel_id: int, page_id: int):
    page = _get_page(funnel_id, page_id)
    funnel = _get_funnel(funnel_id)
    if not page or not funnel:
        return jsonify({"error": "Page not found."}), 404
    db.session.delete(page)
    funnel.updated_at = db.func.now()
    db.session.commit()
    return jsonify({"ok": True})


def _html_response(page: FunnelPage, *, preview: bool) -> Response:
    response = Response(page.html_content, content_type="text/html; charset=utf-8")
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = (
        "sandbox allow-forms allow-modals allow-popups "
        "allow-popups-to-escape-sandbox allow-scripts; "
        "default-src 'none'; img-src * data: blob:; media-src * data: blob:; "
        "style-src * 'unsafe-inline'; font-src * data:; "
        "script-src * 'unsafe-inline' 'unsafe-eval' blob:; connect-src *; "
        "frame-src *; form-action *"
    )
    response.headers["Cache-Control"] = "no-store" if preview else "no-cache"
    if preview:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@funnels_bp.route("/funnels/<int:funnel_id>/pages/<int:page_id>/preview")
@login_required
def preview_page(funnel_id: int, page_id: int):
    page = _get_page(funnel_id, page_id)
    if not page:
        abort(404)
    return _html_response(page, preview=True)


@funnels_bp.route("/<path:slug>")
def published_page(slug: str):
    try:
        normalized = normalize_slug(slug)
    except ValueError:
        abort(404)
    if normalized != slug:
        page = FunnelPage.query.filter_by(slug=normalized, status="published").first()
        if page:
            return redirect(url_for("funnels.published_page", slug=normalized), code=301)
        abort(404)
    page = FunnelPage.query.filter_by(slug=normalized, status="published").first()
    if not page:
        abort(404)
    return _html_response(page, preview=False)
