import io
import mimetypes
from pathlib import Path

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from ai_service import openai_remove_background_with_meta, remove_background_local
from auth import login_required
from billing_service import get_effective_api_key, record_api_request_event
from db import Product, ProductImage, User, db
from media_storage import delete_storage_file, save_product_image

products_bp = Blueprint("products", __name__)
MAX_PRODUCT_IMAGES = 4
MAX_PRODUCT_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PRODUCT_TOTAL_BYTES = 24 * 1024 * 1024
MAX_REMOVE_BG_IMAGE_BYTES = 10 * 1024 * 1024


def _get_user():
    """Return the current logged-in User object."""
    return db.session.get(User, session["user_id"])


def _build_removed_background_name(filename: str | None) -> str:
    safe_name = (filename or "").strip()
    stem = Path(safe_name).stem.strip() or "image"
    return f"{stem}_no_bg.png"


@products_bp.route("/products", methods=["GET", "POST"])
@login_required
def products_page():
    user = _get_user()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        context = request.form.get("context", "").strip()
        price = request.form.get("price", "").strip()
        offer = request.form.get("offer", "").strip()
        benefits = request.form.get("benefits", "").strip()
        files = [file for file in request.files.getlist("images") if file and file.filename]

        if not name:
            flash("Product name is required.", "error")
            return redirect(url_for("products.products_page"))

        if not context:
            flash("Product context is required.", "error")
            return redirect(url_for("products.products_page"))

        if len(files) > MAX_PRODUCT_IMAGES:
            flash(f"You can upload up to {MAX_PRODUCT_IMAGES} product images.", "error")
            return redirect(url_for("products.products_page"))

        prepared_images = []
        total_uploaded_bytes = 0
        for index, file in enumerate(files, start=1):
            mime_type = (
                file.content_type
                or mimetypes.guess_type(file.filename)[0]
                or "application/octet-stream"
            )
            if not mime_type.startswith("image/"):
                flash("Only image files are allowed for product context.", "error")
                return redirect(url_for("products.products_page"))

            image_bytes = file.read()
            if not image_bytes:
                continue
            if len(image_bytes) > MAX_PRODUCT_IMAGE_BYTES:
                flash(
                    f"Each product image must be <= {MAX_PRODUCT_IMAGE_BYTES // (1024 * 1024)} MB.",
                    "error",
                )
                return redirect(url_for("products.products_page"))
            total_uploaded_bytes += len(image_bytes)
            if total_uploaded_bytes > MAX_PRODUCT_TOTAL_BYTES:
                flash(
                    f"Total upload must be <= {MAX_PRODUCT_TOTAL_BYTES // (1024 * 1024)} MB.",
                    "error",
                )
                return redirect(url_for("products.products_page"))

            prepared_images.append({
                "sort_order": index,
                "filename": file.filename,
                "mime_type": mime_type,
                "image_bytes": image_bytes,
            })

        written_paths = []
        try:
            product = Product(
                user_id=user.id,
                name=name,
                context=context,
                price=price or None,
                offer=offer or None,
                benefits=benefits or None,
            )
            db.session.add(product)
            db.session.flush()

            for image in prepared_images:
                product_image = ProductImage(
                    product_id=product.id,
                    sort_order=image["sort_order"],
                    filename=image["filename"],
                    mime_type=image["mime_type"],
                    image_data=None,
                )
                db.session.add(product_image)
                db.session.flush()

                product_image.storage_path = save_product_image(
                    user.id,
                    product.id,
                    product_image.id,
                    product_image.mime_type,
                    image["image_bytes"],
                )
                written_paths.append(product_image.storage_path)

            db.session.commit()
            flash("Product saved successfully.", "success")
            return redirect(url_for("products.products_page"))
        except Exception:
            db.session.rollback()
            for relative_path in written_paths:
                delete_storage_file(relative_path)
            flash("An error occurred while saving the product.", "error")
            return redirect(url_for("products.products_page"))

    products = (
        Product.query
        .filter_by(user_id=user.id)
        .order_by(Product.created_at.desc())
        .all()
    )
    return render_template("products.html", products=products)


@products_bp.route("/products/<int:product_id>/edit", methods=["POST"])
@login_required
def edit_product(product_id):
    user = _get_user()
    product = Product.query.filter_by(id=product_id, user_id=user.id).first_or_404()

    name = request.form.get("name", "").strip()
    context = request.form.get("context", "").strip()
    price = request.form.get("price", "").strip()
    offer = request.form.get("offer", "").strip()
    benefits = request.form.get("benefits", "").strip()
    files = [f for f in request.files.getlist("images") if f and f.filename]

    redirect_url = url_for("products.products_page") + f"?open={product_id}"

    if not name:
        flash("Product name is required.", "error")
        return redirect(redirect_url)
    if not context:
        flash("Product context is required.", "error")
        return redirect(redirect_url)

    current_count = len(product.images)
    if current_count + len(files) > MAX_PRODUCT_IMAGES:
        flash(f"You can have up to {MAX_PRODUCT_IMAGES} images per product.", "error")
        return redirect(redirect_url)

    prepared_images = []
    total_bytes = 0
    for index, file in enumerate(files, start=current_count + 1):
        mime_type = (
            file.content_type
            or mimetypes.guess_type(file.filename)[0]
            or "application/octet-stream"
        )
        if not mime_type.startswith("image/"):
            flash("Only image files are allowed.", "error")
            return redirect(redirect_url)
        image_bytes = file.read()
        if not image_bytes:
            continue
        if len(image_bytes) > MAX_PRODUCT_IMAGE_BYTES:
            flash(f"Each image must be <= {MAX_PRODUCT_IMAGE_BYTES // (1024 * 1024)} MB.", "error")
            return redirect(redirect_url)
        total_bytes += len(image_bytes)
        if total_bytes > MAX_PRODUCT_TOTAL_BYTES:
            flash(f"Total upload must be <= {MAX_PRODUCT_TOTAL_BYTES // (1024 * 1024)} MB.", "error")
            return redirect(redirect_url)
        prepared_images.append({
            "sort_order": index,
            "filename": file.filename,
            "mime_type": mime_type,
            "image_bytes": image_bytes,
        })

    written_paths = []
    try:
        product.name = name
        product.context = context
        product.price = price or None
        product.offer = offer or None
        product.benefits = benefits or None

        for image in prepared_images:
            product_image = ProductImage(
                product_id=product.id,
                sort_order=image["sort_order"],
                filename=image["filename"],
                mime_type=image["mime_type"],
                image_data=None,
            )
            db.session.add(product_image)
            db.session.flush()
            product_image.storage_path = save_product_image(
                user.id, product.id, product_image.id,
                product_image.mime_type, image["image_bytes"],
            )
            written_paths.append(product_image.storage_path)

        db.session.commit()
        flash("Product updated.", "success")
        return redirect(redirect_url)
    except Exception:
        db.session.rollback()
        for path in written_paths:
            delete_storage_file(path)
        flash("An error occurred while updating the product.", "error")
        return redirect(redirect_url)


@products_bp.route("/products/<int:product_id>/delete-image/<int:image_id>", methods=["POST"])
@login_required
def delete_product_image(product_id, image_id):
    user = _get_user()
    product = Product.query.filter_by(id=product_id, user_id=user.id).first_or_404()
    image = ProductImage.query.filter_by(id=image_id, product_id=product.id).first_or_404()
    storage_path = image.storage_path
    try:
        db.session.delete(image)
        db.session.commit()
        if storage_path:
            delete_storage_file(storage_path)
        return jsonify({"ok": True})
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Failed to delete image."}), 500


@products_bp.route("/products/<int:product_id>/delete", methods=["POST"])
@login_required
def delete_product(product_id):
    user = _get_user()
    product = Product.query.filter_by(id=product_id, user_id=user.id).first_or_404()
    storage_paths = [img.storage_path for img in product.images if img.storage_path]
    try:
        db.session.delete(product)
        db.session.commit()
        for path in storage_paths:
            delete_storage_file(path)
        flash("Product deleted.", "success")
    except Exception:
        db.session.rollback()
        flash("Failed to delete product.", "error")
    return redirect(url_for("products.products_page"))


@products_bp.route("/products/remove-background", methods=["POST"])
@login_required
def remove_product_background():
    user = _get_user()
    image_file = request.files.get("image")
    if not image_file or not image_file.filename:
        return jsonify({"error": "Please upload an image file."}), 400

    mime_type = (
        image_file.content_type
        or mimetypes.guess_type(image_file.filename)[0]
        or "application/octet-stream"
    )
    if not mime_type.startswith("image/"):
        return jsonify({"error": "Only image files are supported."}), 400

    image_bytes = image_file.read()
    if not image_bytes:
        return jsonify({"error": "Uploaded image is empty."}), 400
    if len(image_bytes) > MAX_REMOVE_BG_IMAGE_BYTES:
        return jsonify(
            {"error": f"Image must be <= {MAX_REMOVE_BG_IMAGE_BYTES // (1024 * 1024)} MB."}
        ), 413

    try:
        provider = "local"
        request_meta = None
        result_bytes = remove_background_local(image_bytes)
    except Exception as local_exc:
        openai_key = get_effective_api_key(user.id, "openai")
        if not openai_key:
            return jsonify(
                {
                    "error": (
                        "Local background removal is unavailable on this server. "
                        "Install rembg/onnxruntime or configure OpenAI as fallback."
                    )
                }
            ), 500
        try:
            provider = "openai"
            result_bytes, request_meta = openai_remove_background_with_meta(
                api_key=openai_key,
                image_bytes=image_bytes,
                filename=image_file.filename,
                mime_type=mime_type,
            )
        except Exception as openai_exc:
            return jsonify(
                {
                    "error": (
                        "Background removal failed in both local and OpenAI modes. "
                        f"Local error: {local_exc}. OpenAI error: {openai_exc}."
                    )
                }
            ), 500

    try:
        record_api_request_event(
            user_id=user.id,
            feature="products",
            provider=provider,
            operation="remove_background",
            meta=request_meta,
            metadata={"filename": image_file.filename},
        )
    except Exception:
        # Do not fail user download because usage logging failed.
        db.session.rollback()

    db.session.commit()
    response = send_file(
        io.BytesIO(result_bytes),
        mimetype="image/png",
        as_attachment=True,
        download_name=_build_removed_background_name(image_file.filename),
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response
