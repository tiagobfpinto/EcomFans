function parseProductImagesBootstrap() {
    const el = document.getElementById('product-images-bootstrap');
    if (!el) return {};
    try {
        return JSON.parse(el.textContent || '{}');
    } catch {
        return {};
    }
}

const PRODUCT_IMAGES_BOOTSTRAP = parseProductImagesBootstrap();
const LACY_MAX_SOURCE_IMAGES = 10;
const LACY_JOB_POLL_INTERVAL_MS = 1800;
const LACY_JOB_POLL_TIMEOUT_MS = 30 * 60 * 1000;
const LACY_CREDITS_PER_IMAGE = 2;
let lacyManualFiles = [];
let lacyLatestSources = [];

function setProductImagesStep(activeStep) {
    const stepAgents = document.getElementById('pi-step-agents');
    const stepConfig = document.getElementById('pi-step-config');
    const showConfig = activeStep === 'config';

    if (stepAgents) {
        stepAgents.classList.toggle('pi-step-hidden', showConfig);
        stepAgents.hidden = showConfig;
        stepAgents.style.display = showConfig ? 'none' : '';
    }
    if (stepConfig) {
        stepConfig.classList.toggle('pi-step-hidden', !showConfig);
        stepConfig.hidden = !showConfig;
        stepConfig.style.display = showConfig ? '' : 'none';
    }

    document.querySelectorAll('.pi-agent-card[data-agent]').forEach(card => {
        const isActive = showConfig && card.dataset.agent === 'lacy';
        card.classList.toggle('is-active', isActive);
        card.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
}

function selectProductAgent(agentName) {
    if (agentName !== 'lacy') return;
    setProductImagesStep('config');
    document.getElementById('lacy-product')?.focus();
}

function goBackToAgentSelection() {
    setProductImagesStep('agents');
}

// Legacy compat
function selectProductImagesAgent(agentName) {
    selectProductAgent(agentName);
}

window.selectProductAgent = selectProductAgent;
window.selectProductImagesAgent = selectProductImagesAgent;
window.goBackToAgentSelection = goBackToAgentSelection;

function lacyIsAutoScrape() {
    const toggle = document.getElementById('lacy-auto-scrape-toggle');
    return Boolean(toggle && toggle.checked);
}

function toggleLacySourceMode() {
    const autoPanel = document.getElementById('lacy-auto-source');
    const manualPanel = document.getElementById('lacy-manual-source');
    const isAuto = lacyIsAutoScrape();
    if (autoPanel) autoPanel.style.display = isAuto ? '' : 'none';
    if (manualPanel) manualPanel.style.display = isAuto ? 'none' : '';
    lacyLatestSources = [];
    updateLacyCostLabel();
}

function updateLacyProductHint() {
    const select = document.getElementById('lacy-product');
    const hint = document.getElementById('lacy-product-hint');
    if (!select || !hint) return;

    const option = select.options[select.selectedIndex];
    const imageCount = Number(option?.dataset?.imageCount || 0);
    if (!select.value) {
        hint.textContent = 'Select a product with at least one saved product image.';
    } else if (imageCount <= 0) {
        const productsUrl = PRODUCT_IMAGES_BOOTSTRAP.productsUrl || '/products';
        hint.innerHTML = `This product has no product images. Add at least one image in <a href="${escapeHtml(productsUrl)}">Products</a> first.`;
    } else {
        hint.textContent = `${imageCount} product image${imageCount !== 1 ? 's' : ''} available for replacement context.`;
    }
}

function updateLacyCostLabel() {
    const label = document.getElementById('lacy-cost-label');
    if (!label) return;

    if (lacyIsAutoScrape()) {
        const count = lacyLatestSources.length;
        if (count > 0) {
            label.textContent = `Cost: ${count * LACY_CREDITS_PER_IMAGE} credits (${count} scraped image${count !== 1 ? 's' : ''})`;
        } else {
            label.textContent = `Cost: up to ${LACY_MAX_SOURCE_IMAGES * LACY_CREDITS_PER_IMAGE} credits (${LACY_CREDITS_PER_IMAGE} per scraped image)`;
        }
        return;
    }

    const count = lacyManualFiles.length;
    label.textContent = `Cost: ${count * LACY_CREDITS_PER_IMAGE} credits (${count} uploaded image${count !== 1 ? 's' : ''})`;
}

function getLacyProductImageCount() {
    const select = document.getElementById('lacy-product');
    if (!select) return 0;
    const option = select.options[select.selectedIndex];
    return Number(option?.dataset?.imageCount || 0);
}

function handleLacySourceFileSelect(event) {
    addLacySourceFiles(Array.from(event.target.files || []));
    event.target.value = '';
}

function handleLacySourceDrop(event) {
    event.preventDefault();
    const uploadZone = document.getElementById('lacy-upload-zone');
    if (uploadZone) uploadZone.classList.remove('drag-over');
    addLacySourceFiles(Array.from(event.dataTransfer.files || []));
}

function addLacySourceFiles(files) {
    const imageFiles = files.filter(file => file.type && file.type.startsWith('image/'));
    const remaining = LACY_MAX_SOURCE_IMAGES - lacyManualFiles.length;
    if (remaining <= 0) {
        showProductImagesFlash(`Upload at most ${LACY_MAX_SOURCE_IMAGES} source images.`, 'error');
        return;
    }
    lacyManualFiles.push(...imageFiles.slice(0, remaining));
    if (imageFiles.length > remaining) {
        showProductImagesFlash(`Only the first ${remaining} image${remaining !== 1 ? 's' : ''} were added.`, 'error');
    }
    renderLacyManualPreviews();
    updateLacyCostLabel();
}

function removeLacySourceFile(index) {
    lacyManualFiles.splice(index, 1);
    renderLacyManualPreviews();
    updateLacyCostLabel();
}

function renderLacyManualPreviews() {
    const grid = document.getElementById('lacy-manual-previews');
    if (!grid) return;
    grid.innerHTML = '';

    lacyManualFiles.forEach((file, index) => {
        const card = document.createElement('div');
        card.className = 'lacy-source-thumb';
        const reader = new FileReader();
        reader.onload = event => {
            card.innerHTML = `
                <button type="button" class="lacy-source-remove" onclick="removeLacySourceFile(${index})">&times;</button>
                <img src="${event.target.result}" alt="${escapeHtml(file.name)}" onclick="showFullImageSrc(this.src)">
                <span class="lacy-source-thumb-label" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
            `;
        };
        reader.readAsDataURL(file);
        grid.appendChild(card);
    });
}

function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '');
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

async function uploadLacyManualSources() {
    if (lacyManualFiles.length < 1) {
        throw new Error('Upload 1 to 10 source images, or turn Auto scrape on.');
    }

    const images = [];
    for (const file of lacyManualFiles) {
        images.push({
            name: file.name,
            mime_type: file.type || 'image/jpeg',
            data: await fileToBase64(file),
        });
    }

    const resp = await fetch('/ai-creatives/inspirations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ images }),
    });
    const data = await resp.json();
    if (!resp.ok) {
        throw new Error(data.error || 'Failed to upload source images.');
    }
    return (data.saved || []).map(item => ({
        ...item,
        image_url: `/media/inspirations/${item.id}`,
    }));
}

async function autoScrapeLacySources() {
    const urlInput = document.getElementById('lacy-competitor-url');
    const url = urlInput ? urlInput.value.trim() : '';
    if (!url) {
        if (urlInput) urlInput.focus();
        throw new Error('Competitor URL is required when Auto scrape is on.');
    }

    const resp = await fetch('/product-images/lacy/auto-scrape', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, limit: LACY_MAX_SOURCE_IMAGES }),
    });
    const data = await resp.json();
    if (!resp.ok) {
        throw new Error(data.error || 'Failed to auto scrape competitor images.');
    }
    showProductImagesFlash(
        `Imported ${data.imported_count} of ${data.scraped_count} scraped image${data.scraped_count !== 1 ? 's' : ''}.`,
        'success'
    );
    return data.saved || [];
}

function renderLacySourcePreview(sources) {
    const section = document.getElementById('lacy-source-preview');
    const grid = document.getElementById('lacy-source-preview-grid');
    const summary = document.getElementById('lacy-source-summary');
    if (!section || !grid || !summary) return;

    grid.innerHTML = '';
    if (!sources.length) {
        section.style.display = 'none';
        return;
    }

    summary.textContent = `${sources.length} source image${sources.length !== 1 ? 's' : ''} queued for mockup replacement.`;
    sources.forEach((source, index) => {
        const card = document.createElement('div');
        card.className = 'lacy-source-thumb';
        const src = source.image_url || `/media/inspirations/${source.id}`;
        card.innerHTML = `
            <img src="${escapeHtml(src)}" alt="Source image ${index + 1}" onclick="showFullImageSrc(this.src)">
            <span class="lacy-source-thumb-label" title="${escapeHtml(source.name || source.source_url || '')}">Source ${index + 1}</span>
        `;
        grid.appendChild(card);
    });
    section.style.display = '';
}

function getLacyCreditsAvailable() {
    const el = document.getElementById('lacy-credits-count');
    return Number.parseInt(el?.textContent || '0', 10) || 0;
}

function ensureLacyCredits(sourceCount) {
    const required = sourceCount * LACY_CREDITS_PER_IMAGE;
    const available = getLacyCreditsAvailable();
    if (required <= available) return true;

    showProductImagesFlash(`Not enough credits. Need ${required}, have ${available}. Redirecting...`, 'error');
    setTimeout(() => {
        window.location.href = PRODUCT_IMAGES_BOOTSTRAP.creditsRedirectUrl || '/credits-store';
    }, 500);
    return false;
}

async function generateLacyProductImages() {
    const productId = document.getElementById('lacy-product')?.value || '';
    const prompt = document.getElementById('lacy-prompt')?.value.trim() || '';
    const model = document.getElementById('lacy-gemini-model')?.value || '';
    const btn = document.getElementById('lacy-generate-btn');
    const loading = document.getElementById('lacy-loading');
    const loadingText = document.getElementById('lacy-loading-text');

    if (!productId) {
        showProductImagesFlash('Select a product first.', 'error');
        return;
    }
    if (getLacyProductImageCount() <= 0) {
        showProductImagesFlash('Lacy needs a product with at least one saved product image.', 'error');
        return;
    }
    if (!prompt) {
        showProductImagesFlash('Lacy prompt is required.', 'error');
        return;
    }

    if (btn) btn.disabled = true;
    if (loading) loading.style.display = 'flex';
    const results = document.getElementById('lacy-results');
    if (results) results.style.display = 'none';
    if (loadingText) {
        loadingText.textContent = lacyIsAutoScrape()
            ? 'Scraping and importing competitor product images...'
            : 'Uploading source images through the existing inspiration endpoint...';
    }

    try {
        const sources = lacyIsAutoScrape()
            ? await autoScrapeLacySources()
            : await uploadLacyManualSources();
        if (!sources.length) {
            throw new Error('No source images were prepared for generation.');
        }

        lacyLatestSources = sources;
        renderLacySourcePreview(sources);
        updateLacyCostLabel();
        if (!ensureLacyCredits(sources.length)) return;

        if (loadingText) {
            loadingText.textContent = `Queueing ${sources.length} Lacy generation${sources.length !== 1 ? 's' : ''}...`;
        }
        const resp = await fetch('/ai-creatives/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                product_id: Number.parseInt(productId, 10),
                inspiration_ids: sources.map(source => Number.parseInt(source.id, 10)),
                provider: 'gemini',
                base_prompt: prompt,
                analysis_prompt: PRODUCT_IMAGES_BOOTSTRAP.lacyAnalysisPrompt,
                product_info_source: 'product_page',
                prompt_only: false,
                use_batch_api: false,
                gemini_model: model,
                generation_mode: 'standard',
            }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            const keyErrors = { no_gemini_key: 'Gemini API key is not configured on the platform.' };
            throw new Error(keyErrors[data.error] || data.error || 'Failed to queue Lacy generation.');
        }
        if (!data.job_id) {
            throw new Error('Lacy generation job was not created.');
        }

        const job = await pollLacyJob(Number(data.job_id), loadingText);
        renderLacyResults(job.results || []);
        const count = (job.results || []).length;
        showProductImagesFlash(`Generated ${count} product image${count !== 1 ? 's' : ''}.`, 'success');
    } catch (err) {
        showProductImagesFlash(err?.message || 'Lacy generation failed.', 'error');
    } finally {
        if (btn) btn.disabled = false;
        if (loading) loading.style.display = 'none';
    }
}

async function pollLacyJob(jobId, loadingText) {
    const startedAt = Date.now();
    while ((Date.now() - startedAt) < LACY_JOB_POLL_TIMEOUT_MS) {
        const resp = await fetch(`/ai-creatives/jobs/${jobId}`);
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.error || 'Could not fetch Lacy job status.');
        }
        if (data.status === 'completed') return data;
        if (data.status === 'failed') {
            throw new Error(data.error || 'Lacy generation failed.');
        }
        if (loadingText) {
            loadingText.textContent = data.status === 'queued'
                ? 'Queued... waiting for an available worker.'
                : 'Analyzing source images and generating product images...';
        }
        await new Promise(resolve => setTimeout(resolve, LACY_JOB_POLL_INTERVAL_MS));
    }
    throw new Error('Lacy generation timed out. Please try again.');
}

function renderLacyResults(results) {
    const container = document.getElementById('lacy-results');
    const grid = document.getElementById('lacy-results-grid');
    if (!container || !grid) return;
    grid.innerHTML = '';

    results.forEach((result, index) => {
        const card = document.createElement('div');
        card.className = 'lacy-result-card';
        const sourceSrc = result.inspiration_url || '';
        const generatedSrc = result.generated_image_url || '';
        if (result.status === 'completed' && generatedSrc) {
            card.innerHTML = `
                <div class="lacy-result-pair">
                    <div class="lacy-result-image">
                        ${sourceSrc ? `<img src="${escapeHtml(sourceSrc)}" alt="Source image" onclick="showFullImageSrc(this.src)">` : ''}
                        <span class="lacy-image-badge">Source</span>
                    </div>
                    <div class="lacy-result-image">
                        <img src="${escapeHtml(generatedSrc)}" alt="Generated product image" onclick="showFullImageSrc(this.src)">
                        <span class="lacy-image-badge">Lacy</span>
                    </div>
                </div>
                <div class="lacy-result-footer">
                    <span class="var-badge">Image ${index + 1}</span>
                    <a class="btn btn-ghost btn-sm" href="${escapeHtml(generatedSrc)}" download>Download</a>
                </div>
                ${result.generated_prompt ? `<p class="lacy-prompt-preview">${escapeHtml(result.generated_prompt)}</p>` : ''}
            `;
        } else {
            card.innerHTML = `
                <div class="lacy-result-error">
                    Failed image ${index + 1}: ${escapeHtml(result.error || 'Unknown error')}
                </div>
            `;
        }
        grid.appendChild(card);
    });

    container.style.display = 'block';
}

function showProductImagesFlash(message, type) {
    let container = document.querySelector('.flash-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'flash-container';
        document.querySelector('main')?.prepend(container);
    }
    const flash = document.createElement('div');
    flash.className = `flash flash-${type || 'info'}`;
    flash.innerHTML = `<span>${escapeHtml(message)}</span><button class="flash-close" onclick="this.parentElement.remove()">&times;</button>`;
    container.appendChild(flash);
    setTimeout(() => {
        flash.style.opacity = '0';
        flash.style.transform = 'translateX(100%)';
        setTimeout(() => flash.remove(), 300);
    }, 5000);
}

async function loadLacyGeminiModels() {
    const select = document.getElementById('lacy-gemini-model');
    if (!select) return;
    if (typeof loadGeminiImageModelsIntoSelect === 'function') {
        await loadGeminiImageModelsIntoSelect(select);
        return;
    }

    try {
        const resp = await fetch('/ai-image/gemini-models');
        const data = await resp.json();
        if (!resp.ok || !Array.isArray(data.models)) return;
        const defaultModel = select.dataset.defaultModel || data.default_model || '';
        select.innerHTML = '';
        data.models.forEach(model => {
            const option = document.createElement('option');
            option.value = model.name;
            option.textContent = model.display_name || model.name;
            if (model.name === defaultModel) option.selected = true;
            select.appendChild(option);
        });
    } catch {
        // Keep fallback model option.
    }
}

document.addEventListener('DOMContentLoaded', () => {
    setProductImagesStep(PRODUCT_IMAGES_BOOTSTRAP.initialAgent === 'lacy' ? 'config' : 'agents');

    document.querySelectorAll('.pi-agent-card[data-agent]').forEach(card => {
        card.addEventListener('click', event => {
            event.preventDefault();
            selectProductAgent(card.dataset.agent);
        });
    });

    document.querySelectorAll('[data-product-images-back]').forEach(link => {
        link.addEventListener('click', event => {
            event.preventDefault();
            goBackToAgentSelection();
        });
    });

    const modelSelect = document.getElementById('lacy-gemini-model');
    if (modelSelect) {
        loadLacyGeminiModels();
        modelSelect.addEventListener('change', async () => {
            if (typeof saveAiImagePreferences !== 'function') return;
            try {
                await saveAiImagePreferences({ default_gemini_image_model: modelSelect.value });
            } catch (err) {
                showProductImagesFlash(err.message || 'Failed to save model preference.', 'error');
            }
        });
    }

    updateLacyProductHint();
    updateLacyCostLabel();
});
