import base64
import hmac
import mimetypes
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta
from threading import Lock
from typing import Optional
from urllib.parse import quote_plus

import click
import sentry_sdk
from dotenv import load_dotenv
from flask import (
    Blueprint,
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge

from ai_creatives import ai_creatives_bp
from admin import admin_bp
from brand_dna import brand_dna_bp
from ai_image import ai_image_bp
from auth import auth_bp, login_required
from avatars import avatars_bp
from billing import billing_bp
from billing_service import get_billing_summary, refresh_cycle_if_needed
from db import CreativeInspiration, ProductImage, User, db, migrate
from media import media_bp
from media_storage import save_inspiration_image, save_product_image
from metrics import metrics_bp
import net_guard
from security import (
    PERMISSIONS_POLICY,
    build_content_security_policy,
    csp_nonce,
)
from notes import notes_bp
from products import products_bp
from product_images import product_images_bp
from prompts import prompts_bp
from landing_builder import landing_builder_bp
from competitors import competitors_bp
from funnels import funnels_bp
from script_optimizer import script_optimizer_bp
from scraper import scraper_bp
from social_downloader import social_downloader_bp
from storyboarder import storyboarder_bp
from voiceover_tightener import voiceover_tightener_bp
from utils_crypto import encrypt_api_key
from worker_runtime import run_worker_pool

# Landing page blueprint
landing_bp = Blueprint("landing", __name__)

load_dotenv()
net_guard.install()
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")

_sentry_dsn = (os.getenv("SENTRY_DSN") or "").strip()
if _sentry_dsn:
    _PII_FORM_FIELDS = {"password", "password_hash", "current_password", "new_password", "confirm_password"}

    def _sentry_before_send(event, hint):
        # Scrub sensitive form fields from request data so they never reach Sentry
        try:
            data = event.get("request", {}).get("data")
            if isinstance(data, dict):
                for field in _PII_FORM_FIELDS:
                    if field in data:
                        data[field] = "[Filtered]"
        except Exception:
            pass
        return event

    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
        send_default_pii=False,
        before_send=_sentry_before_send,
    )


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


def _is_truthy(raw_value: str | None, default: bool = False) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _is_dev_mode() -> bool:
    env = (os.getenv("FLASK_ENV") or "").strip().lower()
    if env in {"dev", "development"}:
        return True
    return _is_truthy(os.getenv("FLASK_DEBUG"), default=False)


def _request_ip() -> str:
    trusted_raw = (os.getenv("TRUSTED_PROXIES") or "").strip()
    if trusted_raw:
        trusted = {ip.strip() for ip in trusted_raw.split(",") if ip.strip()}
        if request.remote_addr in trusted:
            forwarded_for = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if forwarded_for:
                return forwarded_for
    return request.remote_addr or "unknown"


def _match_rate_limit(path: str, method: str) -> tuple[int, int] | None:
    normalized_method = method.upper()
    if normalized_method == "GET" and path.startswith("/script-optimizer/transcribe/jobs/"):
        return 120, 60
    if normalized_method == "GET" and path == "/voiceover-tightener/items":
        return 120, 60

    rules = {
        ("/login", "POST"): (10, 60),
        ("/register", "POST"): (8, 60),
        ("/forgot-password", "POST"): (5, 60),
        ("/scrape", "POST"): (20, 60),
        ("/download", "POST"): (20, 60),
        ("/ai-image/generate", "POST"): (10, 60),
        ("/products/remove-background", "POST"): (12, 60),
        ("/product-images/lacy/auto-scrape", "POST"): (8, 60),
        ("/metrics/import", "POST"): (10, 60),
        ("/social-downloader/items", "POST"): (20, 60),
        ("/avatars/generate", "POST"): (8, 60),
        ("/ai-creatives/generate", "POST"): (8, 60),
        ("/ai-creatives/inspirations", "POST"): (16, 60),
        ("/script-optimizer/transcribe", "POST"): (6, 60),
        ("/voiceover-tightener/items", "POST"): (6, 60),
        ("/brand-dna/analyze", "POST"): (5, 60),
        ("/billing/account", "GET"): (60, 3600),
        ("/billing/usage", "GET"): (60, 3600),
    }
    return rules.get((path, normalized_method))


def _prefers_json_response() -> bool:
    if request.path.startswith(
        (
            "/ai-",
            "/avatars",
            "/billing/",
            "/script-optimizer",
            "/scrape",
            "/download",
            "/media/",
            "/metrics/",
            "/notes/",
            "/product-images/",
            "/social-downloader/",
            "/storyboarder/",
            "/brand-dna/",
            "/funnels",
            "/voiceover-tightener",
        )
    ):
        return True
    accept = (request.headers.get("Accept") or "").lower()
    return "application/json" in accept


def _ensure_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app = Flask(__name__)
is_dev_mode = _is_dev_mode()
secret_key = (os.getenv("SECRET_KEY") or "").strip()
if not secret_key:
    if is_dev_mode:
        secret_key = "dev-secret-key-change-me"
    else:
        raise RuntimeError("SECRET_KEY is required in non-development mode.")
if secret_key == "dev-secret-key-change-me" and not is_dev_mode:
    raise RuntimeError("SECRET_KEY must not use the default development value.")

app.secret_key = secret_key
app.config["SQLALCHEMY_DATABASE_URI"] = get_database_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = (
    max(1, int(os.getenv("MAX_CONTENT_LENGTH_MB", "32"))) * 1024 * 1024
)
app.config["SCRIPT_TRANSCRIBE_MAX_UPLOAD_BYTES"] = (
    max(25, int(os.getenv("SCRIPT_TRANSCRIBE_MAX_UPLOAD_MB", "500"))) * 1024 * 1024
)
app.config["VOICEOVER_TIGHTENER_MAX_UPLOAD_BYTES"] = (
    max(1, int(os.getenv("VOICEOVER_TIGHTENER_MAX_UPLOAD_MB", "100")))
    * 1024
    * 1024
)
app.config["VOICEOVER_TIGHTENER_MAX_DURATION_SECONDS"] = max(
    60, int(os.getenv("VOICEOVER_TIGHTENER_MAX_DURATION_SECONDS", "3600"))
)
app.config["VOICEOVER_TIGHTENER_FFMPEG_TIMEOUT_SECONDS"] = max(
    30, int(os.getenv("VOICEOVER_TIGHTENER_FFMPEG_TIMEOUT_SECONDS", "1800"))
)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = _is_truthy(
    os.getenv("SESSION_COOKIE_SECURE"),
    default=False,
)
if not is_dev_mode and not app.config["SESSION_COOKIE_SECURE"]:
    raise RuntimeError(
        "SESSION_COOKIE_SECURE must be set to 'true' in production. "
        "Set FLASK_ENV=development to bypass this check in local environments."
    )
app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    hours=max(1, int(os.getenv("SESSION_LIFETIME_HOURS", "12")))
)
app.config["MEDIA_ROOT"] = os.getenv(
    "MEDIA_ROOT",
    os.path.join(app.instance_path, "media"),
)
app.config["ADMIN_USER_IDS"] = os.getenv("ADMIN_USER_IDS", "1")

_rate_limit_hits: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = Lock()

# Redis client for distributed rate limiting (optional — falls back to in-memory)
_redis_client: Optional[object] = None

def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if not redis_url:
        return None
    try:
        import redis as redis_lib  # type: ignore
        client = redis_lib.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        _redis_client = client
    except Exception:
        _redis_client = None
    return _redis_client


def _check_rate_limit_redis(key: str, limit_count: int, window_seconds: int) -> tuple[bool, int]:
    """Sliding-window rate check using Redis sorted sets.
    Returns (is_limited, retry_after_seconds).
    """
    r = _get_redis()
    if r is None:
        return False, 0
    try:
        now = time.time()
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, "-inf", now - window_seconds)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, window_seconds + 1)
        results = pipe.execute()
        count_before_add = results[1]
        if count_before_add >= limit_count:
            # Already over limit — undo the add we just did
            r.zrem(key, str(now))
            # Calculate retry_after from oldest entry
            oldest = r.zrange(key, 0, 0, withscores=True)
            if oldest:
                retry_after = max(1, int(window_seconds - (now - oldest[0][1])))
            else:
                retry_after = window_seconds
            return True, retry_after
        return False, 0
    except Exception:
        return False, 0


def _check_rate_limit_memory(key: str, limit_count: int, window_seconds: int) -> tuple[bool, int]:
    """Sliding-window rate check using in-process memory."""
    now = time.time()
    with _rate_limit_lock:
        hits = _rate_limit_hits[key]
        cutoff = now - window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= limit_count:
            retry_after = max(1, int(window_seconds - (now - hits[0])))
            return True, retry_after
        hits.append(now)
    return False, 0

db.init_app(app)
migrate.init_app(app, db)

# Register blueprints
app.register_blueprint(admin_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(scraper_bp)
app.register_blueprint(ai_image_bp)
app.register_blueprint(products_bp)
app.register_blueprint(product_images_bp)
app.register_blueprint(prompts_bp)
app.register_blueprint(avatars_bp)
app.register_blueprint(ai_creatives_bp)
app.register_blueprint(script_optimizer_bp)
app.register_blueprint(competitors_bp)
app.register_blueprint(billing_bp)
app.register_blueprint(media_bp)
app.register_blueprint(brand_dna_bp)
app.register_blueprint(landing_builder_bp)
app.register_blueprint(metrics_bp)
app.register_blueprint(notes_bp)
app.register_blueprint(social_downloader_bp)
app.register_blueprint(storyboarder_bp)
app.register_blueprint(funnels_bp)
app.register_blueprint(voiceover_tightener_bp)


@app.before_request
def apply_request_guardrails():
    if request.endpoint == "static":
        return None

    # Flask 3.1 supports per-request limits. Larger script-optimizer uploads are
    # streamed to worker storage, while other endpoints keep the tighter default.
    if request.path == "/script-optimizer/transcribe" and request.method == "POST":
        request.max_content_length = (
            app.config["SCRIPT_TRANSCRIBE_MAX_UPLOAD_BYTES"] + 2 * 1024 * 1024
        )
    elif request.path == "/voiceover-tightener/items" and request.method == "POST":
        request.max_content_length = (
            app.config["VOICEOVER_TIGHTENER_MAX_UPLOAD_BYTES"] + 2 * 1024 * 1024
        )

    session.permanent = True
    _ensure_csrf_token()

    rate_limit = _match_rate_limit(request.path, request.method)
    if rate_limit:
        limit_count, window_seconds = rate_limit
        key = f"rl::{_request_ip()}::{request.path}"
        r = _get_redis()
        if r is not None:
            is_limited, retry_after = _check_rate_limit_redis(key, limit_count, window_seconds)
        else:
            is_limited, retry_after = _check_rate_limit_memory(key, limit_count, window_seconds)
        if is_limited:
            if _prefers_json_response():
                response = jsonify(
                    {
                        "error": "Too many requests. Please slow down and try again.",
                        "retry_after_seconds": retry_after,
                    }
                )
            else:
                response = "Too many requests. Please try again shortly."
            return response, 429

    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    # Stripe webhook uses its own signature verification — skip CSRF
    if request.path == "/billing/stripe/webhook":
        return None

    sent_token = (
        request.headers.get("X-CSRF-Token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-XSRF-TOKEN")
        or request.form.get("_csrf_token")
        or ""
    ).strip()
    session_token = (session.get("_csrf_token") or "").strip()
    if not sent_token or not session_token or not hmac.compare_digest(sent_token, session_token):
        if _prefers_json_response():
            return jsonify({"error": "Invalid CSRF token."}), 400
        return "Invalid CSRF token.", 400
    return None


# The funnel editor previews a page template inside a srcdoc iframe, which
# inherits this document's CSP, so a nonce-only script-src would block the
# template's own bundled script. See security.build_content_security_policy.
_INLINE_SCRIPT_ENDPOINTS = {"funnels.page_editor"}


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    if app.config.get("SESSION_COOKIE_SECURE"):
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )

    # Routes that serve user-authored documents (published funnel pages, media
    # proxies) set their own policy; never overwrite it.
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = build_content_security_policy(
            csp_nonce(),
            allow_inline_scripts=request.endpoint in _INLINE_SCRIPT_ENDPOINTS,
        )
    return response


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_error):
    if request.path == "/script-optimizer/transcribe":
        max_bytes = app.config.get("SCRIPT_TRANSCRIBE_MAX_UPLOAD_BYTES", 0)
    elif request.path == "/voiceover-tightener/items":
        max_bytes = app.config.get("VOICEOVER_TIGHTENER_MAX_UPLOAD_BYTES", 0)
    else:
        max_bytes = request.max_content_length or 0
    max_mb = int(max_bytes / (1024 * 1024))
    if _prefers_json_response():
        return jsonify({"error": f"Request too large. Limit is {max_mb} MB."}), 413
    return f"Request too large. Limit is {max_mb} MB.", 413


@app.route("/health")
def health_check():
    try:
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "ok", "db": "ok"}), 200
    except Exception:
        return jsonify({"status": "error", "db": "unavailable"}), 503


@landing_bp.route("/")
def landing_page():
    return render_template("landing.html")


@landing_bp.route("/privacy")
def privacy_page():
    return render_template("privacy.html")


@landing_bp.route("/terms")
def terms_page():
    return render_template("terms.html")


@landing_bp.route("/cookies")
def cookies_page():
    return render_template("cookies.html")


app.register_blueprint(landing_bp)


@app.route("/app")
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


@app.context_processor
def inject_csrf_context():
    return {"csrf_token": _ensure_csrf_token(), "csp_nonce": csp_nonce()}


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


@app.cli.command("encrypt-api-keys")
def encrypt_api_keys():
    """Migrate plaintext user API keys to encrypted Fernet tokens."""
    click.echo("Starting API key encryption migration...")
    from db import ApiKey

    migrated = 0
    skipped = 0

    api_keys = ApiKey.query.all()
    for key_obj in api_keys:
        if not key_obj.api_key:
            skipped += 1
            continue

        if key_obj.api_key.startswith("gAAAAA"):
            skipped += 1
            continue

        try:
            key_obj.api_key = encrypt_api_key(key_obj.api_key)
            migrated += 1
        except Exception as e:
            click.echo(f"Failed to encrypt key ID {key_obj.id}: {e}")

    try:
        db.session.commit()
        click.echo(f"Migration complete. Encrypted {migrated} keys. Skipped {skipped} keys.")
    except Exception as e:
        db.session.rollback()
        click.echo(f"Failed to commit changes: {e}")


@app.cli.command("worker")
@click.option("--queue", "queue_name", default="default", show_default=True)
@click.option("--concurrency", default=2, show_default=True, type=int)
@click.option("--poll-interval", default=1.5, show_default=True, type=float)
@click.option("--stale-timeout", default=3600, show_default=True, type=int)
def run_worker(queue_name, concurrency, poll_interval, stale_timeout):
    """Run the background worker pool for queued jobs."""
    queue_name = (queue_name or "default").strip() or "default"
    concurrency = max(1, int(concurrency))
    poll_interval = max(0.2, float(poll_interval))
    stale_timeout = max(60, int(stale_timeout))

    click.echo(
        "Starting worker pool "
        f"(queue={queue_name}, concurrency={concurrency}, poll_interval={poll_interval:.1f}s, stale_timeout={stale_timeout}s)"
    )
    run_worker_pool(
        app,
        queue_name=queue_name,
        concurrency=concurrency,
        poll_interval=poll_interval,
        stale_timeout_seconds=stale_timeout,
    )


if __name__ == "__main__":
    debug = _is_truthy(os.getenv("FLASK_DEBUG"), default=False)
    app.run(debug=debug, host="0.0.0.0", port=5000)
