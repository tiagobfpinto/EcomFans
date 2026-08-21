import mimetypes
from collections import defaultdict

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from auth import login_required
from db import (
    Product,
    ProductImage,
    PromptLibraryItem,
    PromptLibraryTarget,
    PromptLibraryTargetImage,
    PromptLibraryThumbnail,
    User,
    db,
)
from media_storage import (
    delete_storage_file,
    prepare_prompt_thumbnail_image,
    save_product_image,
    save_prompt_library_target_image,
    save_prompt_library_thumbnail,
)
from security import is_allowed_upload_image, upload_image_type_error

prompts_bp = Blueprint("prompts", __name__)

TARGET_TYPES = ("product", "character", "background")
MAX_PROMPT_THUMBNAILS = 3
MAX_TARGET_IMAGES = 3
MAX_PRODUCT_IMAGES = 4
MAX_PROMPT_NAME_CHARS = 160
MAX_PROMPT_TEXT_CHARS = 12000
MAX_TARGET_NAME_CHARS = 160
MAX_IMAGE_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_IMAGE_TOTAL_UPLOAD_BYTES = 18 * 1024 * 1024
PROMPT_PREVIEW_MAX_EDGE = 480


def _get_user():
    return db.session.get(User, session["user_id"])


def _normalize_target_type(raw_value: str | None) -> str:
    value = (raw_value or "").strip().lower()
    if value not in TARGET_TYPES:
        return ""
    return value


def _normalize_prompt_name(raw_value: str | None) -> str:
    return (raw_value or "").strip()[:MAX_PROMPT_NAME_CHARS].strip()


def _mime_for_upload(file) -> str:
    return (
        file.content_type
        or mimetypes.guess_type(file.filename)[0]
        or "application/octet-stream"
    )


def _read_image_uploads(files, *, max_count: int, total_limit: int) -> list[dict]:
    selected_files = [file for file in files if file and file.filename]
    if len(selected_files) > max_count:
        raise ValueError(f"Upload up to {max_count} images.")

    prepared = []
    total_bytes = 0
    for index, file in enumerate(selected_files, start=1):
        mime_type = _mime_for_upload(file)
        if not is_allowed_upload_image(mime_type):
            raise ValueError(upload_image_type_error())

        image_bytes = file.read()
        if not image_bytes:
            continue
        if len(image_bytes) > MAX_IMAGE_UPLOAD_BYTES:
            max_mb = MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)
            raise ValueError(f"Each image must be <= {max_mb} MB.")

        total_bytes += len(image_bytes)
        if total_bytes > total_limit:
            max_mb = total_limit // (1024 * 1024)
            raise ValueError(f"Total image upload must be <= {max_mb} MB.")

        prepared.append(
            {
                "sort_order": index,
                "filename": secure_filename(file.filename) or f"image_{index}",
                "mime_type": mime_type,
                "image_bytes": image_bytes,
            }
        )
    return prepared


def _prepare_prompt_images(files) -> list[dict]:
    uploads = _read_image_uploads(
        files,
        max_count=MAX_PROMPT_THUMBNAILS,
        total_limit=MAX_IMAGE_TOTAL_UPLOAD_BYTES,
    )
    prepared = []
    for upload in uploads:
        processed_bytes, processed_mime, width, height = prepare_prompt_thumbnail_image(
            upload["image_bytes"],
            max_edge=PROMPT_PREVIEW_MAX_EDGE,
        )
        prepared.append(
            {
                **upload,
                "mime_type": processed_mime,
                "image_bytes": processed_bytes,
                "width": width,
                "height": height,
                "file_size_bytes": len(processed_bytes),
            }
        )
    return prepared


def _prepare_target_images(files) -> list[dict]:
    uploads = _read_image_uploads(
        files,
        max_count=MAX_TARGET_IMAGES,
        total_limit=MAX_IMAGE_TOTAL_UPLOAD_BYTES,
    )
    prepared = []
    for upload in uploads:
        processed_bytes, processed_mime, width, height = prepare_prompt_thumbnail_image(
            upload["image_bytes"],
            max_edge=PROMPT_PREVIEW_MAX_EDGE,
        )
        prepared.append(
            {
                **upload,
                "mime_type": processed_mime,
                "image_bytes": processed_bytes,
                "width": width,
                "height": height,
                "file_size_bytes": len(processed_bytes),
            }
        )
    return prepared


def _read_product_images(files) -> list[dict]:
    return _read_image_uploads(
        files,
        max_count=MAX_PRODUCT_IMAGES,
        total_limit=24 * 1024 * 1024,
    )


def _find_or_create_product_target(user: User, product: Product) -> PromptLibraryTarget:
    target = PromptLibraryTarget.query.filter_by(
        user_id=user.id,
        target_type="product",
        product_id=product.id,
    ).first()
    if target:
        target.name = product.name
        target.description = product.context
        return target

    target = PromptLibraryTarget(
        user_id=user.id,
        target_type="product",
        name=product.name,
        description=product.context,
        product_id=product.id,
    )
    db.session.add(target)
    return target


def _resolve_prompt_link(user: User) -> PromptLibraryTarget | None:
    link_kind = (request.form.get("link_kind") or "").strip().lower()
    if link_kind in {"", "none", "null"}:
        # Backward-compatible fallback for older posts/tests.
        legacy_type = _normalize_target_type(request.form.get("target_type"))
        if legacy_type == "product":
            raw_product_id = (request.form.get("product_id") or "").strip()
            if raw_product_id:
                try:
                    product_id = int(raw_product_id)
                except ValueError:
                    product_id = 0
                product = Product.query.filter_by(id=product_id, user_id=user.id).first()
                if product:
                    return _find_or_create_product_target(user, product)

        legacy_name = (request.form.get("target_name") or "").strip()
        if not legacy_type or not legacy_name:
            return None
        target = PromptLibraryTarget.query.filter_by(
            user_id=user.id,
            target_type=legacy_type,
            name=legacy_name[:MAX_TARGET_NAME_CHARS].strip(),
        ).first()
        if target:
            return target
        target = PromptLibraryTarget(
            user_id=user.id,
            target_type=legacy_type,
            name=legacy_name[:MAX_TARGET_NAME_CHARS].strip(),
        )
        db.session.add(target)
        return target

    if link_kind == "product":
        raw_product_id = (request.form.get("product_id") or "").strip()
        try:
            product_id = int(raw_product_id)
        except ValueError as exc:
            raise ValueError("Choose a product or leave the link empty.") from exc
        product = Product.query.filter_by(id=product_id, user_id=user.id).first()
        if not product:
            raise ValueError("Choose a valid product.")
        return _find_or_create_product_target(user, product)

    if link_kind in {"character", "background"}:
        raw_target_id = (request.form.get(f"{link_kind}_target_id") or "").strip()
        try:
            target_id = int(raw_target_id)
        except ValueError as exc:
            raise ValueError(f"Choose a {link_kind} or leave the link empty.") from exc
        target = PromptLibraryTarget.query.filter_by(
            id=target_id,
            user_id=user.id,
            target_type=link_kind,
        ).first()
        if not target:
            raise ValueError(f"Choose a valid {link_kind}.")
        return target

    raise ValueError("Choose a valid link type.")


def _prompt_count_maps(user: User) -> tuple[dict[int, int], dict[int, int]]:
    target_counts = defaultdict(int)
    product_counts = defaultdict(int)
    rows = (
        db.session.query(PromptLibraryItem.target_id, PromptLibraryTarget.product_id)
        .outerjoin(PromptLibraryTarget, PromptLibraryTarget.id == PromptLibraryItem.target_id)
        .filter(PromptLibraryItem.user_id == user.id)
        .all()
    )
    for target_id, product_id in rows:
        if target_id:
            target_counts[int(target_id)] += 1
        if product_id:
            product_counts[int(product_id)] += 1
    return dict(target_counts), dict(product_counts)


@prompts_bp.route("/prompts", methods=["GET", "POST"])
@login_required
def prompts_page():
    user = _get_user()

    if request.method == "POST":
        prompt_name = _normalize_prompt_name(request.form.get("prompt_name"))
        prompt_text = (request.form.get("prompt_text") or "").strip()
        if not prompt_name:
            flash("Prompt name is required.", "error")
            return redirect(url_for("prompts.prompts_page"))
        if not prompt_text:
            flash("Prompt text is required.", "error")
            return redirect(url_for("prompts.prompts_page"))
        if len(prompt_text) > MAX_PROMPT_TEXT_CHARS:
            flash(f"Prompt text must be <= {MAX_PROMPT_TEXT_CHARS} characters.", "error")
            return redirect(url_for("prompts.prompts_page"))

        try:
            target = _resolve_prompt_link(user)
            prepared_thumbnails = _prepare_prompt_images(
                request.files.getlist("thumbnails")
            )
        except (RuntimeError, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("prompts.prompts_page"))

        written_paths = []
        try:
            prompt = PromptLibraryItem(
                user_id=user.id,
                target=target,
                name=prompt_name,
                prompt_text=prompt_text,
            )
            db.session.add(prompt)
            db.session.flush()

            for thumbnail_data in prepared_thumbnails:
                thumbnail = PromptLibraryThumbnail(
                    prompt_id=prompt.id,
                    sort_order=thumbnail_data["sort_order"],
                    filename=thumbnail_data["filename"],
                    mime_type=thumbnail_data["mime_type"],
                    storage_path="pending",
                    width=thumbnail_data["width"],
                    height=thumbnail_data["height"],
                    file_size_bytes=thumbnail_data["file_size_bytes"],
                )
                db.session.add(thumbnail)
                db.session.flush()
                thumbnail.storage_path = save_prompt_library_thumbnail(
                    user.id,
                    prompt.id,
                    thumbnail.id,
                    thumbnail.mime_type,
                    thumbnail_data["image_bytes"],
                )
                written_paths.append(thumbnail.storage_path)

            db.session.commit()
            flash("Prompt saved.", "success")
        except Exception:
            db.session.rollback()
            for path in written_paths:
                delete_storage_file(path)
            flash("An error occurred while saving the prompt.", "error")
        return redirect(url_for("prompts.prompts_page"))

    products = (
        Product.query
        .filter_by(user_id=user.id)
        .order_by(Product.created_at.desc())
        .all()
    )
    character_targets = (
        PromptLibraryTarget.query
        .filter_by(user_id=user.id, target_type="character")
        .order_by(PromptLibraryTarget.created_at.desc())
        .all()
    )
    background_targets = (
        PromptLibraryTarget.query
        .filter_by(user_id=user.id, target_type="background")
        .order_by(PromptLibraryTarget.created_at.desc())
        .all()
    )
    recent_prompts = (
        PromptLibraryItem.query
        .filter_by(user_id=user.id)
        .order_by(PromptLibraryItem.created_at.desc())
        .all()
    )
    target_prompt_counts, product_prompt_counts = _prompt_count_maps(user)

    return render_template(
        "prompts.html",
        products=products,
        character_targets=character_targets,
        background_targets=background_targets,
        recent_prompts=recent_prompts,
        target_prompt_counts=target_prompt_counts,
        product_prompt_counts=product_prompt_counts,
        max_thumbnails=MAX_PROMPT_THUMBNAILS,
        max_target_images=MAX_TARGET_IMAGES,
        max_product_images=MAX_PRODUCT_IMAGES,
        prompt_preview_max_edge=PROMPT_PREVIEW_MAX_EDGE,
    )


@prompts_bp.route("/prompts/targets", methods=["POST"])
@login_required
def create_target():
    user = _get_user()
    target_type = _normalize_target_type(request.form.get("target_type"))
    if not target_type:
        flash("Choose character, product, or background.", "error")
        return redirect(url_for("prompts.prompts_page"))

    name = (request.form.get("name") or "").strip()[:MAX_TARGET_NAME_CHARS].strip()
    description = (request.form.get("description") or "").strip()
    if not name:
        flash("Name is required.", "error")
        return redirect(url_for("prompts.prompts_page"))

    written_paths = []
    try:
        if target_type == "product":
            context = (request.form.get("context") or description or "").strip()
            if not context:
                flash("Product context is required.", "error")
                return redirect(url_for("prompts.prompts_page"))

            product_images = _read_product_images(request.files.getlist("images"))
            product = Product(
                user_id=user.id,
                name=name,
                context=context,
                price=(request.form.get("price") or "").strip() or None,
                offer=(request.form.get("offer") or "").strip() or None,
                benefits=(request.form.get("benefits") or "").strip() or None,
            )
            db.session.add(product)
            db.session.flush()

            for image_data in product_images:
                image = ProductImage(
                    product_id=product.id,
                    sort_order=image_data["sort_order"],
                    filename=image_data["filename"],
                    mime_type=image_data["mime_type"],
                    image_data=None,
                )
                db.session.add(image)
                db.session.flush()
                image.storage_path = save_product_image(
                    user.id,
                    product.id,
                    image.id,
                    image.mime_type,
                    image_data["image_bytes"],
                )
                written_paths.append(image.storage_path)

            _find_or_create_product_target(user, product)
        else:
            target_images = _prepare_target_images(request.files.getlist("images"))
            target = PromptLibraryTarget(
                user_id=user.id,
                target_type=target_type,
                name=name,
                description=description or None,
            )
            db.session.add(target)
            db.session.flush()

            for image_data in target_images:
                image = PromptLibraryTargetImage(
                    target_id=target.id,
                    sort_order=image_data["sort_order"],
                    filename=image_data["filename"],
                    mime_type=image_data["mime_type"],
                    storage_path="pending",
                    width=image_data["width"],
                    height=image_data["height"],
                    file_size_bytes=image_data["file_size_bytes"],
                )
                db.session.add(image)
                db.session.flush()
                image.storage_path = save_prompt_library_target_image(
                    user.id,
                    target.id,
                    image.id,
                    image.mime_type,
                    image_data["image_bytes"],
                )
                written_paths.append(image.storage_path)

        db.session.commit()
        flash(f"{target_type.title()} saved.", "success")
    except (RuntimeError, ValueError) as exc:
        db.session.rollback()
        for path in written_paths:
            delete_storage_file(path)
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        for path in written_paths:
            delete_storage_file(path)
        flash(f"An error occurred while saving the {target_type}.", "error")
    return redirect(url_for("prompts.prompts_page"))


@prompts_bp.route("/prompts/<int:prompt_id>/link", methods=["POST"])
@login_required
def link_prompt(prompt_id: int):
    user = _get_user()
    prompt = PromptLibraryItem.query.filter_by(id=prompt_id, user_id=user.id).first_or_404()

    try:
        target = _resolve_prompt_link(user)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("prompts.prompts_page"))

    prompt.target = target
    db.session.commit()
    if target:
        flash("Prompt linked.", "success")
    else:
        flash("Prompt unlinked.", "success")
    return redirect(url_for("prompts.prompts_page"))


@prompts_bp.route("/prompts/<int:prompt_id>/delete", methods=["POST"])
@login_required
def delete_prompt(prompt_id: int):
    user = _get_user()
    prompt = PromptLibraryItem.query.filter_by(id=prompt_id, user_id=user.id).first_or_404()
    storage_paths = [thumbnail.storage_path for thumbnail in prompt.thumbnails]

    try:
        db.session.delete(prompt)
        db.session.commit()
        for path in storage_paths:
            delete_storage_file(path)
        flash("Prompt deleted.", "success")
    except Exception:
        db.session.rollback()
        flash("Failed to delete prompt.", "error")
    return redirect(url_for("prompts.prompts_page"))
