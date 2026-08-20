import io
import zipfile

import pytest

import image_harvest
from image_harvest import (
    best_from_srcset,
    extract_image_candidates,
    extract_media_candidates,
    extract_video_candidates,
    is_public_url,
    upgrade_url,
)
from tests.conftest import csrf_token


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["username"] = "testuser"


def _json_post(client, path, payload):
    token = csrf_token(client)
    return client.post(path, json=payload, headers={"X-CSRF-Token": token})


# ── Pure extraction ─────────────────────────────────────────────────────────


def test_best_from_srcset_picks_largest_width():
    value = "https://x.com/s.jpg 320w, https://x.com/l.jpg 1024w, https://x.com/m.jpg 640w"
    assert best_from_srcset(value) == "https://x.com/l.jpg"


def test_best_from_srcset_handles_density():
    assert best_from_srcset("a.jpg 1x, b.jpg 2x") == "b.jpg"


def test_extract_resolves_relative_and_protocol_relative():
    html = """
        <img src="/img/a.jpg">
        <img src="//cdn.example.com/b.png">
        <img data-src="c.webp">
    """
    results = extract_image_candidates(html, base_url="https://shop.com/products/x")
    urls = [item["url"] for item in results]
    assert "https://shop.com/img/a.jpg" in urls
    assert "https://cdn.example.com/b.png" in urls
    assert "https://shop.com/products/c.webp" in urls


def test_extract_prefers_srcset_and_reads_backgrounds_and_meta():
    html = """
        <img src="https://x.com/small.jpg"
             srcset="https://x.com/small.jpg 320w, https://x.com/big.jpg 1600w">
        <div style="background-image: url('https://x.com/bg.jpg')"></div>
        <meta property="og:image" content="https://x.com/social.png">
    """
    urls = [item["url"] for item in extract_image_candidates(html)]
    assert "https://x.com/big.jpg" in urls
    assert "https://x.com/bg.jpg" in urls
    assert "https://x.com/social.png" in urls


def test_extract_skips_tiny_data_uri_but_keeps_real_one():
    big = "data:image/png;base64," + "A" * 800
    html = f'<img src="data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA="><img src="{big}">'
    results = extract_image_candidates(html)
    urls = [item["url"] for item in results]
    assert big in urls
    assert all(not (u.startswith("data:image/gif")) for u in urls)
    data_item = next(item for item in results if item["url"] == big)
    assert data_item["is_data"] is True
    assert data_item["filename"] == "image.png"


def test_extract_dedupes():
    html = '<img src="https://x.com/a.jpg"><img src="https://x.com/a.jpg">'
    assert len(extract_image_candidates(html)) == 1


def test_extract_uses_base_href_tag():
    html = '<head><base href="https://brand.com/store/"></head><body><img src="p.jpg"></body>'
    urls = [item["url"] for item in extract_image_candidates(html)]
    assert "https://brand.com/store/p.jpg" in urls


# ── Video extraction ────────────────────────────────────────────────────────


def test_extract_videos_from_video_source_meta_and_links():
    html = """
        <video poster="https://x.com/p.jpg" src="https://x.com/a.mp4"></video>
        <video><source src="https://x.com/b.webm" type="video/webm"></video>
        <meta property="og:video" content="https://x.com/promo.mp4">
        <a href="https://x.com/download/clip.mov">clip</a>
    """
    videos = extract_video_candidates(html)
    urls = [v["url"] for v in videos]
    assert "https://x.com/a.mp4" in urls
    assert "https://x.com/b.webm" in urls
    assert "https://x.com/promo.mp4" in urls
    assert "https://x.com/download/clip.mov" in urls

    first = next(v for v in videos if v["url"] == "https://x.com/a.mp4")
    assert first["type"] == "video"
    assert first["poster"] == "https://x.com/p.jpg"
    assert first["filename"] == "a.mp4"


def test_extract_videos_skips_manifests_and_blob():
    html = (
        '<video src="https://x.com/stream.m3u8"></video>'
        '<video src="blob:https://x.com/abc"></video>'
        '<video src="https://x.com/ok.mp4"></video>'
    )
    assert [v["url"] for v in extract_video_candidates(html)] == ["https://x.com/ok.mp4"]


def test_image_extraction_ignores_video_sources():
    html = (
        '<video><source src="https://x.com/v.mp4" type="video/mp4"></video>'
        '<picture><source srcset="https://x.com/p.webp" type="image/webp">'
        '<img src="https://x.com/p.jpg"></picture>'
    )
    imgs = [i["url"] for i in extract_image_candidates(html)]
    assert "https://x.com/v.mp4" not in imgs
    assert "https://x.com/p.webp" in imgs
    assert "https://x.com/p.jpg" in imgs


def test_extract_media_returns_images_and_videos():
    html = '<img src="https://x.com/a.jpg"><video src="https://x.com/v.mp4"></video>'
    media = extract_media_candidates(html)
    assert [i["url"] for i in media["images"]] == ["https://x.com/a.jpg"]
    assert [v["url"] for v in media["videos"]] == ["https://x.com/v.mp4"]


# ── Quality upgrade ─────────────────────────────────────────────────────────


def test_upgrade_shopify_strips_size_suffix_and_dimension_params():
    url = "https://cdn.shopify.com/s/files/1/0001/hero_400x400.jpg?width=200&v=9"
    upgraded = upgrade_url(url)
    assert "_400x400" not in upgraded
    assert "hero.jpg" in upgraded
    assert "width=" not in upgraded
    assert "v=9" in upgraded


def test_upgrade_strips_generic_size_params_when_unsigned():
    assert upgrade_url("https://img.com/p.jpg?w=100&h=100") == "https://img.com/p.jpg"


def test_upgrade_keeps_signed_urls_intact():
    url = "https://img.com/p.jpg?width=100&sig=abc123"
    assert upgrade_url(url) == url


def test_upgrade_leaves_data_uri_untouched():
    uri = "data:image/png;base64,AAAA"
    assert upgrade_url(uri) == uri


def test_extract_sets_source_only_when_upgraded():
    html = (
        '<img src="https://cdn.shopify.com/s/files/1/hero_400x400.jpg">'
        '<img src="https://x.com/plain.jpg">'
    )
    results = extract_image_candidates(html)
    shopify = next(i for i in results if "hero" in i["url"])
    plain = next(i for i in results if "plain" in i["url"])
    assert shopify["source"] == "https://cdn.shopify.com/s/files/1/hero_400x400.jpg"
    assert plain["source"] is None


# ── SSRF guard ──────────────────────────────────────────────────────────────


def test_is_public_url_rejects_private(monkeypatch):
    monkeypatch.setattr(
        image_harvest.socket,
        "getaddrinfo",
        lambda host, port, proto=0: [(2, 1, 6, "", ("127.0.0.1", port))],
    )
    assert is_public_url("http://localhost/x.jpg") is False


def test_is_public_url_accepts_public(monkeypatch):
    monkeypatch.setattr(
        image_harvest.socket,
        "getaddrinfo",
        lambda host, port, proto=0: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    assert is_public_url("https://example.com/x.jpg") is True


def test_is_public_url_rejects_non_http():
    assert is_public_url("ftp://example.com/x.jpg") is False


# ── Routes ──────────────────────────────────────────────────────────────────


def test_extract_route_requires_auth(client):
    response = client.get("/competitors/image-extractor")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_extract_route_returns_images(client, test_user):
    _login(client, test_user)
    html = '<img src="https://x.com/a.jpg"><img srcset="https://x.com/s.jpg 100w, https://x.com/l.jpg 900w">'
    response = _json_post(client, "/competitors/extract-images", {"html": html})
    assert response.status_code == 200
    payload = response.get_json()
    urls = [item["url"] for item in payload["images"]]
    assert "https://x.com/a.jpg" in urls
    assert "https://x.com/l.jpg" in urls
    assert payload["count"] == len(payload["images"])


def test_extract_route_returns_videos(client, test_user):
    _login(client, test_user)
    html = '<img src="https://x.com/a.jpg"><video src="https://x.com/v.mp4"></video>'
    response = _json_post(client, "/competitors/extract-images", {"html": html})
    assert response.status_code == 200
    payload = response.get_json()
    assert [v["url"] for v in payload["videos"]] == ["https://x.com/v.mp4"]
    assert payload["count"] == 2


def test_extract_route_rejects_empty(client, test_user):
    _login(client, test_user)
    response = _json_post(client, "/competitors/extract-images", {"html": "   "})
    assert response.status_code == 400


def test_image_proxy_streams_video(client, test_user, monkeypatch):
    _login(client, test_user)

    def fake_stream(url):
        def generator():
            yield b"VID"
            yield b"EO"

        return generator(), "video/mp4"

    monkeypatch.setattr("competitors.stream_media", fake_stream)
    response = client.get(
        "/competitors/image-proxy",
        query_string={
            "url": "https://x.com/v.mp4",
            "kind": "video",
            "download": "1",
            "name": "clip.mp4",
        },
    )
    assert response.status_code == 200
    assert response.data == b"VIDEO"
    assert response.mimetype == "video/mp4"
    assert "clip.mp4" in response.headers["Content-Disposition"]


def test_image_proxy_serves_data_uri(client, test_user):
    _login(client, test_user)
    # base64 for a 1x1 png
    data_uri = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    response = client.get("/competitors/image-proxy", query_string={"url": data_uri})
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert len(response.data) > 0


def test_image_proxy_downloads_remote(client, test_user, monkeypatch):
    _login(client, test_user)
    monkeypatch.setattr(
        "competitors.fetch_image", lambda url: (b"IMGBYTES", "image/jpeg")
    )
    response = client.get(
        "/competitors/image-proxy",
        query_string={"url": "https://x.com/a.jpg", "download": "1", "name": "photo.jpg"},
    )
    assert response.status_code == 200
    assert response.data == b"IMGBYTES"
    assert "attachment" in response.headers["Content-Disposition"]
    assert "photo.jpg" in response.headers["Content-Disposition"]


def test_image_proxy_handles_fetch_failure(client, test_user, monkeypatch):
    _login(client, test_user)

    def _boom(url):
        raise image_harvest.ImageFetchError("blocked")

    monkeypatch.setattr("competitors.fetch_image", _boom)
    response = client.get(
        "/competitors/image-proxy", query_string={"url": "http://127.0.0.1/x.jpg"}
    )
    assert response.status_code == 502


def test_download_zip_bundles_images(client, test_user, monkeypatch):
    _login(client, test_user)
    monkeypatch.setattr(
        "competitors.fetch_image", lambda url: (b"BYTES-" + url.encode(), "image/jpeg")
    )
    payload = {
        "images": [
            {"url": "https://x.com/a.jpg", "filename": "a.jpg"},
            {"url": "https://x.com/b.jpg", "filename": "b.jpg"},
        ]
    }
    response = _json_post(client, "/competitors/download-images-zip", payload)
    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.data))
    assert sorted(archive.namelist()) == ["a.jpg", "b.jpg"]


def test_download_zip_dedupes_names(client, test_user, monkeypatch):
    _login(client, test_user)
    monkeypatch.setattr(
        "competitors.fetch_image", lambda url: (b"DATA", "image/jpeg")
    )
    payload = {
        "images": [
            {"url": "https://x.com/1/img.jpg", "filename": "img.jpg"},
            {"url": "https://x.com/2/img.jpg", "filename": "img.jpg"},
        ]
    }
    response = _json_post(client, "/competitors/download-images-zip", payload)
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.data))
    assert sorted(archive.namelist()) == ["img-2.jpg", "img.jpg"]


def test_download_zip_all_failures_returns_502(client, test_user, monkeypatch):
    _login(client, test_user)

    def _boom(url):
        raise image_harvest.ImageFetchError("nope")

    monkeypatch.setattr("competitors.fetch_image", _boom)
    payload = {"images": [{"url": "https://x.com/a.jpg", "filename": "a.jpg"}]}
    response = _json_post(client, "/competitors/download-images-zip", payload)
    assert response.status_code == 502
