(function () {
    "use strict";

    const root = document.querySelector(".social-downloader");
    if (!root || root.dataset.hasAccess !== "true") return;

    const urlsInput = document.getElementById("social-urls");
    const submitButton = document.getElementById("social-submit");
    const refreshButton = document.getElementById("social-refresh");
    const clearButton = document.getElementById("social-clear");
    const countNode = document.getElementById("social-link-count");
    const itemsNode = document.getElementById("social-items");
    const loadingNode = document.getElementById("social-loading");
    const emptyNode = document.getElementById("social-empty");
    const flashNode = document.getElementById("social-flash");
    const statActive = document.getElementById("social-stat-active");
    const statComplete = document.getElementById("social-stat-complete");
    const statFailed = document.getElementById("social-stat-failed");
    const maxUrls = Number(root.dataset.maxUrls || 10);

    let items = [];
    let pollTimer = null;

    function parseUrls() {
        return urlsInput.value
            .split(/\r?\n/)
            .map((value) => value.trim())
            .filter(Boolean);
    }

    function escapeHtml(value) {
        return String(value || "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function showFlash(message, type) {
        if (!message) {
            flashNode.hidden = true;
            return;
        }
        flashNode.textContent = message;
        flashNode.className = "social-flash" + (type ? " is-" + type : "");
        flashNode.hidden = false;
    }

    function updateComposer() {
        const count = parseUrls().length;
        countNode.textContent = count;
        submitButton.disabled = count === 0 || count > maxUrls;
        countNode.parentElement.classList.toggle("is-over", count > maxUrls);
    }

    function formatSize(bytes) {
        if (!bytes && bytes !== 0) return "";
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
        return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }

    function formatDate(value) {
        if (!value) return "";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "";
        return new Intl.DateTimeFormat(undefined, {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit"
        }).format(date);
    }

    function platformShort(platform) {
        if (platform === "instagram") return "IG";
        if (platform === "facebook") return "FB";
        if (platform === "tiktok") return "TK";
        return "DL";
    }

    function platformLabel(platform) {
        if (!platform) return "";
        return platform.charAt(0).toUpperCase() + platform.slice(1);
    }

    function statusLabel(item) {
        if (item.downloadable) return "Downloaded";
        if (item.status === "success") return "Done";
        if (item.status === "downloading") return "Downloading";
        if (item.status === "queued") return "Queued";
        if (item.status === "failed") return "Failed";
        return item.status || "Queued";
    }

    function itemClass(item) {
        const status = String(item.status || "queued").replace(/[^a-z0-9_-]/gi, "");
        const classes = [`social-item`, `social-item-${status}`];
        if (item.downloadable) classes.push("is-downloaded");
        return classes.join(" ");
    }

    function statusClass(item) {
        return item.downloadable ? "downloaded" : String(item.status || "queued").replace(/[^a-z0-9_-]/gi, "");
    }

    function actionButton(item) {
        const buttons = [];
        if (item.downloadable) {
            buttons.push(
                `<a class="social-item-action social-item-action-primary" href="${escapeHtml(item.download_url)}">Download MP4</a>`
            );
        }
        if (item.retryable) {
            buttons.push(
                `<button class="social-item-action" type="button" data-action="retry" data-id="${item.id}">Retry</button>`
            );
        }
        buttons.push(
            `<button class="social-item-action social-item-action-danger" type="button" data-action="delete" data-id="${item.id}">Delete</button>`
        );
        return buttons.join("");
    }

    function render() {
        const active = items.filter((item) => item.status === "queued" || item.status === "downloading").length;
        const complete = items.filter((item) => item.status === "success").length;
        const failed = items.filter((item) => item.status === "failed").length;

        statActive.textContent = active;
        statComplete.textContent = complete;
        statFailed.textContent = failed;
        emptyNode.hidden = items.length !== 0;
        clearButton.disabled = items.length === 0;

        itemsNode.innerHTML = items.map((item) => {
            const title = item.title || (item.status === "queued" ? "Waiting to start" : "Preparing video");
            const details = [
                platformLabel(item.platform),
                formatSize(item.file_size_bytes),
                formatDate(item.created_at)
            ].filter(Boolean).join(" - ");

            return `
                <article class="${itemClass(item)}">
                    <div class="social-platform-badge">${escapeHtml(platformShort(item.platform))}</div>
                    <div class="social-item-main">
                        <div class="social-item-title-row">
                            <h3 class="social-item-title" title="${escapeHtml(title)}">${escapeHtml(title)}</h3>
                            <span class="social-status social-status-${escapeHtml(statusClass(item))}">${escapeHtml(statusLabel(item))}</span>
                        </div>
                        <p class="social-item-meta" title="${escapeHtml(item.url)}">${escapeHtml(details || item.url)}</p>
                        ${item.error ? `<p class="social-item-error" title="${escapeHtml(item.error)}">${escapeHtml(item.error)}</p>` : ""}
                    </div>
                    <div class="social-item-actions">${actionButton(item)}</div>
                </article>
            `;
        }).join("");
    }

    async function request(url, options) {
        const response = await fetch(url, options || {});
        let payload = {};
        try {
            payload = await response.json();
        } catch (_) {
            payload = {};
        }
        if (!response.ok) {
            const error = new Error(payload.error || "Something went wrong. Please try again.");
            error.payload = payload;
            throw error;
        }
        return payload;
    }

    function schedulePoll() {
        window.clearTimeout(pollTimer);
        const hasActive = items.some((item) => item.status === "queued" || item.status === "downloading");
        pollTimer = window.setTimeout(() => loadItems(true), hasActive ? 2000 : 8000);
    }

    async function loadItems(silent) {
        if (!silent) refreshButton.disabled = true;
        try {
            const payload = await request("/social-downloader/items");
            items = payload.items || [];
            render();
            showFlash("", "");
        } catch (error) {
            showFlash(error.message, "error");
        } finally {
            loadingNode.hidden = true;
            refreshButton.disabled = false;
            schedulePoll();
        }
    }

    async function addItems() {
        const urls = parseUrls();
        if (!urls.length || urls.length > maxUrls) return;

        submitButton.disabled = true;
        submitButton.innerHTML = '<span class="social-button-spinner"></span>Adding';
        showFlash("", "");
        try {
            const payload = await request("/social-downloader/items", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ urls: urls })
            });
            urlsInput.value = "";
            updateComposer();
            showFlash(payload.message || "Downloads added to the queue.", "success");
            await loadItems(true);
        } catch (error) {
            showFlash(error.message, "error");
        } finally {
            submitButton.textContent = "Add to queue";
            updateComposer();
        }
    }

    async function retryItem(id, button) {
        button.disabled = true;
        try {
            await request(`/social-downloader/items/${id}/retry`, { method: "POST" });
            showFlash("Download added back to the queue.", "success");
            await loadItems(true);
        } catch (error) {
            showFlash(error.message, "error");
            button.disabled = false;
        }
    }

    async function deleteItem(id, button) {
        button.disabled = true;
        try {
            await request(`/social-downloader/items/${id}`, { method: "DELETE" });
            items = items.filter((item) => item.id !== Number(id));
            render();
            showFlash("Download removed.", "success");
            schedulePoll();
        } catch (error) {
            showFlash(error.message, "error");
            button.disabled = false;
        }
    }

    async function clearQueue() {
        if (!items.length) return;
        clearButton.disabled = true;
        try {
            const payload = await request("/social-downloader/items", { method: "DELETE" });
            items = [];
            render();
            showFlash(`Cleared ${payload.deleted || 0} download(s).`, "success");
            schedulePoll();
        } catch (error) {
            showFlash(error.message, "error");
            clearButton.disabled = false;
        }
    }

    urlsInput.addEventListener("input", updateComposer);
    submitButton.addEventListener("click", addItems);
    refreshButton.addEventListener("click", () => loadItems(false));
    clearButton.addEventListener("click", clearQueue);
    itemsNode.addEventListener("click", (event) => {
        const button = event.target.closest("[data-action]");
        if (!button) return;
        const id = button.dataset.id;
        if (button.dataset.action === "retry") retryItem(id, button);
        if (button.dataset.action === "delete") deleteItem(id, button);
    });

    updateComposer();
    loadItems(false);
})();
