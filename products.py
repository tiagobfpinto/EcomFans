import base64
import mimetypes

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from auth import login_required
from db import Product, ProductImage, User, db

products_bp = Blueprint("products", __name__)


def _get_user():
    """Return the current logged-in User object."""
    return db.session.get(User, session["user_id"])


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

        if len(files) > 4:
            flash("You can upload up to 4 product images.", "error")
            return redirect(url_for("products.products_page"))

        prepared_images = []
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

            prepared_images.append({
                "sort_order": index,
                "filename": file.filename,
                "mime_type": mime_type,
                "image_data": base64.b64encode(image_bytes).decode("utf-8"),
            })

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
                db.session.add(ProductImage(product_id=product.id, **image))

            db.session.commit()
            flash("Product saved successfully.", "success")
            return redirect(url_for("products.products_page"))
        except Exception:
            db.session.rollback()
            flash("An error occurred while saving the product.", "error")
            return redirect(url_for("products.products_page"))

    products = (
        Product.query
        .filter_by(user_id=user.id)
        .order_by(Product.created_at.desc())
        .all()
    )
    return render_template("products.html", products=products)
