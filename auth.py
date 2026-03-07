from datetime import timedelta

from functools import wraps
from flask import Blueprint, request, render_template, redirect, url_for, session, flash
from sqlalchemy import or_
from werkzeug.security import generate_password_hash, check_password_hash
from billing_service import utc_now
from billing_config import BILLING_CYCLE_DAYS
from db import User, db

auth_bp = Blueprint("auth", __name__)


def login_required(f):
    """Decorator to protect routes that require authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        # Validation
        if not username or not email or not password:
            flash("All fields are required.", "error")
            return render_template("register.html")

        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("register.html")

        try:
            # Check if user already exists
            existing_user = User.query.filter(
                or_(User.username == username, User.email == email)
            ).first()
            if existing_user:
                flash("Username or email already exists.", "error")
                return render_template("register.html")

            # Create user
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

            # Auto-login after registration
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

        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            session["username"] = user.username
            flash("Welcome back!", "success")
            return redirect(url_for("main.dashboard"))

        flash("Invalid username or password.", "error")
        return render_template("login.html")

    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
