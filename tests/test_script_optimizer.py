"""Tests for saving transcribed scripts in the script optimizer."""
import hashlib
import io
import os
from types import SimpleNamespace

import pytest

import worker_runtime
from db import SavedScript, WorkerJob, db
from worker_runtime import (
    _sha256_file,
    _split_media_for_transcription,
    _transcribe_media_file_with_meta,
)
from worker_queue import job_payload
from worker_tasks import JOB_TYPE_SCRIPT_TRANSCRIBE


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def _csrf(client):
    client.get("/login")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _png_bytes():
    """A tiny valid PNG so Pillow can decode the uploaded thumbnail."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 9), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class TestSaveScript:
    def test_requires_login(self, client):
        # Unauthenticated GET (no CSRF involved) must redirect to login.
        resp = client.get("/script-optimizer/scripts")
        assert resp.status_code in (302, 401)

    def test_save_without_transcript_rejected(self, client, test_user):
        _login(client, test_user)
        token = _csrf(client)
        resp = client.post(
            "/script-optimizer/scripts",
            data={"transcript": "   ", "_csrf_token": token},
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 400

    def test_save_script_without_thumbnail(self, client, test_user, app):
        _login(client, test_user)
        token = _csrf(client)
        resp = client.post(
            "/script-optimizer/scripts",
            data={
                "transcript": "Buy now and save big today!",
                "title": "Promo hook",
                "_csrf_token": token,
            },
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 201
        body = resp.get_json()["script"]
        assert body["title"] == "Promo hook"
        assert body["has_thumbnail"] is False

        with app.app_context():
            saved = SavedScript.query.filter_by(user_id=test_user).all()
            assert len(saved) == 1
            assert saved[0].transcript == "Buy now and save big today!"

    def test_save_script_with_thumbnail(self, client, test_user, app):
        _login(client, test_user)
        token = _csrf(client)
        data = {
            "transcript": "First line is the hook\nrest of the script",
            "_csrf_token": token,
            "thumbnail": (io.BytesIO(_png_bytes()), "first_frame.png"),
        }
        resp = client.post(
            "/script-optimizer/scripts",
            data=data,
            headers={"X-CSRF-Token": token},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        body = resp.get_json()["script"]
        # Title auto-derived from the first line when none supplied.
        assert body["title"] == "First line is the hook"
        assert body["has_thumbnail"] is True
        assert body["thumbnail_url"].endswith(f"/{body['id']}")

        # Thumbnail is served back.
        thumb = client.get(body["thumbnail_url"])
        assert thumb.status_code == 200
        assert thumb.mimetype.startswith("image/")

    def test_list_and_delete(self, client, test_user, app):
        _login(client, test_user)
        token = _csrf(client)
        client.post(
            "/script-optimizer/scripts",
            data={"transcript": "one", "_csrf_token": token},
            headers={"X-CSRF-Token": token},
        )
        listed = client.get("/script-optimizer/scripts").get_json()
        assert len(listed["scripts"]) == 1
        script_id = listed["scripts"][0]["id"]

        resp = client.delete(
            f"/script-optimizer/scripts/{script_id}",
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 200
        with app.app_context():
            assert SavedScript.query.count() == 0

    def test_cannot_access_other_users_script(self, client, test_user, app):
        with app.app_context():
            other = SavedScript(user_id=test_user + 999, transcript="secret", title="x")
            db.session.add(other)
            db.session.commit()
            other_id = other.id

        _login(client, test_user)
        resp = client.delete(
            f"/script-optimizer/scripts/{other_id}",
            headers={"X-CSRF-Token": _csrf(client)},
        )
        assert resp.status_code == 404


class TestTranscriptionUpload:
    def test_page_exposes_multi_file_queue_grid(self, client, test_user):
        _login(client, test_user)

        response = client.get("/script-optimizer")

        assert response.status_code == 200
        assert b'id="script-video-input"' in response.data
        assert b"multiple" in response.data
        assert b'id="script-queue-grid"' in response.data
        assert b"queueSelectedVideos(this)" in response.data

    def test_each_uploaded_file_creates_its_own_queue_job(
        self, client, test_user, monkeypatch
    ):
        _login(client, test_user)
        token = _csrf(client)
        persisted = iter(
            [
                ("queued-first.mp4", 1024),
                ("queued-second.mp4", 2048),
            ]
        )
        monkeypatch.setattr(
            "script_optimizer._persist_upload_for_worker",
            lambda *_args: next(persisted),
        )

        for filename in ("first.mp4", "second.mp4"):
            response = client.post(
                "/script-optimizer/transcribe",
                data={
                    "video": (io.BytesIO(b"test video"), filename),
                    "_csrf_token": token,
                },
                headers={"X-CSRF-Token": token},
                content_type="multipart/form-data",
            )
            assert response.status_code == 202

        with client.application.app_context():
            jobs = WorkerJob.query.order_by(WorkerJob.id.asc()).all()
            assert len(jobs) == 2
            assert [job_payload(job)["original_name"] for job in jobs] == [
                "first.mp4",
                "second.mp4",
            ]

    def test_accepts_source_larger_than_openai_limit(
        self, client, test_user, monkeypatch
    ):
        _login(client, test_user)
        token = _csrf(client)
        monkeypatch.setattr(
            "script_optimizer._persist_upload_for_worker",
            lambda *_args: ("queued-large-video.mp4", 26 * 1024 * 1024),
        )

        response = client.post(
            "/script-optimizer/transcribe",
            data={
                "video": (io.BytesIO(b"small test body"), "large.mp4"),
                "_csrf_token": token,
            },
            headers={"X-CSRF-Token": token},
            content_type="multipart/form-data",
        )

        assert response.status_code == 202
        with client.application.app_context():
            job = WorkerJob.query.one()
            assert job.job_type == JOB_TYPE_SCRIPT_TRANSCRIBE

    def test_rejects_source_over_configured_limit(
        self, app, client, test_user, monkeypatch
    ):
        _login(client, test_user)
        token = _csrf(client)
        monkeypatch.setitem(
            app.config, "SCRIPT_TRANSCRIBE_MAX_UPLOAD_BYTES", 10 * 1024 * 1024
        )
        monkeypatch.setattr(
            "script_optimizer._persist_upload_for_worker",
            lambda *_args: ("too-large-video.mp4", 11 * 1024 * 1024),
        )

        response = client.post(
            "/script-optimizer/transcribe",
            data={
                "video": (io.BytesIO(b"small test body"), "large.mp4"),
                "_csrf_token": token,
            },
            headers={"X-CSRF-Token": token},
            content_type="multipart/form-data",
        )

        assert response.status_code == 400
        assert "10 MB" in response.get_json()["error"]


class TestChunkedTranscription:
    def test_small_supported_file_uses_one_request(self, tmp_path, monkeypatch):
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"small media")
        calls = []

        def fake_transcribe(**kwargs):
            calls.append(kwargs)
            return "Single transcript", {"model": "test", "latency_ms": 12}

        monkeypatch.setattr(worker_runtime, "openai_transcribe_file_with_meta", fake_transcribe)

        text, meta = _transcribe_media_file_with_meta(
            api_key="key", file_path=str(source), mime_type="video/mp4"
        )

        assert text == "Single transcript"
        assert len(calls) == 1
        assert calls[0]["file_path"] == str(source)
        assert meta["transcription_chunk_count"] == 1
        assert meta["source_transcoded"] is False

    def test_large_file_transcribes_chunks_sequentially_and_cleans_up(
        self, tmp_path, monkeypatch
    ):
        source = tmp_path / "large.mp4"
        source.write_bytes(b"large media")
        monkeypatch.setattr(
            worker_runtime, "_OPENAI_TRANSCRIPTION_DIRECT_MAX_BYTES", 4
        )
        created_chunk_paths = []

        def fake_split(_source_path, output_dir):
            for index in range(3):
                chunk_path = os.path.join(output_dir, f"chunk_{index:04d}.mp3")
                with open(chunk_path, "wb") as chunk:
                    chunk.write(b"audio")
                created_chunk_paths.append(chunk_path)
            return list(created_chunk_paths)

        calls = []

        def fake_transcribe(**kwargs):
            calls.append(kwargs["file_path"])
            number = len(calls)
            return (
                f"Part {number}",
                {
                    "model": "test",
                    "latency_ms": number,
                    "input_tokens": number * 10,
                    "total_tokens": number * 10,
                },
            )

        monkeypatch.setattr(worker_runtime, "_split_media_for_transcription", fake_split)
        monkeypatch.setattr(worker_runtime, "openai_transcribe_file_with_meta", fake_transcribe)

        text, meta = _transcribe_media_file_with_meta(
            api_key="key", file_path=str(source), mime_type="video/mp4"
        )

        assert text == "Part 1\n\nPart 2\n\nPart 3"
        assert calls == created_chunk_paths
        assert meta["transcription_chunk_count"] == 3
        assert meta["source_transcoded"] is True
        assert meta["latency_ms"] == 6
        assert meta["input_tokens"] == 60
        assert not any(os.path.exists(path) for path in created_chunk_paths)

    def test_chunk_failure_reports_its_position(self, tmp_path, monkeypatch):
        source = tmp_path / "large.mov"
        source.write_bytes(b"media")

        def fake_split(_source_path, output_dir):
            paths = []
            for index in range(2):
                path = os.path.join(output_dir, f"chunk_{index:04d}.mp3")
                with open(path, "wb") as chunk:
                    chunk.write(b"audio")
                paths.append(path)
            return paths

        call_count = 0

        def fake_transcribe(**_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("provider failed")
            return "Part 1", {"model": "test"}

        monkeypatch.setattr(worker_runtime, "_split_media_for_transcription", fake_split)
        monkeypatch.setattr(worker_runtime, "openai_transcribe_file_with_meta", fake_transcribe)

        with pytest.raises(RuntimeError, match="chunk 2 of 2"):
            _transcribe_media_file_with_meta(
                api_key="key", file_path=str(source), mime_type="video/quicktime"
            )

    def test_ffmpeg_split_command_produces_provider_safe_audio(
        self, tmp_path, monkeypatch
    ):
        source = tmp_path / "large.mov"
        source.write_bytes(b"media")
        output_dir = tmp_path / "chunks"
        output_dir.mkdir()
        seen_command = []

        def fake_run(command, **_kwargs):
            seen_command.extend(command)
            output_path = command[-1].replace("%04d", "0000")
            with open(output_path, "wb") as chunk:
                chunk.write(b"encoded audio")
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(worker_runtime, "_resolve_ffmpeg_executable", lambda: "ffmpeg")
        monkeypatch.setattr(worker_runtime.subprocess, "run", fake_run)

        chunks = _split_media_for_transcription(str(source), str(output_dir))

        assert len(chunks) == 1
        assert "-vn" in seen_command
        assert seen_command[seen_command.index("-ac") + 1] == "1"
        assert seen_command[seen_command.index("-ar") + 1] == "16000"
        assert seen_command[seen_command.index("-segment_time") + 1] == "1200"

    def test_hashing_large_file_is_content_stable(self, tmp_path):
        source = tmp_path / "source.bin"
        payload = (b"0123456789abcdef" * 70_000) + b"tail"
        source.write_bytes(payload)

        assert _sha256_file(str(source)) == hashlib.sha256(payload).hexdigest()
