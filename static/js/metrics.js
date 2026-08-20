(() => {
    const page = document.querySelector('.metrics-page');
    if (!page) return;

    const state = {
        data: null,
        chartMetric: 'spend',
        chartView: 'time',
        sortKey: 'spend',
        sortDirection: 'desc',
        search: '',
    };

    const moneyFormatter = new Intl.NumberFormat('en-IE', {
        style: 'currency',
        currency: 'EUR',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
    const integerFormatter = new Intl.NumberFormat('en-IE', {
        maximumFractionDigits: 0,
    });
    const decimalFormatter = new Intl.NumberFormat('en-IE', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
    const compactMoneyFormatter = new Intl.NumberFormat('en-IE', {
        style: 'currency',
        currency: 'EUR',
        notation: 'compact',
        maximumFractionDigits: 1,
    });

    const startInput = document.getElementById('metrics-start-date');
    const endInput = document.getElementById('metrics-end-date');
    const uploadDateInput = document.getElementById('metrics-upload-date');
    const uploadCard = document.getElementById('metrics-upload-card');
    const uploadToggle = document.getElementById('metrics-upload-toggle');
    const uploadForm = document.getElementById('metrics-upload-form');
    const fileInput = document.getElementById('metrics-file-input');
    const fileField = document.getElementById('metrics-file-field');
    const fileName = document.getElementById('metrics-file-name');
    const importStatus = document.getElementById('metrics-import-status');
    const importButton = document.getElementById('metrics-import-button');
    const tableBody = document.getElementById('metrics-table-body');
    const emptyState = document.getElementById('metrics-empty');
    const tableScroll = document.querySelector('.metrics-table-scroll');
    const replaceOverlay = document.getElementById('metrics-replace-overlay');
    const replaceCancel = document.getElementById('metrics-replace-cancel');
    const replaceConfirm = document.getElementById('metrics-replace-confirm');
    let replacePreviousFocus = null;

    function parseDate(isoDate) {
        return new Date(`${isoDate}T00:00:00`);
    }

    function toIsoDate(dateValue) {
        const year = dateValue.getFullYear();
        const month = String(dateValue.getMonth() + 1).padStart(2, '0');
        const day = String(dateValue.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function formatDate(isoDate) {
        return new Intl.DateTimeFormat('en-GB', {
            day: 'numeric',
            month: 'short',
            year: 'numeric',
        }).format(parseDate(isoDate));
    }

    function formatShortDate(isoDate) {
        return new Intl.DateTimeFormat('en-GB', {
            day: 'numeric',
            month: 'short',
        }).format(parseDate(isoDate));
    }

    function setPreset(days, shouldLoad = true) {
        const endDate = parseDate(endInput.value);
        if (Number.isNaN(endDate.getTime())) return;
        const startDate = new Date(endDate);
        startDate.setDate(startDate.getDate() - (days - 1));
        startInput.value = toIsoDate(startDate);

        document.querySelectorAll('.metrics-presets button').forEach((button) => {
            button.classList.toggle('active', Number(button.dataset.days) === days);
        });
        if (shouldLoad) loadMetrics();
    }

    function clearPresetSelection() {
        document.querySelectorAll('.metrics-presets button').forEach((button) => {
            button.classList.remove('active');
        });
    }

    function setImportStatus(message, type, details = []) {
        importStatus.hidden = false;
        importStatus.className = `metrics-import-status ${type}`;
        importStatus.textContent = message;
        if (details.length) {
            const list = document.createElement('ul');
            details.forEach((detail) => {
                const item = document.createElement('li');
                item.textContent = detail;
                list.appendChild(item);
            });
            importStatus.appendChild(list);
        }
    }

    function setImportLoading(isLoading, isReplacement = false) {
        importButton.disabled = isLoading;
        importButton.textContent = isLoading ? 'Importing...' : 'Import snapshot';
        replaceCancel.disabled = isLoading;
        replaceConfirm.disabled = isLoading;
        replaceConfirm.textContent = isLoading && isReplacement
            ? 'Replacing...'
            : 'Replace data';
    }

    function openReplaceConfirmation(data) {
        replacePreviousFocus = document.activeElement;
        const details = document.querySelector('.metrics-confirm-details');
        const copy = document.getElementById('metrics-replace-copy');
        if (Array.isArray(data.conflict_dates) && !data.existing) {
            // Per-day CSV: several days already have data.
            if (details) details.hidden = true;
            const dates = data.conflict_dates.map(formatShortDate).join(', ');
            copy.textContent =
                `${data.conflict_dates.length} of ${data.import_dates.length} days in this file already have metrics (${dates}). Replace those days with the new upload?`;
        } else {
            if (details) details.hidden = false;
            document.getElementById('metrics-existing-file').textContent = data.existing?.filename || 'Existing snapshot';
            document.getElementById('metrics-incoming-file').textContent = data.incoming?.filename || fileInput.files[0]?.name || 'New snapshot';
            copy.textContent =
                `${formatDate(data.metric_date)} already has ${integerFormatter.format(data.existing?.detailed_ad_count || 0)} detailed ad metrics. Do you want to replace them with the new upload?`;
        }
        replaceOverlay.hidden = false;
        document.body.style.overflow = 'hidden';
        replaceConfirm.focus();
    }

    function closeReplaceConfirmation() {
        replaceOverlay.hidden = true;
        document.body.style.overflow = '';
        if (replacePreviousFocus && typeof replacePreviousFocus.focus === 'function') {
            replacePreviousFocus.focus();
        }
        replacePreviousFocus = null;
    }

    function formatMetric(value, metric) {
        const number = Number(value || 0);
        if (metric === 'spend' || metric === 'cpm' || metric === 'cpc' || metric === 'cost_per_purchase') {
            return moneyFormatter.format(number);
        }
        if (metric === 'purchases' || metric === 'adds_to_cart') {
            return integerFormatter.format(number);
        }
        if (metric === 'ctr') {
            return `${decimalFormatter.format(number)}%`;
        }
        return decimalFormatter.format(number);
    }

    function renderKpis(kpis) {
        document.getElementById('kpi-spend').textContent = moneyFormatter.format(kpis.spend || 0);
        document.getElementById('kpi-purchases').textContent = integerFormatter.format(kpis.purchases || 0);
        document.getElementById('kpi-cpp').textContent = moneyFormatter.format(kpis.cost_per_purchase || 0);
        document.getElementById('kpi-roas').textContent = decimalFormatter.format(kpis.roas || 0);
        document.getElementById('kpi-atc').textContent = integerFormatter.format(kpis.adds_to_cart || 0);
        document.getElementById('kpi-ctr').textContent = `${decimalFormatter.format(kpis.ctr || 0)}%`;
    }

    function renderWarnings(data) {
        const missingWarning = document.getElementById('metrics-missing-warning');
        const missingCopy = document.getElementById('metrics-missing-copy');
        missingWarning.hidden = data.missing_dates.length === 0;
        if (data.missing_dates.length) {
            const dates = data.missing_dates.map(formatDate).join(', ');
            missingCopy.textContent = `Showing partial results from ${data.days_with_data} of ${data.days_requested} days. Missing: ${dates}.`;
        }

        const coverageWarning = document.getElementById('metrics-coverage-warning');
        const coverageCopy = document.getElementById('metrics-coverage-copy');
        coverageWarning.hidden = !data.coverage.partial;
        if (data.coverage.partial) {
            const singleDay = data.days_requested === 1;
            coverageCopy.textContent = singleDay
                ? `${data.coverage.detailed_ad_rows} of ${data.coverage.reported_ad_rows} ads included in the detailed table. KPI cards use the reported TOTAL row.`
                : `${data.coverage.detailed_ad_rows} of ${data.coverage.reported_ad_rows} ad rows included across the selected days. KPI cards use each day's reported TOTAL row.`;
        }
    }

    function chartAxisLabel(value, metric) {
        if (metric === 'spend') return compactMoneyFormatter.format(value);
        if (metric === 'purchases') return integerFormatter.format(value);
        return decimalFormatter.format(value);
    }

    function updateChartHeader() {
        const kicker = document.getElementById('metrics-chart-kicker');
        const title = document.getElementById('metrics-chart-title');
        if (state.chartView === 'creative') {
            kicker.textContent = 'Creative breakdown';
            title.textContent = 'Performance by creative';
        } else {
            kicker.textContent = 'Daily trend';
            title.textContent = 'Performance over time';
        }
    }

    function renderChart() {
        updateChartHeader();
        const svg = document.getElementById('metrics-chart');
        if (state.chartView === 'creative') {
            renderCreativeChart(svg);
        } else {
            renderTimeChart(svg);
        }
    }

    function emptyChart(svg, headline) {
        svg.innerHTML = `
            <text x="450" y="145" text-anchor="middle" class="metrics-chart-label">${headline}</text>
            <text x="450" y="168" text-anchor="middle" class="metrics-chart-label">Import a snapshot or change the date range</text>
        `;
    }

    function renderCreativeChart(svg) {
        const ads = state.data ? state.data.ads : [];
        if (!ads.length) {
            emptyChart(svg, 'No creatives to chart');
            return;
        }
        const metric = state.chartMetric;
        const rows = [...ads]
            .sort((left, right) => Number(right[metric] || 0) - Number(left[metric] || 0))
            .slice(0, 10);

        const width = 900;
        const height = 300;
        const left = 150;
        const right = 72;
        const top = 14;
        const bottom = 12;
        const plotWidth = width - left - right;
        const plotHeight = height - top - bottom;
        const rowHeight = plotHeight / rows.length;
        const barHeight = Math.min(rowHeight * 0.62, 26);
        const maxValue = Math.max(...rows.map((ad) => Number(ad[metric] || 0)), 0);
        const scale = maxValue > 0 ? plotWidth / maxValue : 0;

        const bars = rows.map((ad, index) => {
            const value = Number(ad[metric] || 0);
            const barWidth = value > 0 ? Math.max(value * scale, 2) : 0;
            const y = top + index * rowHeight + (rowHeight - barHeight) / 2;
            const name = ad.ad_name.length > 24 ? `${ad.ad_name.slice(0, 23)}…` : ad.ad_name;
            const textY = y + barHeight / 2 + 4;
            return `
                <text x="${left - 10}" y="${textY}" text-anchor="end" class="metrics-chart-bar-label">${escapeHtml(name)}</text>
                <rect x="${left}" y="${y}" width="${barWidth}" height="${barHeight}" rx="4" class="metrics-chart-bar">
                    <title>${escapeHtml(ad.ad_name)}: ${formatMetric(value, metric)}</title>
                </rect>
                <text x="${left + barWidth + 8}" y="${textY}" class="metrics-chart-bar-value">${chartAxisLabel(value, metric)}</text>
            `;
        }).join('');

        svg.innerHTML = bars;
    }

    function renderTimeChart(svg) {
        const rows = state.data ? state.data.daily : [];
        if (!rows.length) {
            emptyChart(svg, 'No daily data to chart');
            return;
        }

        const width = 900;
        const height = 300;
        const left = 62;
        const right = 22;
        const top = 22;
        const bottom = 46;
        const plotWidth = width - left - right;
        const plotHeight = height - top - bottom;
        const values = rows.map((row) => Number(row[state.chartMetric] || 0));
        const maxValue = Math.max(...values, 0);
        const axisMax = maxValue > 0 ? maxValue * 1.12 : 1;
        const xFor = (index) => rows.length === 1
            ? left + plotWidth / 2
            : left + (index / (rows.length - 1)) * plotWidth;
        const yFor = (value) => top + plotHeight - (value / axisMax) * plotHeight;

        const points = rows.map((row, index) => ({
            x: xFor(index),
            y: yFor(values[index]),
            value: values[index],
            date: row.date,
        }));
        const linePoints = points.map((point) => `${point.x},${point.y}`).join(' ');
        const areaPoints = `${left},${top + plotHeight} ${linePoints} ${left + plotWidth},${top + plotHeight}`;

        const grid = Array.from({ length: 5 }, (_, index) => {
            const ratio = index / 4;
            const y = top + ratio * plotHeight;
            const value = axisMax * (1 - ratio);
            return `
                <line x1="${left}" y1="${y}" x2="${left + plotWidth}" y2="${y}" class="metrics-chart-grid"/>
                <text x="${left - 11}" y="${y + 4}" text-anchor="end" class="metrics-chart-label">${chartAxisLabel(value, state.chartMetric)}</text>
            `;
        }).join('');

        const maxLabels = 8;
        const labelEvery = Math.max(1, Math.ceil(rows.length / maxLabels));
        const xLabels = points.map((point, index) => {
            if (index % labelEvery !== 0 && index !== points.length - 1) return '';
            return `<text x="${point.x}" y="${height - 15}" text-anchor="middle" class="metrics-chart-label">${formatShortDate(point.date)}</text>`;
        }).join('');

        const circles = points.map((point) => `
            <circle cx="${point.x}" cy="${point.y}" r="4.5" class="metrics-chart-point">
                <title>${formatDate(point.date)}: ${formatMetric(point.value, state.chartMetric)}</title>
            </circle>
        `).join('');

        svg.innerHTML = `
            <defs>
                <linearGradient id="metrics-area-gradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="#7c6de8" stop-opacity="0.28"/>
                    <stop offset="100%" stop-color="#7c6de8" stop-opacity="0.01"/>
                </linearGradient>
            </defs>
            ${grid}
            <polygon points="${areaPoints}" class="metrics-chart-area"/>
            <polyline points="${linePoints}" class="metrics-chart-line"/>
            ${circles}
            ${xLabels}
        `;
    }

    function escapeHtml(value) {
        const element = document.createElement('div');
        element.textContent = String(value);
        return element.innerHTML;
    }

    function filteredAndSortedAds() {
        if (!state.data) return [];
        const query = state.search.trim().toLocaleLowerCase();
        const rows = state.data.ads.filter((ad) => (
            !query || ad.ad_name.toLocaleLowerCase().includes(query)
        ));
        return [...rows].sort((left, right) => {
            const leftValue = left[state.sortKey];
            const rightValue = right[state.sortKey];
            let comparison;
            if (state.sortKey === 'ad_name') {
                comparison = leftValue.localeCompare(rightValue, undefined, { sensitivity: 'base' });
            } else {
                comparison = Number(leftValue || 0) - Number(rightValue || 0);
            }
            return state.sortDirection === 'asc' ? comparison : -comparison;
        });
    }

    function renderSortIndicators() {
        document.querySelectorAll('.metrics-table th button[data-sort]').forEach((button) => {
            const active = button.dataset.sort === state.sortKey;
            button.classList.toggle('active', active);
            const indicator = button.querySelector('span');
            if (indicator) indicator.textContent = active ? (state.sortDirection === 'asc' ? '↑' : '↓') : '';
        });
    }

    function renderTable() {
        const ads = filteredAndSortedAds();
        const hasAnyAds = Boolean(state.data && state.data.ads.length);
        const hasVisibleAds = ads.length > 0;
        emptyState.querySelector('h3').textContent = 'No metrics for this range';
        emptyState.querySelector('p').textContent = 'Import a daily Markdown snapshot or choose a different date range.';
        tableScroll.hidden = !hasAnyAds;
        emptyState.hidden = hasVisibleAds || hasAnyAds;

        if (!hasVisibleAds) {
            tableBody.innerHTML = '';
            if (hasAnyAds) {
                tableScroll.hidden = false;
                tableBody.innerHTML = '<tr><td colspan="10">No ads match this search.</td></tr>';
            }
        } else {
            tableBody.innerHTML = ads.map((ad) => `
                <tr>
                    <td>${escapeHtml(ad.ad_name)}</td>
                    <td>${moneyFormatter.format(ad.spend || 0)}</td>
                    <td>${moneyFormatter.format(ad.cpm || 0)}</td>
                    <td>${moneyFormatter.format(ad.cpc || 0)}</td>
                    <td>${decimalFormatter.format(ad.ctr || 0)}%</td>
                    <td>${integerFormatter.format(ad.adds_to_cart || 0)}</td>
                    <td>${integerFormatter.format(ad.purchases || 0)}</td>
                    <td>${moneyFormatter.format(ad.cost_per_purchase || 0)}</td>
                    <td>${decimalFormatter.format(ad.roas || 0)}</td>
                    <td title="Estimated from reconstructed impressions">~${decimalFormatter.format(ad.frequency || 0)}</td>
                </tr>
            `).join('');
        }

        const coverage = state.data ? state.data.coverage : null;
        const coverageText = document.getElementById('metrics-table-coverage');
        if (!coverage) {
            coverageText.textContent = '0 ads';
        } else if (coverage.partial) {
            coverageText.textContent = `${coverage.detailed_ad_rows} of ${coverage.reported_ad_rows} ad rows included`;
        } else {
            coverageText.textContent = `${coverage.detailed_ad_rows} ad rows included`;
        }
        renderSortIndicators();
    }

    function renderLatestImport(latestImport) {
        const container = document.getElementById('metrics-import-meta');
        if (!latestImport) {
            container.textContent = 'No snapshots imported yet.';
            return;
        }
        container.textContent = '';
        container.append('Last import: ');
        const strong = document.createElement('strong');
        strong.textContent = latestImport.filename;
        container.append(strong, ` for ${formatDate(latestImport.metric_date)}`);
    }

    async function loadMetrics() {
        if (!startInput.value || !endInput.value) return;
        tableBody.innerHTML = '<tr><td colspan="10">Loading metrics...</td></tr>';
        tableScroll.hidden = false;
        emptyState.hidden = true;
        try {
            const params = new URLSearchParams({
                start_date: startInput.value,
                end_date: endInput.value,
            });
            const response = await fetch(`/metrics/data?${params}`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Failed to load metrics.');
            state.data = data;
            renderKpis(data.kpis);
            renderWarnings(data);
            renderChart();
            renderTable();
            renderLatestImport(data.latest_import);
        } catch (error) {
            state.data = null;
            renderKpis({
                spend: 0,
                purchases: 0,
                cost_per_purchase: 0,
                roas: 0,
                adds_to_cart: 0,
                ctr: 0,
            });
            document.getElementById('metrics-missing-warning').hidden = true;
            document.getElementById('metrics-coverage-warning').hidden = true;
            renderChart();
            tableBody.innerHTML = '';
            tableScroll.hidden = true;
            emptyState.hidden = false;
            emptyState.querySelector('h3').textContent = 'Metrics could not be loaded';
            emptyState.querySelector('p').textContent = error.message;
        }
    }

    uploadToggle.addEventListener('click', () => {
        uploadCard.hidden = !uploadCard.hidden;
        if (!uploadCard.hidden) fileInput.focus();
    });

    const dateHint = document.getElementById('metrics-date-hint');

    function updateDateRequirement(file) {
        const isCsv = file && /\.csv$/i.test(file.name);
        uploadDateInput.required = !isCsv;
        if (dateHint) {
            dateHint.textContent = isCsv
                ? 'Read automatically from the CSV reporting period.'
                : 'Completed day represented by the Markdown snapshot.';
        }
    }

    fileInput.addEventListener('change', () => {
        const file = fileInput.files[0];
        fileName.textContent = file?.name || 'Choose a .md or .csv file';
        updateDateRequirement(file);
    });

    ['dragenter', 'dragover'].forEach((eventName) => {
        fileField.addEventListener(eventName, (event) => {
            event.preventDefault();
            fileField.classList.add('drag-over');
        });
    });
    ['dragleave', 'drop'].forEach((eventName) => {
        fileField.addEventListener(eventName, (event) => {
            event.preventDefault();
            fileField.classList.remove('drag-over');
        });
    });
    fileField.addEventListener('drop', (event) => {
        if (event.dataTransfer.files.length) {
            fileInput.files = event.dataTransfer.files;
            fileName.textContent = event.dataTransfer.files[0].name;
            updateDateRequirement(event.dataTransfer.files[0]);
        }
    });

    async function submitImport(replaceExisting = false) {
        importStatus.hidden = true;
        setImportLoading(true, replaceExisting);
        try {
            const formData = new FormData(uploadForm);
            if (replaceExisting) {
                formData.set('replace_existing', 'true');
            }
            const response = await fetch('/metrics/import', {
                method: 'POST',
                body: formData,
            });
            const data = await response.json();
            if (response.status === 409 && data.conflict && !replaceExisting) {
                openReplaceConfirmation(data);
                return;
            }
            if (!response.ok) {
                closeReplaceConfirmation();
                setImportStatus(data.error || 'Import failed.', 'error', data.details || []);
                return;
            }
            closeReplaceConfirmation();
            const verb = data.replaced ? 'replaced' : 'imported';
            const daysImported = data.days_imported || 1;
            let message;
            if (daysImported > 1) {
                message = `${data.detailed_ad_count} ad rows ${verb} across ${daysImported} days (${formatDate(data.reporting_start)} to ${formatDate(data.reporting_end)}).`;
            } else {
                const spansRange = data.reporting_start
                    && data.reporting_end
                    && data.reporting_start !== data.reporting_end;
                message = spansRange
                    ? `${data.detailed_ad_count} ads ${verb} from ${formatDate(data.reporting_start)} to ${formatDate(data.reporting_end)} (saved under ${formatDate(data.metric_date)}).`
                    : `${data.detailed_ad_count} detailed ads ${verb} for ${formatDate(data.metric_date)}.`;
            }
            const skipped = data.skipped_incomplete || [];
            if (skipped.length) {
                const skippedDates = skipped.map(formatShortDate).join(', ');
                message += ` Skipped ${skipped.length === 1 ? 'the in-progress day' : 'in-progress days'} (${skippedDates}).`;
            }
            setImportStatus(message, 'success');
            endInput.value = data.metric_date;
            if (data.metric_date <= uploadDateInput.max) {
                uploadDateInput.value = data.metric_date;
            }
            const importedDates = data.imported_dates || [];
            if (daysImported > 1 && importedDates.length) {
                // Show the whole imported window for a per-day export.
                startInput.value = importedDates[0];
                clearPresetSelection();
            } else {
                setPreset(1, false);
            }
            await loadMetrics();
        } catch (error) {
            closeReplaceConfirmation();
            setImportStatus('Network error while importing the snapshot.', 'error');
        } finally {
            setImportLoading(false);
        }
    }

    uploadForm.addEventListener('submit', (event) => {
        event.preventDefault();
        submitImport(false);
    });

    replaceCancel.addEventListener('click', closeReplaceConfirmation);
    replaceConfirm.addEventListener('click', () => submitImport(true));
    replaceOverlay.addEventListener('click', (event) => {
        if (event.target === replaceOverlay) closeReplaceConfirmation();
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !replaceOverlay.hidden && !replaceConfirm.disabled) {
            closeReplaceConfirmation();
        }
    });

    document.querySelectorAll('.metrics-presets button').forEach((button) => {
        button.addEventListener('click', () => setPreset(Number(button.dataset.days)));
    });

    document.getElementById('metrics-apply-range').addEventListener('click', () => {
        clearPresetSelection();
        loadMetrics();
    });

    document.querySelectorAll('[data-chart-metric]').forEach((button) => {
        button.addEventListener('click', () => {
            state.chartMetric = button.dataset.chartMetric;
            document.querySelectorAll('[data-chart-metric]').forEach((candidate) => {
                candidate.classList.toggle('active', candidate === button);
            });
            renderChart();
        });
    });

    document.querySelectorAll('[data-chart-view]').forEach((button) => {
        button.addEventListener('click', () => {
            state.chartView = button.dataset.chartView;
            document.querySelectorAll('[data-chart-view]').forEach((candidate) => {
                candidate.classList.toggle('active', candidate === button);
            });
            renderChart();
        });
    });

    document.querySelectorAll('.metrics-table th button[data-sort]').forEach((button) => {
        button.addEventListener('click', () => {
            const key = button.dataset.sort;
            if (state.sortKey === key) {
                state.sortDirection = state.sortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                state.sortKey = key;
                state.sortDirection = key === 'ad_name' ? 'asc' : 'desc';
            }
            renderTable();
        });
    });

    document.getElementById('metrics-ad-search').addEventListener('input', (event) => {
        state.search = event.target.value;
        renderTable();
    });

    const initialDate = page.dataset.latestDate || page.dataset.defaultUploadDate || toIsoDate(new Date());
    endInput.value = initialDate;
    startInput.value = initialDate;
    if (!uploadDateInput.value) uploadDateInput.value = page.dataset.defaultUploadDate || initialDate;
    setPreset(1, false);
    loadMetrics();
})();
