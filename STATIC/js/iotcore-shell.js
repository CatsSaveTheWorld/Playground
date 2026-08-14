(() => {
    window.IOTCORE_CSRF_TOKEN = (
        document.querySelector('meta[name="csrf-token"]')?.content || ''
    );

    const body = document.body;
    const overlay = document.getElementById('iotShellOverlay');
    const navToggle = document.getElementById('iotNavToggle');
    const executionToggle = document.getElementById('iotExecutionToggle');
    const executionClose = document.getElementById('iotExecutionClose');
    const mobileNavigation = window.matchMedia('(max-width: 860px)');

    function isMobileNavigation() {
        return mobileNavigation.matches;
    }

    function navIsExpanded() {
        if (isMobileNavigation()) {
            return body.classList.contains('is-nav-open');
        }
        return !body.classList.contains('is-nav-collapsed');
    }

    function syncOverlay() {
        const modalPanelOpen = (
            body.classList.contains('is-nav-open')
            || body.classList.contains('is-execution-open')
        );
        if (overlay) {
            overlay.hidden = !modalPanelOpen;
        }
        navToggle?.setAttribute('aria-expanded', String(navIsExpanded()));
        executionToggle?.setAttribute(
            'aria-expanded',
            String(body.classList.contains('is-execution-open')),
        );
    }

    function closeOverlayPanels() {
        body.classList.remove('is-nav-open', 'is-execution-open');
        syncOverlay();
    }

    navToggle?.addEventListener('click', () => {
        if (isMobileNavigation()) {
            body.classList.toggle('is-nav-open');
            body.classList.remove('is-execution-open');
        } else {
            body.classList.toggle('is-nav-collapsed');
        }
        syncOverlay();
    });

    executionToggle?.addEventListener('click', () => {
        body.classList.toggle('is-execution-open');
        body.classList.remove('is-nav-open');
        syncOverlay();
    });

    executionClose?.addEventListener('click', closeOverlayPanels);
    overlay?.addEventListener('click', closeOverlayPanels);

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            closeOverlayPanels();
        }
    });

    mobileNavigation.addEventListener?.('change', () => {
        body.classList.remove('is-nav-open');
        syncOverlay();
    });

    syncOverlay();
})();
