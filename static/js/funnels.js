(function () {
    "use strict";

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

    function setBusy(button, busy, busyLabel, normalLabel) {
        if (!button) return;
        button.disabled = busy;
        button.textContent = busy ? busyLabel : normalLabel;
    }

    function createFlash(root) {
        const flash = root.querySelector("#fn-flash");
        return function showFlash(message, isError) {
            if (!flash) return;
            flash.textContent = message;
            flash.className = "fn-flash" + (isError ? " is-error" : "");
            flash.hidden = false;
            clearTimeout(showFlash.timer);
            showFlash.timer = setTimeout(function () { flash.hidden = true; }, 5000);
        };
    }

    function modalController(modal, onOpen) {
        function open() {
            if (!modal) return;
            modal.hidden = false;
            document.body.style.overflow = "hidden";
            if (onOpen) onOpen();
        }
        function close() {
            if (!modal) return;
            modal.hidden = true;
            document.body.style.overflow = "";
            const form = modal.querySelector("form");
            if (form) form.reset();
            const error = modal.querySelector(".fn-form-error");
            if (error) error.hidden = true;
        }
        modal?.addEventListener("click", function (event) {
            if (event.target.closest("[data-close-modal]")) close();
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && modal && !modal.hidden) close();
        });
        return { open: open, close: close };
    }

    async function copyText(value) {
        try {
            await navigator.clipboard.writeText(value);
        } catch (_error) {
            const input = document.createElement("textarea");
            input.value = value;
            input.style.position = "fixed";
            input.style.opacity = "0";
            document.body.appendChild(input);
            input.select();
            document.execCommand("copy");
            input.remove();
        }
    }

    function initFunnelsList(root) {
        const modal = document.getElementById("fn-funnel-modal");
        const form = document.getElementById("fn-funnel-form");
        const nameInput = document.getElementById("fn-funnel-name");
        const descriptionInput = document.getElementById("fn-funnel-description");
        const errorLabel = document.getElementById("fn-funnel-error");
        const submitButton = document.getElementById("fn-create-funnel");
        const controller = modalController(modal, function () {
            window.requestAnimationFrame(function () { nameInput?.focus(); });
        });

        document.getElementById("fn-new-funnel")?.addEventListener("click", controller.open);
        root.querySelectorAll("[data-open-funnel-modal]").forEach(function (button) {
            button.addEventListener("click", controller.open);
        });

        form?.addEventListener("submit", async function (event) {
            event.preventDefault();
            const name = nameInput.value.trim();
            if (!name) {
                errorLabel.textContent = "Enter a funnel name.";
                errorLabel.hidden = false;
                nameInput.focus();
                return;
            }
            setBusy(submitButton, true, "Creating…", "Create funnel");
            try {
                const payload = await requestJSON("/funnels", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        name: name,
                        description: descriptionInput.value.trim(),
                    }),
                });
                window.location.href = "/funnels/" + payload.funnel.id;
            } catch (error) {
                errorLabel.textContent = error.message;
                errorLabel.hidden = false;
                setBusy(submitButton, false, "Creating…", "Create funnel");
            }
        });
    }

    function initFunnelDetail(root) {
        const funnelId = Number(root.dataset.funnelId);
        const showFlash = createFlash(root);
        const modal = document.getElementById("fn-page-modal");
        const form = document.getElementById("fn-page-form");
        const titleInput = document.getElementById("fn-page-title");
        const slugInput = document.getElementById("fn-page-slug");
        const errorLabel = document.getElementById("fn-page-error");
        const submitButton = document.getElementById("fn-create-page");
        let slugWasEdited = false;
        const controller = modalController(modal, function () {
            slugWasEdited = false;
            window.requestAnimationFrame(function () { titleInput?.focus(); });
        });

        document.getElementById("fn-new-page")?.addEventListener("click", controller.open);
        root.querySelectorAll("[data-open-page-modal]").forEach(function (button) {
            button.addEventListener("click", controller.open);
        });
        slugInput?.addEventListener("input", function () { slugWasEdited = true; });
        slugInput?.addEventListener("blur", function () { slugInput.value = slugify(slugInput.value); });
        titleInput?.addEventListener("input", function () {
            if (!slugWasEdited) slugInput.value = slugify(titleInput.value);
        });

        form?.addEventListener("submit", async function (event) {
            event.preventDefault();
            const title = titleInput.value.trim();
            const slug = slugify(slugInput.value);
            const templateInput = form.querySelector('input[name="template_id"]:checked');
            slugInput.value = slug;
            if (!title || !slug || !templateInput) {
                errorLabel.textContent = !templateInput
                    ? "Choose a page template."
                    : (title ? "Enter a URL slug." : "Enter a page title.");
                errorLabel.hidden = false;
                (templateInput ? (title ? slugInput : titleInput) : form.querySelector('input[name="template_id"]'))?.focus();
                return;
            }
            setBusy(submitButton, true, "Creating…", "Create and edit");
            try {
                const payload = await requestJSON("/funnels/" + funnelId + "/pages", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ title: title, template_id: templateInput.value, slug: slug }),
                });
                window.location.href = "/funnels/" + funnelId + "/pages/" + payload.page.id;
            } catch (error) {
                errorLabel.textContent = error.message;
                errorLabel.hidden = false;
                setBusy(submitButton, false, "Creating…", "Create and edit");
            }
        });

        root.querySelector("[data-edit-funnel]")?.addEventListener("click", async function (event) {
            const currentName = root.querySelector("[data-funnel-name]").textContent.trim();
            const currentDescription = root.dataset.description || "";
            const name = window.prompt("Funnel name", currentName);
            if (name == null) return;
            if (!name.trim()) {
                showFlash("Funnel name cannot be empty.", true);
                return;
            }
            const description = window.prompt("Funnel description (optional)", currentDescription);
            if (description == null) return;
            event.currentTarget.disabled = true;
            try {
                const payload = await requestJSON("/funnels/" + funnelId, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name: name.trim(), description: description.trim() }),
                });
                root.querySelector("[data-funnel-name]").textContent = payload.funnel.name;
                root.querySelector("[data-funnel-description]").textContent = payload.funnel.description || "Add a description to explain this conversion flow.";
                root.dataset.description = payload.funnel.description || "";
                showFlash("Funnel details updated.", false);
            } catch (error) {
                showFlash(error.message, true);
            } finally {
                event.currentTarget.disabled = false;
            }
        });

        root.querySelector("[data-delete-funnel]")?.addEventListener("click", async function (event) {
            const name = root.querySelector("[data-funnel-name]").textContent.trim();
            if (!window.confirm('Delete the funnel "' + name + '" and all its pages? This cannot be undone.')) return;
            event.currentTarget.disabled = true;
            try {
                await requestJSON("/funnels/" + funnelId, { method: "DELETE" });
                window.location.href = "/funnels";
            } catch (error) {
                showFlash(error.message, true);
                event.currentTarget.disabled = false;
            }
        });

        root.addEventListener("click", async function (event) {
            const copyButton = event.target.closest("[data-copy-path]");
            if (copyButton) {
                await copyText(window.location.origin + copyButton.dataset.copyPath);
                showFlash("Page URL copied.", false);
                return;
            }

            const actionButton = event.target.closest("[data-page-action]");
            if (!actionButton) return;
            const card = actionButton.closest("[data-page-id]");
            const pageId = Number(card.dataset.pageId);
            const title = card.dataset.pageTitle;
            const action = actionButton.dataset.pageAction;
            if (action === "duplicate") {
                actionButton.disabled = true;
                try {
                    const payload = await requestJSON("/funnels/" + funnelId + "/pages/" + pageId + "/duplicate", { method: "POST" });
                    window.location.href = "/funnels/" + funnelId + "/pages/" + payload.page.id;
                } catch (error) {
                    showFlash(error.message, true);
                    actionButton.disabled = false;
                }
                return;
            }
            if (action === "delete" && window.confirm('Delete the page "' + title + '"? This cannot be undone.')) {
                actionButton.disabled = true;
                try {
                    await requestJSON("/funnels/" + funnelId + "/pages/" + pageId, { method: "DELETE" });
                    window.location.reload();
                } catch (error) {
                    showFlash(error.message, true);
                    actionButton.disabled = false;
                }
            }
        });
    }

    const listRoot = document.getElementById("funnels-list");
    if (listRoot) initFunnelsList(listRoot);
    const detailRoot = document.getElementById("funnel-detail");
    if (detailRoot) initFunnelDetail(detailRoot);
})();
