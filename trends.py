from flask import Blueprint, render_template, request

from auth import login_required
from db import Mention, Product, Video, db

trends_bp = Blueprint("trends", __name__, url_prefix="/trends")


@trends_bp.route("/")
@login_required
def feed():
    category = request.args.get("category", "")
    page = request.args.get("page", 1, type=int)
    per_page = 20

    query = Product.query.filter(Product.total_mentions > 0)

    if category:
        query = query.filter(Product.category == category)

    pagination = query.order_by(Product.trend_score.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Get distinct categories for filter dropdown
    categories = (
        db.session.query(Product.category)
        .filter(Product.category.isnot(None), Product.total_mentions > 0)
        .distinct()
        .order_by(Product.category)
        .all()
    )
    categories = [c[0] for c in categories if c[0]]

    return render_template(
        "trends/feed.html",
        products=pagination.items,
        pagination=pagination,
        categories=categories,
        selected_category=category,
    )


@trends_bp.route("/product/<int:product_id>")
@login_required
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)

    mentions = (
        Mention.query.filter_by(product_id=product.id)
        .join(Video)
        .order_by(Video.analyzed_at.desc())
        .all()
    )

    # Sentiment summary
    sentiments = {"positive": 0, "negative": 0, "neutral": 0}
    for m in mentions:
        if m.sentiment in sentiments:
            sentiments[m.sentiment] += 1

    return render_template(
        "trends/product_detail.html",
        product=product,
        mentions=mentions,
        sentiments=sentiments,
    )
