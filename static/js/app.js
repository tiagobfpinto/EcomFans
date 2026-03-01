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
