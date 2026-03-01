import io
import os
import zipfile
import requests
from urllib.parse import urljoin, urlparse
from flask import Blueprint, request, jsonify, send_file, session
from bs4 import BeautifulSoup
from auth import login_required

scraper_bp = Blueprint("scraper", __name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}


def is_image_url(url):
    """Check if a URL points to an image file."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    return any(path.endswith(ext) for ext in IMAGE_EXTENSIONS)


def extract_images(url):
    """Fetch a page and extract all image URLs."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    images = set()

    # Extract from <img> tags
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if src:
            absolute_url = urljoin(url, src)
            images.add(absolute_url)

    # Extract from <source> tags (inside <picture>)
    for source in soup.find_all("source"):
        srcset = source.get("srcset")
        if srcset:
            # srcset can have multiple URLs separated by commas
            for part in srcset.split(","):
                src = part.strip().split(" ")[0]
                if src:
                    absolute_url = urljoin(url, src)
                    images.add(absolute_url)

    # Extract from CSS background-image inline styles
    for tag in soup.find_all(style=True):
        style = tag["style"]
        if "url(" in style:
            start = style.index("url(") + 4
            end = style.index(")", start)
            bg_url = style[start:end].strip("'\"")
            absolute_url = urljoin(url, bg_url)
            images.add(absolute_url)

    # Filter to only known image extensions
    filtered = [img for img in sorted(images) if is_image_url(img)]

    return filtered


@scraper_bp.route("/scrape", methods=["POST"])
@login_required
def scrape():
    """Scrape images from a given URL."""
    data = request.get_json()
    url = data.get("url", "").strip() if data else ""

    if not url:
        return jsonify({"error": "URL is required."}), 400

    # Basic URL validation
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    elif parsed.scheme not in ("http", "https"):
        return jsonify({"error": "Invalid URL scheme. Use http or https."}), 400

    try:
        images = extract_images(url)
        return jsonify({"images": images, "count": len(images), "url": url})
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

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, url in enumerate(urls):
            try:
                resp = requests.get(url, headers=headers, timeout=10)
                resp.raise_for_status()

                # Determine filename
                parsed = urlparse(url)
                filename = os.path.basename(parsed.path)
                if not filename or "." not in filename:
                    # Try to derive extension from content-type
                    content_type = resp.headers.get("Content-Type", "")
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

                zf.writestr(final_name, resp.content)
            except Exception:
                continue  # Skip failed downloads

    zip_buffer.seek(0)

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name="scraped_images.zip",
    )
