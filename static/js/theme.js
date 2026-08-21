(function () {
    'use strict';

    const STORAGE_KEY = 'ecomfans-theme';
    const THEMES = new Set(['light', 'dark']);
    const root = document.documentElement;

    function readStoredTheme() {
        try {
            const value = window.localStorage.getItem(STORAGE_KEY);
            return THEMES.has(value) ? value : null;
        } catch (_error) {
            return null;
        }
    }

    function updateControls(theme) {
        const isDark = theme === 'dark';
        document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
            button.setAttribute('aria-pressed', String(isDark));
            button.setAttribute('aria-label', isDark ? 'Switch to light mode' : 'Switch to dark mode');
            button.title = isDark ? 'Switch to light mode' : 'Switch to dark mode';
        });
        document.querySelectorAll('[data-theme-state]').forEach((label) => {
            label.textContent = isDark ? 'Dark' : 'Light';
        });
    }

    function updateBrowserChrome(theme) {
        const meta = document.getElementById('theme-color-meta');
        if (meta) meta.setAttribute('content', theme === 'dark' ? '#171513' : '#F3F1ED');
    }

    function applyTheme(theme, options) {
        const nextTheme = THEMES.has(theme) ? theme : 'light';
        const settings = Object.assign({ persist: true, notify: true }, options);

        root.dataset.theme = nextTheme;
        root.style.colorScheme = nextTheme;
        updateControls(nextTheme);
        updateBrowserChrome(nextTheme);

        if (settings.persist) {
            try {
                window.localStorage.setItem(STORAGE_KEY, nextTheme);
            } catch (_error) { /* The visual preference still applies for this page. */ }
        }

        if (settings.notify) {
            window.dispatchEvent(new CustomEvent('ecomfans:themechange', {
                detail: { theme: nextTheme },
            }));
        }
    }

    function toggleTheme() {
        root.classList.add('theme-is-changing');
        applyTheme(root.dataset.theme === 'dark' ? 'light' : 'dark');
        window.setTimeout(() => root.classList.remove('theme-is-changing'), 220);
    }

    function initialiseThemeControls() {
        const initialTheme = readStoredTheme() || (THEMES.has(root.dataset.theme) ? root.dataset.theme : 'light');
        applyTheme(initialTheme, { persist: false, notify: false });

        document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
            button.addEventListener('click', toggleTheme);
        });

        window.addEventListener('storage', (event) => {
            if (event.key === STORAGE_KEY && THEMES.has(event.newValue)) {
                applyTheme(event.newValue, { persist: false });
            }
        });
    }

    window.EcomFansTheme = Object.freeze({
        get: () => root.dataset.theme || 'light',
        set: (theme) => applyTheme(theme),
        toggle: toggleTheme,
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialiseThemeControls, { once: true });
    } else {
        initialiseThemeControls();
    }
}());
