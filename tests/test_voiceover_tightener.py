from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from array import array
from pathlib import Path

import pytest

from db import User, VoiceoverTightening, WorkerJob, db
from tests.conftest import csrf_token
from voiceover_processing import (
    PRESET_DEFAULTS,
    SpeechAnalysis,
    VoiceoverProcessingError,
    analyze_voiceover,
    build_edit_plan,
    normalize_settings,
    probe_mp3,
    process_voiceover,
    resolve_ffmpeg_executable,
)
from voiceover_worker import run_voiceover_tightening_job
from worker_tasks import JOB_TYPE_VOICEOVER_TIGHTEN
from worker_queue import JOB_STATUS_FAILED


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["username"] = "testuser"


def _post_mp3(client, data=b"fake mp3", filename="voice.mp3", **fields):
    token = csrf_token(client)
    payload = {
        "audio": (io.BytesIO(data), filename),
        "preset": fields.pop("preset", "dynamic"),
        "settings": json.dumps(fields.pop("settings", {})),
        "_csrf_token": token,
    }
    payload.update(fields)
    return client.post(
        "/voiceover-tightener/items",
        data=payload,
        headers={"X-CSRF-Token": token},
        content_type="multipart/form-data",
    )


def _synthetic_mp3(path: Path, *, silence_only=False):
    ffmpeg = resolve_ffmpeg_executable()
    if silence_only:
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1.5",
            "-c:a", "libmp3lame", "-q:a", "2", str(path),
        ]
    else:
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.60:sample_rate=44100",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=0.55",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.60:sample_rate=44100",
            "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]",
            "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "2", str(path),
        ]
    completed = subprocess.run(command, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")


def _decode_mono_pcm(path: Path) -> array:
    completed = subprocess.run(
        [
            resolve_ffmpeg_executable(), "-hide_banner", "-loglevel", "error",
            "-i", str(path), "-ac", "1", "-ar", "44100",
            "-f", "s16le", "pipe:1",
        ],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    samples = array("h")
    samples.frombytes(completed.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def test_page_requires_authentication(client):
    response = client.get("/voiceover-tightener")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_page_is_available_to_free_authenticated_user(client, test_user):
    _login(client, test_user)
    response = client.get("/voiceover-tightener")
    assert response.status_code == 200
    assert b"Voiceover Tightener" in response.data
    assert b"Local processing" in response.data


def test_normalize_settings_uses_presets_and_validates():
    preset, settings = normalize_settings(
        "dynamic", {"min_pause_ms": 190, "breath_handling": "remove"}
    )
    assert preset == "dynamic"
    assert settings["min_pause_ms"] == 190
    assert settings["sentence_gap_ms"] == 145
    assert settings["breath_handling"] == "remove"
    with pytest.raises(ValueError, match="Sentence gap"):
        normalize_settings(
            "dynamic",
            {"within_sentence_gap_ms": 200, "sentence_gap_ms": 100},
        )
    with pytest.raises(ValueError, match="Unknown setting"):
        normalize_settings("dynamic", {"gain": 10})


def test_edit_plan_preserves_micro_pause_and_shortens_long_pause():
    levels = [-80.0] * 200
    for start, end in ((10, 60), (70, 120), (175, 195)):
        levels[start:end] = [-18.0] * (end - start)
    analysis = SpeechAnalysis(
        levels_db=levels,
        speech_segments=[(10, 60), (70, 120), (175, 195)],
        noise_floor_db=-80.0,
        speech_level_db=-18.0,
        start_threshold_db=-50.0,
        stop_threshold_db=-53.0,
        warnings=[],
    )
    settings = dict(PRESET_DEFAULTS["dynamic"])
    plan = build_edit_plan(2000, analysis, settings, "dynamic")
    assert plan.pauses_shortened == 1
    assert len(plan.intervals_ms) >= 2
    # The 100 ms gap between the first two speech runs is below the 160 ms
    # editing threshold and remains inside the first kept interval.
    assert plan.intervals_ms[0][1] > 700


def test_breath_modes_preserve_or_remove_low_energy_event():
    levels = [-80.0] * 210
    levels[10:50] = [-18.0] * 40
    levels[90:101] = [-50.0] * 11
    levels[150:195] = [-18.0] * 45
    analysis = SpeechAnalysis(
        levels_db=levels,
        speech_segments=[(10, 50), (150, 195)],
        noise_floor_db=-80.0,
        speech_level_db=-18.0,
        start_threshold_db=-35.0,
        stop_threshold_db=-38.0,
        warnings=[],
    )
    keep_settings = dict(PRESET_DEFAULTS["dynamic"], breath_handling="keep")
    remove_settings = dict(PRESET_DEFAULTS["dynamic"], breath_handling="remove")
    keep_plan = build_edit_plan(2100, analysis, keep_settings, "dynamic")
    remove_plan = build_edit_plan(2100, analysis, remove_settings, "dynamic")
    assert keep_plan.pauses_shortened == 1
    assert remove_plan.pauses_shortened == 1
    assert len(keep_plan.intervals_ms) > len(remove_plan.intervals_ms)


def test_low_contrast_audio_adds_background_warning(monkeypatch):
    levels = [-42.0] * 40 + [-33.0] * 60 + [-42.0] * 40
    monkeypatch.setattr(
        "voiceover_processing._read_analysis_levels",
        lambda *_args, **_kwargs: levels,
    )
    analysis = analyze_voiceover(
        "ignored.mp3", dict(PRESET_DEFAULTS["dynamic"]), timeout_seconds=5
    )
    assert analysis.warnings == [
        "Background audio or music may reduce detection accuracy."
    ]


def test_real_mp3_processing_reduces_duration_and_keeps_source(tmp_path):
    source = tmp_path / "sample.mp3"
    output = tmp_path / "sample_tightened.mp3"
    _synthetic_mp3(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    probe = probe_mp3(str(source))

    result = process_voiceover(
        str(source),
        str(output),
        "dynamic",
        dict(PRESET_DEFAULTS["dynamic"]),
        max_duration_seconds=60,
        timeout_seconds=30,
    )

    assert output.is_file()
    output_probe = probe_mp3(str(output))
    assert output_probe.duration_ms < probe.duration_ms
    assert output_probe.sample_rate == probe.sample_rate
    assert output_probe.channels == probe.channels
    assert result["pauses_shortened"] >= 1
    assert result["removed_duration_ms"] > 0
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    samples = _decode_mono_pcm(output)
    steady = samples[4_410:17_640]
    crossings = sum(
        1 for left, right in zip(steady, steady[1:])
        if (left < 0 <= right) or (left >= 0 > right)
    )
    estimated_frequency = crossings / (2 * (len(steady) / 44_100))
    assert 425 <= estimated_frequency <= 455
    assert max(
        abs(right - left) for left, right in zip(samples, samples[1:])
    ) < 10_000


def test_silence_only_mp3_produces_no_result(tmp_path):
    source = tmp_path / "silence.mp3"
    output = tmp_path / "silence_tightened.mp3"
    _synthetic_mp3(source, silence_only=True)
    with pytest.raises(VoiceoverProcessingError, match="No clear speech"):
        process_voiceover(
            str(source), str(output), "dynamic",
            dict(PRESET_DEFAULTS["dynamic"]), timeout_seconds=30,
        )
    assert not output.exists()


def test_corrupt_mp3_is_rejected(tmp_path):
    source = tmp_path / "broken.mp3"
    source.write_bytes(b"not an mp3")
    with pytest.raises(VoiceoverProcessingError, match="damaged|incompatible"):
        probe_mp3(str(source))


def test_authenticated_user_can_queue_without_credits(
    app, client, test_user, tmp_path, monkeypatch
):
    monkeypatch.setitem(app.config, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "voiceover_tightener.probe_mp3",
        lambda *_args, **_kwargs: type(
            "Probe", (), {"duration_ms": 12_000}
        )(),
    )
    _login(client, test_user)
    with app.app_context():
        before = db.session.get(User, test_user).credits

    response = _post_mp3(client)

    assert response.status_code == 202, response.get_data(as_text=True)
    with app.app_context():
        item = VoiceoverTightening.query.one()
        job = WorkerJob.query.one()
        assert item.status == "queued"
        assert (tmp_path / item.original_storage_path).read_bytes() == b"fake mp3"
        assert job.job_type == JOB_TYPE_VOICEOVER_TIGHTEN
        assert job.max_attempts == 1
        assert db.session.get(User, test_user).credits == before


def test_queue_rejects_second_active_item(
    app, client, test_user, tmp_path, monkeypatch
):
    monkeypatch.setitem(app.config, "MEDIA_ROOT", str(tmp_path))
    with app.app_context():
        db.session.add(
            VoiceoverTightening(
                user_id=test_user,
                status="processing",
                original_filename="first.mp3",
                original_storage_path="first.mp3",
                original_file_size_bytes=10,
                preset="dynamic",
                settings_json="{}",
            )
        )
        db.session.commit()
    _login(client, test_user)
    response = _post_mp3(client)
    assert response.status_code == 409
    assert "current voiceover" in response.get_json()["error"]


def test_invalid_extension_and_settings_are_rejected(client, test_user):
    _login(client, test_user)
    assert _post_mp3(client, filename="voice.wav").status_code == 400
    response = _post_mp3(
        client,
        settings={"within_sentence_gap_ms": 300, "sentence_gap_ms": 100},
    )
    assert response.status_code == 400


def test_history_and_audio_are_user_scoped(
    app, client, test_user, tmp_path, monkeypatch
):
    monkeypatch.setitem(app.config, "MEDIA_ROOT", str(tmp_path))
    original = tmp_path / "users" / str(test_user) / "voice.mp3"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"owned-audio")
    other_path = tmp_path / "users" / "999" / "voice.mp3"
    other_path.parent.mkdir(parents=True)
    other_path.write_bytes(b"secret-audio")
    with app.app_context():
        owned = VoiceoverTightening(
            user_id=test_user, status="failed", original_filename="owned.mp3",
            original_storage_path=original.relative_to(tmp_path).as_posix(),
            original_file_size_bytes=11, preset="dynamic", settings_json="{}",
        )
        other = VoiceoverTightening(
            user_id=999, status="failed", original_filename="secret.mp3",
            original_storage_path=other_path.relative_to(tmp_path).as_posix(),
            original_file_size_bytes=12, preset="dynamic", settings_json="{}",
        )
        db.session.add_all([owned, other])
        db.session.commit()
        owned_id, other_id = owned.id, other.id
    _login(client, test_user)

    history = client.get("/voiceover-tightener/items").get_json()
    assert [item["id"] for item in history["items"]] == [owned_id]
    response = client.get(
        f"/voiceover-tightener/items/{owned_id}/original",
        headers={"Range": "bytes=0-4"},
    )
    assert response.status_code in {200, 206}
    assert client.get(
        f"/voiceover-tightener/items/{other_id}/original"
    ).status_code == 404


def test_delete_completed_item_removes_both_files(
    app, client, test_user, tmp_path, monkeypatch
):
    monkeypatch.setitem(app.config, "MEDIA_ROOT", str(tmp_path))
    original = tmp_path / "users" / str(test_user) / "original.mp3"
    output = tmp_path / "users" / str(test_user) / "output.mp3"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original")
    output.write_bytes(b"output")
    with app.app_context():
        item = VoiceoverTightening(
            user_id=test_user, status="completed", original_filename="voice.mp3",
            original_storage_path=original.relative_to(tmp_path).as_posix(),
            output_storage_path=output.relative_to(tmp_path).as_posix(),
            original_file_size_bytes=8, output_file_size_bytes=6,
            preset="dynamic", settings_json="{}",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id
    _login(client, test_user)
    token = csrf_token(client)
    response = client.delete(
        f"/voiceover-tightener/items/{item_id}",
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200
    assert not original.exists()
    assert not output.exists()


def test_failed_item_can_be_retried(app, client, test_user, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "MEDIA_ROOT", str(tmp_path))
    original = tmp_path / "original.mp3"
    original.write_bytes(b"original")
    with app.app_context():
        item = VoiceoverTightening(
            user_id=test_user, status="failed", original_filename="voice.mp3",
            original_storage_path="original.mp3", original_file_size_bytes=8,
            preset="dynamic", settings_json=json.dumps(PRESET_DEFAULTS["dynamic"]),
            error="Previous failure",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id
    _login(client, test_user)
    token = csrf_token(client)
    response = client.post(
        f"/voiceover-tightener/items/{item_id}/retry",
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 202
    with app.app_context():
        item = db.session.get(VoiceoverTightening, item_id)
        assert item.status == "queued"
        assert item.error is None
        assert item.worker_job.job_type == JOB_TYPE_VOICEOVER_TIGHTEN


def test_worker_completes_record_and_keeps_original(
    app, test_user, tmp_path, monkeypatch
):
    monkeypatch.setitem(app.config, "MEDIA_ROOT", str(tmp_path))
    original = tmp_path / "users" / str(test_user) / "voiceover_tightening" / "1" / "original.mp3"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"source-audio")
    source_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    with app.app_context():
        item = VoiceoverTightening(
            user_id=test_user, status="queued", original_filename="voice.mp3",
            original_storage_path=original.relative_to(tmp_path).as_posix(),
            original_file_size_bytes=12, preset="dynamic",
            settings_json=json.dumps(PRESET_DEFAULTS["dynamic"]),
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    def fake_process(_source, output, *_args, **_kwargs):
        Path(output).write_bytes(b"tightened-audio")
        return {
            "original_duration_ms": 10_000,
            "output_duration_ms": 8_000,
            "removed_duration_ms": 2_000,
            "pauses_shortened": 4,
            "overlaps_applied": 3,
            "warnings": [],
        }

    monkeypatch.setattr("voiceover_worker.process_voiceover", fake_process)
    with app.app_context():
        result = run_voiceover_tightening_job(
            1, test_user, {"tightening_id": item_id}
        )
        item = db.session.get(VoiceoverTightening, item_id)
        assert item.status == "completed"
        assert item.output_duration_ms == 8_000
        assert (tmp_path / item.output_storage_path).read_bytes() == b"tightened-audio"
        assert result["pauses_shortened"] == 4
    assert hashlib.sha256(original.read_bytes()).hexdigest() == source_hash


def test_worker_failure_is_visible_and_preserves_original(
    app, test_user, tmp_path, monkeypatch
):
    monkeypatch.setitem(app.config, "MEDIA_ROOT", str(tmp_path))
    original = tmp_path / "original.mp3"
    original.write_bytes(b"source-audio")
    with app.app_context():
        item = VoiceoverTightening(
            user_id=test_user, status="queued", original_filename="voice.mp3",
            original_storage_path="original.mp3", original_file_size_bytes=12,
            preset="dynamic", settings_json=json.dumps(PRESET_DEFAULTS["dynamic"]),
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    def fail(*_args, **_kwargs):
        raise VoiceoverProcessingError("No clear speech was found.")

    monkeypatch.setattr("voiceover_worker.process_voiceover", fail)
    with app.app_context():
        with pytest.raises(VoiceoverProcessingError, match="No clear speech"):
            run_voiceover_tightening_job(1, test_user, {"tightening_id": item_id})
        item = db.session.get(VoiceoverTightening, item_id)
        assert item.status == "failed"
        assert "No clear speech" in item.error
    assert original.read_bytes() == b"source-audio"


def test_missing_source_marks_worker_item_failed(app, test_user, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "MEDIA_ROOT", str(tmp_path))
    with app.app_context():
        item = VoiceoverTightening(
            user_id=test_user, status="queued", original_filename="missing.mp3",
            original_storage_path="missing.mp3", original_file_size_bytes=12,
            preset="dynamic", settings_json=json.dumps(PRESET_DEFAULTS["dynamic"]),
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id
        with pytest.raises(RuntimeError, match="no longer available"):
            run_voiceover_tightening_job(1, test_user, {"tightening_id": item_id})
        item = db.session.get(VoiceoverTightening, item_id)
        assert item.status == "failed"
        assert "no longer available" in item.error


def test_listing_reconciles_a_stale_failed_worker(app, client, test_user):
    with app.app_context():
        job = WorkerJob(
            user_id=test_user, queue_name="default",
            job_type=JOB_TYPE_VOICEOVER_TIGHTEN, status=JOB_STATUS_FAILED,
            payload_json="{}", max_attempts=1,
            error_message="Worker heartbeat timeout.",
        )
        db.session.add(job)
        db.session.flush()
        item = VoiceoverTightening(
            user_id=test_user, worker_job_id=job.id, status="processing",
            original_filename="voice.mp3", original_storage_path="voice.mp3",
            original_file_size_bytes=12, preset="dynamic", settings_json="{}",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id
    _login(client, test_user)
    response = client.get("/voiceover-tightener/items")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["active_count"] == 0
    assert payload["items"][0]["id"] == item_id
    assert payload["items"][0]["status"] == "failed"
    assert "heartbeat timeout" in payload["items"][0]["error"]
