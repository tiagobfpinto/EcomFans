# Windows Worker System

This project now supports a DB-backed worker queue for heavy AI tasks.

## What runs in background

- `script_optimizer.transcribe` jobs (video/audio transcription)

The web app only uploads and queues the job. A separate worker process picks it up and processes it.

## Run on Windows

1. Run migrations:

```powershell
flask db upgrade
```

2. Start the web server:

```powershell
flask run --host 0.0.0.0 --port 5000
```

3. Start one or more worker processes in separate terminals:

```powershell
flask worker --concurrency 2
```

## Options

- `--queue`: Queue name (default: `default`)
- `--concurrency`: Number of worker threads in the process (default: `2`)
- `--poll-interval`: Seconds between queue polls (default: `1.5`)
- `--stale-timeout`: Seconds before stale running jobs are re-queued/failed (default: `3600`)

Example:

```powershell
flask worker --queue default --concurrency 4 --poll-interval 1 --stale-timeout 7200
```

## Environment variables

- `WORKER_UPLOAD_ROOT`: Optional path to temporary uploaded job files.
  - Default: `<instance_path>\\worker_uploads`

