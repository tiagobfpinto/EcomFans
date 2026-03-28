import io
import ipaddress
import os
import socket
import zipfile
from urllib.parse import urljoin, urlparse

import requests
from flask import Blueprint, jsonify, request, send_file
from bs4 import BeautifulSoup
from auth import login_required

scraper_bp = Blueprint("scraper", __name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
MAX_SCRAPE_IMAGES = 400
MAX_DOWNLOAD_URLS = 50
MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_IMAGE_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5
ALLOWED_PORTS = {None, 80, 443}


def is_image_url(url):
    """Check if a URL points to an image file."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    return any(path.endswith(ext) for ext in IMAGE_EXTENSIONS)


def _ensure_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Invalid URL scheme. Use http or https.")
    if parsed.username or parsed.password:
        raise ValueError("Credentials in URL are not allowed.")
    if parsed.port not in ALLOWED_PORTS:
        raise ValueError("Only ports 80 and 443 are allowed.")
    if not parsed.hostname:
        raise ValueError("URL hostname is required.")

    if parsed.hostname.strip().lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError("Localhost is not allowed.")

    try:
        addr_infos = socket.getaddrinfo(parsed.hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError("Could not resolve URL hostname.") from exc

    resolved_ips = {info[4][0] for info in addr_infos if info and info[4]}
    if not resolved_ips:
        raise ValueError("Could not resolve URL hostname.")

    for raw_ip in resolved_ips:
        ip_obj = ipaddress.ip_address(raw_ip)
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            raise ValueError("Private or local network targets are not allowed.")

    return url


def _safe_get_bytes(
    url: str,
    *,
    headers: dict,
    timeout: int,
    max_bytes: int,
    expected_prefix: str | None = None,
) -> tuple[bytes, str, str]:
    current_url = _ensure_public_url(url)

    for _ in range(MAX_REDIRECTS + 1):
        with requests.get(
            current_url,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        ) as resp:
            if 300 <= resp.status_code < 400:
                location = (resp.headers.get("Location") or "").strip()
                if not location:
                    raise ValueError("Invalid redirect response.")
                current_url = _ensure_public_url(urljoin(current_url, location))
                continue

            resp.raise_for_status()
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if expected_prefix and not content_type.startswith(expected_prefix):
                raise ValueError("Unexpected content type returned by the target URL.")

            data = bytearray()
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError("Response is too large.")
            return bytes(data), content_type, current_url

    raise ValueError("Too many redirects.")


def extract_images(url):
    """Fetch a page and extract all image URLs."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    html_bytes, _, final_url = _safe_get_bytes(
        url,
        headers=headers,
        timeout=15,
        max_bytes=MAX_HTML_BYTES,
        expected_prefix=None,
    )

    soup = BeautifulSoup(html_bytes.decode("utf-8", errors="ignore"), "html.parser")
    images = set()

    # Extract from <img> tags
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if src:
            absolute_url = urljoin(final_url, src)
            images.add(absolute_url)

    # Extract from <source> tags (inside <picture>)
    for source in soup.find_all("source"):
        srcset = source.get("srcset")
        if srcset:
            # srcset can have multiple URLs separated by commas
            for part in srcset.split(","):
                src = part.strip().split(" ")[0]
                if src:
                    absolute_url = urljoin(final_url, src)
                    images.add(absolute_url)

    # Extract from CSS background-image inline styles
    for tag in soup.find_all(style=True):
        style = tag["style"]
        if "url(" in style:
            start = style.index("url(") + 4
            end = style.index(")", start)
            bg_url = style[start:end].strip("'\"")
            absolute_url = urljoin(final_url, bg_url)
            images.add(absolute_url)

    # Filter to only known image extensions
    filtered = []
    for img in sorted(images):
        if not is_image_url(img):
            continue
        try:
            _ensure_public_url(img)
        except ValueError:
            continue
        filtered.append(img)
        if len(filtered) >= MAX_SCRAPE_IMAGES:
            break

    return filtered


@scraper_bp.route("/scrape", methods=["POST"])
@login_required
def scrape():
    """Scrape images from a given URL."""
    data = request.get_json()
    url = data.get("url", "").strip() if data else ""

    if not url:
        return jsonify({"error": "URL is required."}), 400

    # Basic URL normalization
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    elif parsed.scheme not in ("http", "https"):
        return jsonify({"error": "Invalid URL scheme. Use http or https."}), 400

    try:
        _ensure_public_url(url)
        images = extract_images(url)
        return jsonify({"images": images, "count": len(images), "url": url})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out. The site may be slow or unavailable."}), 408
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Could not connect to the site. Check the URL."}), 502
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch the page: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500


@scraper_bp.route("/download", methods=["POST"])
@login_required
def download():
    """Download selected images as a ZIP file."""
    data = request.get_json()
    urls = data.get("urls", []) if data else []

    if not urls:
        return jsonify({"error": "No images selected."}), 400
    if not isinstance(urls, list):
        return jsonify({"error": "Invalid URL list."}), 400
    if len(urls) > MAX_DOWNLOAD_URLS:
        return jsonify({"error": f"You can download at most {MAX_DOWNLOAD_URLS} images per request."}), 400

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    zip_buffer = io.BytesIO()
    written_files = 0

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, url in enumerate(urls):
            try:
                image_bytes, content_type, final_url = _safe_get_bytes(
                    str(url),
                    headers=headers,
                    timeout=10,
                    max_bytes=MAX_IMAGE_DOWNLOAD_BYTES,
                    expected_prefix="image/",
                )

                # Determine filename
                parsed = urlparse(final_url)
                filename = os.path.basename(parsed.path)
                if not filename or "." not in filename:
                    # Try to derive extension from content-type
                    content_type = content_type or ""
                    ext = ".png"
                    if "jpeg" in content_type or "jpg" in content_type:
                        ext = ".jpg"
                    elif "svg" in content_type:
                        ext = ".svg"
                    elif "webp" in content_type:
                        ext = ".webp"
                    elif "gif" in content_type:
                        ext = ".gif"
                    filename = f"image_{i + 1}{ext}"

                # Avoid duplicate filenames
                name, ext = os.path.splitext(filename)
                final_name = f"{name}_{i + 1}{ext}"

                zf.writestr(final_name, image_bytes)
                written_files += 1
            except Exception:
                continue  # Skip failed downloads

    if written_files == 0:
        return jsonify({"error": "No downloadable public images were found."}), 400

    zip_buffer.seek(0)

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name="scraped_images.zip",
    )
