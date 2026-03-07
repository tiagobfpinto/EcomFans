from datetime import datetime, timedelta, timezone

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func


db = SQLAlchemy()
migrate = Migrate()


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    credits = db.Column(db.Integer, nullable=False, default=30)
    extra_credits = db.Column(db.Integer, nullable=False, default=0)
    plan_tier = db.Column(db.String(20), nullable=False, default="free")
    next_credit_reset_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc) + timedelta(days=30),
    )
    default_base_prompt = db.Column(db.Text, nullable=True)
    default_analysis_prompt = db.Column(db.Text, nullable=True)
    default_product_info = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    api_keys = db.relationship("ApiKey", backref="user", lazy=True)
    prompts = db.relationship("ImagePrompt", backref="user", lazy=True)
    products = db.relationship(
        "Product", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    avatar_batches = db.relationship(
        "AvatarBatch", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    inspirations = db.relationship(
        "CreativeInspiration", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    creative_batches = db.relationship(
        "CreativeBatch", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    credit_ledger_entries = db.relationship(
        "CreditLedger", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    mock_payments = db.relationship(
        "MockPayment", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    usage_events = db.relationship(
        "UsageEvent", backref="user", lazy=True, cascade="all, delete-orphan"
    )


class ApiKey(db.Model):
    __tablename__ = "api_keys"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    service = db.Column(db.String(50), nullable=False, default="gemini")
    api_key = db.Column(db.String(512), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )


class CreditLedger(db.Model):
    __tablename__ = "credit_ledger"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    entry_type = db.Column(db.String(20), nullable=False)  # credit | debit
    source = db.Column(db.String(50), nullable=False)  # usage | plan_purchase | etc
    monthly_delta = db.Column(db.Integer, nullable=False, default=0)
    extra_delta = db.Column(db.Integer, nullable=False, default=0)
    monthly_balance = db.Column(db.Integer, nullable=False, default=0)
    extra_balance = db.Column(db.Integer, nullable=False, default=0)
    feature = db.Column(db.String(50), nullable=True)
    provider = db.Column(db.String(50), nullable=True)
    description = db.Column(db.Text, nullable=True)
    metadata_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MockPayment(db.Model):
    __tablename__ = "mock_payments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    payment_type = db.Column(db.String(20), nullable=False)  # plan | credits
    plan_tier = db.Column(db.String(20), nullable=True)
    pack_id = db.Column(db.String(50), nullable=True)
    amount_eur = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(10), nullable=False, default="EUR")
    status = db.Column(db.String(20), nullable=False, default="paid")
    metadata_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UsageEvent(db.Model):
    __tablename__ = "usage_events"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    feature = db.Column(db.String(50), nullable=False)
    provider = db.Column(db.String(50), nullable=True)
    units = db.Column(db.Integer, nullable=False, default=1)
    credits_charged = db.Column(db.Integer, nullable=False)
    estimated_cost_usd = db.Column(db.Numeric(12, 5), nullable=False, default=0)
    metadata_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ImagePrompt(db.Model):
    __tablename__ = "image_prompts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    prompt_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    generations = db.relationship(
        "ImageGeneration", backref="prompt", lazy=True,
        cascade="all, delete-orphan"
    )


class ImageGeneration(db.Model):
    __tablename__ = "image_generations"

    id = db.Column(db.Integer, primary_key=True)
    prompt_id = db.Column(
        db.Integer, db.ForeignKey("image_prompts.id"), nullable=False
    )
    variation_index = db.Column(db.Integer, nullable=False, default=1)
    image_data = db.Column(db.Text, nullable=True)  # base64 encoded image
    status = db.Column(db.String(20), nullable=False, default="pending")
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    context = db.Column(db.Text, nullable=False)
    price = db.Column(db.Text, nullable=True)
    offer = db.Column(db.Text, nullable=True)
    benefits = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    images = db.relationship(
        "ProductImage",
        backref="product",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="ProductImage.sort_order",
    )

    def build_product_info(self):
        parts = []
        if self.price:
            parts.append(f"Price: {self.price.strip()}")
        if self.offer:
            parts.append(f"Offer: {self.offer.strip()}")
        if self.benefits:
            parts.append(f"Benefits: {self.benefits.strip()}")
        return "\n".join(parts)


class ProductImage(db.Model):
    __tablename__ = "product_images"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=1)
    filename = db.Column(db.String(255), nullable=True)
    mime_type = db.Column(db.String(100), nullable=False)
    storage_path = db.Column(db.String(500), nullable=True)
    image_data = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AvatarBatch(db.Model):
    __tablename__ = "avatar_batches"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    characteristic = db.Column(db.String(200), nullable=False)
    personas = db.Column(db.Text, nullable=False)  # JSON list of persona strings
    count_per_persona = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    product = db.relationship("Product", backref="avatar_batches", lazy=True)
    results = db.relationship(
        "AvatarResult", backref="batch", lazy=True,
        cascade="all, delete-orphan",
        order_by="AvatarResult.id",
    )


class AvatarResult(db.Model):
    __tablename__ = "avatar_results"

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(
        db.Integer, db.ForeignKey("avatar_batches.id"), nullable=False
    )
    persona = db.Column(db.String(200), nullable=False)
    before_image = db.Column(db.Text, nullable=True)
    after_image = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CreativeInspiration(db.Model):
    __tablename__ = "creative_inspirations"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(255), nullable=True)
    storage_path = db.Column(db.String(500), nullable=True)
    image_data = db.Column(db.Text, nullable=True)  # base64 fallback during migration
    mime_type = db.Column(db.String(100), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    creative_results = db.relationship(
        "CreativeResult", backref="inspiration", lazy=True
    )


class CreativeBatch(db.Model):
    __tablename__ = "creative_batches"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    product = db.relationship("Product", backref="creative_batches", lazy=True)
    results = db.relationship(
        "CreativeResult", backref="batch", lazy=True,
        cascade="all, delete-orphan",
        order_by="CreativeResult.id",
    )


class CreativeResult(db.Model):
    __tablename__ = "creative_results"

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(
        db.Integer, db.ForeignKey("creative_batches.id"), nullable=False
    )
    inspiration_id = db.Column(
        db.Integer, db.ForeignKey("creative_inspirations.id"), nullable=True
    )
    generated_prompt = db.Column(db.Text, nullable=True)
    generated_image = db.Column(db.Text, nullable=True)  # base64 encoded
    status = db.Column(db.String(20), nullable=False, default="pending")
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
