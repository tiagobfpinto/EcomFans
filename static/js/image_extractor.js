(function () {
    "use strict";

    // ── Helpers ─────────────────────────────────────────────────────

    function flash(message, kind) {
        const el = document.getElementById("comp-flash");
        if (!el) return;
        el.textContent = message;
        el.className = "comp-flash " + (kind === "error" ? "is-error" : "is-success");
        el.hidden = false;
        clearTimeout(flash._t);
        flash._t = setTimeout(function () {
            el.hidden = true;
        }, 6000);
    }

    async function requestJSON(url, options) {
        const response = await fetch(url, options || {});
        let payload = {};
        try {
            payload = await response.json();
        } catch (err) {
            payload = {};
        }
        if (!response.ok) {
            const error = new Error(payload.error || "Something went wrong. Please try again.");
            error.status = response.status;
            throw error;
        }
        return payload;
    }

    function proxyUrl(rawUrl, opts) {
        opts = opts || {};
        let out = "/competitors/image-proxy?url=" + encodeURIComponent(rawUrl);
        if (opts.kind === "video") out += "&kind=video";
        if (opts.download) {
            out += "&download=1";
            if (opts.filename) out += "&name=" + encodeURIComponent(opts.filename);
        }
        return out;
    }

    function triggerBlobDownload(blob, filename) {
        const objectUrl = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = filename || "media";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(function () {
            URL.revokeObjectURL(objectUrl);
        }, 4000);
    }

    function copyText(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text);
        }
        return Promise.reject(new Error("clipboard unavailable"));
    }

    // ── Init ────────────────────────────────────────────────────────

    function init(root) {
        const maxHtmlMb = parseInt(root.dataset.maxHtmlMb || "6", 10);

        const sourceInput = document.getElementById("harvest-source");
        const baseInput = document.getElementById("harvest-base");
        const extractButton = document.getElementById("harvest-extract");
        const clearButton = document.getElementById("harvest-clear");

        const resultsSection = document.getElementById("harvest-results");
        const grid = document.getElementById("harvest-grid");
        const countLabel = document.getElementById("harvest-count");
        const emptyState = document.getElementById("harvest-empty");
        const downloadAllButton = document.getElementById("harvest-download-all");

        const filterControl = document.getElementById("harvest-filter");
        const filterButtons = filterControl.querySelectorAll(".harvest-filter-btn");
        const filterImagesBtn = document.getElementById("harvest-filter-images");
        const filterVideosBtn = document.getElementById("harvest-filter-videos");

        const modal = document.getElementById("harvest-modal");
        const modalImg = document.getElementById("harvest-modal-img");

        let items = [];
        let currentFilter = "all";

        function countText() {
            const imgs = items.filter(function (i) { return i.type === "image"; }).length;
            const vids = items.filter(function (i) { return i.type === "video"; }).length;
            const parts = [];
            if (imgs) parts.push(imgs + (imgs === 1 ? " image" : " images"));
            if (vids) parts.push(vids + (vids === 1 ? " video" : " videos"));
            return parts.join(" · ") || "No media";
        }

        // ── Image loading with graceful fallbacks ───────────────────

        function loadImageWithFallback(imgEl, item, dimsEl) {
            const chain = [item.url];
            if (item.source) chain.push(item.source);
            if (!item.is_data) chain.push(proxyUrl(item.url, {}));
            let attempt = 0;

            imgEl.addEventListener("error", function () {
                if (attempt >= chain.length) {
                    const card = imgEl.closest(".harvest-card");
                    if (card) card.classList.add("is-broken");
                    return;
                }
                imgEl.src = chain[attempt++];
            });
            imgEl.addEventListener("load", function () {
                if (imgEl.naturalWidth > 0) {
                    item._displaySrc = imgEl.currentSrc || imgEl.src;
                    if (dimsEl) {
                        dimsEl.textContent =
                            imgEl.naturalWidth + " × " + imgEl.naturalHeight;
                    }
                }
            });
            imgEl.src = chain[attempt++];
        }

        // ── Card builders ────────────────────────────────────────────

        function buildBody(item) {
            const body = document.createElement("div");
            body.className = "harvest-card-body";

            const name = document.createElement("span");
            name.className = "harvest-filename";
            name.textContent = item.filename || (item.type === "video" ? "video" : "image");
            name.title = item.url;

            const actions = document.createElement("div");
            actions.className = "harvest-card-actions";

            const downloadBtn = document.createElement("button");
            downloadBtn.type = "button";
            downloadBtn.className = "comp-btn comp-btn-primary";
            downloadBtn.dataset.action = "download";
            downloadBtn.innerHTML =
                '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
                'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
                '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
                '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>' +
                "Download";

            const copyBtn = document.createElement("button");
            copyBtn.type = "button";
            copyBtn.className = "comp-icon-btn harvest-copy";
            copyBtn.dataset.action = "copy";
            copyBtn.title = "Copy media URL";
            copyBtn.setAttribute("aria-label", "Copy media URL");
            copyBtn.innerHTML =
                '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
                'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
                '<rect x="9" y="9" width="13" height="13" rx="2"/>' +
                '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
            if (item.is_data) copyBtn.hidden = true;

            actions.appendChild(downloadBtn);
            actions.appendChild(copyBtn);
            body.appendChild(name);
            body.appendChild(actions);
            return body;
        }

        function renderImageCard(item, index) {
            const card = document.createElement("figure");
            card.className = "harvest-card";
            card.dataset.index = String(index);

            const thumb = document.createElement("div");
            thumb.className = "harvest-thumb";
            thumb.dataset.action = "open";
            thumb.title = "View full size";

            const img = document.createElement("img");
            img.loading = "lazy";
            img.alt = item.filename || "Extracted image";

            const dims = document.createElement("span");
            dims.className = "harvest-dims";

            thumb.appendChild(img);
            thumb.appendChild(dims);
            loadImageWithFallback(img, item, dims);

            card.appendChild(thumb);
            card.appendChild(buildBody(item));
            return card;
        }

        function renderVideoCard(item, index) {
            const card = document.createElement("figure");
            card.className = "harvest-card is-video";
            card.dataset.index = String(index);

            const thumb = document.createElement("div");
            thumb.className = "harvest-thumb";

            const badge = document.createElement("span");
            badge.className = "harvest-badge";
            badge.textContent = "VIDEO";

            const video = document.createElement("video");
            video.controls = true;
            video.preload = "metadata";
            video.playsInline = true;
            if (item.poster) video.poster = item.poster;

            const dims = document.createElement("span");
            dims.className = "harvest-dims";

            video.addEventListener("loadedmetadata", function () {
                if (video.videoWidth > 0) {
                    dims.textContent = video.videoWidth + " × " + video.videoHeight;
                }
            });
            video.addEventListener("error", function () {
                card.classList.add("is-broken");
            });
            video.src = item.url;

            thumb.appendChild(video);
            thumb.appendChild(badge);
            thumb.appendChild(dims);

            card.appendChild(thumb);
            card.appendChild(buildBody(item));
            return card;
        }

        function applyFilter() {
            grid.dataset.filter = currentFilter;
            filterButtons.forEach(function (button) {
                button.classList.toggle(
                    "is-active", button.dataset.filter === currentFilter
                );
            });
        }

        function render() {
            grid.innerHTML = "";
            if (!items.length) {
                resultsSection.hidden = true;
                emptyState.hidden = false;
                return;
            }
            emptyState.hidden = true;
            resultsSection.hidden = false;
            countLabel.textContent = countText();

            const imageCount = items.filter(function (i) { return i.type === "image"; }).length;
            const videoCount = items.filter(function (i) { return i.type === "video"; }).length;
            downloadAllButton.hidden = imageCount === 0;

            // The filter is only useful when both kinds are present.
            filterControl.hidden = !(imageCount && videoCount);
            filterImagesBtn.innerHTML =
                'Images <span class="harvest-filter-count">' + imageCount + "</span>";
            filterVideosBtn.innerHTML =
                'Videos <span class="harvest-filter-count">' + videoCount + "</span>";

            // Reset to "All" if the active filter no longer has any matches.
            if (
                filterControl.hidden ||
                (currentFilter === "image" && imageCount === 0) ||
                (currentFilter === "video" && videoCount === 0)
            ) {
                currentFilter = "all";
            }
            applyFilter();

            const fragment = document.createDocumentFragment();
            items.forEach(function (item, index) {
                fragment.appendChild(
                    item.type === "video"
                        ? renderVideoCard(item, index)
                        : renderImageCard(item, index)
                );
            });
            grid.appendChild(fragment);
        }

        // ── Extract ─────────────────────────────────────────────────

        async function extract() {
            const html = sourceInput.value;
            if (!html.trim()) {
                flash("Paste a page's source code first.", "error");
                sourceInput.focus();
                return;
            }
            if (html.length > maxHtmlMb * 1024 * 1024) {
                flash("That source is larger than " + maxHtmlMb + " MB.", "error");
                return;
            }

            extractButton.disabled = true;
            extractButton.classList.add("is-loading");
            try {
                const payload = await requestJSON("/competitors/extract-images", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        html: html,
                        base_url: baseInput.value.trim() || null,
                    }),
                });
                items = (payload.images || []).concat(payload.videos || []);
                render();
                if (items.length) {
                    flash("Found " + countText() + ".", "success");
                    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
                }
            } catch (err) {
                flash(err.message, "error");
            } finally {
                extractButton.disabled = false;
                extractButton.classList.remove("is-loading");
            }
        }

        // ── Downloads ────────────────────────────────────────────────

        async function downloadMedia(item, button) {
            if (button) button.disabled = true;
            try {
                if (item.is_data) {
                    const resp = await fetch(item.url);
                    triggerBlobDownload(await resp.blob(), item.filename);
                    return;
                }
                const kind = item.type === "video" ? "video" : undefined;
                const targets = [item.url];
                if (item.source) targets.push(item.source);
                for (let i = 0; i < targets.length; i++) {
                    try {
                        const resp = await fetch(
                            proxyUrl(targets[i], { download: true, filename: item.filename, kind: kind })
                        );
                        if (!resp.ok) continue;
                        triggerBlobDownload(await resp.blob(), item.filename);
                        return;
                    } catch (err) {
                        /* try next target */
                    }
                }
                flash("Could not download this file.", "error");
            } finally {
                if (button) button.disabled = false;
            }
        }

        async function downloadAllImages() {
            const imageItems = items.filter(function (i) { return i.type === "image"; });
            if (!imageItems.length) {
                flash("There are no images to bundle.", "error");
                return;
            }
            downloadAllButton.disabled = true;
            downloadAllButton.classList.add("is-loading");
            try {
                const response = await fetch("/competitors/download-images-zip", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        images: imageItems.map(function (item) {
                            return { url: item.url, filename: item.filename };
                        }),
                    }),
                });
                if (!response.ok) {
                    let message = "Could not build the ZIP file.";
                    try {
                        message = (await response.json()).error || message;
                    } catch (err) {
                        /* ignore */
                    }
                    throw new Error(message);
                }
                triggerBlobDownload(await response.blob(), "competitor-images.zip");
                flash("Your ZIP download is ready.", "success");
            } catch (err) {
                flash(err.message, "error");
            } finally {
                downloadAllButton.disabled = false;
                downloadAllButton.classList.remove("is-loading");
            }
        }

        // ── Modal (images only) ──────────────────────────────────────

        function openModal(item) {
            modalImg.src = item._displaySrc || item.url;
            modalImg.alt = item.filename || "Extracted image";
            modal.hidden = false;
            document.body.style.overflow = "hidden";
        }

        function closeModal() {
            modal.hidden = true;
            modalImg.removeAttribute("src");
            document.body.style.overflow = "";
        }

        // ── Events ───────────────────────────────────────────────────

        extractButton.addEventListener("click", extract);
        clearButton.addEventListener("click", function () {
            sourceInput.value = "";
            baseInput.value = "";
            items = [];
            currentFilter = "all";
            filterControl.hidden = true;
            resultsSection.hidden = true;
            emptyState.hidden = true;
            sourceInput.focus();
        });
        downloadAllButton.addEventListener("click", downloadAllImages);

        filterControl.addEventListener("click", function (event) {
            const button = event.target.closest(".harvest-filter-btn");
            if (!button) return;
            currentFilter = button.dataset.filter || "all";
            applyFilter();
        });

        grid.addEventListener("click", function (event) {
            const card = event.target.closest(".harvest-card");
            if (!card) return;
            const item = items[parseInt(card.dataset.index, 10)];
            if (!item) return;
            const actionEl = event.target.closest("[data-action]");
            const action = actionEl ? actionEl.dataset.action : null;

            if (action === "download") {
                downloadMedia(item, actionEl);
            } else if (action === "copy") {
                copyText(item.url)
                    .then(function () { flash("Media URL copied.", "success"); })
                    .catch(function () { flash("Could not copy the URL.", "error"); });
            } else if (action === "open" && item.type === "image") {
                openModal(item);
            }
        });

        modal.addEventListener("click", function (event) {
            if (event.target.closest("[data-modal-close]")) closeModal();
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !modal.hidden) closeModal();
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        const root = document.getElementById("image-extractor");
        if (root) init(root);
    });
})();
