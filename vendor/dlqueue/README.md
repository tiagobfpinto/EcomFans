# dlqueue

This package is vendored in EcomFans so that deployments can install the
TikTok, Instagram, and Facebook downloader without relying on a sibling
directory outside the repository.

It is installed by the root `requirements.txt` with:

```text
./vendor/dlqueue
```

The package depends on `yt-dlp` and exposes the queue and stateless download
helpers used by `social_downloader.py` and `worker_runtime.py`.
