(function () {
    "use strict";

    const root = document.getElementById("voiceover-tightener");
    if (!root) return;

    const presets = JSON.parse(document.getElementById("vt-preset-data").textContent || "{}");
    const input = document.getElementById("vt-audio-input");
    const dropzone = document.getElementById("vt-dropzone");
    const fileLabel = document.getElementById("vt-file-label");
    const fileMeta = document.getElementById("vt-file-meta");
    const processButton = document.getElementById("vt-process");
    const processLabel = processButton.querySelector(".vt-process-label");
    const flash = document.getElementById("vt-flash");
    const summary = document.getElementById("vt-settings-summary");
    const customBadge = document.getElementById("vt-custom-badge");
    const readyTitle = document.getElementById("vt-ready-title");
    const readyCopy = document.getElementById("vt-ready-copy");
    const itemsNode = document.getElementById("vt-items");
    const emptyNode = document.getElementById("vt-empty");
    const loadingNode = document.getElementById("vt-loading");
    const refreshButton = document.getElementById("vt-refresh");
    const loadMoreButton = document.getElementById("vt-load-more");
    const maxUploadBytes = Number(root.dataset.maxUploadMb || 100) * 1024 * 1024;

    let selectedPreset = "dynamic";
    let settings = Object.assign({}, presets.dynamic || {});
    let selectedFile = null;
    let items = [];
    let nextCursor = null;
    let activeCount = 0;
    let isSubmitting = false;
    let pollTimer = null;

    function csrfToken() {
        return document.querySelector('meta[name="csrf-token"]')?.content || "";
    }

    function showFlash(message, type) {
        flash.hidden = !message;
        flash.textContent = message || "";
        flash.className = "vt-flash" + (type ? ` is-${type}` : "");
        flash.setAttribute("role", type === "error" ? "alert" : "status");
    }

    function formatBytes(bytes) {
        if (bytes == null) return "—";
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    function formatDuration(ms) {
        if (ms == null) return "—";
        const totalSeconds = Math.max(0, Math.round(ms / 1000));
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        return `${minutes}:${String(seconds).padStart(2, "0")}`;
    }

    function formatRemoved(ms) {
        if (ms == null) return "—";
        return `${(ms / 1000).toFixed(1)}s`;
    }

    function formatDate(value) {
        if (!value) return "";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "";
        return new Intl.DateTimeFormat(undefined, {
            month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
        }).format(date);
    }

    function isCustomized() {
        return JSON.stringify(settings) !== JSON.stringify(presets[selectedPreset] || {});
    }

    function updateControlsFromSettings() {
        document.querySelectorAll("[data-setting]").forEach((control) => {
            const key = control.dataset.setting;
            if (control.type === "checkbox") control.checked = Boolean(settings[key]);
            else control.value = settings[key];
        });
        document.querySelectorAll(".vt-preset").forEach((button) => {
            const active = button.dataset.preset === selectedPreset;
            button.classList.toggle("is-active", active);
            button.setAttribute("aria-checked", String(active));
        });
        customBadge.hidden = !isCustomized();
        summary.replaceChildren(
            summaryChip(`${settings.min_pause_ms}ms+ pauses`),
            summaryChip(`${settings.within_sentence_gap_ms}ms phrase gap`),
            summaryChip(`${settings.sentence_gap_ms}ms sentence gap`),
            summaryChip(`${settings.overlap_ms}ms overlap`),
            summaryChip(settings.breath_handling + " breaths")
        );
    }

    function summaryChip(text) {
        const chip = document.createElement("span");
        chip.textContent = text;
        return chip;
    }

    function selectPreset(name) {
        if (!presets[name]) return;
        selectedPreset = name;
        settings = Object.assign({}, presets[name]);
        updateControlsFromSettings();
        updateComposer();
    }

    function updateComposer() {
        const active = activeCount > 0;
        processButton.disabled = !selectedFile || active || isSubmitting;
        input.disabled = active || isSubmitting;
        dropzone.classList.toggle("is-disabled", active || isSubmitting);
        if (active) {
            readyTitle.textContent = "A voiceover is already processing";
            readyCopy.textContent = "You can add another as soon as it finishes.";
        } else if (selectedFile) {
            readyTitle.textContent = selectedFile.name;
            readyCopy.textContent = `${formatBytes(selectedFile.size)} · ${selectedPreset[0].toUpperCase() + selectedPreset.slice(1)}${isCustomized() ? " · Customized" : ""}`;
        } else {
            readyTitle.textContent = "Choose an MP3 to continue";
            readyCopy.textContent = "Dynamic is selected by default.";
        }
    }

    function setFile(file) {
        if (!file) return;
        const isMp3 = /\.mp3$/i.test(file.name || "") && (!file.type || file.type === "audio/mpeg" || file.type === "audio/mp3");
        if (!isMp3) {
            showFlash("Only MP3 voiceovers are supported.", "error");
            return;
        }
        if (file.size <= 0 || file.size > maxUploadBytes) {
            showFlash(`Choose a non-empty MP3 up to ${root.dataset.maxUploadMb} MB.`, "error");
            return;
        }
        selectedFile = file;
        fileLabel.textContent = file.name;
        fileMeta.textContent = `${formatBytes(file.size)} · Ready to process`;
        showFlash("", "");
        updateComposer();
    }

    async function request(url, options) {
        const config = Object.assign({}, options || {});
        const method = (config.method || "GET").toUpperCase();
        config.headers = Object.assign({}, config.headers || {});
        if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
            config.headers["X-CSRF-Token"] = csrfToken();
        }
        const response = await fetch(url, config);
        let payload = {};
        try { payload = await response.json(); } catch (_) { payload = {}; }
        if (!response.ok) throw new Error(payload.error || "Something went wrong. Please try again.");
        return payload;
    }

    async function submitVoiceover() {
        if (!selectedFile || processButton.disabled) return;
        isSubmitting = true;
        processButton.classList.add("is-loading");
        processLabel.textContent = "Uploading…";
        updateComposer();
        showFlash("", "");
        try {
            const form = new FormData();
            form.append("audio", selectedFile);
            form.append("preset", selectedPreset);
            form.append("settings", JSON.stringify(settings));
            const payload = await request("/voiceover-tightener/items", { method: "POST", body: form });
            selectedFile = null;
            input.value = "";
            fileLabel.textContent = "Drop an MP3 here or choose a file";
            fileMeta.textContent = "The original stays intact and remains available in your history.";
            showFlash("Voiceover added to the processing queue.", "success");
            if (payload.item) items = [payload.item, ...items.filter((item) => item.id !== payload.item.id)];
            activeCount = 1;
            renderItems();
            schedulePoll();
        } catch (error) {
            showFlash(error.message, "error");
        } finally {
            isSubmitting = false;
            processButton.classList.remove("is-loading");
            processLabel.textContent = "Tighten voiceover";
            updateComposer();
        }
    }

    function statusLabel(status) {
        if (status === "completed") return "Completed";
        if (status === "processing") return "Processing";
        if (status === "failed") return "Failed";
        return "Queued";
    }

    function metric(value, label) {
        const node = document.createElement("div");
        node.className = "vt-metric";
        const strong = document.createElement("strong");
        strong.textContent = value;
        const span = document.createElement("span");
        span.textContent = label;
        node.append(strong, span);
        return node;
    }

    function player(label, url) {
        const wrapper = document.createElement("div");
        wrapper.className = "vt-player";
        const title = document.createElement("span");
        title.textContent = label;
        const audio = document.createElement("audio");
        audio.controls = true;
        audio.preload = "metadata";
        audio.src = url;
        wrapper.append(title, audio);
        return wrapper;
    }

    function action(label, className, handler) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `vt-action ${className || ""}`.trim();
        button.textContent = label;
        button.addEventListener("click", () => handler(button));
        return button;
    }

    function renderItem(item) {
        const card = document.createElement("article");
        card.className = "vt-item";
        card.dataset.id = item.id;

        const head = document.createElement("div");
        head.className = "vt-item-head";
        const title = document.createElement("div");
        title.className = "vt-item-title";
        const h3 = document.createElement("h3");
        h3.textContent = item.original_filename || "Voiceover";
        h3.title = h3.textContent;
        const meta = document.createElement("p");
        meta.textContent = `${formatBytes(item.original_file_size_bytes)} · ${formatDate(item.created_at)}`;
        title.append(h3, meta);
        const badge = document.createElement("span");
        badge.className = `vt-status vt-status-${item.status}`;
        badge.textContent = statusLabel(item.status);
        head.append(title, badge);
        card.appendChild(head);

        const body = document.createElement("div");
        body.className = "vt-item-body";
        if (item.status === "completed") {
            const metrics = document.createElement("div");
            metrics.className = "vt-metrics";
            metrics.append(
                metric(formatDuration(item.original_duration_ms), "Original"),
                metric(formatDuration(item.output_duration_ms), "Tightened"),
                metric(formatRemoved(item.removed_duration_ms), "Removed"),
                metric(String(item.pauses_shortened ?? 0), "Pauses shortened"),
                metric(String(item.overlaps_applied ?? 0), "Overlaps")
            );
            body.appendChild(metrics);
        }
        const players = document.createElement("div");
        players.className = "vt-players";
        if (item.original_audio_url) players.appendChild(player("Original", item.original_audio_url));
        if (item.output_audio_url) players.appendChild(player("Tightened", item.output_audio_url));
        body.appendChild(players);

        (item.warnings || []).forEach((message) => {
            const warning = document.createElement("p");
            warning.className = "vt-warning";
            warning.textContent = message;
            body.appendChild(warning);
        });
        if (item.error) {
            const error = document.createElement("p");
            error.className = "vt-error-copy";
            error.textContent = item.error;
            body.appendChild(error);
        }
        if (item.status === "queued" || item.status === "processing") {
            const progress = document.createElement("div");
            progress.className = "vt-progress";
            progress.setAttribute("aria-hidden", "true");
            body.appendChild(progress);
        }

        const footer = document.createElement("div");
        footer.className = "vt-item-footer";
        const parameters = document.createElement("span");
        parameters.className = "vt-parameter-line";
        const itemSettings = item.settings || {};
        parameters.textContent = `${(item.preset || "dynamic").replace(/^./, (c) => c.toUpperCase())} · ${itemSettings.min_pause_ms ?? "—"}ms pause threshold · ${itemSettings.overlap_ms ?? "—"}ms overlap`;
        const actions = document.createElement("div");
        actions.className = "vt-actions";
        if (item.download_url) {
            const link = document.createElement("a");
            link.className = "vt-action vt-action-primary";
            link.href = item.download_url;
            link.textContent = "Download MP3";
            actions.appendChild(link);
        }
        if (item.retryable) actions.appendChild(action("Retry", "", (button) => retryItem(item.id, button)));
        if (item.deletable) actions.appendChild(action("Delete", "vt-action-danger", (button) => deleteItem(item.id, button)));
        footer.append(parameters, actions);
        body.appendChild(footer);
        card.appendChild(body);
        return card;
    }

    function renderItems() {
        itemsNode.replaceChildren(...items.map(renderItem));
        loadingNode.hidden = true;
        emptyNode.hidden = items.length > 0;
        loadMoreButton.hidden = !nextCursor;
        updateComposer();
    }

    async function loadItems(reset, silent) {
        if (!silent) refreshButton.disabled = true;
        try {
            const query = !reset && nextCursor ? `?cursor=${encodeURIComponent(nextCursor)}` : "";
            const payload = await request(`/voiceover-tightener/items${query}`);
            if (reset) items = payload.items || [];
            else items = [...items, ...(payload.items || [])];
            nextCursor = payload.next_cursor || null;
            activeCount = Number(payload.active_count || 0);
            renderItems();
            schedulePoll();
        } catch (error) {
            showFlash(error.message, "error");
            loadingNode.hidden = true;
        } finally {
            refreshButton.disabled = false;
        }
    }

    function schedulePoll() {
        window.clearTimeout(pollTimer);
        if (activeCount > 0) pollTimer = window.setTimeout(() => loadItems(true, true), 2000);
    }

    async function retryItem(id, button) {
        button.disabled = true;
        try {
            const payload = await request(`/voiceover-tightener/items/${id}/retry`, { method: "POST" });
            items = items.map((item) => item.id === id ? payload.item : item);
            activeCount = 1;
            renderItems();
            showFlash("Voiceover added back to the queue.", "success");
            schedulePoll();
        } catch (error) {
            showFlash(error.message, "error");
            button.disabled = false;
        }
    }

    async function deleteItem(id, button) {
        button.disabled = true;
        try {
            await request(`/voiceover-tightener/items/${id}`, { method: "DELETE" });
            items = items.filter((item) => item.id !== id);
            renderItems();
            showFlash("Voiceover and stored audio deleted.", "success");
        } catch (error) {
            showFlash(error.message, "error");
            button.disabled = false;
        }
    }

    document.querySelectorAll(".vt-preset").forEach((button) => {
        button.addEventListener("click", () => selectPreset(button.dataset.preset));
    });
    document.querySelectorAll("[data-setting]").forEach((control) => {
        control.addEventListener("change", () => {
            const key = control.dataset.setting;
            settings[key] = control.type === "checkbox" ? control.checked :
                (control.type === "number" ? Number(control.value) : control.value);
            updateControlsFromSettings();
            updateComposer();
        });
    });
    input.addEventListener("change", () => setFile(input.files?.[0]));
    ["dragenter", "dragover"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
        event.preventDefault();
        if (!input.disabled) dropzone.classList.add("is-dragging");
    }));
    ["dragleave", "drop"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropzone.classList.remove("is-dragging");
    }));
    dropzone.addEventListener("drop", (event) => {
        if (!input.disabled) setFile(event.dataTransfer?.files?.[0]);
    });
    processButton.addEventListener("click", submitVoiceover);
    refreshButton.addEventListener("click", () => loadItems(true, false));
    loadMoreButton.addEventListener("click", () => loadItems(false, false));

    updateControlsFromSettings();
    updateComposer();
    loadItems(true, false);
})();
