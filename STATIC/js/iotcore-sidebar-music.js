(() => {
    'use strict';
    const root = document.getElementById('iotSidebarMusic');
    if (!root) return;
    const config = JSON.parse(document.getElementById('iotSidebarMusicConfig').textContent);
    const find = id => document.getElementById(`iotMusic${id}`);
    const toggle = find('Toggle');
    const body = find('Body');
    const store = {
        get(key) { try { return localStorage.getItem(`iotcore.music.${key}`); } catch { return null; } },
        set(key, value) { try { localStorage.setItem(`iotcore.music.${key}`, value); } catch { /* Storage is optional. */ } },
    };
    let expanded = store.get('expanded') !== 'false';
    function renderExpanded() {
        toggle.setAttribute('aria-expanded', String(expanded));
        toggle.title = expanded ? '음악 제어 접기' : '음악 제어 펼치기';
        body.hidden = !expanded;
    }
    renderExpanded();
    toggle.addEventListener('click', () => {
        expanded = !expanded;
        store.set('expanded', String(expanded));
        renderExpanded();
        if (expanded) refresh();
    });
    if (!config.length) return;

    const device = find('Device');
    const playlist = find('Playlist');
    const playlistPlay = find('PlaylistPlay');
    const volume = find('Volume');
    const volumeValue = find('VolumeValue');
    const shuffle = find('Shuffle');
    const repeat = find('Repeat');
    const controls = find('Controls');
    const status = find('Status');
    const track = find('Track');
    let busy = false;
    let revision = 0;
    let volumeDirty = false;
    let loadingState = false;
    let loadingPlaylists = false;
    let playlistsLoaded = false;
    let stateController;
    let playlistController;
    let confirmedShuffle = '';
    let confirmedRepeat = '';
    const savedDevice = store.get('device');
    if (config.some(item => item.id === savedDevice)) device.value = savedDevice;
    function selected() { return config.find(item => item.id === device.value); }
    function message(text, error = false) {
        status.textContent = text;
        status.classList.toggle('is-error', error);
    }
    function updatePlaylistButton() { playlistPlay.disabled = busy || !playlist.value; }
    function rememberPlaylist() { store.set(`playlist.${device.value}`, playlist.value); }
    function showVolume(value) { volumeValue.value = value; volumeValue.textContent = value; }
    async function read(url, controller) {
        const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store',
            headers: { Accept: 'application/json' }, signal: controller.signal });
        const result = await window.IOTCORE_PARSE_JSON_RESPONSE(response);
        if (result.success === false) throw new Error(result.message || '음악 정보를 불러오지 못했습니다.');
        return result;
    }
    async function loadPlaylists() {
        if (loadingPlaylists || playlistsLoaded) return;
        const current = revision;
        loadingPlaylists = true;
        playlistController = new AbortController();
        try {
            const data = await read(`${selected().urls.snapshot}?section=playlists`, playlistController);
            if (current !== revision) return;
            playlist.replaceChildren(new Option(data.playlists.length ? '재생목록 선택' : '등록된 재생목록 없음', ''));
            for (const item of data.playlists) playlist.add(new Option(item.name, item.item_id));
            const saved = store.get(`playlist.${device.value}`);
            if ([...playlist.options].some(option => option.value === saved)) playlist.value = saved;
            playlistsLoaded = true;
            updatePlaylistButton();
        } catch (error) {
            if (current !== revision || error.name === 'AbortError') return;
            playlist.replaceChildren(new Option('재생목록 조회 실패', ''));
            updatePlaylistButton();
            message(error.message, true);
        } finally { if (current === revision) loadingPlaylists = false; }
    }
    async function loadState() {
        if (loadingState || busy || !expanded || document.hidden) return;
        const current = revision;
        loadingState = true;
        stateController = new AbortController();
        try {
            const data = await read(selected().urls.snapshot, stateController);
            if (current !== revision || busy) return;
            const state = data.state;
            const labels = { playing: '재생 중', paused: '일시정지', idle: '대기 중', stopped: '정지', unknown: '상태 확인' };
            track.textContent = state.available === false ? '스피커 오프라인' :
                [labels[state.playback] || '상태 확인', state.track].filter(Boolean).join(' · ');
            track.title = track.textContent;
            if (!volumeDirty && Number.isFinite(state.volume)) {
                volume.value = Math.max(0, Math.min(100, state.volume));
                showVolume(volume.value);
            }
            confirmedShuffle = state.shuffle === true ? 'on' : state.shuffle === false ? 'off' : '';
            confirmedRepeat = ['off', 'all', 'one'].includes(state.repeat) ? state.repeat : '';
            // Do not move a select while someone is choosing an option.
            if (document.activeElement !== shuffle) shuffle.value = confirmedShuffle;
            if (document.activeElement !== repeat) repeat.value = confirmedRepeat;
            if (data.message) message(data.message, true);
        } catch (error) {
            if (current !== revision || error.name === 'AbortError') return;
            track.textContent = '재생 상태 확인 불가';
            track.title = error.message;
        } finally { if (current === revision) loadingState = false; }
    }
    function refresh() {
        if (!config.length || !expanded || busy || document.hidden) return;
        loadPlaylists();
        loadState();
    }
    async function command(action) {
        if (busy) return;
        const speaker = selected();
        let url = speaker.urls[action];
        const params = new URLSearchParams({ device_id: speaker.id });
        const nextShuffle = shuffle.value;
        const nextRepeat = repeat.value;
        if (action === 'playlist') {
            if (!playlist.value) return;
            url = speaker.urls.playlist.replace('__playlist__', encodeURIComponent(playlist.value));
            params.set('playlist_id', playlist.value);
        } else if (action === 'volume') {
            params.set('volume', volume.value);
        } else if (action === 'shuffle') {
            if (!['on', 'off'].includes(nextShuffle)) return;
            url = speaker.urls[nextShuffle === 'on' ? 'shuffleOn' : 'shuffleOff'];
        } else if (action === 'repeat') {
            if (!['off', 'all', 'one'].includes(nextRepeat)) return;
            url = speaker.urls.repeat.replace('__repeat__', nextRepeat);
        }
        if (!url) return;
        stateController?.abort();
        busy = true;
        controls.disabled = true;
        device.disabled = true;
        root.setAttribute('aria-busy', 'true');
        message('요청을 처리하고 있습니다.');
        try {
            const data = await window.IOTCORE_POST_FORM(url, params);
            if (data.success === false || data.status === 'fail') throw new Error(data.message || '음악 제어에 실패했습니다.');
            if (action === 'volume') volumeDirty = false;
            if (action === 'shuffle') confirmedShuffle = nextShuffle;
            if (action === 'repeat') confirmedRepeat = nextRepeat;
            const text = action === 'playlist' ? `${playlist.selectedOptions[0].textContent} 재생을 시작했습니다.` : data.message || '요청을 완료했습니다.';
            message(text);
        } catch (error) {
            if (action === 'shuffle') shuffle.value = confirmedShuffle;
            if (action === 'repeat') repeat.value = confirmedRepeat;
            message(error.message || '음악 제어에 실패했습니다.', true);
        } finally {
            busy = false;
            controls.disabled = false;
            device.disabled = false;
            root.removeAttribute('aria-busy');
            updatePlaylistButton();
            loadState();
        }
    }
    root.addEventListener('click', event => {
        const button = event.target.closest('[data-music-action]');
        if (button) command(button.dataset.musicAction);
    });
    volume.addEventListener('input', () => { volumeDirty = true; showVolume(volume.value); });
    playlist.addEventListener('change', () => { rememberPlaylist(); updatePlaylistButton(); });
    shuffle.addEventListener('change', () => command('shuffle'));
    repeat.addEventListener('change', () => command('repeat'));
    device.addEventListener('change', () => {
        revision++;
        stateController?.abort();
        playlistController?.abort();
        loadingState = loadingPlaylists = playlistsLoaded = volumeDirty = false;
        confirmedShuffle = confirmedRepeat = '';
        shuffle.value = repeat.value = '';
        volume.value = 30;
        showVolume('—');
        playlist.replaceChildren(new Option('불러오는 중…', ''));
        updatePlaylistButton();
        track.textContent = '재생 정보 확인 중…';
        message('재생할 목록을 선택하세요.');
        store.set('device', device.value);
        refresh();
    });
    find('Refresh').addEventListener('click', () => { playlistsLoaded = false; refresh(); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    window.addEventListener('storage', event => {
        if (event.key === 'iotcore.music.expanded') {
            expanded = event.newValue !== 'false';
            renderExpanded();
            refresh();
        }
    });
    // Refresh changes from automations, the detailed controller, or other clients.
    window.setInterval(refresh, 15000);
    refresh();
})();
