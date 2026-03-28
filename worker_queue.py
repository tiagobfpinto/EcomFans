from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from db import WorkerJob, db

JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"

TERMINAL_JOB_STATUSES = {JOB_STATUS_COMPLETED, JOB_STATUS_FAILED}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_dump(data: dict[str, Any] | None) -> str:
    return json.dumps(data or {})


def _json_load(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _dt_to_iso(value: datetime | None) -> str | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def enqueue_worker_job(
    *,
    user_id: int | None,
    job_type: str,
    payload: dict[str, Any] | None,
    queue_name: str = "default",
    max_attempts: int = 2,
    available_at: datetime | None = None,
) -> WorkerJob:
    now = utc_now()
    job = WorkerJob(
        user_id=user_id,
        queue_name=(queue_name or "default").strip() or "default",
        job_type=job_type.strip(),
        status=JOB_STATUS_QUEUED,
        payload_json=_json_dump(payload),
        max_attempts=max(1, int(max_attempts)),
        available_at=available_at or now,
    )
    db.session.add(job)
    db.session.commit()
    return job


def get_worker_job_for_user(job_id: int, user_id: int) -> WorkerJob | None:
    return WorkerJob.query.filter_by(id=job_id, user_id=user_id).first()


def job_payload(job: WorkerJob) -> dict[str, Any]:
    return _json_load(job.payload_json)


def serialize_worker_job(job: WorkerJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "user_id": job.user_id,
        "queue_name": job.queue_name,
        "job_type": job.job_type,
        "status": job.status,
        "attempts": int(job.attempts or 0),
        "max_attempts": int(job.max_attempts or 0),
        "error": job.error_message,
        "result": _json_load(job.result_json),
        "created_at": _dt_to_iso(job.created_at),
        "available_at": _dt_to_iso(job.available_at),
        "started_at": _dt_to_iso(job.started_at),
        "finished_at": _dt_to_iso(job.finished_at),
    }


def claim_next_worker_job(
    *,
    worker_name: str,
    queue_name: str = "default",
    job_types: set[str] | None = None,
) -> tuple[WorkerJob, str] | None:
    now = utc_now()
    query = WorkerJob.query.filter(
        WorkerJob.status == JOB_STATUS_QUEUED,
        WorkerJob.queue_name == queue_name,
        WorkerJob.available_at <= now,
    )
    if job_types:
        query = query.filter(WorkerJob.job_type.in_(list(job_types)))

    candidate = query.order_by(WorkerJob.available_at.asc(), WorkerJob.id.asc()).first()
    if not candidate:
        return None

    next_attempt = int(candidate.attempts or 0) + 1
    lock_token = secrets.token_hex(16)
    updated = (
        WorkerJob.query.filter(
            WorkerJob.id == candidate.id,
            WorkerJob.status == JOB_STATUS_QUEUED,
        )
        .update(
            {
                WorkerJob.status: JOB_STATUS_RUNNING,
                WorkerJob.attempts: next_attempt,
                WorkerJob.locked_at: now,
                WorkerJob.started_at: now,
                WorkerJob.finished_at: None,
                WorkerJob.lock_token: lock_token,
                WorkerJob.worker_name: worker_name,
                WorkerJob.error_message: None,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.session.rollback()
        return None

    db.session.commit()
    claimed = db.session.get(WorkerJob, candidate.id)
    if not claimed:
        return None
    return claimed, lock_token


def complete_worker_job(job_id: int, lock_token: str, result: dict[str, Any] | None) -> bool:
    now = utc_now()
    updated = (
        WorkerJob.query.filter(
            WorkerJob.id == job_id,
            WorkerJob.status == JOB_STATUS_RUNNING,
            WorkerJob.lock_token == lock_token,
        )
        .update(
            {
                WorkerJob.status: JOB_STATUS_COMPLETED,
                WorkerJob.result_json: _json_dump(result),
                WorkerJob.finished_at: now,
                WorkerJob.locked_at: None,
                WorkerJob.lock_token: None,
                WorkerJob.worker_name: None,
                WorkerJob.error_message: None,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.session.rollback()
        return False
    db.session.commit()
    return True


def fail_worker_job(
    job_id: int,
    lock_token: str,
    error_message: str,
    *,
    retry_delay_seconds: int = 15,
) -> str | None:
    now = utc_now()
    job = WorkerJob.query.filter(
        WorkerJob.id == job_id,
        WorkerJob.status == JOB_STATUS_RUNNING,
        WorkerJob.lock_token == lock_token,
    ).first()
    if not job:
        db.session.rollback()
        return None

    job.error_message = (error_message or "").strip()[:4000]
    job.locked_at = None
    job.lock_token = None
    job.worker_name = None

    should_retry = int(job.attempts or 0) < int(job.max_attempts or 1)
    if should_retry:
        job.status = JOB_STATUS_QUEUED
        job.available_at = now + timedelta(seconds=max(3, int(retry_delay_seconds)))
        job.finished_at = None
    else:
        job.status = JOB_STATUS_FAILED
        job.finished_at = now

    db.session.commit()
    return job.status


def requeue_stale_running_jobs(stale_after_seconds: int = 3600) -> dict[str, int]:
    now = utc_now()
    cutoff = now - timedelta(seconds=max(30, int(stale_after_seconds)))
    stale_jobs = WorkerJob.query.filter(
        WorkerJob.status == JOB_STATUS_RUNNING,
        WorkerJob.locked_at.isnot(None),
        WorkerJob.locked_at <= cutoff,
    ).all()
    if not stale_jobs:
        return {"requeued": 0, "failed": 0}

    requeued = 0
    failed = 0
    for job in stale_jobs:
        job.locked_at = None
        job.lock_token = None
        job.worker_name = None
        job.error_message = "Worker heartbeat timeout."
        should_retry = int(job.attempts or 0) < int(job.max_attempts or 1)
        if should_retry:
            job.status = JOB_STATUS_QUEUED
            job.available_at = now
            job.finished_at = None
            requeued += 1
        else:
            job.status = JOB_STATUS_FAILED
            job.finished_at = now
            failed += 1

    db.session.commit()
    return {"requeued": requeued, "failed": failed}

