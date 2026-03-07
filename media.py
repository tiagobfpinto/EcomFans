import base64
import io

from flask import Blueprint, abort, send_file, session

from auth import login_required
from db import CreativeInspiration, Product, ProductImage
from media_storage import read_storage_bytes


media_bp = Blueprint("media", __name__)


@media_bp.route("/media/product-images/<int:image_id>")
@login_required
def product_image(image_id: int):
    image = (
        ProductImage.query
        .join(Product, Product.id == ProductImage.product_id)
        .filter(
            ProductImage.id == image_id,
            Product.user_id == session["user_id"],
        )
        .first()
    )
    if not image:
        abort(404)

    storage_bytes = read_storage_bytes(image.storage_path)
    if storage_bytes is not None:
        return send_file(
            io.BytesIO(storage_bytes),
            mimetype=image.mime_type,
            as_attachment=False,
            download_name=image.filename or f"product_image_{image.id}",
        )

    if image.image_data:
        try:
            decoded = base64.b64decode(image.image_data, validate=False)
        except Exception:
            decoded = b""
        if decoded:
            return send_file(
                io.BytesIO(decoded),
                mimetype=image.mime_type,
                as_attachment=False,
                download_name=image.filename or f"product_image_{image.id}",
            )

    abort(404)


@media_bp.route("/media/inspirations/<int:inspiration_id>")
@login_required
def inspiration_image(inspiration_id: int):
    inspiration = CreativeInspiration.query.filter_by(
        id=inspiration_id,
        user_id=session["user_id"],
    ).first()
    if not inspiration:
        abort(404)

    storage_bytes = read_storage_bytes(inspiration.storage_path)
    if storage_bytes is not None:
        return send_file(
            io.BytesIO(storage_bytes),
            mimetype=inspiration.mime_type,
            as_attachment=False,
            download_name=inspiration.name or f"inspiration_{inspiration.id}",
        )

    if inspiration.image_data:
        try:
            decoded = base64.b64decode(inspiration.image_data, validate=False)
        except Exception:
            decoded = b""
        if decoded:
            return send_file(
                io.BytesIO(decoded),
                mimetype=inspiration.mime_type,
                as_attachment=False,
                download_name=inspiration.name or f"inspiration_{inspiration.id}",
            )

    abort(404)
