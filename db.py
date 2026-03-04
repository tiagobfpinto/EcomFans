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
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    jobs = db.relationship("ProcessingJob", backref="user", lazy=True)
    videos = db.relationship("Video", backref="user", lazy=True)


class ProcessingJob(db.Model):
    __tablename__ = "processing_jobs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    total_urls = db.Column(db.Integer, nullable=False, default=0)
    processed_urls = db.Column(db.Integer, nullable=False, default=0)
    failed_urls = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at = db.Column(db.DateTime(timezone=True))

    videos = db.relationship("Video", backref="job", lazy=True)


class Video(db.Model):
    __tablename__ = "videos"

    id = db.Column(db.Integer, primary_key=True)
    youtube_id = db.Column(db.String(20), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    title = db.Column(db.String(500))
    channel_name = db.Column(db.String(200))
    upload_date = db.Column(db.String(20))
    view_count = db.Column(db.Integer)
    thumbnail_url = db.Column(db.String(500))
    transcript_text = db.Column(db.Text)
    processing_status = db.Column(db.String(30), nullable=False, default="pending")
    error_message = db.Column(db.Text)
    analyzed_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    job_id = db.Column(
        db.Integer, db.ForeignKey("processing_jobs.id"), nullable=False
    )

    mentions = db.relationship("Mention", backref="video", lazy=True)

    __table_args__ = (
        db.UniqueConstraint("youtube_id", "user_id", name="uq_video_user"),
    )


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(300), nullable=False)
    normalized_name = db.Column(db.String(300), nullable=False, index=True)
    brand = db.Column(db.String(200))
    category = db.Column(db.String(100))
    first_seen_date = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    total_mentions = db.Column(db.Integer, nullable=False, default=0)
    unique_creators = db.Column(db.Integer, nullable=False, default=0)
    trend_score = db.Column(db.Float, nullable=False, default=0.0)
    trend_classification = db.Column(db.String(50))
    updated_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    mentions = db.relationship("Mention", backref="product", lazy=True)


class Mention(db.Model):
    __tablename__ = "mentions"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    video_id = db.Column(db.Integer, db.ForeignKey("videos.id"), nullable=False)
    sentiment = db.Column(db.String(20))
    context_quote = db.Column(db.Text)
    mention_type = db.Column(db.String(30))
    is_sponsored = db.Column(db.Boolean, default=False)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        db.UniqueConstraint("product_id", "video_id", name="uq_mention_product_video"),
    )
