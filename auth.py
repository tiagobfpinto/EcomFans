import logging
import os
import secrets
from datetime import timedelta
from functools import wraps

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from billing_config import BILLING_CYCLE_DAYS
from billing_service import utc_now
from db import User, db

auth_bp = Blueprint("auth", __name__)

_RESET_TOKEN_HOURS = 1
_RESET_COOLDOWN_MINUTES = 5
_MAX_FAILED_LOGINS = 10
_LOCKOUT_MINUTES = 15

logger = logging.getLogger(__name__)


def _aware(dt):
    """
    Ensure *dt* is timezone-aware UTC.

    PostgreSQL returns aware datetimes; SQLite (used in tests) returns
    naive ones. This normalisation makes all comparisons consistent.
    """
    from datetime import timezone
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        try:
            current_user = db.session.get(User, session["user_id"])
        except SQLAlchemyError:
            db.session.rollback()
            session.clear()
            message = "Database is not ready. Run migrations and try again."
            if request.is_json or "application/json" in (request.headers.get("Accept") or "").lower():
                return jsonify({"error": message}), 503
            flash(message, "error")
            return redirect(url_for("auth.login"))
        if current_user is None:
            session.clear()
            if request.is_json or "application/json" in (request.headers.get("Accept") or "").lower():
                return jsonify({"error": "Session expired. Please log in again."}), 401
            flash("Your session expired. Please sign in again.", "error")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function


# ── helpers ─────────────────────────────────────────────────────────────────

def _password_valid(password: str) -> tuple[bool, str]:
    """Return (is_valid, error_message). Empty error_message means valid."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    has_digit = any(c.isdigit() for c in password)
    has_special = any(not c.isalnum() and not c.isspace() for c in password)
    if not (has_digit or has_special):
        return False, "Password must contain at least one number or special character."
    return True, ""


def _make_reset_token(user: User) -> str:
    """Generate a random token, store it on the user object (caller must commit)."""
    token = secrets.token_urlsafe(32)
    user.reset_token = token
    user.reset_token_expires_at = utc_now() + timedelta(hours=_RESET_TOKEN_HOURS)
    return token


def _verify_reset_token(token: str) -> User | None:
    """Verify token and return User if valid, else None."""
    if not token or len(token) > 128:
        return None
    try:
        user = User.query.filter_by(reset_token=token).first()
        if not user:
            return None
        if not user.reset_token_expires_at:
            return None
        if utc_now() > _aware(user.reset_token_expires_at):
            return None
        return user
    except Exception:
        return None


def _send_password_reset_email(user: User, reset_url: str) -> bool:
    """
    Send a password reset email.
    Returns True if sent, False if email is not configured.

    Configure SMTP via environment variables:
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    smtp_host = (os.getenv("SMTP_HOST") or "").strip()
    if not smtp_host:
        return False

    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = (os.getenv("SMTP_USER") or "").strip()
    smtp_password = (os.getenv("SMTP_PASSWORD") or "").strip()
    smtp_from = (os.getenv("SMTP_FROM") or smtp_user or "no-reply@ecomfans.com").strip()
    app_name = (os.getenv("APP_NAME") or "EcomFans").strip()

    subject = f"{app_name} — Password Reset"
    body_text = (
        f"Hi {user.username},\n\n"
        f"Click the link below to reset your password. "
        f"This link expires in {_RESET_TOKEN_HOURS} hour(s).\n\n"
        f"{reset_url}\n\n"
        f"If you did not request a password reset, please ignore this email.\n\n"
        f"— {app_name} team"
    )
    body_html = (
        f"<p>Hi <strong>{user.username}</strong>,</p>"
        f"<p>Click the button below to reset your password. "
        f"This link expires in {_RESET_TOKEN_HOURS} hour(s).</p>"
        f"<p><a href='{reset_url}' style='display:inline-block;padding:10px 20px;"
        f"background:#6366f1;color:#fff;border-radius:6px;text-decoration:none'>"
        f"Reset Password</a></p>"
        f"<p>If you did not request a password reset, ignore this email.</p>"
        f"<p>— {app_name} team</p>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = user.email
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            if smtp_port != 465:
                server.starttls()
                server.ehlo()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [user.email], msg.as_string())
        return True
    except Exception as exc:
        logger.error("SMTP send failed for user %s: %s", user.id, exc, exc_info=True)
        return False


# ── routes ───────────────────────────────────────────────────────────────────

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not username or not email or not password:
            flash("All fields are required.", "error")
            return render_template("register.html")

        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("register.html")

        valid, pw_error = _password_valid(password)
        if not valid:
            flash(pw_error, "error")
            return render_template("register.html")

        try:
            existing_user = User.query.filter(
                or_(User.username == username, User.email == email)
            ).first()
            if existing_user:
                flash("Username or email already exists.", "error")
                return render_template("register.html")

            password_hash = generate_password_hash(password)
            user = User(
                username=username,
                email=email,
                password_hash=password_hash,
                plan_tier="free",
                credits=30,
                extra_credits=0,
                next_credit_reset_at=utc_now() + timedelta(days=BILLING_CYCLE_DAYS),
            )
            db.session.add(user)
            db.session.commit()

            logger.info("New user registered: id=%s", user.id)
            session.clear()
            session["user_id"] = user.id
            session["username"] = username
            flash("Account created successfully!", "success")
            return redirect(url_for("main.dashboard"))

        except Exception:
            db.session.rollback()
            flash("An error occurred. Please try again.", "error")
            return render_template("register.html")

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("All fields are required.", "error")
            return render_template("login.html")

        user = User.query.filter_by(username=username).first()

        if user:
            # Check account lockout
            if user.locked_until and utc_now() < _aware(user.locked_until):
                remaining = max(1, int((_aware(user.locked_until) - utc_now()).total_seconds() / 60) + 1)
                flash(
                    f"Account is temporarily locked due to too many failed attempts. "
                    f"Try again in {remaining} minute(s).",
                    "error",
                )
                logger.warning("Login blocked (locked): user_id=%s", user.id)
                return render_template("login.html")

            if check_password_hash(user.password_hash, password):
                user.failed_login_attempts = 0
                user.locked_until = None
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()

                session.clear()
                session["user_id"] = user.id
                session["username"] = user.username
                logger.info("Login success: user_id=%s", user.id)
                flash("Welcome back!", "success")
                return redirect(url_for("main.dashboard"))

            # Failed attempt
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= _MAX_FAILED_LOGINS:
                user.locked_until = utc_now() + timedelta(minutes=_LOCKOUT_MINUTES)
                logger.warning(
                    "Account locked after %s failed attempts: user_id=%s",
                    user.failed_login_attempts, user.id,
                )
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

        logger.warning("Failed login attempt for username=%s", username)
        flash("Invalid username or password.", "error")
        return render_template("login.html")

    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logger.info("Logout: user_id=%s", session.get("user_id"))
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if "user_id" in session:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Please enter your email address.", "error")
            return render_template("forgot_password.html")

        user = User.query.filter(
            db.func.lower(User.email) == email
        ).first()

        # Always show the same message to prevent user enumeration
        flash(
            "If an account with that email exists, a password reset link has been sent.",
            "success",
        )

        if user:
            # Enforce cooldown: max 1 reset request per 5 minutes per account
            if user.last_password_reset_request_at:
                elapsed = utc_now() - _aware(user.last_password_reset_request_at)
                if elapsed < timedelta(minutes=_RESET_COOLDOWN_MINUTES):
                    logger.info("Password reset cooldown active for user_id=%s", user.id)
                    return redirect(url_for("auth.login"))

            user.last_password_reset_request_at = utc_now()
            token = _make_reset_token(user)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                return redirect(url_for("auth.login"))

            reset_url = url_for("auth.reset_password", token=token, _external=True)
            sent = _send_password_reset_email(user, reset_url)
            if not sent:
                # Email not configured — log to stderr for admin visibility
                import sys
                print(
                    f"[auth] Password reset requested for user {user.id} but SMTP is not configured. "
                    f"Reset URL: {reset_url}",
                    file=sys.stderr,
                )

        return redirect(url_for("auth.login"))

    return render_template("forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    if "user_id" in session:
        return redirect(url_for("main.dashboard"))

    user = _verify_reset_token(token)
    if not user:
        flash("This password reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not password:
            flash("Password is required.", "error")
            return render_template("reset_password.html", token=token)

        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html", token=token)

        valid, pw_error = _password_valid(password)
        if not valid:
            flash(pw_error, "error")
            return render_template("reset_password.html", token=token)

        # Re-verify token just before writing (in case of concurrent requests)
        fresh_user = _verify_reset_token(token)
        if not fresh_user or fresh_user.id != user.id:
            flash("This password reset link is invalid or has expired.", "error")
            return redirect(url_for("auth.forgot_password"))

        fresh_user.password_hash = generate_password_hash(password)
        fresh_user.reset_token = None
        fresh_user.reset_token_expires_at = None
        fresh_user.failed_login_attempts = 0
        fresh_user.locked_until = None
        try:
            db.session.commit()
            logger.info("Password reset completed for user_id=%s", fresh_user.id)
            flash("Your password has been reset. Please log in.", "success")
            return redirect(url_for("auth.login"))
        except Exception:
            db.session.rollback()
            flash("An error occurred. Please try again.", "error")
            return render_template("reset_password.html", token=token)

    return render_template("reset_password.html", token=token)


@auth_bp.route("/account/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    user = db.session.get(User, session["user_id"])
    if not user:
        session.clear()
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        if not check_password_hash(user.password_hash, current_pw):
            flash("Current password is incorrect.", "error")
            return render_template("change_password.html")

        if new_pw != confirm_pw:
            flash("New passwords do not match.", "error")
            return render_template("change_password.html")

        valid, pw_error = _password_valid(new_pw)
        if not valid:
            flash(pw_error, "error")
            return render_template("change_password.html")

        user.password_hash = generate_password_hash(new_pw)
        try:
            db.session.commit()
            logger.info("Password changed for user_id=%s", user.id)
            flash("Password changed successfully.", "success")
            return redirect(url_for("main.dashboard"))
        except Exception:
            db.session.rollback()
            flash("An error occurred. Please try again.", "error")

    return render_template("change_password.html")
