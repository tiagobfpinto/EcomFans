(function () {
    "use strict";

    const root = document.getElementById("funnel-editor");
    if (!root) return;

    const bootstrap = JSON.parse(document.getElementById("fn-editor-bootstrap").textContent || "{}");
    const page = bootstrap.page || {};
    const defaultTemplate = bootstrap.default_template || "";
    const funnelId = Number(root.dataset.funnelId);
    const pageId = Number(root.dataset.pageId);

    const titleInput = document.getElementById("fn-editor-title");
    const typeInput = document.getElementById("fn-editor-type");
    const slugInput = document.getElementById("fn-editor-slug");
    const statusInput = document.getElementById("fn-editor-status");
    const htmlSource = document.getElementById("fn-html-source");
    const visualFrame = document.getElementById("fn-visual-frame");
    const visualWorkspace = document.getElementById("fn-visual-workspace");
    const htmlWorkspace = document.getElementById("fn-html-workspace");
    const browserFrame = document.getElementById("fn-browser-frame");
    const browserUrl = document.getElementById("fn-browser-url");
    const publicLink = document.getElementById("fn-public-link");
    const publishNote = document.getElementById("fn-publish-note");
    const saveButton = document.getElementById("fn-save-page");
    const previewButton = document.getElementById("fn-preview-page");
    const saveState = document.getElementById("fn-save-state");
    const unsavedLabel = document.getElementById("fn-unsaved");
    const flash = document.getElementById("fn-editor-flash");
    const codeSize = document.getElementById("fn-code-size");

    let revision = Number(page.revision || 1);
    let dirty = false;
    let saving = false;
    let changeSerial = 0;
    let activeView = "visual";
    let visualChanged = false;
    let visualSyncTimer = null;

    htmlSource.value = page.html_content || "";

    async function requestJSON(url, options) {
        const response = await fetch(url, options || {});
        let payload = {};
        try {
            if ((response.headers.get("content-type") || "").includes("application/json")) {
                payload = await response.json();
            }
        } catch (_error) {
            payload = {};
        }
        if (!response.ok) {
            const error = new Error(payload.error || "Something went wrong. Please try again.");
            error.status = response.status;
            error.payload = payload;
            throw error;
        }
        return payload;
    }

    function slugify(value) {
        return String(value || "")
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLowerCase()
            .trim()
            .replace(/[^a-z0-9/_-]+/g, "-")
            .replace(/[-_]{2,}/g, "-")
            .replace(/^[-_/]+|[-_/]+$/g, "");
    }

    function byteSize(value) {
        return new Blob([value]).size;
    }

    function formatBytes(value) {
        if (value < 1024) return value + " B";
        if (value < 1024 * 1024) return (value / 1024).toFixed(1) + " KB";
        return (value / (1024 * 1024)).toFixed(2) + " MB";
    }

    function updateCodeSize() {
        codeSize.textContent = formatBytes(byteSize(htmlSource.value));
    }

    function showFlash(message, isError) {
        flash.textContent = message;
        flash.className = "fn-editor-flash" + (isError ? " is-error" : "");
        flash.hidden = false;
        clearTimeout(showFlash.timer);
        showFlash.timer = setTimeout(function () { flash.hidden = true; }, 5000);
    }

    function markDirty() {
        dirty = true;
        changeSerial += 1;
        unsavedLabel.hidden = false;
        saveState.textContent = "Unsaved changes";
        saveState.classList.remove("error");
    }

    function markClean() {
        dirty = false;
        unsavedLabel.hidden = true;
        saveState.textContent = "All changes saved";
        saveState.classList.remove("error");
    }

    function updateUrlDisplay() {
        const slug = slugify(slugInput.value);
        browserUrl.textContent = "/" + slug;
        publicLink.textContent = window.location.origin + "/" + slug;
        publicLink.href = "/" + slug;
    }

    function updatePublishUI() {
        const published = statusInput.value === "published";
        statusInput.classList.toggle("published", published);
        statusInput.classList.toggle("draft", !published);
        publishNote.classList.toggle("is-live", published);
        publishNote.querySelector("strong").textContent = published ? "Live page" : "Draft page";
        publishNote.querySelector("span").textContent = published
            ? "Anyone with the URL can view it."
            : "Only you can open the preview.";
    }

    function serializedVisualDocument() {
        const documentNode = visualFrame.contentDocument;
        if (!documentNode || !documentNode.documentElement) return htmlSource.value;
        const clone = documentNode.documentElement.cloneNode(true);
        clone.querySelector("#fn-visual-editor-style")?.remove();
        clone.querySelectorAll("[contenteditable]").forEach(function (node) {
            node.removeAttribute("contenteditable");
            node.removeAttribute("spellcheck");
        });
        return "<!DOCTYPE html>\n" + clone.outerHTML;
    }

    function syncVisualToSource() {
        clearTimeout(visualSyncTimer);
        if (!visualChanged) return;
        htmlSource.value = serializedVisualDocument();
        visualChanged = false;
        updateCodeSize();
    }

    function scheduleVisualSync() {
        clearTimeout(visualSyncTimer);
        visualSyncTimer = setTimeout(syncVisualToSource, 220);
    }

    function enhanceVisualEditor() {
        let documentNode;
        try {
            documentNode = visualFrame.contentDocument;
        } catch (_error) {
            showFlash("The visual editor could not open this HTML. Use the HTML tab instead.", true);
            return;
        }
        if (!documentNode || !documentNode.documentElement) return;

        const style = documentNode.createElement("style");
        style.id = "fn-visual-editor-style";
        style.textContent = [
            ":root{--ec-editor-accent:#895A3A;--ec-editor-accent-subtle:rgba(137,90,58,.08)}",
            "[data-editable]{outline:1px dashed var(--ec-editor-accent)!important;outline-offset:3px;cursor:text!important;transition:outline-color .15s,background .15s}",
            "[data-editable]:hover,[data-editable]:focus{outline:2px solid var(--ec-editor-accent)!important;background:var(--ec-editor-accent-subtle)!important}",
            "img{cursor:pointer!important;transition:box-shadow .15s,opacity .15s}",
            "img:hover{box-shadow:0 0 0 4px var(--ec-editor-accent)!important;opacity:.88!important}",
        ].join("");
        (documentNode.head || documentNode.documentElement).appendChild(style);

        documentNode.querySelectorAll("[data-editable]").forEach(function (node) {
            node.setAttribute("contenteditable", "true");
            node.setAttribute("spellcheck", "true");
            node.addEventListener("input", function () {
                visualChanged = true;
                markDirty();
                scheduleVisualSync();
            });
        });

        documentNode.addEventListener("click", function (event) {
            const link = event.target.closest("a");
            if (link) event.preventDefault();
            const image = event.target.closest("img");
            if (!image) return;
            event.preventDefault();
            event.stopPropagation();
            const nextUrl = window.prompt("Image URL", image.getAttribute("src") || "");
            if (nextUrl == null || !nextUrl.trim()) return;
            image.setAttribute("src", nextUrl.trim());
            const nextAlt = window.prompt("Image alt text", image.getAttribute("alt") || "");
            if (nextAlt != null) image.setAttribute("alt", nextAlt.trim());
            visualChanged = true;
            markDirty();
            syncVisualToSource();
        });

        documentNode.addEventListener("submit", function (event) { event.preventDefault(); });
    }

    function renderVisual() {
        visualChanged = false;
        visualFrame.removeEventListener("load", enhanceVisualEditor);
        visualFrame.addEventListener("load", enhanceVisualEditor, { once: true });
        visualFrame.srcdoc = htmlSource.value;
    }

    function switchView(view) {
        if (view === activeView) return;
        if (activeView === "visual") syncVisualToSource();
        activeView = view;
        visualWorkspace.hidden = view !== "visual";
        htmlWorkspace.hidden = view !== "html";
        document.querySelectorAll("[data-editor-view]").forEach(function (button) {
            const selected = button.dataset.editorView === view;
            button.classList.toggle("active", selected);
            button.setAttribute("aria-selected", String(selected));
        });
        if (view === "visual") renderVisual();
        if (view === "html") updateCodeSize();
    }

    async function savePage() {
        if (saving) return false;
        if (activeView === "visual") syncVisualToSource();
        const title = titleInput.value.trim();
        const slug = slugify(slugInput.value);
        slugInput.value = slug;
        updateUrlDisplay();
        if (!title) {
            showFlash("Page title is required.", true);
            titleInput.focus();
            return false;
        }
        if (!slug) {
            showFlash("URL slug is required.", true);
            slugInput.focus();
            return false;
        }
        if (byteSize(htmlSource.value) > 2 * 1024 * 1024) {
            showFlash("HTML content must be 2 MB or smaller.", true);
            return false;
        }

        saving = true;
        const serialAtStart = changeSerial;
        saveButton.disabled = true;
        saveButton.lastChild.textContent = " Saving…";
        saveState.textContent = "Saving…";
        try {
            const payload = await requestJSON(
                "/funnels/" + funnelId + "/pages/" + pageId,
                {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        title: title,
                        page_type: typeInput.value,
                        slug: slug,
                        status: statusInput.value,
                        html_content: htmlSource.value,
                        revision: revision,
                    }),
                }
            );
            revision = payload.page.revision;
            titleInput.value = payload.page.title;
            slugInput.value = payload.page.slug;
            document.querySelector("[data-editor-page-name]").textContent = payload.page.title;
            document.title = payload.page.title + " - Funnel editor - EcomFans";
            updateUrlDisplay();
            if (changeSerial === serialAtStart) markClean();
            else saveState.textContent = "New changes not saved";
            showFlash(payload.page.status === "published" ? "Page saved and published." : "Draft saved.", false);
            return true;
        } catch (error) {
            saveState.textContent = error.status === 409 ? "Save conflict" : "Save failed";
            saveState.classList.add("error");
            showFlash(
                error.status === 409
                    ? "This page changed in another tab. Reload before saving again."
                    : error.message,
                true
            );
            return false;
        } finally {
            saving = false;
            saveButton.disabled = false;
            saveButton.lastChild.textContent = " Save changes";
        }
    }

    [titleInput, typeInput, statusInput].forEach(function (input) {
        input.addEventListener("input", function () {
            if (input === statusInput) updatePublishUI();
            markDirty();
        });
    });

    slugInput.addEventListener("input", function () {
        updateUrlDisplay();
        markDirty();
    });
    slugInput.addEventListener("blur", function () {
        slugInput.value = slugify(slugInput.value);
        updateUrlDisplay();
    });

    htmlSource.addEventListener("input", function () {
        updateCodeSize();
        markDirty();
    });
    htmlSource.addEventListener("keydown", function (event) {
        if (event.key !== "Tab") return;
        event.preventDefault();
        const start = htmlSource.selectionStart;
        const end = htmlSource.selectionEnd;
        htmlSource.setRangeText("  ", start, end, "end");
        updateCodeSize();
        markDirty();
    });

    document.querySelectorAll("[data-editor-view]").forEach(function (button) {
        button.addEventListener("click", function () { switchView(button.dataset.editorView); });
    });
    document.querySelectorAll("[data-device]").forEach(function (button) {
        button.addEventListener("click", function () {
            document.querySelectorAll("[data-device]").forEach(function (item) {
                item.classList.toggle("active", item === button);
            });
            browserFrame.className = "fn-browser-frame " + button.dataset.device;
        });
    });

    document.getElementById("fn-reset-template").addEventListener("click", function () {
        if (!window.confirm("Reset this page to ex.html? Your current HTML will be replaced after you save.")) return;
        htmlSource.value = defaultTemplate;
        updateCodeSize();
        renderVisual();
        markDirty();
        showFlash("Template reset. Save to keep this change.", false);
    });

    document.getElementById("fn-copy-url").addEventListener("click", async function () {
        const url = window.location.origin + "/" + slugify(slugInput.value);
        try {
            await navigator.clipboard.writeText(url);
        } catch (_error) {
            window.prompt("Copy this URL", url);
        }
        showFlash("Public URL copied.", false);
    });

    saveButton.addEventListener("click", savePage);
    previewButton.addEventListener("click", async function () {
        const previewWindow = window.open("about:blank", "_blank");
        const saved = await savePage();
        if (!saved) {
            previewWindow?.close();
            return;
        }
        const previewUrl = "/funnels/" + funnelId + "/pages/" + pageId + "/preview";
        if (previewWindow) previewWindow.location.href = previewUrl;
        else window.open(previewUrl, "_blank", "noopener");
    });

    document.addEventListener("keydown", function (event) {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
            event.preventDefault();
            savePage();
        }
    });
    window.addEventListener("beforeunload", function (event) {
        if (!dirty) return;
        event.preventDefault();
        event.returnValue = "";
    });

    updateCodeSize();
    updateUrlDisplay();
    updatePublishUI();
    renderVisual();
})();
