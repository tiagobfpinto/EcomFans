(function () {
    "use strict";

    const MAX_STORYBOARD_TEXT_BYTES = 2 * 1024 * 1024;
    const MAX_THUMBNAIL_BYTES = 8 * 1024 * 1024;
    const SAVE_DELAY_MS = 650;

    function errorMessage(payload, fallback) {
        let message = fallback || "Something went wrong. Please try again.";
        if (payload) {
            const candidate = payload.message || payload.error || payload.detail;
            if (typeof candidate === "string" && candidate.trim()) {
                message = candidate.trim();
            } else if (candidate && typeof candidate.message === "string") {
                message = candidate.message.trim();
            }

            const context = [];
            if (payload.clip != null && !message.toLowerCase().includes("clip")) {
                context.push("clip " + payload.clip);
            }
            if (payload.line != null && !message.toLowerCase().includes("line")) {
                context.push("line " + payload.line);
            }
            if (context.length) message += " (" + context.join(", ") + ")";
        }
        return message;
    }

    async function requestJSON(url, options) {
        const response = await fetch(url, options || {});
        let payload = {};
        const contentType = response.headers.get("content-type") || "";
        if (response.status !== 204 && contentType.includes("application/json")) {
            try {
                payload = await response.json();
            } catch (err) {
                payload = {};
            }
        }

        if (!response.ok) {
            const error = new Error(errorMessage(payload));
            error.status = response.status;
            error.payload = payload;
            throw error;
        }
        return payload;
    }

    function flash(message, kind) {
        const el = document.getElementById("sb-flash");
        if (!el) return;
        el.textContent = message;
        el.className = "sb-flash" + (kind === "error" ? " is-error" : "");
        el.hidden = false;
        clearTimeout(flash._timer);
        flash._timer = setTimeout(function () {
            el.hidden = true;
        }, kind === "error" ? 8000 : 4500);
    }

    function setInlineError(el, message) {
        if (!el) return;
        el.textContent = message || "";
        el.hidden = !message;
    }

    function setButtonBusy(button, busy, busyLabel) {
        if (!button) return;
        if (button._defaultHTML == null) button._defaultHTML = button.innerHTML;
        button.disabled = busy;
        if (busy && busyLabel) button.textContent = busyLabel;
        if (!busy) button.innerHTML = button._defaultHTML;
    }

    function openModal(modal, preferredFocus) {
        if (!modal || !modal.hidden) return;
        modal._returnFocus = document.activeElement;
        modal.hidden = false;
        document.body.style.overflow = "hidden";
        window.requestAnimationFrame(function () {
            const focusTarget = preferredFocus || modal.querySelector("input, select, button, textarea");
            if (focusTarget) focusTarget.focus();
        });
    }

    function closeModal(modal) {
        if (!modal || modal.hidden) return;
        modal.hidden = true;
        document.body.style.overflow = "";
        if (modal._returnFocus && typeof modal._returnFocus.focus === "function") {
            modal._returnFocus.focus();
        }
    }

    function copyText(value) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(value);
        }
        return new Promise(function (resolve, reject) {
            const textarea = document.createElement("textarea");
            textarea.value = value;
            textarea.setAttribute("readonly", "");
            textarea.style.position = "fixed";
            textarea.style.left = "-9999px";
            document.body.appendChild(textarea);
            textarea.select();
            try {
                if (!document.execCommand("copy")) throw new Error("Copy failed");
                resolve();
            } catch (err) {
                reject(err);
            } finally {
                textarea.remove();
            }
        });
    }

    function initListPage(root) {
        const modal = document.getElementById("sb-project-modal");
        const newButton = document.getElementById("sb-new-project");
        const nameInput = document.getElementById("sb-project-name");
        const productInput = document.getElementById("sb-project-product");
        const form = document.getElementById("sb-project-form");
        const formError = document.getElementById("sb-project-form-error");
        const submitButton = document.getElementById("sb-create-project");
        const filter = document.getElementById("sb-product-filter");
        const cards = Array.from(root.querySelectorAll(".sb-project-card"));
        const empty = document.getElementById("sb-project-empty");
        const emptyTitle = document.getElementById("sb-empty-title");
        const emptyCopy = document.getElementById("sb-empty-copy");

        function showCreateModal() {
            setInlineError(formError, "");
            openModal(modal, nameInput);
        }

        if (newButton) newButton.addEventListener("click", showCreateModal);
        root.querySelectorAll("[data-open-project-modal]").forEach(function (button) {
            button.addEventListener("click", showCreateModal);
        });

        if (modal) {
            modal.addEventListener("click", function (event) {
                if (event.target.closest("[data-close-modal]")) closeModal(modal);
            });
        }

        if (filter) {
            filter.addEventListener("change", function () {
                const selected = filter.value;
                let visibleCount = 0;
                cards.forEach(function (card) {
                    const visible = !selected || card.dataset.productId === selected;
                    card.hidden = !visible;
                    if (visible) visibleCount += 1;
                });
                if (empty) {
                    empty.hidden = visibleCount !== 0;
                    if (visibleCount === 0 && cards.length) {
                        if (emptyTitle) emptyTitle.textContent = "No projects for this product";
                        if (emptyCopy) emptyCopy.textContent = "Choose another product or create a new project for this one.";
                    } else {
                        if (emptyTitle) emptyTitle.textContent = "No projects yet";
                        if (emptyCopy) emptyCopy.textContent = "Create a project, paste your clip script, and your storyboard images will appear here.";
                    }
                }
            });
        }

        if (form) {
            form.addEventListener("submit", async function (event) {
                event.preventDefault();
                setInlineError(formError, "");
                const name = nameInput.value.trim();
                const productId = Number(productInput.value);
                if (!name) {
                    setInlineError(formError, "Enter a project name.");
                    nameInput.focus();
                    return;
                }
                if (!productId) {
                    setInlineError(formError, "Select a product.");
                    productInput.focus();
                    return;
                }

                setButtonBusy(submitButton, true, "Creating…");
                try {
                    const payload = await requestJSON("/storyboarder/projects", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ name: name, product_id: productId }),
                    });
                    const projectId = payload.project_id || payload.id || (payload.project && payload.project.id);
                    if (payload.redirect_url) {
                        window.location.href = payload.redirect_url;
                    } else if (projectId) {
                        window.location.href = "/storyboarder/projects/" + projectId;
                    } else {
                        window.location.reload();
                    }
                } catch (err) {
                    setInlineError(formError, err.message);
                    setButtonBusy(submitButton, false);
                }
            });
        }

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && modal && !modal.hidden) closeModal(modal);
        });
    }

    function initDetailPage(root) {
        const projectId = root.dataset.projectId;
        const importModal = document.getElementById("sb-import-modal");
        const importForm = document.getElementById("sb-import-form");
        const importBasePrompt = document.getElementById("sb-import-base-prompt");
        const importClipsText = document.getElementById("sb-import-clips-text");
        const importError = document.getElementById("sb-import-error");
        const importSubmit = document.getElementById("sb-import-submit");
        const openImportButton = document.getElementById("sb-open-import");
        const deleteButton = document.getElementById("sb-delete-project");
        const basePrompt = document.getElementById("sb-base-prompt");
        const promptBlocksData = document.getElementById("sb-prompt-blocks-data");
        const canvasViewport = document.getElementById("sb-canvas-viewport");
        const canvasWorld = document.getElementById("sb-canvas-world");
        const canvasConnections = document.getElementById("sb-canvas-connections");
        const canvasZoomLabel = document.getElementById("sb-canvas-zoom");
        const nodeInspector = document.getElementById("sb-node-inspector");
        const inspectorTitle = document.getElementById("sb-inspector-title");

        const saveJobs = new WeakMap();
        const saveScopes = new WeakMap();
        let promptBlocks = {};
        try {
            promptBlocks = JSON.parse(promptBlocksData ? promptBlocksData.textContent : "{}") || {};
        } catch (err) {
            promptBlocks = {};
        }

        function normalizePromptVariable(name) {
            return String(name || "").trim().replace(/[\s_-]+/g, " ").toUpperCase();
        }

        function resolvePromptVariables(value) {
            const variables = {};
            Object.keys(promptBlocks).forEach(function (name) {
                const content = promptBlocks[name];
                if (typeof content === "string" && content.trim()) {
                    variables[normalizePromptVariable(name)] = content.trim();
                }
            });
            const currentBasePrompt = basePrompt ? basePrompt.value.trim() : "";
            if (currentBasePrompt) {
                variables["BASE PROMPT"] = currentBasePrompt;
                if (!variables["BASE BLOCK"]) variables["BASE BLOCK"] = currentBasePrompt;
            }

            const tokenPattern = /\[([^\[\]\r\n]{1,80})\]/g;
            let resolved = String(value || "");
            for (let pass = 0; pass < 12; pass += 1) {
                let replaced = false;
                resolved = resolved.replace(tokenPattern, function (token, rawName) {
                    const replacement = variables[normalizePromptVariable(rawName)];
                    if (replacement == null) return token;
                    replaced = true;
                    return replacement;
                });
                if (!replaced) break;
            }

            const unresolved = [];
            resolved.replace(tokenPattern, function (token, rawName) {
                const normalized = normalizePromptVariable(rawName);
                if (!unresolved.includes(normalized)) unresolved.push(normalized);
                return token;
            });
            return { value: resolved, unresolved: unresolved };
        }

        function scopeFor(element) {
            const inspector = element.closest("[data-frame-inspector]");
            const stateEl = inspector
                ? inspector.querySelector(".sb-frame-save-state")
                : document.getElementById("sb-project-save-state");
            if (!saveScopes.has(stateEl)) {
                saveScopes.set(stateEl, { pending: new Set(), errors: new Set() });
            }
            return { stateEl: stateEl, state: saveScopes.get(stateEl) };
        }

        function renderSaveState(scope) {
            const stateEl = scope.stateEl;
            const state = scope.state;
            if (!stateEl) return;
            stateEl.classList.remove("is-saving", "is-saved", "is-error");
            if (state.errors.size) {
                stateEl.textContent = "Save failed";
                stateEl.classList.add("is-error");
            } else if (state.pending.size) {
                stateEl.textContent = "Saving…";
                stateEl.classList.add("is-saving");
            } else {
                stateEl.textContent = "Saved";
                stateEl.classList.add("is-saved");
            }
        }

        function markPending(element) {
            const scope = scopeFor(element);
            scope.state.errors.delete(element);
            scope.state.pending.add(element);
            renderSaveState(scope);
            return scope;
        }

        function currentValue(element) {
            const key = element.dataset.projectField || element.dataset.frameField;
            if (key === "product_id") return Number(element.value);
            if (key === "name") return element.value.trim();
            return element.value;
        }

        async function saveElement(element, version) {
            const job = saveJobs.get(element);
            if (!job || job.version !== version) return;
            const scope = scopeFor(element);
            const projectField = element.dataset.projectField;
            const frameField = element.dataset.frameField;
            const key = projectField || frameField;
            const value = currentValue(element);

            if ((key === "name" || key === "product_id") && !value) {
                scope.state.pending.delete(element);
                scope.state.errors.add(element);
                renderSaveState(scope);
                flash(key === "name" ? "Project name cannot be empty." : "Select a product.", "error");
                return;
            }

            const url = projectField
                ? "/storyboarder/projects/" + projectId
                : "/storyboarder/frames/" + element.closest("[data-frame-inspector]").dataset.frameId;
            const payload = {};
            payload[key] = value;

            try {
                await requestJSON(url, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
                const latest = saveJobs.get(element);
                if (!latest || latest.version !== version) return;
                scope.state.pending.delete(element);
                scope.state.errors.delete(element);
                element.dataset.savedValue = element.value;
                renderSaveState(scope);
            } catch (err) {
                const latest = saveJobs.get(element);
                if (!latest || latest.version !== version) return;
                scope.state.pending.delete(element);
                scope.state.errors.add(element);
                renderSaveState(scope);
                flash(err.message, "error");
            }
        }

        function scheduleSave(element, immediate) {
            const previous = saveJobs.get(element);
            if (previous && previous.timer) clearTimeout(previous.timer);
            const version = previous ? previous.version + 1 : 1;
            markPending(element);
            const job = { version: version, timer: null };
            saveJobs.set(element, job);
            if (immediate) {
                saveElement(element, version);
            } else {
                job.timer = setTimeout(function () {
                    job.timer = null;
                    saveElement(element, version);
                }, SAVE_DELAY_MS);
            }
        }

        function flushSave(element) {
            const job = saveJobs.get(element);
            if (!job || !job.timer) return;
            clearTimeout(job.timer);
            job.timer = null;
            saveElement(element, job.version);
        }

        root.querySelectorAll("[data-project-field], [data-frame-field]").forEach(function (element) {
            element.dataset.savedValue = element.value;
            const eventName = element.tagName === "SELECT" ? "change" : "input";
            element.addEventListener(eventName, function () {
                if (element.dataset.frameField === "label") {
                    const frameId = element.closest("[data-frame-inspector]").dataset.frameId;
                    const node = root.querySelector('[data-frame-node][data-frame-id="' + frameId + '"]');
                    const heading = node && node.querySelector(".sb-node-title");
                    if (heading) heading.textContent = element.value.trim() || "Untitled clip";
                    if (node) node.setAttribute("aria-label", "Open clip: " + (element.value.trim() || "Untitled clip"));
                }
                scheduleSave(element, element.tagName === "SELECT");
            });
            if (element.tagName !== "SELECT") {
                element.addEventListener("blur", function () { flushSave(element); });
            }
        });

        root.querySelectorAll("[data-copy-field]").forEach(function (button) {
            button.addEventListener("click", async function () {
                const inspector = button.closest("[data-frame-inspector]");
                const field = inspector && inspector.querySelector(
                    '[data-frame-field="' + button.dataset.copyField + '"]'
                );
                const fieldValue = field ? field.value.trim() : "";
                const baseValue = basePrompt ? basePrompt.value.trim() : "";
                const exactValue = button.hasAttribute("data-include-base-prompt")
                    ? [baseValue, fieldValue].filter(Boolean).join("\n\n")
                    : fieldValue;
                const expandedPrompt = resolvePromptVariables(exactValue);
                try {
                    await copyText(expandedPrompt.value);
                    const oldLabel = button.textContent;
                    button.classList.add("is-copied");
                    button.textContent = "Copied";
                    clearTimeout(button._copyTimer);
                    button._copyTimer = setTimeout(function () {
                        button.classList.remove("is-copied");
                        button.textContent = oldLabel;
                    }, 1700);
                    if (expandedPrompt.unresolved.length) {
                        flash(
                            "Copied, but these blocks are not defined: " +
                            expandedPrompt.unresolved.map(function (name) { return "[" + name + "]"; }).join(", "),
                            "error"
                        );
                    }
                } catch (err) {
                    flash("Could not copy this field. Select and copy it manually.", "error");
                }
            });
        });

        root.querySelectorAll(".sb-thumbnail-input").forEach(function (input) {
            input.addEventListener("change", async function () {
                const file = input.files && input.files[0];
                if (!file) return;
                if (file.size === 0) {
                    flash("The selected image is empty.", "error");
                    input.value = "";
                    return;
                }
                if (file.size > MAX_THUMBNAIL_BYTES) {
                    flash("Thumbnail images must be 8 MB or smaller.", "error");
                    input.value = "";
                    return;
                }
                if (file.type && !file.type.startsWith("image/")) {
                    flash("Choose a valid image file.", "error");
                    input.value = "";
                    return;
                }

                const node = input.closest("[data-frame-node]");
                const frameId = node.dataset.frameId;
                const action = node.querySelector(".sb-thumbnail-action");
                const loading = node.querySelector(".sb-thumbnail-loading");
                action.hidden = true;
                loading.hidden = false;
                const formData = new FormData();
                formData.append("thumbnail", file);

                try {
                    const payload = await requestJSON("/storyboarder/frames/" + frameId + "/thumbnail", {
                        method: "POST",
                        body: formData,
                    });
                    const returnedUrl = payload.thumbnail_url || payload.url ||
                        (payload.frame && payload.frame.thumbnail_url);
                    const thumbnailUrl = returnedUrl || "/media/storyboard-thumbnails/" + frameId;
                    const image = node.querySelector(".sb-frame-image");
                    const placeholder = node.querySelector(".sb-frame-placeholder");
                    const separator = thumbnailUrl.includes("?") ? "&" : "?";
                    image.src = thumbnailUrl + separator + "v=" + Date.now();
                    image.classList.remove("is-empty");
                    placeholder.hidden = true;
                    flash("Thumbnail uploaded.", "success");
                } catch (err) {
                    flash(err.message, "error");
                } finally {
                    action.hidden = false;
                    loading.hidden = true;
                    input.value = "";
                }
            });
        });

        function initStoryboardCanvas() {
            if (!canvasViewport || !canvasWorld || !canvasConnections) return;

            const nodes = Array.from(canvasWorld.querySelectorAll("[data-frame-node]"))
                .sort(function (a, b) {
                    return Number(a.dataset.sortOrder) - Number(b.dataset.sortOrder);
                });
            const storageKey = "storyboard-canvas:" + projectId;
            const minScale = 0.35;
            const maxScale = 1.8;
            let view = { x: 30, y: 70, scale: 1 };
            let savedCanvas = null;
            let persistTimer = null;

            try {
                savedCanvas = JSON.parse(window.localStorage.getItem(storageKey) || "null");
            } catch (err) {
                savedCanvas = null;
            }

            nodes.forEach(function (node, index) {
                const savedPosition = savedCanvas && savedCanvas.positions &&
                    savedCanvas.positions[node.dataset.frameId];
                if (savedPosition && Number.isFinite(savedPosition.x) && Number.isFinite(savedPosition.y)) {
                    node.style.left = savedPosition.x + "px";
                    node.style.top = savedPosition.y + "px";
                    return;
                }
                const row = Math.floor(index / 4);
                const offset = index % 4;
                const column = row % 2 ? 3 - offset : offset;
                node.style.left = (90 + column * 350) + "px";
                node.style.top = (90 + row * 350) + "px";
            });

            function nodePosition(node) {
                return {
                    x: Number.parseFloat(node.style.left) || node.offsetLeft || 0,
                    y: Number.parseFloat(node.style.top) || node.offsetTop || 0,
                };
            }

            function drawConnections() {
                canvasConnections.replaceChildren();
                nodes.slice(0, -1).forEach(function (node, index) {
                    const next = nodes[index + 1];
                    const start = nodePosition(node);
                    const end = nodePosition(next);
                    const x1 = start.x + node.offsetWidth;
                    const y1 = start.y + node.offsetHeight / 2;
                    const x2 = end.x;
                    const y2 = end.y + next.offsetHeight / 2;
                    const bend = Math.max(75, Math.min(230, Math.abs(x2 - x1) * 0.45));
                    const direction = x2 >= x1 ? 1 : -1;
                    const pathData = "M " + x1 + " " + y1 + " C " +
                        (x1 + bend * direction) + " " + y1 + ", " +
                        (x2 - bend * direction) + " " + y2 + ", " + x2 + " " + y2;

                    ["sb-canvas-connection-shadow", "sb-canvas-connection"].forEach(function (className) {
                        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
                        path.setAttribute("class", className);
                        path.setAttribute("d", pathData);
                        canvasConnections.appendChild(path);
                    });
                });
            }

            function applyView() {
                canvasWorld.style.transform = "translate(" + view.x + "px, " + view.y + "px) scale(" + view.scale + ")";
                canvasViewport.style.backgroundSize = (24 * view.scale) + "px " + (24 * view.scale) + "px";
                canvasViewport.style.backgroundPosition = view.x + "px " + view.y + "px";
                if (canvasZoomLabel) canvasZoomLabel.textContent = Math.round(view.scale * 100) + "%";
            }

            function persistCanvas() {
                const positions = {};
                nodes.forEach(function (node) {
                    positions[node.dataset.frameId] = nodePosition(node);
                });
                try {
                    window.localStorage.setItem(storageKey, JSON.stringify({
                        positions: positions,
                        view: view,
                    }));
                } catch (err) {
                    // Canvas persistence is a convenience; the editor still works without it.
                }
            }

            function queuePersist() {
                clearTimeout(persistTimer);
                persistTimer = setTimeout(persistCanvas, 180);
            }

            function fitNodes() {
                if (!nodes.length) return;
                const positions = nodes.map(nodePosition);
                const minX = Math.min.apply(null, positions.map(function (position) { return position.x; }));
                const minY = Math.min.apply(null, positions.map(function (position) { return position.y; }));
                const maxX = Math.max.apply(null, nodes.map(function (node, index) {
                    return positions[index].x + node.offsetWidth;
                }));
                const maxY = Math.max.apply(null, nodes.map(function (node, index) {
                    return positions[index].y + node.offsetHeight;
                }));
                const contentWidth = Math.max(1, maxX - minX);
                const contentHeight = Math.max(1, maxY - minY);
                const availableWidth = Math.max(200, canvasViewport.clientWidth - 90);
                const availableHeight = Math.max(200, canvasViewport.clientHeight - 120);
                view.scale = Math.max(minScale, Math.min(1.15, availableWidth / contentWidth, availableHeight / contentHeight));
                view.x = (canvasViewport.clientWidth - contentWidth * view.scale) / 2 - minX * view.scale;
                view.y = 72 + (availableHeight - contentHeight * view.scale) / 2 - minY * view.scale;
                applyView();
                queuePersist();
            }

            function setZoom(nextScale, clientX, clientY) {
                const rect = canvasViewport.getBoundingClientRect();
                const anchorX = (clientX == null ? rect.left + rect.width / 2 : clientX) - rect.left;
                const anchorY = (clientY == null ? rect.top + rect.height / 2 : clientY) - rect.top;
                const oldScale = view.scale;
                const clamped = Math.max(minScale, Math.min(maxScale, nextScale));
                const worldX = (anchorX - view.x) / oldScale;
                const worldY = (anchorY - view.y) / oldScale;
                view.scale = clamped;
                view.x = anchorX - worldX * clamped;
                view.y = anchorY - worldY * clamped;
                applyView();
                queuePersist();
            }

            function closeInspector() {
                if (nodeInspector) nodeInspector.hidden = true;
                nodes.forEach(function (node) { node.classList.remove("is-selected"); });
                root.querySelectorAll("[data-frame-inspector]").forEach(function (panel) {
                    panel.hidden = true;
                });
            }

            function openInspector(node) {
                if (!nodeInspector) return;
                const frameId = node.dataset.frameId;
                nodes.forEach(function (candidate) {
                    candidate.classList.toggle("is-selected", candidate === node);
                });
                root.querySelectorAll("[data-frame-inspector]").forEach(function (panel) {
                    panel.hidden = panel.dataset.frameInspector !== frameId;
                });
                const title = node.querySelector(".sb-node-title");
                if (inspectorTitle) inspectorTitle.textContent = title ? title.textContent : "Clip details";
                nodeInspector.hidden = false;
            }

            nodes.forEach(function (node) {
                let drag = null;
                node.addEventListener("pointerdown", function (event) {
                    if (event.button !== 0 || event.target.closest("button, input, label, textarea, select, a")) return;
                    event.stopPropagation();
                    const position = nodePosition(node);
                    drag = {
                        pointerId: event.pointerId,
                        startX: event.clientX,
                        startY: event.clientY,
                        nodeX: position.x,
                        nodeY: position.y,
                        moved: false,
                    };
                    node.setPointerCapture(event.pointerId);
                    node.classList.add("is-dragging");
                });
                node.addEventListener("pointermove", function (event) {
                    if (!drag || drag.pointerId !== event.pointerId) return;
                    const deltaX = (event.clientX - drag.startX) / view.scale;
                    const deltaY = (event.clientY - drag.startY) / view.scale;
                    if (Math.abs(deltaX) > 3 || Math.abs(deltaY) > 3) drag.moved = true;
                    node.style.left = Math.max(20, Math.min(4650, drag.nodeX + deltaX)) + "px";
                    node.style.top = Math.max(70, Math.min(2700, drag.nodeY + deltaY)) + "px";
                    drawConnections();
                });
                function finishNodeDrag(event) {
                    if (!drag || drag.pointerId !== event.pointerId) return;
                    const moved = drag.moved;
                    drag = null;
                    node.classList.remove("is-dragging");
                    if (node.hasPointerCapture(event.pointerId)) node.releasePointerCapture(event.pointerId);
                    if (moved) persistCanvas();
                    else openInspector(node);
                }
                node.addEventListener("pointerup", finishNodeDrag);
                node.addEventListener("pointercancel", finishNodeDrag);
                node.addEventListener("keydown", function (event) {
                    if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        openInspector(node);
                    }
                });
            });

            let pan = null;
            canvasViewport.addEventListener("pointerdown", function (event) {
                if (event.button !== 0 || event.target.closest("[data-frame-node]")) return;
                pan = {
                    pointerId: event.pointerId,
                    startX: event.clientX,
                    startY: event.clientY,
                    viewX: view.x,
                    viewY: view.y,
                };
                canvasViewport.setPointerCapture(event.pointerId);
                canvasViewport.classList.add("is-panning");
            });
            canvasViewport.addEventListener("pointermove", function (event) {
                if (!pan || pan.pointerId !== event.pointerId) return;
                view.x = pan.viewX + event.clientX - pan.startX;
                view.y = pan.viewY + event.clientY - pan.startY;
                applyView();
            });
            function finishPan(event) {
                if (!pan || pan.pointerId !== event.pointerId) return;
                pan = null;
                canvasViewport.classList.remove("is-panning");
                if (canvasViewport.hasPointerCapture(event.pointerId)) canvasViewport.releasePointerCapture(event.pointerId);
                persistCanvas();
            }
            canvasViewport.addEventListener("pointerup", finishPan);
            canvasViewport.addEventListener("pointercancel", finishPan);
            canvasViewport.addEventListener("wheel", function (event) {
                event.preventDefault();
                if (event.ctrlKey || event.metaKey) {
                    setZoom(view.scale * Math.exp(-event.deltaY * 0.002), event.clientX, event.clientY);
                } else {
                    view.x -= event.deltaX;
                    view.y -= event.deltaY;
                    applyView();
                    queuePersist();
                }
            }, { passive: false });

            const zoomIn = root.querySelector("[data-canvas-zoom-in]");
            const zoomOut = root.querySelector("[data-canvas-zoom-out]");
            const fitButton = root.querySelector("[data-canvas-fit]");
            if (zoomIn) zoomIn.addEventListener("click", function () { setZoom(view.scale * 1.2); });
            if (zoomOut) zoomOut.addEventListener("click", function () { setZoom(view.scale / 1.2); });
            if (fitButton) fitButton.addEventListener("click", fitNodes);
            if (nodeInspector) {
                nodeInspector.addEventListener("click", function (event) {
                    if (event.target.closest("[data-close-inspector]")) closeInspector();
                });
            }
            document.addEventListener("keydown", function (event) {
                if (event.key === "Escape" && nodeInspector && !nodeInspector.hidden) closeInspector();
            });
            window.addEventListener("resize", function () {
                drawConnections();
                applyView();
            });

            window.requestAnimationFrame(function () {
                drawConnections();
                const savedView = savedCanvas && savedCanvas.view;
                if (savedView && Number.isFinite(savedView.x) && Number.isFinite(savedView.y) && Number.isFinite(savedView.scale)) {
                    view = {
                        x: savedView.x,
                        y: savedView.y,
                        scale: Math.max(minScale, Math.min(maxScale, savedView.scale)),
                    };
                    applyView();
                } else {
                    fitNodes();
                }
            });
        }

        initStoryboardCanvas();

        function showImportModal() {
            setInlineError(importError, "");
            if (importBasePrompt && basePrompt) importBasePrompt.value = basePrompt.value;
            const focusTarget = importBasePrompt && !importBasePrompt.value.trim()
                ? importBasePrompt
                : importClipsText;
            openModal(importModal, focusTarget);
        }

        if (openImportButton) openImportButton.addEventListener("click", showImportModal);
        root.querySelectorAll("[data-open-import]").forEach(function (button) {
            button.addEventListener("click", showImportModal);
        });

        if (importModal) {
            importModal.addEventListener("click", function (event) {
                if (event.target.closest("[data-close-import]")) closeModal(importModal);
            });
        }

        function validateStoryboardText(basePromptValue, clipsTextValue) {
            if (!basePromptValue.trim()) return "Add a base prompt.";
            if (!clipsTextValue.trim()) return "Paste at least one CLIP block.";
            if (new Blob([clipsTextValue]).size > MAX_STORYBOARD_TEXT_BYTES) {
                return "Clip text must be 2 MB or smaller.";
            }
            return "";
        }

        async function createStoryboard(replaceExisting) {
            const basePromptValue = importBasePrompt ? importBasePrompt.value : "";
            const clipsTextValue = importClipsText ? importClipsText.value : "";
            const problem = validateStoryboardText(basePromptValue, clipsTextValue);
            if (problem) {
                setInlineError(importError, problem);
                if (!basePromptValue.trim() && importBasePrompt) importBasePrompt.focus();
                else if (importClipsText) importClipsText.focus();
                return;
            }

            setInlineError(importError, "");
            setButtonBusy(importSubmit, true, replaceExisting ? "Replacing…" : "Creating…");

            try {
                await requestJSON("/storyboarder/projects/" + projectId + "/clips", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        base_prompt: basePromptValue,
                        clips_text: clipsTextValue,
                        replace_existing: replaceExisting,
                    }),
                });
                window.location.reload();
            } catch (err) {
                if (err.status === 409 && !replaceExisting) {
                    setButtonBusy(importSubmit, false);
                    const confirmed = window.confirm(
                        "This project already has storyboard images. Replacing them will also permanently remove their thumbnails. Continue?"
                    );
                    if (confirmed) await createStoryboard(true);
                    return;
                }
                setInlineError(importError, err.message);
                setButtonBusy(importSubmit, false);
            }
        }

        if (importForm) {
            importForm.addEventListener("submit", function (event) {
                event.preventDefault();
                createStoryboard(false);
            });
        }

        if (deleteButton) {
            deleteButton.addEventListener("click", async function () {
                const projectName = document.getElementById("sb-project-title").value.trim() || "this project";
                if (!window.confirm('Delete "' + projectName + '" and all of its storyboard images? This cannot be undone.')) return;
                setButtonBusy(deleteButton, true, "Deleting…");
                try {
                    await requestJSON("/storyboarder/projects/" + projectId, { method: "DELETE" });
                    window.location.href = "/storyboarder";
                } catch (err) {
                    flash(err.message, "error");
                    setButtonBusy(deleteButton, false);
                }
            });
        }

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && importModal && !importModal.hidden) closeModal(importModal);
        });

        if (Number(root.dataset.frameCount || "0") === 0) {
            window.requestAnimationFrame(showImportModal);
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        const listPage = document.getElementById("storyboarder-list");
        const detailPage = document.getElementById("storyboarder-detail");
        if (listPage) initListPage(listPage);
        if (detailPage) initDetailPage(detailPage);
    });
})();
