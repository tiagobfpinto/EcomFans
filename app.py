import base64
import os
from urllib.parse import quote_plus

import click
from dotenv import load_dotenv
from flask import Blueprint, Flask, redirect, render_template, session, url_for

from ai_creatives import ai_creatives_bp
from ai_image import ai_image_bp
from auth import auth_bp, login_required
from avatars import avatars_bp
from billing import billing_bp
from billing_service import get_billing_summary, refresh_cycle_if_needed
from db import CreativeInspiration, ProductImage, User, db, migrate
from media import media_bp
from media_storage import save_inspiration_image, save_product_image
from products import products_bp
from script_optimizer import script_optimizer_bp
from scraper import scraper_bp

load_dotenv()


def get_database_uri():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    user = quote_plus(os.getenv("DB_USER", "postgres"))
    password = quote_plus(os.getenv("DB_PASSWORD", ""))
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "ecomfans")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = get_database_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MEDIA_ROOT"] = os.getenv(
    "MEDIA_ROOT",
    os.path.join(app.instance_path, "media"),
)

db.init_app(app)
migrate.init_app(app, db)

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(scraper_bp)
app.register_blueprint(ai_image_bp)
app.register_blueprint(products_bp)
app.register_blueprint(avatars_bp)
app.register_blueprint(ai_creatives_bp)
app.register_blueprint(script_optimizer_bp)
app.register_blueprint(billing_bp)
app.register_blueprint(media_bp)


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


# Main blueprint for dashboard
main_bp = Blueprint("main", __name__)


@main_bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", username=session.get("username"))


app.register_blueprint(main_bp)


@app.context_processor
def inject_billing_context():
    if "user_id" not in session:
        return {}

    user = db.session.get(User, session["user_id"])
    if not user:
        return {}

    changed = refresh_cycle_if_needed(user)
    summary = get_billing_summary(user)
    if changed:
        db.session.commit()
    return {"billing_summary": summary}


@app.cli.command("backfill-media-files")
@click.option("--batch-size", default=100, show_default=True, type=int)
def backfill_media_files(batch_size):
    """Backfill DB-stored product and inspiration images to filesystem storage."""
    batch_size = max(1, int(batch_size))
    counters = {
        "product_total": 0,
        "product_migrated": 0,
        "product_failed": 0,
        "inspiration_total": 0,
        "inspiration_migrated": 0,
        "inspiration_failed": 0,
    }

    def flush_checkpoint():
        db.session.commit()
        click.echo(
            "Checkpoint commit: "
            f"products migrated={counters['product_migrated']} failed={counters['product_failed']} | "
            f"inspirations migrated={counters['inspiration_migrated']} failed={counters['inspiration_failed']}"
        )

    click.echo("Starting product image backfill...")
    pending = 0
    product_rows = (
        ProductImage.query
        .filter(
            ProductImage.storage_path.is_(None),
            ProductImage.image_data.isnot(None),
        )
        .order_by(ProductImage.id.asc())
        .all()
    )
    for image in product_rows:
        counters["product_total"] += 1
        try:
            image_bytes = (
                base64.b64decode(image.image_data, validate=True)
                if image.image_data
                else b""
            )
            if not image_bytes:
                counters["product_failed"] += 1
                continue
            if not image.product:
                counters["product_failed"] += 1
                continue
            image.storage_path = save_product_image(
                image.product.user_id,
                image.product_id,
                image.id,
                image.mime_type,
                image_bytes,
            )
            image.image_data = None
            counters["product_migrated"] += 1
            pending += 1
        except Exception:
            counters["product_failed"] += 1

        if pending >= batch_size:
            flush_checkpoint()
            pending = 0

    if pending:
        flush_checkpoint()
        pending = 0

    click.echo("Starting inspiration image backfill...")
    inspiration_rows = (
        CreativeInspiration.query
        .filter(
            CreativeInspiration.storage_path.is_(None),
            CreativeInspiration.image_data.isnot(None),
        )
        .order_by(CreativeInspiration.id.asc())
        .all()
    )
    for inspiration in inspiration_rows:
        counters["inspiration_total"] += 1
        try:
            image_bytes = (
                base64.b64decode(inspiration.image_data, validate=True)
                if inspiration.image_data
                else b""
            )
            if not image_bytes:
                counters["inspiration_failed"] += 1
                continue
            inspiration.storage_path = save_inspiration_image(
                inspiration.user_id,
                inspiration.id,
                inspiration.mime_type,
                image_bytes,
            )
            inspiration.image_data = None
            counters["inspiration_migrated"] += 1
            pending += 1
        except Exception:
            counters["inspiration_failed"] += 1

        if pending >= batch_size:
            flush_checkpoint()
            pending = 0

    if pending:
        flush_checkpoint()

    click.echo("Backfill complete.")
    click.echo(
        "Products: "
        f"total={counters['product_total']} migrated={counters['product_migrated']} failed={counters['product_failed']}"
    )
    click.echo(
        "Inspirations: "
        f"total={counters['inspiration_total']} migrated={counters['inspiration_migrated']} failed={counters['inspiration_failed']}"
    )

if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "False").lower() in ("true", "1", "yes")
    app.run(debug=debug, host="0.0.0.0", port=5000)
