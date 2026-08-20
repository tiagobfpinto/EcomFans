from pathlib import Path

import pytest

from db import SocialDownload, User, WorkerJob, db
from tests.conftest import csrf_token
from worker_queue import JOB_STATUS_FAILED, JOB_STATUS_QUEUED
from worker_runtime import _run_social_download
from worker_tasks import JOB_TYPE_SOCIAL_DOWNLOAD


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["username"] = "testuser"


def _make_pro(app, user_id):
    with app.app_context():
        user = db.session.get(User, user_id)
        user.plan_tier = "pro"
        db.session.commit()


def _json_post(client, path, payload):
    token = csrf_token(client)
    return client.post(
        path,
        json=payload,
        headers={"X-CSRF-Token": token},
    )


def _json_delete(client, path):
    token = csrf_token(client)
    return client.delete(path, headers={"X-CSRF-Token": token})


def test_page_requires_authentication(client):
    response = client.get("/social-downloader")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_free_user_sees_upgrade_and_cannot_enqueue(app, client, test_user):
    _login(client, test_user)

    page = client.get("/social-downloader")
    response = _json_post(
        client,
        "/social-downloader/items",
        {"urls": ["https://www.tiktok.com/@creator/video/123"]},
    )

    assert page.status_code == 200
    assert b"Unlock with Pro" in page.data
    assert response.status_code == 403
    assert response.get_json()["reason"] == "plan_required"


def test_pro_user_can_enqueue_supported_links(app, client, test_user):
    _make_pro(app, test_user)
    _login(client, test_user)

    response = _json_post(
        client,
        "/social-downloader/items",
        {
            "urls": [
                "https://www.tiktok.com/@creator/video/123",
                "instagram.com/reel/ABC123/",
                "https://www.facebook.com/share/r/18zUcmn7DS/",
            ]
        },
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert [item["platform"] for item in payload["items"]] == [
        "tiktok",
        "instagram",
        "facebook",
    ]

    with app.app_context():
        downloads = SocialDownload.query.order_by(SocialDownload.id).all()
        jobs = WorkerJob.query.order_by(WorkerJob.id).all()
        assert len(downloads) == 3
        assert len(jobs) == 3
        assert all(item.status == "queued" for item in downloads)
        assert all(job.job_type == JOB_TYPE_SOCIAL_DOWNLOAD for job in jobs)
        assert [item.worker_job_id for item in downloads] == [
            job.id for job in jobs
        ]


def test_unsupported_link_rejects_entire_batch(app, client, test_user):
    _make_pro(app, test_user)
    _login(client, test_user)

    response = _json_post(
        client,
        "/social-downloader/items",
        {
            "urls": [
                "https://www.tiktok.com/@creator/video/123",
                "https://www.youtube.com/shorts/ABC123",
            ]
        },
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["details"]
    assert "Facebook" in payload["details"][0]
    with app.app_context():
        assert SocialDownload.query.count() == 0
        assert WorkerJob.query.count() == 0


def test_list_is_isolated_by_user(app, client, test_user):
    _make_pro(app, test_user)
    with app.app_context():
        other = User(
            username="other",
            email="other@example.com",
            password_hash="unused",
            plan_tier="pro",
        )
        db.session.add(other)
        db.session.flush()
        db.session.add_all(
            [
                SocialDownload(
                    user_id=test_user,
                    source_url="https://www.tiktok.com/@one/video/1",
                    platform="tiktok",
                    status="success",
                ),
                SocialDownload(
                    user_id=other.id,
                    source_url="https://www.instagram.com/reel/OTHER/",
                    platform="instagram",
                    status="success",
                ),
            ]
        )
        db.session.commit()

    _login(client, test_user)
    response = client.get("/social-downloader/items")

    assert response.status_code == 200
    items = response.get_json()["items"]
    assert len(items) == 1
    assert items[0]["platform"] == "tiktok"


def test_failed_download_can_be_retried(app, client, test_user):
    _make_pro(app, test_user)
    with app.app_context():
        item = SocialDownload(
            user_id=test_user,
            source_url="https://www.tiktok.com/@creator/video/123",
            platform="tiktok",
            status="failed",
            error="Network error",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    _login(client, test_user)
    response = _json_post(
        client,
        f"/social-downloader/items/{item_id}/retry",
        {},
    )

    assert response.status_code == 202
    with app.app_context():
        item = db.session.get(SocialDownload, item_id)
        assert item.status == "queued"
        assert item.error is None
        assert item.worker_job.job_type == JOB_TYPE_SOCIAL_DOWNLOAD


def test_download_file_is_user_scoped(app, client, test_user, tmp_path):
    _make_pro(app, test_user)
    old_media_root = app.config.get("MEDIA_ROOT")
    app.config["MEDIA_ROOT"] = str(tmp_path)
    try:
        relative_path = Path("users") / str(test_user) / "social_downloads" / "1" / "video.mp4"
        absolute_path = tmp_path / relative_path
        absolute_path.parent.mkdir(parents=True)
        absolute_path.write_bytes(b"fake-mp4-data")

        with app.app_context():
            item = SocialDownload(
                user_id=test_user,
                source_url="https://www.instagram.com/reel/ABC/",
                platform="instagram",
                status="success",
                title="Campaign video",
                storage_path=relative_path.as_posix(),
                mime_type="video/mp4",
                file_size_bytes=13,
            )
            db.session.add(item)
            db.session.commit()
            item_id = item.id

        _login(client, test_user)
        response = client.get(
            f"/social-downloader/items/{item_id}/download"
        )

        assert response.status_code == 200
        assert response.data == b"fake-mp4-data"
        assert "Campaign_video.mp4" in response.headers["Content-Disposition"]
    finally:
        app.config["MEDIA_ROOT"] = old_media_root


def test_any_item_can_be_deleted_and_pending_job_is_cancelled(app, client, test_user):
    _make_pro(app, test_user)
    with app.app_context():
        item = SocialDownload(
            user_id=test_user,
            source_url="https://www.tiktok.com/@creator/video/123",
            platform="tiktok",
            status="queued",
        )
        db.session.add(item)
        db.session.flush()
        job = WorkerJob(
            user_id=test_user,
            queue_name="default",
            job_type=JOB_TYPE_SOCIAL_DOWNLOAD,
            status=JOB_STATUS_QUEUED,
            payload_json="{}",
            max_attempts=1,
        )
        db.session.add(job)
        db.session.flush()
        item.worker_job_id = job.id
        db.session.commit()
        item_id = item.id
        job_id = job.id

    _login(client, test_user)
    response = _json_delete(
        client,
        f"/social-downloader/items/{item_id}",
    )

    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(SocialDownload, item_id) is None
        job = db.session.get(WorkerJob, job_id)
        assert job.status == JOB_STATUS_FAILED
        assert job.error_message == "Download removed by user."


def test_clear_queue_deletes_only_current_users_items(app, client, test_user):
    _make_pro(app, test_user)
    with app.app_context():
        other = User(
            username="other",
            email="other-clear@example.com",
            password_hash="unused",
            plan_tier="pro",
        )
        db.session.add(other)
        db.session.flush()

        user_item = SocialDownload(
            user_id=test_user,
            source_url="https://www.tiktok.com/@creator/video/123",
            platform="tiktok",
            status="queued",
        )
        other_item = SocialDownload(
            user_id=other.id,
            source_url="https://www.instagram.com/reel/OTHER/",
            platform="instagram",
            status="queued",
        )
        db.session.add_all([user_item, other_item])
        db.session.flush()

        user_job = WorkerJob(
            user_id=test_user,
            queue_name="default",
            job_type=JOB_TYPE_SOCIAL_DOWNLOAD,
            status=JOB_STATUS_QUEUED,
            payload_json="{}",
            max_attempts=1,
        )
        other_job = WorkerJob(
            user_id=other.id,
            queue_name="default",
            job_type=JOB_TYPE_SOCIAL_DOWNLOAD,
            status=JOB_STATUS_QUEUED,
            payload_json="{}",
            max_attempts=1,
        )
        db.session.add_all([user_job, other_job])
        db.session.flush()
        user_item.worker_job_id = user_job.id
        other_item.worker_job_id = other_job.id
        db.session.commit()
        other_item_id = other_item.id
        user_job_id = user_job.id
        other_job_id = other_job.id

    _login(client, test_user)
    response = _json_delete(client, "/social-downloader/items")

    assert response.status_code == 200
    assert response.get_json()["deleted"] == 1
    with app.app_context():
        assert SocialDownload.query.filter_by(user_id=test_user).count() == 0
        assert db.session.get(SocialDownload, other_item_id) is not None
        assert db.session.get(WorkerJob, user_job_id).status == JOB_STATUS_FAILED
        assert db.session.get(WorkerJob, other_job_id).status == JOB_STATUS_QUEUED


def test_worker_uses_dlqueue_and_persists_file(
    app, test_user, tmp_path, monkeypatch
):
    old_media_root = app.config.get("MEDIA_ROOT")
    app.config["MEDIA_ROOT"] = str(tmp_path)
    try:
        with app.app_context():
            item = SocialDownload(
                user_id=test_user,
                source_url="https://www.facebook.com/share/r/18zUcmn7DS/",
                platform="facebook",
                status="queued",
            )
            db.session.add(item)
            db.session.flush()
            job = WorkerJob(
                user_id=test_user,
                queue_name="default",
                job_type=JOB_TYPE_SOCIAL_DOWNLOAD,
                status="running",
                payload_json="{}",
                max_attempts=1,
            )
            db.session.add(job)
            db.session.flush()
            item.worker_job_id = job.id
            db.session.commit()
            item_id = item.id
            job_id = job.id

        def fake_download(url, dest_dir, out_id, extra_opts):
            assert "facebook.com" in url
            assert out_id == "video"
            output = Path(dest_dir) / "video.mp4"
            output.write_bytes(b"downloaded-video")
            return str(output), "Facebook short"

        monkeypatch.setattr(
            "worker_runtime.dlqueue_download",
            fake_download,
        )

        with app.app_context():
            result = _run_social_download(
                job_id,
                test_user,
                {
                    "download_id": item_id,
                    "source_url": "https://www.facebook.com/share/r/18zUcmn7DS/",
                },
            )
            item = db.session.get(SocialDownload, item_id)

            assert item.status == "success"
            assert item.title == "Facebook short"
            assert item.file_size_bytes == len(b"downloaded-video")
            assert (tmp_path / item.storage_path).read_bytes() == b"downloaded-video"
            assert result["download_id"] == item_id
    finally:
        app.config["MEDIA_ROOT"] = old_media_root


def test_worker_failure_is_visible_to_user(
    app, test_user, tmp_path, monkeypatch
):
    old_media_root = app.config.get("MEDIA_ROOT")
    app.config["MEDIA_ROOT"] = str(tmp_path)
    try:
        with app.app_context():
            item = SocialDownload(
                user_id=test_user,
                source_url="https://www.instagram.com/reel/ABC/",
                platform="instagram",
                status="queued",
            )
            db.session.add(item)
            db.session.flush()
            job = WorkerJob(
                user_id=test_user,
                queue_name="default",
                job_type=JOB_TYPE_SOCIAL_DOWNLOAD,
                status="running",
                payload_json="{}",
                max_attempts=1,
            )
            db.session.add(job)
            db.session.flush()
            item.worker_job_id = job.id
            db.session.commit()
            item_id = item.id
            job_id = job.id

        def fail_download(*_args, **_kwargs):
            raise RuntimeError("Video is private or unavailable")

        monkeypatch.setattr(
            "worker_runtime.dlqueue_download",
            fail_download,
        )

        with app.app_context():
            with pytest.raises(
                RuntimeError, match="private or unavailable"
            ):
                _run_social_download(
                    job_id,
                    test_user,
                    {
                        "download_id": item_id,
                        "source_url": "https://www.instagram.com/reel/ABC/",
                    },
                )
            item = db.session.get(SocialDownload, item_id)
            assert item.status == "failed"
            assert "private or unavailable" in item.error
    finally:
        app.config["MEDIA_ROOT"] = old_media_root
