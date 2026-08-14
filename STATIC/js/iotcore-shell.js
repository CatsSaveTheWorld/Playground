(() => {
    function getCookie(name) {
        const prefix = `${name}=`;
        return document.cookie
            .split(';')
            .map(value => value.trim())
            .find(value => value.startsWith(prefix))
            ?.slice(prefix.length) || '';
    }

    function getCsrfToken() {
        // The cookie is preferred so a long-lived/stale mobile tab can recover
        // after Django rotates the CSRF token. Fall back to the rendered meta tag.
        return (
            decodeURIComponent(getCookie('csrftoken'))
            || document.querySelector('meta[name="csrf-token"]')?.content
            || ''
        );
    }

    async function parseJsonResponse(response) {
        const contentType = (response.headers.get('content-type') || '').toLowerCase();
        const text = await response.text();

        if (response.redirected && /\/login(?:\/|\?|$)/i.test(response.url)) {
            throw new Error(
                '로그인 세션이 만료되었습니다. 페이지를 새로고침한 뒤 다시 로그인해주세요.'
            );
        }

        if (!contentType.includes('application/json')) {
            if (response.status === 403) {
                throw new Error(
                    '보안 토큰(CSRF)이 만료되었거나 일치하지 않습니다. 페이지를 새로고침한 뒤 다시 시도해주세요.'
                );
            }
            throw new Error(
                `서버가 JSON이 아닌 응답을 반환했습니다. (HTTP ${response.status})`
            );
        }

        let data;
        try {
            data = text ? JSON.parse(text) : {};
        } catch (error) {
            throw new Error('서버에서 올바르지 않은 JSON 응답을 반환했습니다.');
        }

        if (!response.ok) {
            throw new Error(data.message || `요청에 실패했습니다. (HTTP ${response.status})`);
        }

        return data;
    }

    async function postForm(url, params) {
        const csrfToken = getCsrfToken();
        const headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-Requested-With': 'XMLHttpRequest',
        };

        if (csrfToken) {
            headers['X-CSRFToken'] = csrfToken;
        }

        const response = await fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            cache: 'no-store',
            headers,
            body: params,
        });

        return parseJsonResponse(response);
    }

    window.IOTCORE_GET_CSRF_TOKEN = getCsrfToken;
    window.IOTCORE_CSRF_TOKEN = getCsrfToken();
    window.IOTCORE_PARSE_JSON_RESPONSE = parseJsonResponse;
    window.IOTCORE_POST_FORM = postForm;

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
