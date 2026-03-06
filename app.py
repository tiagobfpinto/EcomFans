import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from flask import Blueprint, Flask, redirect, render_template, session, url_for

from ai_creatives import ai_creatives_bp
from ai_image import ai_image_bp
from auth import auth_bp, login_required
from avatars import avatars_bp
from db import db, migrate
from products import products_bp
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

db.init_app(app)
migrate.init_app(app, db)

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(scraper_bp)
app.register_blueprint(ai_image_bp)
app.register_blueprint(products_bp)
app.register_blueprint(avatars_bp)
app.register_blueprint(ai_creatives_bp)


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

if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "False").lower() in ("true", "1", "yes")
    app.run(debug=debug, host="0.0.0.0", port=5000)
