from sqlalchemy import func as sql_func

from db import Mention, Product, Video, db


def recompute_product_stats(product_id):
    """Recompute mentions and unique creators for a single product."""
    total = Mention.query.filter_by(product_id=product_id).count()

    unique = (
        db.session.query(sql_func.count(sql_func.distinct(Video.channel_name)))
        .join(Mention, Mention.video_id == Video.id)
        .filter(Mention.product_id == product_id)
        .scalar()
    ) or 0

    product = db.session.get(Product, product_id)
    if product:
        product.total_mentions = total
        product.unique_creators = unique
        product.trend_score = float(unique)
        product.updated_at = sql_func.now()
        db.session.commit()


def recompute_all_product_stats():
    """Batch recompute stats for all products."""
    products = Product.query.all()
    for product in products:
        total = Mention.query.filter_by(product_id=product.id).count()

        unique = (
            db.session.query(sql_func.count(sql_func.distinct(Video.channel_name)))
            .join(Mention, Mention.video_id == Video.id)
            .filter(Mention.product_id == product.id)
            .scalar()
        ) or 0

        product.total_mentions = total
        product.unique_creators = unique
        product.trend_score = float(unique)
        product.updated_at = sql_func.now()

    db.session.commit()
