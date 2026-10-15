(() => {
    'use strict';
    const root = document.querySelector('.history-page');
    if (!root) return;
    root.addEventListener('click', async event => {
        const summary = event.target.closest('.history-summary');
        if (!summary || !root.contains(summary)) return;
        const control = event.target.closest('a, button, input, select, textarea, label, [contenteditable]');
        if (control && !control.closest('[data-history-url]')) return;
        const selection = window.getSelection();
        if (event.detail > 0 && selection && !selection.isCollapsed
            && summary.contains(selection.anchorNode) && summary.contains(selection.focusNode)) return;
        const button = summary.querySelector('[data-history-url]');
        if (!button) return;
        const row = document.getElementById(button.getAttribute('aria-controls'));
        const expanded = button.getAttribute('aria-expanded') !== 'true';
        button.setAttribute('aria-expanded', String(expanded));
        button.closest('tr').classList.toggle('is-expanded', expanded);
        button.querySelector('.history-arrow').textContent = expanded ? '▾' : '▸';
        row.hidden = !expanded;
        const content = row.querySelector('.history-detail-content');
        if (!expanded || content.dataset.loaded || content.getAttribute('aria-busy') === 'true') return;
        content.setAttribute('aria-busy', 'true');
        content.textContent = '실행 상세를 불러오는 중…';
        try {
            const response = await fetch(button.dataset.historyUrl, {credentials:'same-origin', headers:{'X-Requested-With':'XMLHttpRequest'}});
            if (!response.ok || response.redirected) throw new Error('detail unavailable');
            const template = document.createElement('template');
            template.innerHTML = await response.text();
            if (!template.content.querySelector('.history-detail')) throw new Error('invalid detail');
            content.replaceChildren(template.content);
            content.dataset.loaded = 'true';
        } catch (_) {
            content.textContent = '기록을 불러오지 못했습니다. 접었다 다시 펼치면 재시도합니다.';
            content.classList.add('history-load-error');
        } finally {
            content.removeAttribute('aria-busy');
            if (content.dataset.loaded) content.classList.remove('history-load-error');
        }
    });
})();
