(() => {
    'use strict';
    const panels = [...document.querySelectorAll('.pc-ai-routing')];
    if (!panels.length) return;
    let routes = JSON.parse(document.getElementById('iotAiRoutingConfig')?.textContent || '{}');
    let saving = false;
    let refreshing = false;
    function acceptRoutes(incoming) {
        for (const [service, next] of Object.entries(incoming || {})) {
            const current = routes[service];
            if (current && current.revision > next.revision) continue;
            if (current?.revision === next.revision &&
                Date.parse(current.last_result?.at || '') > Date.parse(next.last_result?.at || '')) {
                routes[service] = {...next, last_result: current.last_result};
            } else { routes[service] = next; }
        }
    }
    function message(panel, text, error = false) {
        const output = panel.querySelector('[data-routing-message]');
        output.textContent = text;
        output.classList.toggle('is-error', error);
    }
    function render() {
        for (const panel of panels) {
            const id = Number(panel.dataset.pcId);
            for (const row of panel.querySelectorAll('[data-service]')) {
                const route = routes[row.dataset.service];
                if (!route) continue;
                const role = route.active?.id === id ? 'active' : route.standby?.id === id ? 'standby' : 'off';
                const select = row.querySelector('[data-role-select]');
                select.value = role;
                select.disabled = saving || select.dataset.editable !== 'true';
                row.dataset.role = role;
                row.querySelector('[data-selection-summary]').textContent = route.configured ?
                    `액티브 ${route.active?.name || '미지정'} · 스탠바이 ${route.standby?.name || '없음'}` : '기존 Django 기본 주소 사용';
                const outcome = row.querySelector('[data-routing-outcome]');
                const last = route.last_result || {};
                const labels = { active: '최근 요청: 액티브 처리', standby: '최근 요청: 스탠바이 처리', unavailable: '최근 요청: 사용 가능한 워커 없음', rejected: '최근 요청: 요청 거절 또는 해석 실패' };
                outcome.textContent = route.pending ? '연동 준비 중 · 배치만 저장' : labels[last.status] || '최근 요청 없음';
                outcome.classList.toggle('is-error', last.status === 'unavailable');
                outcome.title = (last.attempts || []).map(item => `${item.node_uid}: ${item.error || '정상'}`).join('\n');
            }
        }
    }
    async function readState(url) {
        const response = await fetch(url, {credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});
        return window.IOTCORE_PARSE_JSON_RESPONSE(response);
    }
    async function refresh() {
        if (saving || refreshing || document.hidden) return;
        refreshing = true;
        try {
            const data = await readState(panels[0].dataset.stateUrl);
            if (!saving) { acceptRoutes(data.routes); render(); }
        } catch (error) { message(panels[0], error.message, true); }
        finally { refreshing = false; }
    }
    for (const panel of panels) {
        panel.addEventListener('change', async event => {
            const select = event.target.closest('[data-role-select]');
            if (!select || saving) return;
            const service = select.closest('[data-service]').dataset.service;
            const chosenRole = select.value;
            const route = routes[service];
            if (!route) { message(panel, '배치 정보를 새로고침한 뒤 다시 선택하세요.', true); return; }
            saving = true;
            render();
            message(panel, '배치를 저장하고 있습니다.');
            try {
                const data = await window.IOTCORE_POST_FORM(panel.dataset.assignUrl, new URLSearchParams({
                    service, device_id: panel.dataset.pcId, role: chosenRole, revision: String(route.revision),
                }));
                if (data.success === false) throw new Error(data.message);
                acceptRoutes(data.routes);
                message(panel, data.message);
            } catch (error) { message(panel, error.message, true); }
            finally { saving = false; render(); refresh(); }
        });
        const probe = panel.querySelector('.pc-ai-probe');
        probe.addEventListener('click', async () => {
            probe.disabled = true;
            message(panel, '워커 연결을 확인하고 있습니다.');
            try {
                const url = new URL(panel.dataset.stateUrl, location.origin);
                url.searchParams.set('probe_device', panel.dataset.pcId);
                const data = await readState(url);
                if (!saving) { acceptRoutes(data.routes); render(); }
                const health = data.health[panel.dataset.pcId];
                for (const row of panel.querySelectorAll('[data-service]')) {
                    const result = health[row.dataset.service];
                    const output = row.querySelector('[data-worker-health]');
                    output.textContent = result.message;
                    output.dataset.status = result.status;
                }
                message(panel, '연결 확인을 완료했습니다.');
            } catch (error) { message(panel, error.message, true); }
            finally { probe.disabled = false; }
        });
    }
    render();
    window.setInterval(refresh, 10000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
