import io
import json

import pytest

from db import Competitor, CompetitorAd, Product, WorkerJob, db
from tests.conftest import csrf_token
from worker_queue import JOB_STATUS_FAILED, JOB_STATUS_QUEUED
from worker_runtime import (
    _parse_competitor_analysis,
    _run_competitor_ad_analyze,
    _run_competitor_ad_transcribe,
)
from worker_tasks import (
    JOB_TYPE_COMPETITOR_AD_ANALYZE,
    JOB_TYPE_COMPETITOR_AD_TRANSCRIBE,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["username"] = "testuser"


def _json_post(client, path, payload=None):
    token = csrf_token(client)
    return client.post(path, json=payload or {}, headers={"X-CSRF-Token": token})


def _json_patch(client, path, payload):
    token = csrf_token(client)
    return client.patch(path, json=payload, headers={"X-CSRF-Token": token})


def _json_delete(client, path):
    token = csrf_token(client)
    return client.delete(path, headers={"X-CSRF-Token": token})


@pytest.fixture()
def media_root(app, tmp_path):
    original = app.config.get("MEDIA_ROOT")
    app.config["MEDIA_ROOT"] = str(tmp_path)
    yield tmp_path
    app.config["MEDIA_ROOT"] = original


@pytest.fixture()
def competitor_id(app, test_user):
    with app.app_context():
        competitor = Competitor(user_id=test_user, name="GlowSkin Co.")
        db.session.add(competitor)
        db.session.commit()
        return competitor.id


def _create_product(app, user_id, name="Serum"):
    with app.app_context():
        product = Product(user_id=user_id, name=name, context="A serum.")
        db.session.add(product)
        db.session.commit()
        return product.id


def _upload_ad(client, competitor_id, filename="ad.mp4", mime="video/mp4"):
    token = csrf_token(client)
    return client.post(
        f"/competitors/{competitor_id}/ads",
        data={"video": (io.BytesIO(b"fake video bytes"), filename, mime)},
        headers={"X-CSRF-Token": token},
        content_type="multipart/form-data",
    )


def test_page_requires_authentication(client):
    response = client.get("/competitors")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_create_competitor_with_product(app, client, test_user):
    _login(client, test_user)
    product_id = _create_product(app, test_user)

    response = _json_post(
        client, "/competitors", {"name": "GlowSkin Co.", "product_id": product_id}
    )

    assert response.status_code == 201
    payload = response.get_json()["competitor"]
    assert payload["name"] == "GlowSkin Co."
    assert payload["product"]["id"] == product_id
    assert payload["ad_count"] == 0


def test_create_competitor_requires_name(client, test_user):
    _login(client, test_user)

    response = _json_post(client, "/competitors", {"name": "   "})

    assert response.status_code == 400


def _create_other_user(app):
    with app.app_context():
        from datetime import timedelta

        from werkzeug.security import generate_password_hash

        from billing_service import utc_now
        from db import User

        other = User(
            username="other",
            email="other@example.com",
            password_hash=generate_password_hash("Password1!"),
            plan_tier="free",
            credits=30,
            extra_credits=0,
            next_credit_reset_at=utc_now() + timedelta(days=30),
        )
        db.session.add(other)
        db.session.commit()
        return other.id


def test_create_competitor_rejects_foreign_product(app, client, test_user):
    _login(client, test_user)
    other_id = _create_other_user(app)
    product_id = _create_product(app, other_id)

    response = _json_post(
        client, "/competitors", {"name": "Sneaky", "product_id": product_id}
    )

    assert response.status_code == 400


def test_detail_page_scoped_to_owner(app, client, test_user, competitor_id):
    _login(client, test_user)
    assert client.get(f"/competitors/{competitor_id}").status_code == 200
    assert client.get("/competitors/999999").status_code == 404


def test_update_competitor_product_assignment(app, client, test_user, competitor_id):
    _login(client, test_user)
    product_id = _create_product(app, test_user)

    response = _json_patch(
        client, f"/competitors/{competitor_id}", {"product_id": product_id}
    )
    cleared = _json_patch(
        client, f"/competitors/{competitor_id}", {"product_id": None}
    )

    assert response.status_code == 200
    assert response.get_json()["competitor"]["product"]["id"] == product_id
    assert cleared.status_code == 200
    assert cleared.get_json()["competitor"]["product"] is None


def test_upload_ad_enqueues_transcription(app, client, test_user, competitor_id, media_root):
    _login(client, test_user)

    response = _upload_ad(client, competitor_id)

    assert response.status_code == 202
    payload = response.get_json()["ad"]
    assert payload["transcript_status"] == "queued"
    assert payload["video_url"].startswith("/media/competitor-ads/")

    with app.app_context():
        ad = db.session.get(CompetitorAd, payload["id"])
        job = WorkerJob.query.one()
        assert ad.storage_path
        assert (media_root / ad.storage_path).is_file()
        assert job.job_type == JOB_TYPE_COMPETITOR_AD_TRANSCRIBE
        assert job.status == JOB_STATUS_QUEUED
        assert ad.transcribe_job_id == job.id


def test_upload_rejects_non_video(client, test_user, competitor_id, media_root):
    _login(client, test_user)

    response = _upload_ad(client, competitor_id, filename="doc.pdf", mime="application/pdf")

    assert response.status_code == 400


def test_analyze_requires_transcript(app, client, test_user, competitor_id, media_root):
    _login(client, test_user)
    upload = _upload_ad(client, competitor_id)
    ad_id = upload.get_json()["ad"]["id"]

    blocked = _json_post(client, f"/competitors/ads/{ad_id}/analyze")

    with app.app_context():
        ad = db.session.get(CompetitorAd, ad_id)
        ad.transcript = "Buy this amazing serum now!"
        ad.transcript_status = "completed"
        db.session.commit()

    allowed = _json_post(client, f"/competitors/ads/{ad_id}/analyze")

    assert blocked.status_code == 400
    assert allowed.status_code == 202
    assert allowed.get_json()["ad"]["analysis_status"] == "queued"
    with app.app_context():
        jobs = WorkerJob.query.filter_by(
            job_type=JOB_TYPE_COMPETITOR_AD_ANALYZE
        ).all()
        assert len(jobs) == 1


def test_delete_ad_cancels_queued_job_and_removes_file(
    app, client, test_user, competitor_id, media_root
):
    _login(client, test_user)
    upload = _upload_ad(client, competitor_id)
    ad_payload = upload.get_json()["ad"]

    response = _json_delete(client, f"/competitors/ads/{ad_payload['id']}")

    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(CompetitorAd, ad_payload["id"]) is None
        job = WorkerJob.query.one()
        assert job.status == JOB_STATUS_FAILED
    assert not list(media_root.rglob("*.mp4"))


def test_delete_competitor_cascades_ads(app, client, test_user, competitor_id, media_root):
    _login(client, test_user)
    _upload_ad(client, competitor_id)

    response = _json_delete(client, f"/competitors/{competitor_id}")

    assert response.status_code == 200
    with app.app_context():
        assert Competitor.query.count() == 0
        assert CompetitorAd.query.count() == 0


def test_worker_transcribe_updates_ad(app, client, test_user, competitor_id, media_root, monkeypatch):
    _login(client, test_user)
    upload = _upload_ad(client, competitor_id)
    ad_id = upload.get_json()["ad"]["id"]

    monkeypatch.setattr(
        "worker_runtime.openai_transcribe_file_with_meta",
        lambda **kwargs: ("Hello from the ad.", {"model": "test"}),
    )

    with app.app_context():
        result = _run_competitor_ad_transcribe(1, test_user, {"ad_id": ad_id})
        ad = db.session.get(CompetitorAd, ad_id)
        assert result["text"] == "Hello from the ad."
        assert ad.transcript == "Hello from the ad."
        assert ad.transcript_status == "completed"


def test_worker_transcribe_marks_failure(app, client, test_user, competitor_id, media_root, monkeypatch):
    _login(client, test_user)
    upload = _upload_ad(client, competitor_id)
    ad_id = upload.get_json()["ad"]["id"]

    def _boom(**kwargs):
        raise RuntimeError("OpenAI exploded")

    monkeypatch.setattr("worker_runtime.openai_transcribe_file_with_meta", _boom)

    with app.app_context():
        with pytest.raises(RuntimeError):
            _run_competitor_ad_transcribe(1, test_user, {"ad_id": ad_id})
        ad = db.session.get(CompetitorAd, ad_id)
        assert ad.transcript_status == "failed"
        assert "OpenAI exploded" in ad.transcript_error


def test_worker_analyze_stores_parsed_analysis(app, client, test_user, competitor_id, media_root, monkeypatch):
    _login(client, test_user)
    upload = _upload_ad(client, competitor_id)
    ad_id = upload.get_json()["ad"]["id"]

    with app.app_context():
        ad = db.session.get(CompetitorAd, ad_id)
        ad.transcript = "Buy this amazing serum now!"
        ad.transcript_status = "completed"
        db.session.commit()

    fake_response = json.dumps(
        {
            "rating": 8,
            "overview": "Strong hook with a clear offer.",
            "strengths": ["Great hook", "Clear CTA"],
            "weaknesses": ["No social proof"],
        }
    )
    monkeypatch.setattr(
        "worker_runtime.openai_chat_with_meta",
        lambda **kwargs: (fake_response, {"model": "test"}),
    )

    with app.app_context():
        result = _run_competitor_ad_analyze(2, test_user, {"ad_id": ad_id})
        ad = db.session.get(CompetitorAd, ad_id)
        analysis = json.loads(ad.analysis_json)
        assert result["analysis"]["rating"] == 8
        assert ad.analysis_status == "completed"
        assert analysis["overview"] == "Strong hook with a clear offer."
        assert analysis["strengths"] == ["Great hook", "Clear CTA"]


def test_parse_competitor_analysis_handles_fences_and_clamps_rating():
    raw = '```json\n{"rating": 27, "overview": "ok", "strengths": [], "weaknesses": []}\n```'

    parsed = _parse_competitor_analysis(raw)

    assert parsed["rating"] == 10
    assert parsed["overview"] == "ok"


def test_media_route_scoped_to_owner(app, client, test_user, competitor_id, media_root):
    _login(client, test_user)
    upload = _upload_ad(client, competitor_id)
    ad_id = upload.get_json()["ad"]["id"]

    ok = client.get(f"/media/competitor-ads/{ad_id}")
    assert ok.status_code == 200

    other_id = _create_other_user(app)
    with client.session_transaction() as sess:
        sess["user_id"] = other_id
    denied = client.get(f"/media/competitor-ads/{ad_id}")
    assert denied.status_code == 404
