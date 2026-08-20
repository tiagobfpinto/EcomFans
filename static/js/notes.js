(function () {
    "use strict";

    const SVG_NS = "http://www.w3.org/2000/svg";
    const SAVE_DELAY_MS = 700;
    const MAX_HISTORY = 100;
    const MAX_OBJECTS = 1000;
    const MIN_ZOOM = 0.1;
    const MAX_ZOOM = 4;

    function clone(value) {
        return JSON.parse(JSON.stringify(value));
    }

    function clamp(value, minimum, maximum) {
        return Math.min(maximum, Math.max(minimum, value));
    }

    function normalizedRotation(value) {
        return ((value + 180) % 360 + 360) % 360 - 180;
    }

    function svg(tag, attributes) {
        const node = document.createElementNS(SVG_NS, tag);
        Object.entries(attributes || {}).forEach(function (entry) {
            node.setAttribute(entry[0], String(entry[1]));
        });
        return node;
    }

    function setHidden(element, hidden) {
        if (element) element.hidden = hidden;
    }

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

    function relativeTime(value) {
        const date = new Date(value);
        if (!value || Number.isNaN(date.getTime())) return "Recently updated";
        const seconds = Math.round((date.getTime() - Date.now()) / 1000);
        const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
        const ranges = [
            [31536000, "year"],
            [2592000, "month"],
            [604800, "week"],
            [86400, "day"],
            [3600, "hour"],
            [60, "minute"],
        ];
        for (const range of ranges) {
            if (Math.abs(seconds) >= range[0]) {
                return "Updated " + formatter.format(Math.round(seconds / range[0]), range[1]);
            }
        }
        return "Updated just now";
    }

    function initListPage(root) {
        const modal = document.getElementById("notes-board-modal");
        const form = document.getElementById("notes-board-form");
        const nameInput = document.getElementById("notes-board-name");
        const createButton = document.getElementById("notes-create-board");
        const formError = document.getElementById("notes-form-error");
        const flash = document.getElementById("notes-flash");

        root.querySelectorAll("[data-relative-time]").forEach(function (time) {
            time.textContent = relativeTime(time.getAttribute("datetime"));
        });

        function showFlash(message, isError) {
            if (!flash) return;
            flash.textContent = message;
            flash.className = "notes-flash" + (isError ? " is-error" : "");
            flash.hidden = false;
            clearTimeout(showFlash.timer);
            showFlash.timer = setTimeout(function () { flash.hidden = true; }, 5000);
        }

        function openModal() {
            if (!modal) return;
            modal.hidden = false;
            document.body.style.overflow = "hidden";
            formError.hidden = true;
            window.requestAnimationFrame(function () { nameInput.focus(); });
        }

        function closeModal() {
            if (!modal) return;
            modal.hidden = true;
            document.body.style.overflow = "";
            form.reset();
        }

        document.getElementById("notes-new-board")?.addEventListener("click", openModal);
        root.querySelectorAll("[data-open-board-modal]").forEach(function (button) {
            button.addEventListener("click", openModal);
        });
        modal?.addEventListener("click", function (event) {
            if (event.target.closest("[data-close-modal]")) closeModal();
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && modal && !modal.hidden) closeModal();
        });

        form?.addEventListener("submit", async function (event) {
            event.preventDefault();
            const name = nameInput.value.trim();
            if (!name) {
                formError.textContent = "Enter a board name.";
                formError.hidden = false;
                nameInput.focus();
                return;
            }
            createButton.disabled = true;
            createButton.textContent = "Creating…";
            try {
                const payload = await requestJSON("/notes/boards", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name: name }),
                });
                window.location.href = "/notes/boards/" + payload.board.id;
            } catch (error) {
                formError.textContent = error.message;
                formError.hidden = false;
                createButton.disabled = false;
                createButton.textContent = "Create board";
            }
        });

        root.addEventListener("click", async function (event) {
            const button = event.target.closest("[data-board-action]");
            if (!button) return;
            const card = button.closest(".notes-board-card");
            const boardId = Number(card.dataset.boardId);
            const boardName = card.querySelector("[data-board-name]").textContent.trim();
            const action = button.dataset.boardAction;

            if (action === "rename") {
                const proposed = window.prompt("Rename this board", boardName);
                if (proposed == null) return;
                const name = proposed.trim();
                if (!name) {
                    showFlash("Board name cannot be empty.", true);
                    return;
                }
                button.disabled = true;
                try {
                    const payload = await requestJSON("/notes/boards/" + boardId, {
                        method: "PATCH",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ name: name, revision: Number(card.dataset.revision) }),
                    });
                    card.dataset.revision = payload.board.revision;
                    card.querySelector("[data-board-name]").textContent = payload.board.name;
                    showFlash("Board renamed.", false);
                } catch (error) {
                    showFlash(error.status === 409 ? "This board changed elsewhere. Refresh and try again." : error.message, true);
                } finally {
                    button.disabled = false;
                }
                return;
            }

            if (action === "duplicate") {
                button.disabled = true;
                try {
                    const payload = await requestJSON("/notes/boards/" + boardId + "/duplicate", { method: "POST" });
                    window.location.href = "/notes/boards/" + payload.board.id;
                } catch (error) {
                    showFlash(error.message, true);
                    button.disabled = false;
                }
                return;
            }

            if (action === "delete" && window.confirm('Delete "' + boardName + '"? This cannot be undone.')) {
                button.disabled = true;
                try {
                    await requestJSON("/notes/boards/" + boardId, { method: "DELETE" });
                    card.remove();
                    if (!root.querySelector(".notes-board-card")) {
                        setHidden(document.getElementById("notes-empty"), false);
                    }
                    showFlash("Board deleted.", false);
                } catch (error) {
                    showFlash(error.message, true);
                    button.disabled = false;
                }
            }
        });
    }

    function initEditor(root) {
        const bootstrap = JSON.parse(document.getElementById("notes-bootstrap").textContent || "{}");
        const boardId = Number(root.dataset.boardId);
        const stage = document.getElementById("notes-stage");
        const world = document.getElementById("notes-world");
        const titleInput = document.getElementById("notes-board-title");
        const textEditor = document.getElementById("notes-text-editor");
        const saveState = document.getElementById("notes-save-state");
        const saveLabel = document.getElementById("notes-save-label");
        const retryButton = document.getElementById("notes-save-retry");
        const conflictBar = document.getElementById("notes-conflict");
        const recoveryBar = document.getElementById("notes-recovery");
        const inspectorEmpty = document.getElementById("notes-inspector-empty");
        const inspectorPanel = document.getElementById("notes-inspector-panel");
        const recoveryKey = "ecomfans.notes.recovery." + boardId;

        let state = clone(bootstrap.document);
        let revision = bootstrap.revision;
        let selectedId = null;
        let activeTool = "select";
        let action = null;
        let history = [];
        let future = [];
        let saveTimer = null;
        let saving = false;
        let resave = false;
        let changeSerial = 0;
        let pendingConflict = null;
        let textEdit = null;
        let spaceHeld = false;
        let lastNonEmptyName = bootstrap.name;
        const pointers = new Map();

        function selectedObject() {
            return state.objects.find(function (item) { return item.id === selectedId; }) || null;
        }

        function constrainViewport() {
            state.viewport.x = clamp(state.viewport.x, -1000000, 1000000);
            state.viewport.y = clamp(state.viewport.y, -1000000, 1000000);
            state.viewport.zoom = clamp(state.viewport.zoom, MIN_ZOOM, MAX_ZOOM);
        }

        function constrainObject(object) {
            object.x = clamp(object.x, -1000000, 1000000);
            object.y = clamp(object.y, -1000000, 1000000);
            object.rotation = normalizedRotation(object.rotation);
            if (object.type === "line" || object.type === "arrow") {
                object.width = clamp(object.width, -100000, 100000);
                object.height = clamp(object.height, -100000, 100000);
                if (object.width === 0 && object.height === 0) object.width = 1;
            } else {
                object.width = clamp(object.width, 1, 100000);
                object.height = clamp(object.height, 1, 100000);
            }
        }

        function makeId() {
            if (window.crypto && typeof window.crypto.randomUUID === "function") {
                return window.crypto.randomUUID();
            }
            return "obj-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
        }

        function defaultObject(type, point) {
            const common = {
                id: makeId(), type: type, x: point.x, y: point.y,
                width: 180, height: 110, rotation: 0,
                fill: "#ddd8fb", stroke: "#7463df", stroke_width: 2, opacity: 1,
            };
            if (type === "ellipse") {
                common.width = 150;
                common.fill = "#d9f4eb";
                common.stroke = "#20a980";
            } else if (type === "line" || type === "arrow") {
                common.width = 180;
                common.height = 0;
                common.fill = "transparent";
                common.stroke = "#4e485d";
                common.stroke_width = 3;
            } else if (type === "sticky") {
                common.width = 180;
                common.height = 160;
                common.fill = "#f7d96c";
                common.stroke = "#e2bd36";
                common.stroke_width = 1;
                Object.assign(common, { text: "New note", font_size: 22, font_weight: 500, text_align: "left", text_color: "#292338" });
            } else if (type === "text") {
                common.width = 220;
                common.height = 100;
                common.fill = "transparent";
                common.stroke = "transparent";
                common.stroke_width = 0;
                Object.assign(common, { text: "Add text", font_size: 28, font_weight: 600, text_align: "left", text_color: "#292338" });
            }
            return common;
        }

        function setSaveStatus(kind, label, canRetry) {
            saveState.className = "notes-save-state" + (kind ? " is-" + kind : "");
            saveLabel.textContent = label;
            retryButton.hidden = !canRetry;
        }

        function persistRecovery() {
            try {
                localStorage.setItem(recoveryKey, JSON.stringify({
                    board_id: boardId,
                    base_revision: revision,
                    saved_at: Date.now(),
                    name: titleInput.value.trim() || bootstrap.name,
                    document: state,
                }));
            } catch (_error) {
                // Storage can be unavailable in private browsing; server autosave still works.
            }
        }

        function clearRecovery() {
            try { localStorage.removeItem(recoveryKey); } catch (_error) { /* no-op */ }
        }

        function markDirty() {
            changeSerial += 1;
            persistRecovery();
            setSaveStatus("dirty", "Unsaved", false);
            clearTimeout(saveTimer);
            saveTimer = setTimeout(flushSave, SAVE_DELAY_MS);
        }

        async function flushSave() {
            clearTimeout(saveTimer);
            if (saving) {
                resave = true;
                return;
            }
            const name = titleInput.value.trim();
            if (!name) {
                setSaveStatus("error", "Name required", false);
                return;
            }
            saving = true;
            resave = false;
            const serial = changeSerial;
            const sentRevision = revision;
            const sentDocument = clone(state);
            setSaveStatus("saving", "Saving…", false);
            try {
                const payload = await requestJSON("/notes/boards/" + boardId, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name: name, document: sentDocument, revision: sentRevision }),
                });
                revision = payload.board.revision;
                if (changeSerial === serial) {
                    titleInput.value = payload.board.name;
                    lastNonEmptyName = payload.board.name;
                    document.title = payload.board.name + " - Notes - EcomFans";
                    clearRecovery();
                    setSaveStatus("", "Saved", false);
                } else {
                    resave = true;
                    setSaveStatus("dirty", "Unsaved", false);
                }
            } catch (error) {
                if (error.status === 409 && error.payload && error.payload.board) {
                    pendingConflict = error.payload.board;
                    conflictBar.hidden = false;
                    setSaveStatus("error", "Conflict", false);
                } else {
                    setSaveStatus("error", navigator.onLine ? "Save failed" : "Offline", true);
                }
            } finally {
                saving = false;
                if (resave && !pendingConflict) {
                    saveTimer = setTimeout(flushSave, 50);
                }
            }
        }

        function pushHistory(snapshot) {
            history.push(snapshot);
            if (history.length > MAX_HISTORY) history.shift();
            future = [];
            syncHistoryButtons();
        }

        function syncHistoryButtons() {
            document.getElementById("notes-undo").disabled = history.length === 0;
            document.getElementById("notes-redo").disabled = future.length === 0;
        }

        function undo() {
            if (!history.length) return;
            finishTextEdit(false);
            future.push(clone(state.objects));
            state.objects = history.pop();
            selectedId = null;
            render();
            syncHistoryButtons();
            markDirty();
        }

        function redo() {
            if (!future.length) return;
            finishTextEdit(false);
            history.push(clone(state.objects));
            state.objects = future.pop();
            selectedId = null;
            render();
            syncHistoryButtons();
            markDirty();
        }

        function setTool(tool) {
            activeTool = tool;
            root.querySelectorAll("[data-tool]").forEach(function (button) {
                button.classList.toggle("is-active", button.dataset.tool === tool);
            });
            stage.classList.toggle("is-hand", tool === "hand");
            stage.classList.toggle("is-crosshair", !["select", "hand"].includes(tool));
            if (tool !== "select") selectedId = null;
            finishTextEdit(false);
            render();
        }

        function screenPoint(event) {
            const bounds = stage.getBoundingClientRect();
            return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
        }

        function worldPointFromScreen(point) {
            return {
                x: (point.x - state.viewport.x) / state.viewport.zoom,
                y: (point.y - state.viewport.y) / state.viewport.zoom,
            };
        }

        function rotatePoint(point, center, degrees) {
            const radians = degrees * Math.PI / 180;
            const cosine = Math.cos(radians);
            const sine = Math.sin(radians);
            const dx = point.x - center.x;
            const dy = point.y - center.y;
            return {
                x: center.x + dx * cosine - dy * sine,
                y: center.y + dx * sine + dy * cosine,
            };
        }

        function appendWrappedText(group, object) {
            const textNode = svg("text", {
                fill: object.text_color,
                "font-size": object.font_size,
                "font-weight": object.font_weight,
                "font-family": "Inter, sans-serif",
                "dominant-baseline": "auto",
            });
            const padding = object.type === "sticky" ? 13 : 5;
            const availableWidth = Math.max(20, object.width - padding * 2);
            const approximateCharacters = Math.max(1, Math.floor(availableWidth / (object.font_size * 0.54)));
            const lines = [];
            String(object.text || "").split("\n").forEach(function (paragraph) {
                if (!paragraph) {
                    lines.push("");
                    return;
                }
                const words = paragraph.split(/\s+/);
                let line = "";
                words.forEach(function (word) {
                    if (word.length > approximateCharacters) {
                        if (line) { lines.push(line); line = ""; }
                        for (let index = 0; index < word.length; index += approximateCharacters) {
                            lines.push(word.slice(index, index + approximateCharacters));
                        }
                    } else if (!line || (line + " " + word).length <= approximateCharacters) {
                        line += (line ? " " : "") + word;
                    } else {
                        lines.push(line);
                        line = word;
                    }
                });
                if (line) lines.push(line);
            });
            const lineHeight = object.font_size * 1.24;
            const maxLines = Math.max(1, Math.floor((object.height - padding * 2) / lineHeight));
            const visible = lines.slice(0, maxLines);
            if (lines.length > maxLines && visible.length) {
                const last = visible.length - 1;
                visible[last] = visible[last].slice(0, Math.max(1, approximateCharacters - 1)) + "…";
            }
            let x = padding;
            let anchor = "start";
            if (object.text_align === "center") { x = object.width / 2; anchor = "middle"; }
            if (object.text_align === "right") { x = object.width - padding; anchor = "end"; }
            textNode.setAttribute("text-anchor", anchor);
            visible.forEach(function (line, index) {
                const span = svg("tspan", { x: x, y: padding + object.font_size + index * lineHeight });
                span.textContent = line;
                textNode.appendChild(span);
            });
            group.appendChild(textNode);
        }

        function renderObject(object) {
            const group = svg("g", {
                class: "notes-object",
                "data-object-id": object.id,
                opacity: object.opacity,
                transform: "translate(" + object.x + " " + object.y + ") rotate(" + object.rotation + " " + (object.width / 2) + " " + (object.height / 2) + ")",
            });
            if (object.type === "rectangle") {
                group.appendChild(svg("rect", { width: object.width, height: object.height, rx: 10, fill: object.fill, stroke: object.stroke, "stroke-width": object.stroke_width }));
            } else if (object.type === "ellipse") {
                group.appendChild(svg("ellipse", { cx: object.width / 2, cy: object.height / 2, rx: object.width / 2, ry: object.height / 2, fill: object.fill, stroke: object.stroke, "stroke-width": object.stroke_width }));
            } else if (object.type === "line" || object.type === "arrow") {
                group.appendChild(svg("line", { x1: 0, y1: 0, x2: object.width, y2: object.height, stroke: object.stroke, "stroke-width": object.stroke_width, "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" }));
                if (object.type === "arrow") {
                    const angle = Math.atan2(object.height, object.width);
                    const size = Math.max(10, object.stroke_width * 4);
                    const baseX = object.width - Math.cos(angle) * size;
                    const baseY = object.height - Math.sin(angle) * size;
                    const wing = size * 0.52;
                    const points = [
                        [object.width, object.height],
                        [baseX + Math.sin(angle) * wing, baseY - Math.cos(angle) * wing],
                        [baseX - Math.sin(angle) * wing, baseY + Math.cos(angle) * wing],
                    ].map(function (point) { return point[0] + "," + point[1]; }).join(" ");
                    group.appendChild(svg("polygon", { points: points, fill: object.stroke }));
                }
            } else if (object.type === "sticky" || object.type === "text") {
                group.appendChild(svg("rect", {
                    width: object.width, height: object.height,
                    rx: object.type === "sticky" ? 5 : 2,
                    fill: object.fill, stroke: object.stroke, "stroke-width": object.stroke_width,
                }));
                appendWrappedText(group, object);
            }
            return group;
        }

        function renderSelection(object) {
            const zoom = state.viewport.zoom;
            const size = 9 / zoom;
            const radius = size / 2;
            const selection = svg("g", {
                class: "notes-selection",
                transform: "translate(" + object.x + " " + object.y + ") rotate(" + object.rotation + " " + (object.width / 2) + " " + (object.height / 2) + ")",
            });
            if (object.type === "line" || object.type === "arrow") {
                selection.appendChild(svg("line", { class: "notes-selection-box", x1: 0, y1: 0, x2: object.width, y2: object.height }));
                [["start", 0, 0], ["end", object.width, object.height]].forEach(function (handle) {
                    selection.appendChild(svg("circle", { class: "notes-handle", "data-handle": handle[0], "data-object-id": object.id, cx: handle[1], cy: handle[2], r: radius }));
                });
                world.appendChild(selection);
                return;
            }
            selection.appendChild(svg("rect", { class: "notes-selection-box", width: object.width, height: object.height }));
            const positions = {
                nw: [0, 0], n: [object.width / 2, 0], ne: [object.width, 0],
                e: [object.width, object.height / 2], se: [object.width, object.height],
                s: [object.width / 2, object.height], sw: [0, object.height], w: [0, object.height / 2],
            };
            Object.keys(positions).forEach(function (name) {
                selection.appendChild(svg("rect", {
                    class: "notes-handle", "data-handle": name, "data-object-id": object.id,
                    x: positions[name][0] - radius, y: positions[name][1] - radius,
                    width: size, height: size, rx: 1.5 / zoom,
                }));
            });
            const rotateY = -27 / zoom;
            selection.appendChild(svg("line", { class: "notes-selection-line", x1: object.width / 2, y1: 0, x2: object.width / 2, y2: rotateY }));
            selection.appendChild(svg("circle", { class: "notes-handle", "data-handle": "rotate", "data-object-id": object.id, cx: object.width / 2, cy: rotateY, r: radius }));
            world.appendChild(selection);
        }

        function render() {
            world.replaceChildren();
            world.setAttribute("transform", "translate(" + state.viewport.x + " " + state.viewport.y + ") scale(" + state.viewport.zoom + ")");
            state.objects.forEach(function (object) { world.appendChild(renderObject(object)); });
            const selected = selectedObject();
            if (selected) renderSelection(selected);
            document.getElementById("notes-canvas-hint").hidden = state.objects.length > 0;
            document.getElementById("notes-zoom-label").textContent = Math.round(state.viewport.zoom * 100) + "%";
            const gridSize = 24 * state.viewport.zoom;
            stage.style.backgroundSize = gridSize + "px " + gridSize + "px";
            stage.style.backgroundPosition = state.viewport.x + "px " + state.viewport.y + "px";
            syncInspector();
        }

        function syncInspector() {
            const object = selectedObject();
            inspectorEmpty.hidden = Boolean(object);
            inspectorPanel.hidden = !object;
            if (!object) return;
            document.getElementById("notes-selection-type").textContent = object.type;
            document.getElementById("notes-fill-section").hidden = object.type === "line" || object.type === "arrow";
            document.getElementById("notes-text-section").hidden = !["text", "sticky"].includes(object.type);
            document.getElementById("notes-fill").value = object.fill === "transparent" ? "#ffffff" : object.fill;
            document.getElementById("notes-no-fill").checked = object.fill === "transparent";
            document.getElementById("notes-stroke").value = object.stroke === "transparent" ? "#ffffff" : object.stroke;
            document.getElementById("notes-stroke-width").value = object.stroke_width;
            document.getElementById("notes-stroke-width-value").textContent = object.stroke_width;
            document.getElementById("notes-opacity").value = Math.round(object.opacity * 100);
            document.getElementById("notes-opacity-value").textContent = Math.round(object.opacity * 100) + "%";
            if (["text", "sticky"].includes(object.type)) {
                document.getElementById("notes-text-color").value = object.text_color;
                document.getElementById("notes-font-size").value = object.font_size;
                document.getElementById("notes-font-weight").value = object.font_weight;
                root.querySelectorAll("[data-align]").forEach(function (button) {
                    button.classList.toggle("is-active", button.dataset.align === object.text_align);
                });
            }
        }

        function mutateSelected(mutator) {
            const object = selectedObject();
            if (!object) return;
            const snapshot = clone(state.objects);
            mutator(object);
            constrainObject(object);
            pushHistory(snapshot);
            render();
            markDirty();
        }

        function removeSelected() {
            if (!selectedObject()) return;
            const snapshot = clone(state.objects);
            state.objects = state.objects.filter(function (item) { return item.id !== selectedId; });
            selectedId = null;
            pushHistory(snapshot);
            render();
            markDirty();
        }

        function beginTextEdit(object) {
            if (!object || !["text", "sticky"].includes(object.type)) return;
            finishTextEdit(false);
            selectedId = object.id;
            const bounds = stage.getBoundingClientRect();
            const zoom = state.viewport.zoom;
            textEdit = { id: object.id, original: object.text, snapshot: clone(state.objects) };
            textEditor.value = object.text;
            textEditor.hidden = false;
            textEditor.style.left = (state.viewport.x + object.x * zoom) + "px";
            textEditor.style.top = (state.viewport.y + object.y * zoom) + "px";
            textEditor.style.width = Math.max(50, object.width * zoom) + "px";
            textEditor.style.height = Math.max(40, object.height * zoom) + "px";
            textEditor.style.fontSize = Math.max(10, object.font_size * zoom) + "px";
            textEditor.style.fontWeight = object.font_weight;
            textEditor.style.textAlign = object.text_align;
            textEditor.style.color = object.text_color;
            textEditor.style.background = object.fill === "transparent" ? "rgba(255,255,255,.94)" : object.fill;
            textEditor.style.transform = "rotate(" + object.rotation + "deg)";
            textEditor.style.maxWidth = bounds.width + "px";
            textEditor.focus();
            textEditor.select();
            render();
        }

        function finishTextEdit(cancel) {
            if (!textEdit) return;
            const edit = textEdit;
            const object = state.objects.find(function (item) { return item.id === edit.id; });
            if (object && cancel) object.text = edit.original;
            const changed = object && object.text !== edit.original;
            textEdit = null;
            textEditor.hidden = true;
            if (changed && !cancel) {
                pushHistory(edit.snapshot);
                markDirty();
            }
            render();
        }

        textEditor.addEventListener("input", function () {
            const object = selectedObject();
            if (!object || !textEdit) return;
            object.text = textEditor.value.slice(0, 20000);
            render();
        });
        textEditor.addEventListener("blur", function () { finishTextEdit(false); });
        textEditor.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                event.preventDefault();
                finishTextEdit(true);
                stage.focus();
            } else if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                event.preventDefault();
                finishTextEdit(false);
                stage.focus();
            }
        });

        function startPinch() {
            if (pointers.size < 2) return;
            if (action && action.changed && action.snapshot) {
                pushHistory(action.snapshot);
                markDirty();
            }
            const points = Array.from(pointers.values()).slice(0, 2);
            const midpoint = { x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2 };
            action = {
                type: "pinch",
                distance: Math.hypot(points[1].x - points[0].x, points[1].y - points[0].y),
                zoom: state.viewport.zoom,
                anchor: worldPointFromScreen(midpoint),
            };
        }

        function startResize(handle, object, point) {
            action = { type: "resize", handle: handle, id: object.id, original: clone(object), snapshot: clone(state.objects), start: point, changed: false };
        }

        stage.addEventListener("pointerdown", function (event) {
            if (textEdit) finishTextEdit(false);
            const screen = screenPoint(event);
            pointers.set(event.pointerId, screen);
            stage.setPointerCapture(event.pointerId);
            if (pointers.size === 2) {
                startPinch();
                return;
            }
            if (event.button !== 0 && event.button !== 1) return;
            const point = worldPointFromScreen(screen);
            const handle = event.target.closest?.("[data-handle]");
            if (handle) {
                const object = state.objects.find(function (item) { return item.id === handle.dataset.objectId; });
                if (!object) return;
                selectedId = object.id;
                if (handle.dataset.handle === "rotate") {
                    const center = { x: object.x + object.width / 2, y: object.y + object.height / 2 };
                    action = { type: "rotate", id: object.id, original: clone(object), snapshot: clone(state.objects), center: center, startAngle: Math.atan2(point.y - center.y, point.x - center.x) * 180 / Math.PI, changed: false };
                } else {
                    startResize(handle.dataset.handle, object, point);
                }
                return;
            }

            const hit = event.target.closest?.(".notes-object");
            const wantsPan = activeTool === "hand" || spaceHeld || event.button === 1;
            if (wantsPan) {
                action = { type: "pan", start: screen, viewport: clone(state.viewport), changed: false };
                stage.classList.add("is-panning");
                return;
            }

            if (activeTool === "select") {
                if (hit) {
                    selectedId = hit.dataset.objectId;
                    const object = selectedObject();
                    action = { type: "move", id: selectedId, start: point, original: clone(object), snapshot: clone(state.objects), changed: false };
                } else {
                    selectedId = null;
                    action = null;
                }
                render();
                return;
            }

            if (["text", "sticky", "rectangle", "ellipse", "line", "arrow"].includes(activeTool)) {
                if (state.objects.length >= MAX_OBJECTS) {
                    window.alert("This board has reached the 1,000-object limit.");
                    setTool("select");
                    return;
                }
                const object = defaultObject(activeTool, point);
                const snapshot = clone(state.objects);
                state.objects.push(object);
                selectedId = object.id;
                action = { type: "create", id: object.id, tool: activeTool, start: point, original: clone(object), snapshot: snapshot, changed: true, dragged: false };
                render();
            }
        });

        stage.addEventListener("pointermove", function (event) {
            if (!pointers.has(event.pointerId)) return;
            const screen = screenPoint(event);
            pointers.set(event.pointerId, screen);
            if (!action) return;

            if (action.type === "pinch") {
                if (pointers.size < 2) return;
                const points = Array.from(pointers.values()).slice(0, 2);
                const midpoint = { x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2 };
                const distance = Math.max(1, Math.hypot(points[1].x - points[0].x, points[1].y - points[0].y));
                const zoom = clamp(action.zoom * distance / Math.max(1, action.distance), MIN_ZOOM, MAX_ZOOM);
                state.viewport.zoom = zoom;
                state.viewport.x = midpoint.x - action.anchor.x * zoom;
                state.viewport.y = midpoint.y - action.anchor.y * zoom;
                constrainViewport();
                render();
                return;
            }

            if (action.type === "pan") {
                state.viewport.x = action.viewport.x + screen.x - action.start.x;
                state.viewport.y = action.viewport.y + screen.y - action.start.y;
                constrainViewport();
                action.changed = true;
                render();
                return;
            }

            const point = worldPointFromScreen(screen);
            const object = state.objects.find(function (item) { return item.id === action.id; });
            if (!object) return;

            if (action.type === "move") {
                object.x = clamp(action.original.x + point.x - action.start.x, -1000000, 1000000);
                object.y = clamp(action.original.y + point.y - action.start.y, -1000000, 1000000);
                action.changed = true;
            } else if (action.type === "create") {
                const dx = point.x - action.start.x;
                const dy = point.y - action.start.y;
                action.dragged = Math.hypot(dx, dy) > 6 / state.viewport.zoom;
                if (action.tool === "line" || action.tool === "arrow") {
                    object.width = dx;
                    object.height = dy;
                } else if (action.dragged) {
                    object.x = Math.min(action.start.x, point.x);
                    object.y = Math.min(action.start.y, point.y);
                    object.width = Math.max(1, Math.abs(dx));
                    object.height = Math.max(1, Math.abs(dy));
                }
            } else if (action.type === "rotate") {
                const angle = Math.atan2(point.y - action.center.y, point.x - action.center.x) * 180 / Math.PI;
                let rotation = action.original.rotation + angle - action.startAngle;
                if (event.shiftKey) rotation = Math.round(rotation / 15) * 15;
                object.rotation = Math.round(rotation * 10) / 10;
                action.changed = true;
            } else if (action.type === "resize") {
                if (action.handle === "start" || action.handle === "end") {
                    if (action.handle === "start") {
                        const end = { x: action.original.x + action.original.width, y: action.original.y + action.original.height };
                        object.x = point.x;
                        object.y = point.y;
                        object.width = end.x - point.x;
                        object.height = end.y - point.y;
                    } else {
                        object.width = point.x - action.original.x;
                        object.height = point.y - action.original.y;
                    }
                } else {
                    const original = action.original;
                    const oldCenter = { x: original.x + original.width / 2, y: original.y + original.height / 2 };
                    const unrotated = rotatePoint(point, oldCenter, -original.rotation);
                    const local = { x: unrotated.x - original.x, y: unrotated.y - original.y };
                    let left = 0, top = 0, right = original.width, bottom = original.height;
                    if (action.handle.includes("w")) left = Math.min(local.x, right - 30);
                    if (action.handle.includes("e")) right = Math.max(local.x, left + 30);
                    if (action.handle.includes("n")) top = Math.min(local.y, bottom - 30);
                    if (action.handle.includes("s")) bottom = Math.max(local.y, top + 30);
                    const width = right - left;
                    const height = bottom - top;
                    const localCenter = { x: original.x + (left + right) / 2, y: original.y + (top + bottom) / 2 };
                    const worldCenter = rotatePoint(localCenter, oldCenter, original.rotation);
                    object.x = worldCenter.x - width / 2;
                    object.y = worldCenter.y - height / 2;
                    object.width = width;
                    object.height = height;
                }
                action.changed = true;
            }
            constrainObject(object);
            render();
        });

        function finishPointer(event) {
            pointers.delete(event.pointerId);
            if (!action) return;
            if (action.type === "pinch") {
                if (pointers.size < 2) {
                    action = null;
                    markDirty();
                }
                return;
            }
            const completed = action;
            action = null;
            stage.classList.remove("is-panning");
            const object = state.objects.find(function (item) { return item.id === completed.id; });
            if (completed.type === "create" && object) {
                if (!completed.dragged) {
                    Object.assign(object, completed.original);
                } else if ((object.type === "line" || object.type === "arrow") && Math.hypot(object.width, object.height) < 10) {
                    object.width = 180;
                    object.height = 0;
                }
                pushHistory(completed.snapshot);
                markDirty();
                setTool("select");
                render();
                if (object.type === "text" || object.type === "sticky") beginTextEdit(object);
            } else if (completed.type === "pan" && completed.changed) {
                markDirty();
            } else if (completed.changed && completed.snapshot) {
                pushHistory(completed.snapshot);
                markDirty();
                render();
            }
        }

        stage.addEventListener("pointerup", finishPointer);
        stage.addEventListener("pointercancel", finishPointer);

        stage.addEventListener("dblclick", function (event) {
            const hit = event.target.closest?.(".notes-object");
            if (!hit) return;
            const object = state.objects.find(function (item) { return item.id === hit.dataset.objectId; });
            if (object && ["text", "sticky"].includes(object.type)) beginTextEdit(object);
        });

        function zoomAt(screen, nextZoom) {
            const anchor = worldPointFromScreen(screen);
            state.viewport.zoom = clamp(nextZoom, MIN_ZOOM, MAX_ZOOM);
            state.viewport.x = screen.x - anchor.x * state.viewport.zoom;
            state.viewport.y = screen.y - anchor.y * state.viewport.zoom;
            constrainViewport();
            render();
            markDirty();
        }

        stage.addEventListener("wheel", function (event) {
            event.preventDefault();
            const factor = Math.exp(-event.deltaY * 0.0015);
            zoomAt(screenPoint(event), state.viewport.zoom * factor);
        }, { passive: false });

        function zoomCenter(factor) {
            zoomAt({ x: stage.clientWidth / 2, y: stage.clientHeight / 2 }, state.viewport.zoom * factor);
        }

        function fitContent() {
            if (!state.objects.length) {
                state.viewport = { x: 0, y: 0, zoom: 1 };
                render();
                markDirty();
                return;
            }
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            state.objects.forEach(function (object) {
                const right = object.x + object.width;
                const bottom = object.y + object.height;
                minX = Math.min(minX, object.x, right);
                minY = Math.min(minY, object.y, bottom);
                maxX = Math.max(maxX, object.x, right);
                maxY = Math.max(maxY, object.y, bottom);
            });
            const width = Math.max(1, maxX - minX);
            const height = Math.max(1, maxY - minY);
            const zoom = clamp(Math.min((stage.clientWidth - 120) / width, (stage.clientHeight - 120) / height), MIN_ZOOM, 2);
            state.viewport.zoom = zoom;
            state.viewport.x = (stage.clientWidth - width * zoom) / 2 - minX * zoom;
            state.viewport.y = (stage.clientHeight - height * zoom) / 2 - minY * zoom;
            constrainViewport();
            render();
            markDirty();
        }

        root.querySelectorAll("[data-tool]").forEach(function (button) {
            button.addEventListener("click", function () { setTool(button.dataset.tool); stage.focus(); });
        });
        document.getElementById("notes-undo").addEventListener("click", undo);
        document.getElementById("notes-redo").addEventListener("click", redo);
        document.getElementById("notes-delete-object").addEventListener("click", removeSelected);
        document.getElementById("notes-zoom-in").addEventListener("click", function () { zoomCenter(1.2); });
        document.getElementById("notes-zoom-out").addEventListener("click", function () { zoomCenter(1 / 1.2); });
        document.getElementById("notes-zoom-label").addEventListener("click", function () { zoomAt({ x: stage.clientWidth / 2, y: stage.clientHeight / 2 }, 1); });
        document.getElementById("notes-fit").addEventListener("click", fitContent);
        retryButton.addEventListener("click", flushSave);

        document.getElementById("notes-fill").addEventListener("change", function (event) {
            mutateSelected(function (object) { object.fill = event.target.value; });
        });
        document.getElementById("notes-no-fill").addEventListener("change", function (event) {
            mutateSelected(function (object) { object.fill = event.target.checked ? "transparent" : document.getElementById("notes-fill").value; });
        });
        document.getElementById("notes-stroke").addEventListener("change", function (event) {
            mutateSelected(function (object) { object.stroke = event.target.value; });
        });
        document.getElementById("notes-stroke-width").addEventListener("change", function (event) {
            mutateSelected(function (object) { object.stroke_width = Number(event.target.value); });
        });
        document.getElementById("notes-opacity").addEventListener("change", function (event) {
            mutateSelected(function (object) { object.opacity = Number(event.target.value) / 100; });
        });
        document.getElementById("notes-text-color").addEventListener("change", function (event) {
            mutateSelected(function (object) { object.text_color = event.target.value; });
        });
        document.getElementById("notes-font-size").addEventListener("change", function (event) {
            mutateSelected(function (object) { object.font_size = clamp(Number(event.target.value) || 8, 8, 240); });
        });
        document.getElementById("notes-font-weight").addEventListener("change", function (event) {
            mutateSelected(function (object) { object.font_weight = Number(event.target.value); });
        });
        root.querySelectorAll("[data-align]").forEach(function (button) {
            button.addEventListener("click", function () { mutateSelected(function (object) { object.text_align = button.dataset.align; }); });
        });

        root.querySelectorAll("[data-layer]").forEach(function (button) {
            button.addEventListener("click", function () {
                const index = state.objects.findIndex(function (item) { return item.id === selectedId; });
                if (index < 0) return;
                let target = index;
                if (button.dataset.layer === "front") target = state.objects.length - 1;
                if (button.dataset.layer === "forward") target = Math.min(state.objects.length - 1, index + 1);
                if (button.dataset.layer === "backward") target = Math.max(0, index - 1);
                if (button.dataset.layer === "back") target = 0;
                if (target === index) return;
                const snapshot = clone(state.objects);
                const object = state.objects.splice(index, 1)[0];
                state.objects.splice(target, 0, object);
                pushHistory(snapshot);
                render();
                markDirty();
            });
        });

        titleInput.addEventListener("input", function () {
            if (titleInput.value.trim()) {
                lastNonEmptyName = titleInput.value.trim();
                markDirty();
            }
            else setSaveStatus("error", "Name required", false);
        });
        titleInput.addEventListener("blur", function () {
            if (!titleInput.value.trim()) {
                titleInput.value = lastNonEmptyName;
                markDirty();
            }
        });

        document.addEventListener("keydown", function (event) {
            const tag = event.target.tagName;
            const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || event.target.isContentEditable;
            if (event.code === "Space" && !typing) {
                spaceHeld = true;
                stage.classList.add("is-hand");
                event.preventDefault();
            }
            if (typing) return;
            const command = event.ctrlKey || event.metaKey;
            if (command && event.key.toLowerCase() === "z") {
                event.preventDefault();
                if (event.shiftKey) redo(); else undo();
                return;
            }
            if (command && event.key.toLowerCase() === "y") {
                event.preventDefault(); redo(); return;
            }
            if (event.key === "Delete" || event.key === "Backspace") {
                event.preventDefault(); removeSelected(); return;
            }
            if (event.key === "Escape") { setTool("select"); return; }
            if (event.key === "Enter" && selectedObject() && ["text", "sticky"].includes(selectedObject().type)) {
                event.preventDefault(); beginTextEdit(selectedObject()); return;
            }
            if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key) && selectedObject()) {
                event.preventDefault();
                const distance = event.shiftKey ? 10 : 1;
                mutateSelected(function (object) {
                    if (event.key === "ArrowLeft") object.x -= distance;
                    if (event.key === "ArrowRight") object.x += distance;
                    if (event.key === "ArrowUp") object.y -= distance;
                    if (event.key === "ArrowDown") object.y += distance;
                });
                return;
            }
            const tools = { v: "select", h: "hand", t: "text", n: "sticky", r: "rectangle", o: "ellipse", l: "line", a: "arrow" };
            const tool = tools[event.key.toLowerCase()];
            if (tool && !command && !event.altKey) {
                event.preventDefault(); setTool(tool); stage.focus();
            }
        });
        document.addEventListener("keyup", function (event) {
            if (event.code === "Space") {
                spaceHeld = false;
                stage.classList.toggle("is-hand", activeTool === "hand");
            }
        });

        conflictBar.addEventListener("click", function (event) {
            const button = event.target.closest("[data-conflict]");
            if (!button || !pendingConflict) return;
            if (button.dataset.conflict === "remote") {
                state = clone(pendingConflict.document);
                revision = pendingConflict.revision;
                titleInput.value = pendingConflict.name;
                lastNonEmptyName = pendingConflict.name;
                history = [];
                future = [];
                selectedId = null;
                clearRecovery();
                setSaveStatus("", "Saved", false);
                syncHistoryButtons();
                render();
            } else {
                revision = pendingConflict.revision;
                pendingConflict = null;
                conflictBar.hidden = true;
                markDirty();
                flushSave();
                return;
            }
            pendingConflict = null;
            conflictBar.hidden = true;
        });

        recoveryBar.addEventListener("click", function (event) {
            const button = event.target.closest("[data-recovery]");
            if (!button) return;
            if (button.dataset.recovery === "restore" && recoveryBar._draft) {
                state = clone(recoveryBar._draft.document);
                titleInput.value = recoveryBar._draft.name || bootstrap.name;
                lastNonEmptyName = titleInput.value;
                history = [];
                future = [];
                selectedId = null;
                render();
                syncHistoryButtons();
                markDirty();
            } else {
                clearRecovery();
            }
            recoveryBar.hidden = true;
        });

        document.getElementById("notes-delete-board").addEventListener("click", async function () {
            if (!window.confirm('Delete "' + (titleInput.value.trim() || bootstrap.name) + '"? This cannot be undone.')) return;
            try {
                await requestJSON("/notes/boards/" + boardId, { method: "DELETE" });
                clearRecovery();
                window.location.href = "/notes";
            } catch (error) {
                window.alert(error.message);
            }
        });

        window.addEventListener("online", function () {
            if (changeSerial) flushSave();
        });
        window.addEventListener("resize", function () {
            if (textEdit) finishTextEdit(false);
            render();
        });

        try {
            const draft = JSON.parse(localStorage.getItem(recoveryKey) || "null");
            if (draft && draft.board_id === boardId && draft.document && (draft.name !== bootstrap.name || JSON.stringify(draft.document) !== JSON.stringify(bootstrap.document))) {
                recoveryBar._draft = draft;
                recoveryBar.hidden = false;
            }
        } catch (_error) {
            clearRecovery();
        }

        render();
        syncHistoryButtons();
        stage.focus();
    }

    const listPage = document.getElementById("notes-list");
    const editorPage = document.getElementById("notes-editor");
    if (listPage) initListPage(listPage);
    if (editorPage) initEditor(editorPage);
})();
