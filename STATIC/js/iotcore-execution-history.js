(() => {
    'use strict';
    const root = document.querySelector('.history-page');
    if (!root) return;
    const form = document.getElementById('history-search-form');
    form.addEventListener('formdata', event => {
        [...event.formData.keys()].forEach(key => {
            if (event.formData.getAll(key).every(value => value === '')) event.formData.delete(key);
        });
    });
    const period = form.elements.period;
    const updateDates = () => {
        root.querySelectorAll('[data-custom-date]').forEach(label => {
            label.hidden = period.value !== 'custom';
            label.querySelector('input').disabled = period.value !== 'custom';
        });
    };
    period.addEventListener('change', updateDates);
    updateDates();
    root.querySelectorAll('[data-picker]').forEach(picker => {
        const choices = [...picker.querySelectorAll('.history-picker-options label')];
        const search = picker.querySelector('.history-choice-search');
        const update = () => {
            const count = picker.querySelectorAll('input[type="checkbox"]:checked').length;
            picker.querySelector('[data-picker-count]').textContent = count ? `${count}개 선택` : '전체';
        };
        picker.addEventListener('change', update);
        picker.querySelector('.history-clear-picker').addEventListener('click', () => {
            picker.querySelectorAll('input[type="checkbox"]').forEach(input => {input.checked = false;});
            update();
        });
        search.addEventListener('input', () => {
            const text = search.value.trim().toLocaleLowerCase();
            choices.forEach(label => {label.parentElement.hidden = !label.textContent.toLocaleLowerCase().includes(text);});
            picker.querySelector('.history-choice-empty').hidden = choices.some(label => !label.parentElement.hidden);
        });
        picker.addEventListener('toggle', () => {
            if (picker.open) root.querySelectorAll('[data-picker]').forEach(other => {if (other !== picker) other.open = false;});
        });
        update();
    });
    document.addEventListener('click', event => {
        if (!event.target.closest('[data-picker]')) root.querySelectorAll('[data-picker]').forEach(picker => {picker.open = false;});
    });
    root.addEventListener('keydown', event => {
        if (event.key === 'Escape') root.querySelectorAll('[data-picker][open]').forEach(picker => {picker.open = false; picker.querySelector('summary').focus();});
    });
    form.elements.status.addEventListener('change', () => {
        if (form.elements.status.value === 'no_action') form.elements.include_no_action.checked = true;
    });
    const feedback = document.getElementById('history-search-feedback');
    const savedSelect = document.getElementById('history-saved-select');
    const savedName = document.getElementById('history-saved-name');
    const storageKey = 'iotcore.executionHistory.searches.v1';
    const allowed = new Set(['period', 'start', 'end', 'status', 'include_no_action', 'q', 'tasks', 'devices', 'source', 'functions', 'exclude_tasks', 'run_id', 'min_duration']);
    let saved = [];
    try {
        const stored = JSON.parse(localStorage.getItem(storageKey) || '[]');
        if (Array.isArray(stored)) saved = stored.filter(item => item && typeof item.name === 'string' && typeof item.query === 'string' && item.name.length <= 60 && item.query.length <= 16000).slice(0, 20);
    } catch (_) { feedback.textContent = '저장된 검색을 읽을 수 없습니다. 일반 조회는 계속 사용할 수 있습니다.'; }
    const renderSaved = () => {
        savedSelect.replaceChildren(new Option('선택하세요', ''));
        saved.forEach((item, index) => savedSelect.add(new Option(item.name, String(index))));
    };
    const persist = () => {
        try {localStorage.setItem(storageKey, JSON.stringify(saved)); renderSaved(); return true;}
        catch (_) {feedback.textContent = '이 브라우저에서 검색 조건을 저장할 수 없습니다.'; return false;}
    };
    root.querySelector('.history-saved').hidden = false;
    renderSaved();
    root.querySelector('[data-saved-save]').addEventListener('click', () => {
        const name = savedName.value.trim();
        if (!name) {feedback.textContent = '저장할 검색 이름을 입력하세요.'; savedName.focus(); return;}
        const params = new URLSearchParams(new FormData(form));
        const previous = saved.findIndex(item => item.name === name);
        if (previous < 0 && saved.length >= 20) {feedback.textContent = '검색은 최대 20개까지 저장할 수 있습니다.'; return;}
        const item = {name, query: params.toString()};
        if (item.query.length > 16000) {feedback.textContent = '선택한 조건이 너무 많아 저장할 수 없습니다.'; return;}
        const original = saved.slice();
        if (previous < 0) saved.push(item); else saved[previous] = item;
        if (persist()) {feedback.textContent = `‘${name}’ 검색을 저장했습니다.`; savedName.value = '';}
        else saved = original;
    });
    root.querySelector('[data-saved-load]').addEventListener('click', () => {
        const item = savedSelect.value === '' ? null : saved[Number(savedSelect.value)];
        if (!item) {feedback.textContent = '불러올 검색을 선택하세요.'; return;}
        const params = new URLSearchParams(item.query);
        [...params.keys()].forEach(key => {if (!allowed.has(key)) params.delete(key);});
        window.location.assign(`${window.location.pathname}?${params.toString()}`);
    });
    root.querySelector('[data-saved-delete]').addEventListener('click', () => {
        if (savedSelect.value === '') {feedback.textContent = '삭제할 검색을 선택하세요.'; return;}
        const original = saved.slice();
        saved.splice(Number(savedSelect.value), 1);
        if (persist()) feedback.textContent = '저장된 검색을 삭제했습니다.'; else saved = original;
    });
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
