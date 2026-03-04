// Videos processing loop
document.addEventListener('DOMContentLoaded', function () {
    if (typeof JOB_ID !== 'undefined') {
        processLoop(JOB_ID);
    }
});

async function processLoop(jobId) {
    while (true) {
        try {
            const response = await fetch(`/videos/job/${jobId}/process-next`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });

            if (!response.ok) {
                console.error('Process request failed:', response.status);
                break;
            }

            const data = await response.json();

            // Update individual video status
            if (data.video_id) {
                updateVideoStatus(data.video_id, data.status, data.error);
            }

            // Refresh progress from server
            await updateProgress(jobId);

            if (data.done) {
                onProcessingComplete();
                break;
            }
        } catch (err) {
            console.error('Processing error:', err);
            // Wait and retry
            await sleep(3000);
        }
    }
}

async function updateProgress(jobId) {
    try {
        const response = await fetch(`/videos/job/${jobId}/status`);
        const data = await response.json();

        const total = data.total || 1;
        const processed = data.processed || 0;
        const percent = Math.round((processed / total) * 100);

        document.getElementById('progress-text').textContent =
            `${processed} / ${total} videos processed`;
        document.getElementById('progress-percent').textContent = `${percent}%`;
        document.getElementById('progress-fill').style.width = `${percent}%`;

        // Update all video statuses
        for (const video of data.videos) {
            updateVideoStatus(video.id, video.status, video.error);
        }
    } catch (err) {
        console.error('Status update error:', err);
    }
}

function updateVideoStatus(videoId, status, error) {
    const el = document.getElementById(`video-${videoId}`);
    if (!el) return;

    const badge = el.querySelector('.video-status-badge');
    if (!badge) return;

    // Remove old status classes
    badge.className = 'video-status-badge';

    const statusMap = {
        pending: { class: 'status-pending', icon: '\u25CF', label: 'Pending' },
        fetching_transcript: { class: 'status-processing', icon: '', label: 'Fetching transcript...' },
        analyzing: { class: 'status-processing', icon: '', label: 'Analyzing...' },
        completed: { class: 'status-completed', icon: '\u2713', label: 'Completed' },
        failed: { class: 'status-failed', icon: '\u2717', label: error || 'Failed' },
    };

    const info = statusMap[status] || statusMap.pending;
    badge.classList.add(info.class);

    const iconEl = badge.querySelector('.status-icon');
    const labelEl = badge.querySelector('.status-label');

    if (info.class === 'status-processing') {
        iconEl.innerHTML = '<span class="spinner-sm"></span>';
    } else {
        iconEl.textContent = info.icon;
    }
    labelEl.textContent = info.label;
}

function onProcessingComplete() {
    document.getElementById('completion-actions').style.display = 'flex';
    document.querySelector('.dashboard-header p').textContent =
        'Processing complete! View your trend analysis below.';
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}
