(function () {
    "use strict";

    // ── Shared helpers ──────────────────────────────────────────────

    function esc(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

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
            const message = payload.error === "no_openai_key"
                ? "An OpenAI API key is required for this action."
                : payload.error || "Something went wrong. Please try again.";
            const error = new Error(message);
            error.status = response.status;
            throw error;
        }
        return payload;
    }

    function copyText(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text);
        }
        return new Promise(function (resolve, reject) {
            const area = document.createElement("textarea");
            area.value = text;
            area.style.position = "fixed";
            area.style.opacity = "0";
            document.body.appendChild(area);
            area.select();
            try {
                document.execCommand("copy");
                resolve();
            } catch (err) {
                reject(err);
            } finally {
                area.remove();
            }
        });
    }

    function formatDate(iso) {
        if (!iso) return "";
        const date = new Date(iso);
        if (isNaN(date.getTime())) return "";
        return date.toLocaleDateString(undefined, {
            year: "numeric",
            month: "short",
            day: "numeric",
        });
    }

    // ── List page ───────────────────────────────────────────────────

    function initListPage(root) {
        const form = document.getElementById("comp-add-form");
        const nameInput = document.getElementById("comp-name");
        const productSelect = document.getElementById("comp-product");
        const submitButton = document.getElementById("comp-add-submit");

        form.addEventListener("submit", async function (event) {
            event.preventDefault();
            const name = nameInput.value.trim();
            if (!name) return;

            submitButton.disabled = true;
            try {
                const payload = await requestJSON("/competitors", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        name: name,
                        product_id: productSelect.value || null,
                    }),
                });
                window.location.href = "/competitors/" + payload.competitor.id;
            } catch (err) {
                flash(err.message, "error");
                submitButton.disabled = false;
            }
        });

        root.addEventListener("click", async function (event) {
            const button = event.target.closest(".comp-delete-competitor");
            if (!button) return;
            event.preventDefault();
            event.stopPropagation();

            const name = button.dataset.competitorName || "this competitor";
            if (!window.confirm('Delete "' + name + '" and all of their ads? This cannot be undone.')) {
                return;
            }
            try {
                await requestJSON("/competitors/" + button.dataset.competitorId, {
                    method: "DELETE",
                });
                window.location.reload();
            } catch (err) {
                flash(err.message, "error");
            }
        });
    }

    // ── Detail page ─────────────────────────────────────────────────

    function initDetailPage(root) {
        const competitorId = root.dataset.competitorId;
        const hasOpenAIKey = root.dataset.hasOpenaiKey === "true";
        const maxUploadMb = parseInt(root.dataset.maxUploadMb || "25", 10);
        const maxUploadBytes = maxUploadMb * 1024 * 1024;

        const grid = document.getElementById("comp-ads-grid");
        const emptyState = document.getElementById("comp-ads-empty");
        const countChip = document.getElementById("comp-detail-count");
        const dropzone = document.getElementById("comp-dropzone");
        const fileInput = document.getElementById("comp-file-input");
        const browseButton = document.getElementById("comp-browse");
        const uploadList = document.getElementById("comp-upload-list");
        const productSelect = document.getElementById("comp-detail-product");

        const modal = document.getElementById("comp-modal");
        const modalVideo = document.getElementById("comp-modal-video");
        const modalTitle = document.getElementById("comp-modal-title");
        const modalSub = document.getElementById("comp-modal-sub");
        const modalTranscript = document.getElementById("comp-modal-transcript");
        const modalAnalysis = document.getElementById("comp-modal-analysis");
        const modalAnalyzeButton = document.getElementById("comp-modal-analyze");
        const modalCopyButton = document.getElementById("comp-copy-transcript");

        let ads = [];
        try {
            ads = JSON.parse(document.getElementById("comp-bootstrap").textContent) || [];
        } catch (err) {
            ads = [];
        }

        let openAdId = null;
        let pollTimer = null;
        let lastRenderKey = "";

        function findAd(adId) {
            return ads.find(function (ad) {
                return ad.id === adId;
            });
        }

        function isBusy(ad) {
            return (
                ad.transcript_status === "queued" ||
                ad.transcript_status === "processing" ||
                ad.analysis_status === "queued" ||
                ad.analysis_status === "processing"
            );
        }

        function transcriptReady(ad) {
            return ad.transcript_status === "completed" && (ad.transcript || "").trim();
        }

        function statusBadge(ad) {
            if (ad.transcript_status === "queued" || ad.transcript_status === "processing") {
                return '<span class="comp-ad-status is-processing"><span class="comp-spinner"></span>Transcribing</span>';
            }
            if (ad.transcript_status === "failed") {
                return '<span class="comp-ad-status is-failed">Transcript failed</span>';
            }
            if (ad.analysis_status === "queued" || ad.analysis_status === "processing") {
                return '<span class="comp-ad-status is-processing"><span class="comp-spinner"></span>Analysing</span>';
            }
            if (ad.analysis_status === "failed") {
                return '<span class="comp-ad-status is-failed">Analysis failed</span>';
            }
            if (transcriptReady(ad)) {
                return '<span class="comp-ad-status is-ready">Script ready</span>';
            }
            return "";
        }

        function ratingBadge(ad) {
            if (ad.analysis_status === "completed" && ad.analysis && ad.analysis.rating) {
                return '<span class="comp-ad-rating">' + esc(ad.analysis.rating) + "/10</span>";
            }
            return "";
        }

        function renderCard(ad) {
            const analysing =
                ad.analysis_status === "queued" || ad.analysis_status === "processing";
            const canCopy = transcriptReady(ad);
            const canAnalyse = canCopy && !analysing;
            const retry = ad.transcript_status === "failed";

            return (
                '<article class="comp-ad-card" data-ad-id="' + ad.id + '">' +
                '<div class="comp-ad-media" data-action="open" title="Open ad">' +
                '<video preload="metadata" muted playsinline src="' + esc(ad.video_url) + '"></video>' +
                statusBadge(ad) +
                ratingBadge(ad) +
                '<span class="comp-ad-play">' +
                '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><polygon points="6 3 20 12 6 21 6 3"/></svg>' +
                "</span>" +
                "</div>" +
                '<div class="comp-ad-body">' +
                '<span class="comp-ad-filename" title="' + esc(ad.original_filename) + '">' +
                esc(ad.original_filename || "Untitled ad") +
                "</span>" +
                '<span class="comp-ad-date">' + esc(formatDate(ad.created_at)) + "</span>" +
                '<div class="comp-ad-actions">' +
                (retry
                    ? '<button class="comp-btn comp-btn-ghost" type="button" data-action="retry">Retry transcript</button>'
                    : '<button class="comp-btn comp-btn-ghost" type="button" data-action="copy"' +
                      (canCopy ? "" : " disabled") +
                      ">Copy script</button>" +
                      '<button class="comp-btn comp-btn-primary" type="button" data-action="analyze"' +
                      (canAnalyse ? "" : " disabled") +
                      ">" +
                      (analysing ? "Analysing…" : "AI analyse") +
                      "</button>") +
                '<button class="comp-icon-btn" type="button" data-action="delete" title="Delete ad" aria-label="Delete ad">' +
                '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
                '<polyline points="3 6 5 6 21 6"/>' +
                '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>' +
                "</svg>" +
                "</button>" +
                "</div>" +
                "</div>" +
                "</article>"
            );
        }

        function render() {
            const key = JSON.stringify(ads);
            if (key === lastRenderKey) return;
            lastRenderKey = key;

            emptyState.hidden = ads.length > 0;
            grid.innerHTML = ads.map(renderCard).join("");
            if (countChip) {
                countChip.textContent = ads.length + (ads.length === 1 ? " ad" : " ads");
            }
            if (openAdId != null) {
                const ad = findAd(openAdId);
                if (ad) {
                    fillModal(ad, { keepVideo: true });
                }
            }
            schedulePolling();
        }

        // ── Polling ─────────────────────────────────────────────────

        function schedulePolling() {
            const busy = ads.some(isBusy);
            if (busy && !pollTimer) {
                pollTimer = setInterval(refreshAds, 4000);
            } else if (!busy && pollTimer) {
                clearInterval(pollTimer);
                pollTimer = null;
            }
        }

        async function refreshAds() {
            try {
                const payload = await requestJSON(
                    "/competitors/" + competitorId + "/ads"
                );
                ads = payload.ads || [];
                render();
            } catch (err) {
                /* transient polling errors are ignored */
            }
        }

        // ── Upload ──────────────────────────────────────────────────

        function addUploadRow(name) {
            uploadList.hidden = false;
            const row = document.createElement("div");
            row.className = "comp-upload-item";
            row.innerHTML =
                '<span class="name">' + esc(name) + "</span>" +
                '<span class="state">Uploading…</span>';
            uploadList.appendChild(row);
            return row;
        }

        async function uploadFile(file) {
            const row = addUploadRow(file.name);
            const state = row.querySelector(".state");

            if (!file.type.startsWith("video/") && !file.type.startsWith("audio/")) {
                row.classList.add("is-error");
                state.textContent = "Not a video file";
                return;
            }
            if (file.size > maxUploadBytes) {
                row.classList.add("is-error");
                state.textContent = "Larger than " + maxUploadMb + " MB";
                return;
            }

            const formData = new FormData();
            formData.append("video", file, file.name);

            try {
                const payload = await requestJSON(
                    "/competitors/" + competitorId + "/ads",
                    { method: "POST", body: formData }
                );
                ads.unshift(payload.ad);
                row.classList.add("is-done");
                state.textContent = "Queued for transcription";
                render();
            } catch (err) {
                row.classList.add("is-error");
                state.textContent = err.message;
            }
            setTimeout(function () {
                row.remove();
                if (!uploadList.children.length) uploadList.hidden = true;
            }, 5000);
        }

        function handleFiles(fileList) {
            if (!hasOpenAIKey) {
                flash("An OpenAI API key is required before uploading ads.", "error");
                return;
            }
            Array.prototype.forEach.call(fileList, uploadFile);
        }

        browseButton.addEventListener("click", function () {
            fileInput.click();
        });
        fileInput.addEventListener("change", function () {
            handleFiles(fileInput.files);
            fileInput.value = "";
        });
        ["dragenter", "dragover"].forEach(function (type) {
            dropzone.addEventListener(type, function (event) {
                event.preventDefault();
                dropzone.classList.add("is-dragover");
            });
        });
        ["dragleave", "drop"].forEach(function (type) {
            dropzone.addEventListener(type, function (event) {
                event.preventDefault();
                dropzone.classList.remove("is-dragover");
            });
        });
        dropzone.addEventListener("drop", function (event) {
            if (event.dataTransfer && event.dataTransfer.files.length) {
                handleFiles(event.dataTransfer.files);
            }
        });

        // ── Product assignment ──────────────────────────────────────

        productSelect.addEventListener("change", async function () {
            try {
                await requestJSON("/competitors/" + competitorId, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ product_id: productSelect.value || null }),
                });
                flash("Product assignment saved.", "success");
            } catch (err) {
                flash(err.message, "error");
            }
        });

        // ── Ad actions ──────────────────────────────────────────────

        async function analyzeAd(adId, button) {
            if (button) button.disabled = true;
            try {
                const payload = await requestJSON(
                    "/competitors/ads/" + adId + "/analyze",
                    { method: "POST" }
                );
                replaceAd(payload.ad);
            } catch (err) {
                flash(err.message, "error");
                if (button) button.disabled = false;
            }
        }

        function replaceAd(updated) {
            const index = ads.findIndex(function (ad) {
                return ad.id === updated.id;
            });
            if (index !== -1) {
                ads[index] = updated;
            }
            render();
        }

        grid.addEventListener("click", async function (event) {
            const card = event.target.closest(".comp-ad-card");
            if (!card) return;
            const adId = parseInt(card.dataset.adId, 10);
            const ad = findAd(adId);
            if (!ad) return;

            const actionEl = event.target.closest("[data-action]");
            const action = actionEl ? actionEl.dataset.action : null;

            if (action === "open") {
                openModal(ad);
            } else if (action === "copy") {
                try {
                    await copyText(ad.transcript || "");
                    flash("Script copied to clipboard.", "success");
                } catch (err) {
                    flash("Could not copy to clipboard.", "error");
                }
            } else if (action === "analyze") {
                analyzeAd(adId, actionEl);
            } else if (action === "retry") {
                try {
                    const payload = await requestJSON(
                        "/competitors/ads/" + adId + "/transcribe",
                        { method: "POST" }
                    );
                    replaceAd(payload.ad);
                } catch (err) {
                    flash(err.message, "error");
                }
            } else if (action === "delete") {
                if (!window.confirm("Delete this ad and its transcript?")) return;
                try {
                    await requestJSON("/competitors/ads/" + adId, {
                        method: "DELETE",
                    });
                    ads = ads.filter(function (item) {
                        return item.id !== adId;
                    });
                    if (openAdId === adId) closeModal();
                    render();
                } catch (err) {
                    flash(err.message, "error");
                }
            }
        });

        // ── Modal ───────────────────────────────────────────────────

        function fillModal(ad, options) {
            const keepVideo = options && options.keepVideo;
            modalTitle.textContent = ad.original_filename || "Untitled ad";
            modalSub.textContent = formatDate(ad.created_at);
            if (!keepVideo && ad.video_url) {
                modalVideo.src = ad.video_url;
            }

            if (transcriptReady(ad)) {
                modalTranscript.classList.remove("is-placeholder");
                modalTranscript.textContent = ad.transcript;
                modalCopyButton.disabled = false;
            } else {
                modalTranscript.classList.add("is-placeholder");
                modalCopyButton.disabled = true;
                if (ad.transcript_status === "failed") {
                    modalTranscript.textContent =
                        "Transcription failed: " + (ad.transcript_error || "unknown error");
                } else {
                    modalTranscript.textContent = "Transcribing this ad in the background…";
                }
            }

            renderModalAnalysis(ad);
        }

        function renderModalAnalysis(ad) {
            const analysing =
                ad.analysis_status === "queued" || ad.analysis_status === "processing";
            modalAnalyzeButton.disabled = !transcriptReady(ad) || analysing;
            modalAnalyzeButton.lastChild.textContent = analysing
                ? " Analysing…"
                : ad.analysis_status === "completed"
                    ? " Re-analyse"
                    : " AI analyse";

            if (ad.analysis_status === "completed" && ad.analysis) {
                const analysis = ad.analysis;
                let html = "";
                if (analysis.rating) {
                    const percent = Math.max(0, Math.min(100, analysis.rating * 10));
                    html +=
                        '<div class="comp-rating-row">' +
                        '<span class="comp-rating-badge">' + esc(analysis.rating) +
                        "<small>/10</small></span>" +
                        '<span class="comp-rating-bar"><span style="width:' + percent + '%"></span></span>' +
                        "</div>";
                }
                if (analysis.overview) {
                    html += '<p class="comp-analysis-overview">' + esc(analysis.overview) + "</p>";
                }
                const strengths = analysis.strengths || [];
                const weaknesses = analysis.weaknesses || [];
                if (strengths.length || weaknesses.length) {
                    html += '<div class="comp-analysis-lists">';
                    html +=
                        '<div class="pros"><h5>Strengths</h5><ul>' +
                        strengths.map(function (item) { return "<li>" + esc(item) + "</li>"; }).join("") +
                        "</ul></div>";
                    html +=
                        '<div class="cons"><h5>Weaknesses</h5><ul>' +
                        weaknesses.map(function (item) { return "<li>" + esc(item) + "</li>"; }).join("") +
                        "</ul></div>";
                    html += "</div>";
                }
                modalAnalysis.innerHTML = html;
            } else if (analysing) {
                modalAnalysis.innerHTML =
                    '<span class="is-placeholder">Analysing this ad with AI…</span>';
            } else if (ad.analysis_status === "failed") {
                modalAnalysis.innerHTML =
                    '<span class="comp-analysis-error">' +
                    esc(ad.analysis_error || "Analysis failed.") +
                    "</span>";
            } else {
                modalAnalysis.innerHTML =
                    '<span class="is-placeholder">Run an AI analysis to get an overview and rating of this ad.</span>';
            }
        }

        function openModal(ad) {
            openAdId = ad.id;
            fillModal(ad, { keepVideo: false });
            modal.hidden = false;
            document.body.style.overflow = "hidden";
        }

        function closeModal() {
            openAdId = null;
            modal.hidden = true;
            modalVideo.pause();
            modalVideo.removeAttribute("src");
            modalVideo.load();
            document.body.style.overflow = "";
        }

        modal.addEventListener("click", function (event) {
            if (event.target.closest("[data-modal-close]")) closeModal();
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !modal.hidden) closeModal();
        });

        modalCopyButton.addEventListener("click", async function () {
            const ad = findAd(openAdId);
            if (!ad || !transcriptReady(ad)) return;
            try {
                await copyText(ad.transcript || "");
                flash("Script copied to clipboard.", "success");
            } catch (err) {
                flash("Could not copy to clipboard.", "error");
            }
        });

        modalAnalyzeButton.addEventListener("click", function () {
            if (openAdId != null) analyzeAd(openAdId, modalAnalyzeButton);
        });

        render();
    }

    // ── Bootstrap ───────────────────────────────────────────────────

    document.addEventListener("DOMContentLoaded", function () {
        const listRoot = document.getElementById("competitors-page");
        const detailRoot = document.getElementById("competitor-detail");
        if (listRoot) initListPage(listRoot);
        if (detailRoot) initDetailPage(detailRoot);
    });
})();
