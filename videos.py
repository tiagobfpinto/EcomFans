import os

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy.sql import func

from auth import login_required
from db import Mention, ProcessingJob, Video, db
from services.analyzer import analyze_transcript
from services.dedup import get_or_create_product
from services.transcript import extract_youtube_id, fetch_transcript
from services.trend_scoring import recompute_product_stats

videos_bp = Blueprint("videos", __name__, url_prefix="/videos")


@videos_bp.route("/")
@login_required
def input_page():
    return render_template("videos/input.html")


@videos_bp.route("/submit", methods=["POST"])
@login_required
def submit():
    raw_urls = request.form.get("urls", "").strip()
    if not raw_urls:
        flash("Please paste at least one YouTube URL.", "error")
        return redirect(url_for("videos.input_page"))

    lines = [line.strip() for line in raw_urls.splitlines() if line.strip()]
    valid_videos = []
    invalid_count = 0

    for url in lines:
        yt_id = extract_youtube_id(url)
        if not yt_id:
            invalid_count += 1
            continue
        valid_videos.append((url, yt_id))

    if not valid_videos:
        flash("No valid YouTube URLs found. Please check your input.", "error")
        return redirect(url_for("videos.input_page"))

    user_id = session["user_id"]

    job = ProcessingJob(
        user_id=user_id,
        status="processing",
        total_urls=len(valid_videos),
    )
    db.session.add(job)
    db.session.flush()

    skipped = 0
    for url, yt_id in valid_videos:
        existing = Video.query.filter_by(
            youtube_id=yt_id, user_id=user_id
        ).first()
        if existing:
            skipped += 1
            continue

        video = Video(
            youtube_id=yt_id,
            url=url,
            thumbnail_url=f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg",
            processing_status="pending",
            user_id=user_id,
            job_id=job.id,
        )
        db.session.add(video)

    db.session.commit()

    if skipped:
        flash(f"{skipped} duplicate video(s) skipped.", "error")
    if invalid_count:
        flash(f"{invalid_count} invalid URL(s) skipped.", "error")

    return redirect(url_for("videos.job_page", job_id=job.id))


@videos_bp.route("/job/<int:job_id>")
@login_required
def job_page(job_id):
    job = ProcessingJob.query.get_or_404(job_id)
    if job.user_id != session["user_id"]:
        return redirect(url_for("videos.input_page"))

    videos = Video.query.filter_by(job_id=job.id).all()
    return render_template("videos/processing.html", job=job, videos=videos)


@videos_bp.route("/job/<int:job_id>/status")
@login_required
def job_status(job_id):
    job = ProcessingJob.query.get_or_404(job_id)
    if job.user_id != session["user_id"]:
        return jsonify({"error": "unauthorized"}), 403

    videos = Video.query.filter_by(job_id=job.id).all()
    return jsonify({
        "status": job.status,
        "total": job.total_urls,
        "processed": job.processed_urls,
        "failed": job.failed_urls,
        "videos": [
            {
                "id": v.id,
                "youtube_id": v.youtube_id,
                "title": v.title or v.url,
                "status": v.processing_status,
                "error": v.error_message,
                "thumbnail_url": v.thumbnail_url,
            }
            for v in videos
        ],
    })


@videos_bp.route("/job/<int:job_id>/process-next", methods=["POST"])
@login_required
def process_next(job_id):
    job = ProcessingJob.query.get_or_404(job_id)
    if job.user_id != session["user_id"]:
        return jsonify({"error": "unauthorized"}), 403

    # Find next pending video in this job
    video = Video.query.filter_by(
        job_id=job.id, processing_status="pending"
    ).first()

    if not video:
        # All done
        job.status = "completed"
        job.completed_at = func.now()
        db.session.commit()
        return jsonify({"done": True, "job_status": "completed"})

    api_key = os.getenv("ANTHROPIC_API_KEY")

    # Step 1: Fetch transcript
    video.processing_status = "fetching_transcript"
    db.session.commit()

    result = fetch_transcript(video.youtube_id)

    if not result["success"]:
        video.processing_status = "failed"
        video.error_message = result["error"]
        job.failed_urls += 1
        job.processed_urls += 1
        db.session.commit()
        return jsonify({
            "done": False,
            "video_id": video.id,
            "status": "failed",
            "error": result["error"],
        })

    video.transcript_text = result["text"]

    # Step 2: AI analysis
    video.processing_status = "analyzing"
    db.session.commit()

    analysis = analyze_transcript(result["text"], api_key)

    if not analysis["success"]:
        video.processing_status = "failed"
        video.error_message = analysis["error"]
        job.failed_urls += 1
        job.processed_urls += 1
        db.session.commit()
        return jsonify({
            "done": False,
            "video_id": video.id,
            "status": "failed",
            "error": analysis["error"],
        })

    # Step 3: Save products and mentions
    data = analysis["data"]
    products_extracted = []

    for prod_data in data.get("products", []):
        product = get_or_create_product(prod_data)

        # Check for existing mention
        existing_mention = Mention.query.filter_by(
            product_id=product.id, video_id=video.id
        ).first()
        if not existing_mention:
            mention = Mention(
                product_id=product.id,
                video_id=video.id,
                sentiment=prod_data.get("sentiment"),
                context_quote=prod_data.get("context_quote"),
                mention_type=prod_data.get("mention_type"),
                is_sponsored=prod_data.get("is_sponsored", False),
            )
            db.session.add(mention)

        products_extracted.append(product.id)

    video.processing_status = "completed"
    video.title = video.title or f"Video {video.youtube_id}"
    video.analyzed_at = func.now()
    job.processed_urls += 1
    db.session.commit()

    # Recompute stats for affected products
    for pid in set(products_extracted):
        recompute_product_stats(pid)

    return jsonify({
        "done": False,
        "video_id": video.id,
        "status": "completed",
        "products_count": len(products_extracted),
    })


@videos_bp.route("/<int:video_id>")
@login_required
def detail(video_id):
    video = Video.query.get_or_404(video_id)
    if video.user_id != session["user_id"]:
        return redirect(url_for("videos.input_page"))

    mentions = (
        Mention.query.filter_by(video_id=video.id)
        .join(Mention.product)
        .all()
    )
    return render_template("videos/detail.html", video=video, mentions=mentions)
