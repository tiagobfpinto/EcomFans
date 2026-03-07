/* ============================================
   Image Scraper – Client-Side Logic
   ============================================ */

let scrapedImages = [];
let selectedImages = new Set();

/**
 * Scrape images from the URL provided in the input field.
 */
async function scrapeImages() {
    const urlInput = document.getElementById('url-input');
    const url = urlInput.value.trim();

    if (!url) {
        urlInput.focus();
        return;
    }

    // UI state: loading
    hideAllStates();
    show('loading');
    disableButton('scrape-btn', true);

    try {
        const response = await fetch('/scrape', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
        });

        const data = await response.json();
        hide('loading');
        disableButton('scrape-btn', false);

        if (!response.ok) {
            showError(data.error || 'Failed to scrape images.');
            return;
        }

        scrapedImages = data.images || [];
        selectedImages.clear();

        if (scrapedImages.length === 0) {
            show('empty-state');
            return;
        }

        renderImages();
    } catch (err) {
        hide('loading');
        disableButton('scrape-btn', false);
        showError('Network error. Please check your connection and try again.');
    }
}

/**
 * Render the scraped images into the grid.
 */
function renderImages() {
    const grid = document.getElementById('images-grid');
    grid.innerHTML = '';

    scrapedImages.forEach((imgUrl, index) => {
        const filename = getFilename(imgUrl);

        const card = document.createElement('div');
        card.className = 'image-card';
        card.dataset.index = index;
        card.onclick = () => toggleSelect(index);

        card.innerHTML = `
            <div class="card-image">
                <img src="${escapeHtml(imgUrl)}" alt="${escapeHtml(filename)}" loading="lazy"
                     onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2280%22 height=%2280%22 viewBox=%220 0 24 24%22 fill=%22none%22 stroke=%22%235a5a72%22 stroke-width=%221%22><rect x=%223%22 y=%223%22 width=%2218%22 height=%2218%22 rx=%222%22/><circle cx=%228.5%22 cy=%228.5%22 r=%221.5%22/><polyline points=%2221 15 16 10 5 21%22/></svg>'">
            </div>
            <div class="card-info">
                <div class="card-checkbox"></div>
                <span class="card-filename" title="${escapeHtml(imgUrl)}">${escapeHtml(filename)}</span>
            </div>
        `;

        grid.appendChild(card);
    });

    updateCounts();
    show('results-section');
}

/**
 * Toggle selection of a single image card.
 */
function toggleSelect(index) {
    if (selectedImages.has(index)) {
        selectedImages.delete(index);
    } else {
        selectedImages.add(index);
    }

    const card = document.querySelector(`.image-card[data-index="${index}"]`);
    if (card) {
        card.classList.toggle('selected', selectedImages.has(index));
    }

    updateCounts();
}

/**
 * Select all images.
 */
function selectAll() {
    scrapedImages.forEach((_, i) => selectedImages.add(i));
    document.querySelectorAll('.image-card').forEach(card => card.classList.add('selected'));
    updateCounts();
}

/**
 * Deselect all images.
 */
function deselectAll() {
    selectedImages.clear();
    document.querySelectorAll('.image-card').forEach(card => card.classList.remove('selected'));
    updateCounts();
}

/**
 * Update image and selected counts in the toolbar.
 */
function updateCounts() {
    const imageCount = document.getElementById('image-count');
    const selectedCount = document.getElementById('selected-count');
    const downloadBtn = document.getElementById('download-btn');

    if (imageCount) imageCount.textContent = scrapedImages.length;
    if (selectedCount) selectedCount.textContent = selectedImages.size;
    if (downloadBtn) downloadBtn.disabled = selectedImages.size === 0;
}

/**
 * Download selected images as a ZIP file.
 */
async function downloadSelected() {
    if (selectedImages.size === 0) return;

    const urls = Array.from(selectedImages).map(i => scrapedImages[i]);

    show('download-loading');
    disableButton('download-btn', true);

    try {
        const response = await fetch('/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ urls }),
        });

        if (!response.ok) {
            const data = await response.json();
            showError(data.error || 'Failed to download images.');
            return;
        }

        // Trigger download
        const blob = await response.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'scraped_images.zip';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(a.href);
    } catch (err) {
        showError('Download failed. Please try again.');
    } finally {
        hide('download-loading');
        disableButton('download-btn', false);
    }
}

/* ============================================
   Helpers
   ============================================ */

function show(id) {
    const el = document.getElementById(id);
    if (el) el.style.display = 'flex';
}

function hide(id) {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
}

function hideAllStates() {
    ['loading', 'error-state', 'empty-state', 'results-section', 'download-loading'].forEach(hide);
}

function showError(msg) {
    const el = document.getElementById('error-message');
    if (el) el.textContent = msg;
    show('error-state');
}

function disableButton(id, disabled) {
    const el = document.getElementById(id);
    if (el) el.disabled = disabled;
}

function getFilename(url) {
    try {
        const path = new URL(url).pathname;
        const name = path.split('/').pop();
        return name || 'image';
    } catch {
        return 'image';
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/* ============================================
   Keyboard Shortcut
   ============================================ */
document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('url-input');
    if (urlInput) {
        urlInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                scrapeImages();
            }
        });
    }

    // Auto-dismiss flash messages after 5 seconds
    document.querySelectorAll('.flash').forEach(flash => {
        setTimeout(() => {
            flash.style.opacity = '0';
            flash.style.transform = 'translateX(100%)';
            setTimeout(() => flash.remove(), 300);
        }, 5000);
    });
});


/* ============================================
   AI Image – Client-Side Logic
   ============================================ */

let selectedVariations = 1;
let uploadedFiles = [];
let remixEnabled = false;

function getInsufficientCreditsRedirect(creditsElementId) {
    const el = document.getElementById(creditsElementId);
    const tier = (el?.dataset?.planTier || 'free').toLowerCase();
    return tier === 'free' ? '/upgrade' : '/credits-store';
}

/**
 * Set the number of variations for image generation.
 */
function setVariations(count) {
    selectedVariations = count;
    document.querySelectorAll('.var-btn').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.count) === count);
    });
    const costLabel = document.getElementById('cost-label');
    if (costLabel) {
        const credits = count * 2;
        costLabel.textContent = `Cost: ${credits} credits (2 per variation)`;
    }
}

/**
 * Save an API key (works for both Gemini and OpenAI).
 */
async function saveApiKey(service = 'gemini', inputId = 'api-key-input') {
    const input = document.getElementById(inputId);
    const key = input ? input.value.trim() : '';

    if (!key) {
        if (input) input.focus();
        return;
    }

    try {
        const resp = await fetch('/ai-image/api-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: key, service }),
        });
        const data = await resp.json();

        if (data.success) {
            window.location.reload();
        } else {
            alert(data.error || 'Failed to save API key.');
        }
    } catch {
        alert('Network error. Please try again.');
    }
}

/**
 * Toggle the Gemini API key update input.
 */
function toggleApiKeyUpdate() {
    const el = document.getElementById('api-key-update');
    if (el) {
        el.style.display = el.style.display === 'none' ? 'flex' : 'none';
    }
}

/**
 * Toggle the OpenAI API key update input.
 */
function toggleOpenAIKeyUpdate() {
    const el = document.getElementById('openai-key-update');
    if (el) {
        el.style.display = el.style.display === 'none' ? 'flex' : 'none';
    }
}

/**
 * Toggle the remix prompt feature on/off.
 */
function toggleRemix() {
    const toggle = document.getElementById('remix-toggle');
    remixEnabled = toggle ? toggle.checked : false;
    const options = document.getElementById('remix-options');
    if (options) {
        options.style.display = remixEnabled ? 'block' : 'none';
    }
}

/**
 * Generate images from the prompt input.
 */
async function generateImages() {
    const promptInput = document.getElementById('prompt-input');
    const prompt = promptInput ? promptInput.value.trim() : '';

    if (!prompt) {
        promptInput.focus();
        return;
    }

    // Check credits
    const creditsEl = document.getElementById('credits-count');
    const currentCredits = parseInt(creditsEl?.textContent || '0');
    const requiredCredits = selectedVariations * 2;
    if (currentCredits < requiredCredits) {
        alert(`Not enough credits. You need ${requiredCredits} but have ${currentCredits}. Redirecting...`);
        window.location.href = getInsufficientCreditsRedirect('credits-count');
        return;
    }

    // Show loading
    const genLoading = document.getElementById('gen-loading');
    const genResults = document.getElementById('gen-results');
    const genBtn = document.getElementById('generate-btn');

    if (genLoading) genLoading.style.display = 'flex';
    if (genResults) genResults.style.display = 'none';
    if (genBtn) genBtn.disabled = true;

    try {
        const formData = new FormData();
        formData.append('prompt', prompt);
        formData.append('variations', selectedVariations);
        formData.append('remix', remixEnabled ? 'true' : 'false');

        if (remixEnabled) {
            const remixPrompt = document.getElementById('remix-system-prompt');
            if (remixPrompt) {
                formData.append('remix_system_prompt', remixPrompt.value);
            }
        }

        uploadedFiles.forEach((file, idx) => {
            formData.append(`image_${idx}`, file);
        });

        const resp = await fetch('/ai-image/generate', {
            method: 'POST',
            body: formData,
        });
        const data = await resp.json();

        if (genLoading) genLoading.style.display = 'none';
        if (genBtn) genBtn.disabled = false;

        if (!resp.ok) {
            if (data.error === 'no_api_key') {
                alert('Gemini API key is not configured on the platform.');
                return;
            }

            if (data.error === 'no_openai_key') {
                alert('OpenAI API key is not configured on the platform for prompt remixing.');
                return;
            }

            if (data.redirect_url) {
                alert((data.error || 'Action required.') + ' Redirecting...');
                window.location.href = data.redirect_url;
                return;
            }
            alert(data.error || 'Generation failed.');
            return;
        }

        // Update credits
        if (creditsEl && data.credits_remaining !== undefined) {
            creditsEl.textContent = data.credits_remaining;
        }
        const monthlyEl = document.getElementById('monthly-credits-count');
        const extraEl = document.getElementById('extra-credits-count');
        if (monthlyEl && data.monthly_credits !== undefined) {
            monthlyEl.textContent = data.monthly_credits;
        }
        if (extraEl && data.extra_credits !== undefined) {
            extraEl.textContent = data.extra_credits;
        }

        // Show results
        renderGenerationResults(data.results);

        // Clear input and uploaded files
        promptInput.value = '';
        uploadedFiles = [];
        const previews = document.getElementById('image-previews');
        if (previews) previews.innerHTML = '';
        const uploadZone = document.getElementById('upload-zone');
        if (uploadZone) uploadZone.style.display = '';

        // Reload page after short delay to update history
        setTimeout(() => window.location.reload(), 1500);

    } catch {
        if (genLoading) genLoading.style.display = 'none';
        if (genBtn) genBtn.disabled = false;
        alert('Network error. Please try again.');
    }
}

/**
 * Render generation results into the results grid.
 */
function renderGenerationResults(results) {
    const container = document.getElementById('gen-results');
    const grid = document.getElementById('gen-results-grid');
    if (!container || !grid) return;

    grid.innerHTML = '';

    results.forEach(r => {
        const card = document.createElement('div');
        card.className = 'gen-result-card';

        if (r.status === 'completed' && r.image_data) {
            card.innerHTML = `
                <img src="data:image/png;base64,${r.image_data}"
                     alt="Generated variation ${r.variation}"
                     onclick="showFullImageSrc(this.src)">
                <div class="gen-result-label">
                    <span class="var-badge">Variation ${r.variation}</span>
                </div>
            `;
        } else {
            card.innerHTML = `
                <div class="gen-result-error">
                    <p>⚠ Failed: ${escapeHtml(r.error || 'Unknown error')}</p>
                </div>
                <div class="gen-result-label">
                    <span class="var-badge">Variation ${r.variation}</span>
                </div>
            `;
        }

        grid.appendChild(card);
    });

    container.style.display = 'block';
}

/**
 * Create API key banner HTML for when no key is set.
 */
function createApiKeyBannerHTML() {
    return `
        <div class="api-key-banner-content">
            <div class="api-key-icon">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
                    stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
                </svg>
            </div>
            <div class="api-key-text">
                <h3>Gemini API Key Required</h3>
                <p>To generate images, you need a Google Gemini API key.</p>
                <ol>
                    <li>Go to <a href="https://aistudio.google.com/apikey" target="_blank">Google AI Studio</a></li>
                    <li>Sign in with your Google account</li>
                    <li>Click <strong>"Create API Key"</strong></li>
                    <li>Copy the key and paste it below</li>
                </ol>
            </div>
        </div>
        <div class="api-key-input-row">
            <input type="password" id="api-key-input" placeholder="Paste your Gemini API key here..." autocomplete="off">
            <button class="btn btn-primary" onclick="saveApiKey()">Save Key</button>
        </div>
    `;
}

/**
 * Re-run a prompt from history.
 */
function rerunPrompt(text) {
    const promptInput = document.getElementById('prompt-input');
    if (promptInput) {
        promptInput.value = text;
        promptInput.focus();
        promptInput.scrollIntoView({ behavior: 'smooth' });
    }
}

/**
 * Delete a prompt from history.
 */
async function deletePrompt(promptId) {
    if (!confirm('Delete this prompt and its generated images?')) return;

    try {
        const resp = await fetch(`/ai-image/prompt/${promptId}`, { method: 'DELETE' });
        const data = await resp.json();

        if (data.success) {
            const card = document.querySelector(`.prompt-history-card[data-prompt-id="${promptId}"]`);
            if (card) {
                card.style.opacity = '0';
                card.style.transform = 'translateX(20px)';
                card.style.transition = 'all 0.3s ease';
                setTimeout(() => card.remove(), 300);
            }
        } else {
            alert(data.error || 'Failed to delete prompt.');
        }
    } catch {
        alert('Network error. Please try again.');
    }
}

/**
 * Show full-size image in modal from a thumbnail click.
 */
function showFullImage(thumbEl) {
    const img = thumbEl.querySelector('img');
    if (!img) return;
    showFullImageSrc(img.src);
}

/**
 * Show full-size image in modal given a src URL.
 */
function showFullImageSrc(src) {
    const modal = document.getElementById('image-modal');
    const modalImg = document.getElementById('modal-image');
    if (modal && modalImg) {
        modalImg.src = src;
        modal.style.display = 'flex';
    }
}

/**
 * Close the image modal.
 */
function closeModal(event) {
    if (event.target === event.currentTarget) {
        document.getElementById('image-modal').style.display = 'none';
    }
}

// Close modal on Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const modal = document.getElementById('image-modal');
        if (modal) modal.style.display = 'none';
    }
});

/**
 * Handle image file uploads (up to 3).
 */
function handleImageUpload(input) {
    const files = Array.from(input.files);
    const remaining = 3 - uploadedFiles.length;

    if (remaining <= 0) {
        alert('Maximum 3 images allowed.');
        input.value = '';
        return;
    }

    const toAdd = files.slice(0, remaining);
    uploadedFiles.push(...toAdd);
    renderImagePreviews();
    input.value = '';
}

/**
 * Remove an uploaded image by index.
 */
function removeUploadedImage(index) {
    uploadedFiles.splice(index, 1);
    renderImagePreviews();
}

/**
 * Render thumbnail previews of uploaded images.
 */
function renderImagePreviews() {
    const container = document.getElementById('image-previews');
    const uploadZone = document.getElementById('upload-zone');
    if (!container) return;

    container.innerHTML = '';

    uploadedFiles.forEach((file, idx) => {
        const thumb = document.createElement('div');
        thumb.className = 'upload-preview';

        const reader = new FileReader();
        reader.onload = (e) => {
            thumb.innerHTML = `
                <img src="${e.target.result}" alt="${escapeHtml(file.name)}">
                <button class="upload-preview-remove" onclick="removeUploadedImage(${idx})" title="Remove">&times;</button>
                <span class="upload-preview-name">${escapeHtml(file.name)}</span>
            `;
        };
        reader.readAsDataURL(file);

        container.appendChild(thumb);
    });

    // Hide upload zone if 3 images reached
    if (uploadZone) {
        uploadZone.style.display = uploadedFiles.length >= 3 ? 'none' : '';
    }
}


/* ============================================
   Avatars – Client-Side Logic
   ============================================ */

let selectedPersonas = [];
let avatarCountPerPersona = 1;

/**
 * Toggle a persona chip on/off.
 */
function togglePersonaChip(el) {
    const persona = el.dataset.persona;
    el.classList.toggle('active');
    if (el.classList.contains('active')) {
        if (!selectedPersonas.includes(persona)) {
            selectedPersonas.push(persona);
        }
    } else {
        selectedPersonas = selectedPersonas.filter(p => p !== persona);
    }
    updateAvatarCost();
}

/**
 * Add a custom persona.
 */
function addCustomPersona() {
    const input = document.getElementById('custom-persona-input');
    const val = input ? input.value.trim() : '';
    if (!val) return;
    if (selectedPersonas.includes(val)) {
        input.value = '';
        return;
    }

    // Create chip
    const container = document.getElementById('persona-chips');
    if (container) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'persona-chip active custom';
        chip.dataset.persona = val;
        chip.textContent = val;
        chip.onclick = function () { togglePersonaChip(this); };
        container.appendChild(chip);
    }

    selectedPersonas.push(val);
    input.value = '';
    updateAvatarCost();
}

/**
 * Set avatar count per persona.
 */
function setAvatarCount(count) {
    avatarCountPerPersona = count;
    document.querySelectorAll('.avatars-page .var-btn').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.count) === count);
    });
    updateAvatarCost();
}

/**
 * Update cost label.
 */
function updateAvatarCost() {
    const total = selectedPersonas.length * avatarCountPerPersona * 4;
    const label = document.getElementById('avatar-cost-label');
    if (label) {
        label.textContent = `Cost: ${total} credits (4 per avatar pair)`;
    }
}

/**
 * Generate avatar before/after pairs.
 */
async function generateAvatars() {
    const productSelect = document.getElementById('avatar-product');
    const charInput = document.getElementById('avatar-characteristic');
    const productId = productSelect ? productSelect.value : '';
    const characteristic = charInput ? charInput.value.trim() : '';

    if (!productId) { alert('Please select a product.'); return; }
    if (!characteristic) { charInput.focus(); return; }
    if (selectedPersonas.length === 0) { alert('Select at least one persona.'); return; }

    const totalCost = selectedPersonas.length * avatarCountPerPersona * 4;
    const creditsEl = document.getElementById('avatar-credits-count');
    const currentCredits = parseInt(creditsEl?.textContent || '0');
    if (currentCredits < totalCost) {
        alert(`Not enough credits. You need ${totalCost} but have ${currentCredits}. Redirecting...`);
        window.location.href = getInsufficientCreditsRedirect('avatar-credits-count');
        return;
    }

    const loading = document.getElementById('avatar-loading');
    const results = document.getElementById('avatar-results');
    const btn = document.getElementById('avatar-generate-btn');

    if (loading) loading.style.display = 'flex';
    if (results) results.style.display = 'none';
    if (btn) btn.disabled = true;

    try {
        const resp = await fetch('/avatars/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                product_id: parseInt(productId),
                characteristic,
                personas: selectedPersonas,
                count_per_persona: avatarCountPerPersona,
            }),
        });
        const data = await resp.json();

        if (loading) loading.style.display = 'none';
        if (btn) btn.disabled = false;

        if (!resp.ok) {
            if (data.error === 'no_api_key') {
                alert('Gemini API key is not configured on the platform.');
                return;
            }
            if (data.redirect_url) {
                alert((data.error || 'Action required.') + ' Redirecting...');
                window.location.href = data.redirect_url;
                return;
            }
            alert(data.error || 'Generation failed.');
            return;
        }

        if (creditsEl && data.credits_remaining !== undefined) {
            creditsEl.textContent = data.credits_remaining;
        }
        const monthlyEl = document.getElementById('avatar-monthly-credits-count');
        const extraEl = document.getElementById('avatar-extra-credits-count');
        if (monthlyEl && data.monthly_credits !== undefined) {
            monthlyEl.textContent = data.monthly_credits;
        }
        if (extraEl && data.extra_credits !== undefined) {
            extraEl.textContent = data.extra_credits;
        }

        renderAvatarResults(data.results);
        setTimeout(() => window.location.reload(), 2000);

    } catch {
        if (loading) loading.style.display = 'none';
        if (btn) btn.disabled = false;
        alert('Network error. Please try again.');
    }
}

/**
 * Render avatar before/after results.
 */
function renderAvatarResults(results) {
    const container = document.getElementById('avatar-results');
    const grid = document.getElementById('avatar-results-grid');
    if (!container || !grid) return;

    grid.innerHTML = '';
    container.style.display = 'block';

    results.forEach(r => {
        const card = document.createElement('div');
        card.className = 'avatar-result-card';

        if (r.status === 'completed') {
            card.innerHTML = `
                <div class="avatar-persona-label">${escapeHtml(r.persona)}</div>
                <div class="avatar-ba-pair">
                    <div class="avatar-ba-img" onclick="showFullImage(this)">
                        <img src="data:image/png;base64,${r.before_image}" alt="Before">
                        <span class="ba-label ba-before">Before</span>
                    </div>
                    <div class="avatar-ba-arrow">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                            stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <line x1="5" y1="12" x2="19" y2="12" />
                            <polyline points="12 5 19 12 12 19" />
                        </svg>
                    </div>
                    <div class="avatar-ba-img" onclick="showFullImage(this)">
                        <img src="data:image/png;base64,${r.after_image}" alt="After">
                        <span class="ba-label ba-after">After</span>
                    </div>
                </div>
            `;
        } else {
            card.innerHTML = `
                <div class="avatar-persona-label">${escapeHtml(r.persona)}</div>
                <div class="avatar-failed">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <circle cx="12" cy="12" r="10" />
                        <line x1="15" y1="9" x2="9" y2="15" />
                        <line x1="9" y1="9" x2="15" y2="15" />
                    </svg>
                    <span>Failed: ${escapeHtml(r.error || 'Unknown error')}</span>
                </div>
            `;
        }
        grid.appendChild(card);
    });
}

/**
 * Delete an avatar batch.
 */
async function deleteAvatarBatch(batchId) {
    if (!confirm('Delete this avatar batch?')) return;

    try {
        const resp = await fetch(`/avatars/batch/${batchId}`, { method: 'DELETE' });
        const data = await resp.json();
        if (data.success) {
            const card = document.querySelector(`[data-batch-id="${batchId}"]`);
            if (card) card.remove();
        } else {
            alert(data.error || 'Failed to delete batch.');
        }
    } catch {
        alert('Network error.');
    }
}
