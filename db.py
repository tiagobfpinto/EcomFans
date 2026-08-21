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
    default_gemini_image_model = db.Column(
        db.String(120), nullable=False, default="gemini-2.5-flash-image"
    )
    batch_api_for_queued_jobs = db.Column(
        db.Boolean, nullable=False, default=False
    )
    stripe_customer_id = db.Column(db.String(60), nullable=True, unique=True)
    stripe_subscription_id = db.Column(db.String(60), nullable=True)
    stripe_subscription_status = db.Column(db.String(30), nullable=True)
    # Auth security fields
    failed_login_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime(timezone=True), nullable=True)
    last_password_reset_request_at = db.Column(db.DateTime(timezone=True), nullable=True)
    reset_token = db.Column(db.String(128), nullable=True, unique=True)
    reset_token_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # Bumped whenever the password changes. Sessions carry the value they were
    # issued with, so every older session — including one an attacker stole —
    # stops validating the moment the real owner resets their password.
    session_epoch = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    api_keys = db.relationship("ApiKey", backref="user", lazy=True)
    prompts = db.relationship("ImagePrompt", backref="user", lazy=True)
    prompt_library_targets = db.relationship(
        "PromptLibraryTarget",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan",
    )
    prompt_library_items = db.relationship(
        "PromptLibraryItem",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan",
    )
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
    api_request_events = db.relationship(
        "ApiRequestEvent", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    worker_jobs = db.relationship(
        "WorkerJob", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    brand_dna_analyses = db.relationship(
        "BrandDNAAnalysis", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    ad_metrics_snapshots = db.relationship(
        "AdMetricsSnapshot", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    social_downloads = db.relationship(
        "SocialDownload", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    storyboard_projects = db.relationship(
        "StoryboardProject",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan",
    )
    note_boards = db.relationship(
        "NoteBoard",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan",
    )
    funnels = db.relationship(
        "Funnel",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan",
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


class ApiRequestEvent(db.Model):
    __tablename__ = "api_request_events"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    feature = db.Column(db.String(50), nullable=True)
    provider = db.Column(db.String(50), nullable=False)
    operation = db.Column(db.String(80), nullable=True)
    model = db.Column(db.String(120), nullable=True)
    traffic_type = db.Column(db.String(20), nullable=True)  # standard | batch
    status = db.Column(db.String(20), nullable=False, default="completed")
    http_status = db.Column(db.Integer, nullable=True)
    latency_ms = db.Column(db.Integer, nullable=True)
    input_tokens = db.Column(db.Integer, nullable=True)
    cached_tokens = db.Column(db.Integer, nullable=True)
    output_tokens = db.Column(db.Integer, nullable=True)
    total_tokens = db.Column(db.Integer, nullable=True)
    input_image_count = db.Column(db.Integer, nullable=True, default=0)
    images_generated = db.Column(db.Integer, nullable=True, default=0)
    estimated_cost_usd = db.Column(db.Numeric(12, 6), nullable=True)
    estimated_cost_eur = db.Column(db.Numeric(12, 6), nullable=True)
    request_id = db.Column(db.String(120), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    metadata_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkerJob(db.Model):
    __tablename__ = "worker_jobs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    queue_name = db.Column(db.String(50), nullable=False, default="default")
    job_type = db.Column(db.String(80), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="queued")
    payload_json = db.Column(db.Text, nullable=False)
    result_json = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    max_attempts = db.Column(db.Integer, nullable=False, default=2)
    available_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    locked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    lock_token = db.Column(db.String(64), nullable=True)
    worker_name = db.Column(db.String(120), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
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
    storage_path = db.Column(db.String(500), nullable=True)
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


class PromptLibraryTarget(db.Model):
    __tablename__ = "prompt_library_targets"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    target_type = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=True)
    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    product = db.relationship("Product", lazy=True)
    prompts = db.relationship(
        "PromptLibraryItem",
        backref="target",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="PromptLibraryItem.created_at.desc()",
    )
    images = db.relationship(
        "PromptLibraryTargetImage",
        backref="target",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="PromptLibraryTargetImage.sort_order",
    )

    __table_args__ = (
        db.Index(
            "ix_prompt_library_targets_user_type_name",
            "user_id",
            "target_type",
            "name",
        ),
        db.Index("ix_prompt_library_targets_product_id", "product_id"),
    )


class PromptLibraryItem(db.Model):
    __tablename__ = "prompt_library_items"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    target_id = db.Column(
        db.Integer,
        db.ForeignKey("prompt_library_targets.id"),
        nullable=True,
    )
    name = db.Column(db.String(160), nullable=False, default="Untitled prompt")
    prompt_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    thumbnails = db.relationship(
        "PromptLibraryThumbnail",
        backref="prompt",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="PromptLibraryThumbnail.sort_order",
    )

    __table_args__ = (
        db.Index(
            "ix_prompt_library_items_user_created",
            "user_id",
            "created_at",
        ),
        db.Index("ix_prompt_library_items_target_id", "target_id"),
    )


class PromptLibraryThumbnail(db.Model):
    __tablename__ = "prompt_library_thumbnails"

    id = db.Column(db.Integer, primary_key=True)
    prompt_id = db.Column(
        db.Integer,
        db.ForeignKey("prompt_library_items.id"),
        nullable=False,
    )
    sort_order = db.Column(db.Integer, nullable=False, default=1)
    filename = db.Column(db.String(255), nullable=True)
    mime_type = db.Column(db.String(100), nullable=False)
    storage_path = db.Column(db.String(500), nullable=False)
    width = db.Column(db.Integer, nullable=True)
    height = db.Column(db.Integer, nullable=True)
    file_size_bytes = db.Column(db.Integer, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        db.Index("ix_prompt_library_thumbnails_prompt_id", "prompt_id"),
    )


class PromptLibraryTargetImage(db.Model):
    __tablename__ = "prompt_library_target_images"

    id = db.Column(db.Integer, primary_key=True)
    target_id = db.Column(
        db.Integer,
        db.ForeignKey("prompt_library_targets.id"),
        nullable=False,
    )
    sort_order = db.Column(db.Integer, nullable=False, default=1)
    filename = db.Column(db.String(255), nullable=True)
    mime_type = db.Column(db.String(100), nullable=False)
    storage_path = db.Column(db.String(500), nullable=False)
    width = db.Column(db.Integer, nullable=True)
    height = db.Column(db.Integer, nullable=True)
    file_size_bytes = db.Column(db.Integer, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        db.Index("ix_prompt_library_target_images_target_id", "target_id"),
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
    before_storage_path = db.Column(db.String(500), nullable=True)
    after_storage_path = db.Column(db.String(500), nullable=True)
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
    generated_storage_path = db.Column(db.String(500), nullable=True)
    generated_image = db.Column(db.Text, nullable=True)  # base64 encoded
    status = db.Column(db.String(20), nullable=False, default="pending")
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CreativeInspirationAnalysis(db.Model):
    """Cached vision analysis results for creative inspiration images.

    Keyed by (inspiration_id, product_id, provider, prompt_hash) so that
    re-running creatives generation on the same inspiration+product combination
    skips the expensive vision API call.
    """
    __tablename__ = "creative_inspiration_analyses"

    id = db.Column(db.Integer, primary_key=True)
    inspiration_id = db.Column(
        db.Integer, db.ForeignKey("creative_inspirations.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    provider = db.Column(db.String(20), nullable=False)
    prompt_hash = db.Column(db.String(64), nullable=False)
    result_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        db.Index(
            "ix_creative_inspiration_analyses_lookup",
            "inspiration_id", "product_id", "provider", "prompt_hash",
        ),
    )


class BrandDNAAnalysis(db.Model):
    __tablename__ = "brand_dna_analyses"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    url = db.Column(db.String(2048), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="queued")
    brand_name = db.Column(db.String(255), nullable=True)
    brand_description = db.Column(db.Text, nullable=True)
    color_palette = db.Column(db.Text, nullable=True)  # JSON array of hex strings
    products_created = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.Text, nullable=True)
    worker_job_id = db.Column(db.Integer, db.ForeignKey("worker_jobs.id"), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    __table_args__ = (
        db.Index("ix_brand_dna_analyses_user_id", "user_id"),
        db.Index("ix_brand_dna_analyses_worker_job_id", "worker_job_id"),
    )


class AdMetricsSnapshot(db.Model):
    __tablename__ = "ad_metrics_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    metric_date = db.Column(db.Date, nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    reported_ad_count = db.Column(db.Integer, nullable=False, default=0)
    has_reported_total = db.Column(db.Boolean, nullable=False, default=False)
    total_spend = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    total_cpm = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    total_cpc = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    total_ctr = db.Column(db.Numeric(10, 4), nullable=False, default=0)
    total_adds_to_cart = db.Column(db.Integer, nullable=False, default=0)
    total_purchases = db.Column(db.Integer, nullable=False, default=0)
    total_cost_per_purchase = db.Column(
        db.Numeric(14, 4), nullable=False, default=0
    )
    total_roas = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    total_frequency = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    imported_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    metrics = db.relationship(
        "AdMetric",
        backref="snapshot",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="AdMetric.spend.desc()",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "metric_date", name="uq_ad_metrics_snapshots_user_date"
        ),
        db.Index(
            "ix_ad_metrics_snapshots_user_date", "user_id", "metric_date"
        ),
    )


class AdMetric(db.Model):
    __tablename__ = "ad_metrics"

    id = db.Column(db.Integer, primary_key=True)
    snapshot_id = db.Column(
        db.Integer, db.ForeignKey("ad_metrics_snapshots.id"), nullable=False
    )
    ad_name = db.Column(db.String(255), nullable=False)
    spend = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    cpm = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    cpc = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    ctr = db.Column(db.Numeric(10, 4), nullable=False, default=0)
    adds_to_cart = db.Column(db.Integer, nullable=False, default=0)
    purchases = db.Column(db.Integer, nullable=False, default=0)
    cost_per_purchase = db.Column(
        db.Numeric(14, 4), nullable=False, default=0
    )
    roas = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    frequency = db.Column(db.Numeric(14, 4), nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint(
            "snapshot_id", "ad_name", name="uq_ad_metrics_snapshot_ad"
        ),
        db.Index("ix_ad_metrics_snapshot_id", "snapshot_id"),
    )


class SocialDownload(db.Model):
    __tablename__ = "social_downloads"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    worker_job_id = db.Column(
        db.Integer, db.ForeignKey("worker_jobs.id"), nullable=True
    )
    source_url = db.Column(db.String(2048), nullable=False)
    platform = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="queued")
    title = db.Column(db.String(500), nullable=True)
    storage_path = db.Column(db.String(500), nullable=True)
    mime_type = db.Column(db.String(100), nullable=True)
    file_size_bytes = db.Column(db.BigInteger, nullable=True)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)

    worker_job = db.relationship("WorkerJob", lazy=True)

    __table_args__ = (
        db.Index(
            "ix_social_downloads_user_created", "user_id", "created_at"
        ),
        db.Index(
            "ix_social_downloads_user_status", "user_id", "status"
        ),
        db.Index("ix_social_downloads_worker_job_id", "worker_job_id"),
    )


class VoiceoverTightening(db.Model):
    __tablename__ = "voiceover_tightenings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    worker_job_id = db.Column(
        db.Integer, db.ForeignKey("worker_jobs.id"), nullable=True
    )
    status = db.Column(db.String(20), nullable=False, default="queued")
    original_filename = db.Column(db.String(255), nullable=False)
    original_storage_path = db.Column(db.String(500), nullable=False)
    output_storage_path = db.Column(db.String(500), nullable=True)
    original_file_size_bytes = db.Column(db.BigInteger, nullable=False)
    output_file_size_bytes = db.Column(db.BigInteger, nullable=True)
    preset = db.Column(db.String(20), nullable=False, default="dynamic")
    settings_json = db.Column(db.Text, nullable=False)
    original_duration_ms = db.Column(db.BigInteger, nullable=True)
    output_duration_ms = db.Column(db.BigInteger, nullable=True)
    removed_duration_ms = db.Column(db.BigInteger, nullable=True)
    pauses_shortened = db.Column(db.Integer, nullable=True)
    overlaps_applied = db.Column(db.Integer, nullable=True)
    warnings_json = db.Column(db.Text, nullable=True)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)

    worker_job = db.relationship("WorkerJob", lazy=True)

    __table_args__ = (
        db.Index(
            "ix_voiceover_tightenings_user_created",
            "user_id",
            "created_at",
        ),
        db.Index(
            "ix_voiceover_tightenings_user_status",
            "user_id",
            "status",
        ),
        db.Index(
            "ix_voiceover_tightenings_worker_job_id", "worker_job_id"
        ),
    )


class SavedScript(db.Model):
    __tablename__ = "saved_scripts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False, default="Untitled script")
    transcript = db.Column(db.Text, nullable=False)
    source_filename = db.Column(db.String(255), nullable=True)
    thumbnail_storage_path = db.Column(db.String(500), nullable=True)
    thumbnail_mime_type = db.Column(db.String(100), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        db.Index("ix_saved_scripts_user_created", "user_id", "created_at"),
    )


class Competitor(db.Model):
    __tablename__ = "competitors"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    name = db.Column(db.String(160), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    product = db.relationship("Product", lazy=True)
    ads = db.relationship(
        "CompetitorAd",
        backref="competitor",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="desc(CompetitorAd.created_at)",
    )

    __table_args__ = (
        db.Index("ix_competitors_user_created", "user_id", "created_at"),
        db.Index("ix_competitors_product_id", "product_id"),
    )


class CompetitorAd(db.Model):
    __tablename__ = "competitor_ads"

    id = db.Column(db.Integer, primary_key=True)
    competitor_id = db.Column(
        db.Integer, db.ForeignKey("competitors.id"), nullable=False
    )
    original_filename = db.Column(db.String(255), nullable=True)
    mime_type = db.Column(db.String(100), nullable=False, default="video/mp4")
    storage_path = db.Column(db.String(500), nullable=True)
    file_size_bytes = db.Column(db.BigInteger, nullable=True)

    transcript = db.Column(db.Text, nullable=True)
    transcript_status = db.Column(db.String(20), nullable=False, default="queued")
    transcript_error = db.Column(db.Text, nullable=True)
    transcribe_job_id = db.Column(
        db.Integer, db.ForeignKey("worker_jobs.id"), nullable=True
    )

    analysis_json = db.Column(db.Text, nullable=True)
    analysis_status = db.Column(db.String(20), nullable=False, default="none")
    analysis_error = db.Column(db.Text, nullable=True)
    analysis_job_id = db.Column(
        db.Integer, db.ForeignKey("worker_jobs.id"), nullable=True
    )

    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        db.Index("ix_competitor_ads_competitor_created", "competitor_id", "created_at"),
    )


class StoryboardProject(db.Model):
    __tablename__ = "storyboard_projects"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(160), nullable=False)
    base_prompt = db.Column(db.Text, nullable=False, default="")
    prompt_blocks_json = db.Column(db.Text, nullable=False, default="{}")
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    product = db.relationship("Product", lazy=True)
    frames = db.relationship(
        "StoryboardFrame",
        backref="project",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="StoryboardFrame.sort_order",
    )

    __table_args__ = (
        db.Index(
            "ix_storyboard_projects_user_created",
            "user_id",
            "created_at",
        ),
        db.Index("ix_storyboard_projects_product_id", "product_id"),
    )


class StoryboardFrame(db.Model):
    __tablename__ = "storyboard_frames"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("storyboard_projects.id"),
        nullable=False,
    )
    sort_order = db.Column(db.Integer, nullable=False)
    label = db.Column(db.Text, nullable=False)
    clip_type = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.Text, nullable=False)
    photo = db.Column(db.Text, nullable=False)
    transform_prompt = db.Column(db.Text, nullable=False)
    voiceover = db.Column(db.Text, nullable=False)
    video_prompt = db.Column(db.Text, nullable=False)
    thumbnail_filename = db.Column(db.String(255), nullable=True)
    thumbnail_mime_type = db.Column(db.String(100), nullable=True)
    thumbnail_storage_path = db.Column(db.String(500), nullable=True)
    thumbnail_width = db.Column(db.Integer, nullable=True)
    thumbnail_height = db.Column(db.Integer, nullable=True)
    thumbnail_file_size_bytes = db.Column(db.BigInteger, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "project_id",
            "sort_order",
            name="uq_storyboard_frames_project_sort_order",
        ),
        db.Index("ix_storyboard_frames_project_id", "project_id"),
    )


class NoteBoard(db.Model):
    __tablename__ = "note_boards"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    document_json = db.Column(
        db.Text,
        nullable=False,
        default='{"schema_version":1,"viewport":{"x":0,"y":0,"zoom":1},"objects":[]}',
    )
    object_count = db.Column(db.Integer, nullable=False, default=0)
    revision = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        db.Index("ix_note_boards_user_updated", "user_id", "updated_at"),
    )


class Funnel(db.Model):
    __tablename__ = "funnels"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    pages = db.relationship(
        "FunnelPage",
        backref="funnel",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="FunnelPage.sort_order",
    )

    __table_args__ = (
        db.Index("ix_funnels_user_updated", "user_id", "updated_at"),
    )


class FunnelPage(db.Model):
    __tablename__ = "funnel_pages"

    id = db.Column(db.Integer, primary_key=True)
    funnel_id = db.Column(
        db.Integer,
        db.ForeignKey("funnels.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = db.Column(db.String(200), nullable=False)
    page_type = db.Column(db.String(30), nullable=False, default="listicle")
    slug = db.Column(db.String(180), nullable=False)
    html_content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="draft")
    sort_order = db.Column(db.Integer, nullable=False, default=1)
    revision = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint("slug", name="uq_funnel_pages_slug"),
        db.Index("ix_funnel_pages_funnel_order", "funnel_id", "sort_order"),
        db.Index("ix_funnel_pages_status_slug", "status", "slug"),
    )
