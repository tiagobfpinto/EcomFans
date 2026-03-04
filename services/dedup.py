from difflib import SequenceMatcher

from db import Product, db


FUZZY_THRESHOLD = 0.85


def find_existing_product(normalized_name, brand=None):
    """Two-tier matching: exact then fuzzy."""
    # Tier 1: Exact match
    exact = Product.query.filter_by(normalized_name=normalized_name).first()
    if exact:
        return exact

    # Tier 2: Fuzzy match
    if brand:
        candidates = Product.query.filter_by(brand=brand).all()
    else:
        candidates = Product.query.all()

    for candidate in candidates:
        ratio = SequenceMatcher(
            None, normalized_name, candidate.normalized_name
        ).ratio()
        if ratio >= FUZZY_THRESHOLD:
            return candidate

    return None


def get_or_create_product(product_data):
    """Find existing product or create a new one."""
    normalized = product_data.get("normalized_name", "").lower().strip()
    brand = product_data.get("brand")

    existing = find_existing_product(normalized, brand)
    if existing:
        return existing

    product = Product(
        name=product_data.get("product_name", normalized),
        normalized_name=normalized,
        brand=brand,
        category=product_data.get("category"),
    )
    db.session.add(product)
    db.session.flush()
    return product
