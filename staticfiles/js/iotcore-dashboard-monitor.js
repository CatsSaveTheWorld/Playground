(() => {
    const endpoint = window.IOTCORE_NODE_METRICS_URL;
    if (!endpoint) return;

    const cards = new Map(
        Array.from(document.querySelectorAll('[data-node-monitor]')).map((card) => [
            card.dataset.nodeMonitor,
            card,
        ])
    );

    const entryCard = document.querySelector('[data-entry-status-card]');
    const weatherCard = document.querySelector('[data-weather-card]');

    const clamp = (value, min, max) => Math.min(max, Math.max(min, Number(value) || 0));

    const formatLastSeen = (iso) => {
        if (!iso) return '기록 없음';
        const date = new Date(iso);
        if (Number.isNaN(date.getTime())) return '기록 없음';
        return new Intl.DateTimeFormat('ko-KR', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false,
        }).format(date);
    };

    const formatEntryTime = (iso) => {
        if (!iso) return '--';
        const date = new Date(iso);
        if (Number.isNaN(date.getTime())) return '--';
        return new Intl.DateTimeFormat('ko-KR', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
        }).format(date);
    };

    const updateEntryStatus = (entry) => {
        if (!entryCard) return;

        const state = entryCard.querySelector('[data-entry-state]');
        const openCount = entryCard.querySelector('[data-entry-open-count]');
        const attemptCount = entryCard.querySelector('[data-entry-attempt-count]');
        const lastEvent = entryCard.querySelector('[data-entry-last-event]');

        if (state) {
            state.textContent = entry?.current_state || '상태 대기';
            state.classList.toggle('is-entry-open', entry?.is_open === true);
        }
        if (openCount) {
            openCount.textContent = entry?.open_count == null ? '--' : String(entry.open_count);
        }
        if (attemptCount) {
            attemptCount.textContent = entry?.attempt_count == null ? '--' : String(entry.attempt_count);
        }
        if (lastEvent) {
            lastEvent.textContent = formatEntryTime(entry?.last_event_at);
        }
    };

    const updateWeather = (weather) => {
        if (!weatherCard || !weather) return;

        const temperature = weatherCard.querySelector('[data-weather-temperature]');
        const condition = weatherCard.querySelector('[data-weather-condition]');
        const precipitation = weatherCard.querySelector('[data-weather-pop]');
        const highLow = weatherCard.querySelector('[data-weather-high-low]');
        const updatedAt = weatherCard.querySelector('[data-weather-updated-at]');

        if (temperature) {
            temperature.textContent = weather.temperature == null
                ? '--'
                : `${Number(weather.temperature).toFixed(1)}°C`;
        }
        if (condition) {
            const label = weather.condition || '정보 없음';
            condition.textContent = weather.location ? `${label} · ${weather.location}` : label;
        }
        if (precipitation) {
            precipitation.textContent = weather.precipitation_probability == null
                ? '--'
                : `${weather.precipitation_probability}%`;
        }
        if (highLow) {
            highLow.textContent = weather.high == null || weather.low == null
                ? '--'
                : `${Number(weather.high).toFixed(0)}° / ${Number(weather.low).toFixed(0)}°`;
        }
        if (updatedAt) {
            updatedAt.textContent = `${formatEntryTime(weather.updated_at)} 갱신${weather.stale ? ' · 이전 자료' : ''}`;
            updatedAt.hidden = false;
        }
    };

    const niceMax = (value) => {
        const v = Math.max(1, Number(value) || 0);
        const power = Math.pow(10, Math.floor(Math.log10(v)));
        const normalized = v / power;
        const nice = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
        return nice * power;
    };

    const drawNetwork = (canvas, history, accent, uploadColor) => {
        if (!canvas) return;
        const rect = canvas.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        const width = Math.max(180, rect.width);
        const height = Math.max(70, rect.height);
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);

        const ctx = canvas.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);

        const pad = { left: 1, right: 1, top: 4, bottom: 4 };
        const plotW = width - pad.left - pad.right;
        const plotH = height - pad.top - pad.bottom;
        const values = history.flatMap((p) => [p.download_mbps || 0, p.upload_mbps || 0]);
        const maxValue = niceMax(Math.max(1, ...values) * 1.1);

        ctx.strokeStyle = 'rgba(183,183,183,.13)';
        ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i += 1) {
            const y = pad.top + (plotH * i) / 4;
            ctx.beginPath();
            ctx.moveTo(pad.left, y);
            ctx.lineTo(width - pad.right, y);
            ctx.stroke();
        }
        for (let i = 0; i <= 6; i += 1) {
            const x = pad.left + (plotW * i) / 6;
            ctx.beginPath();
            ctx.moveTo(x, pad.top);
            ctx.lineTo(x, height - pad.bottom);
            ctx.stroke();
        }

        const drawSeries = (key, color) => {
            if (!history.length) return;
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            history.forEach((point, index) => {
                const x = pad.left + (plotW * index) / Math.max(1, history.length - 1);
                const y = pad.top + plotH - (clamp(point[key], 0, maxValue) / maxValue) * plotH;
                if (index === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            });
            ctx.stroke();
        };

        drawSeries('download_mbps', accent);
        drawSeries('upload_mbps', uploadColor);
        return maxValue;
    };

    const resultLabel = (event) => {
        if (!event) return '처리 기록 없음';
        if (event.route === 'automation_match') return '자동화 직접 매칭';
        return { success: '최근 요청 성공', failed: '최근 요청 실패', processing: '요청 처리 중', disabled: '서비스 비활성' }[event.status] || '확인 불가';
    };
    const duration = (value) => value == null ? '—' : `${Number(value).toFixed(0)} ms`;
    const updateAI = (card, ai) => {
        if (!ai) return;
        card.querySelectorAll('[data-ai-service]').forEach((element) => {
            const service = ai.services?.[element.dataset.aiService] || {};
            const badge = element.querySelector('[data-ai-service-state]');
            badge.textContent = service.label || '확인 불가';
            badge.dataset.aiState = service.status || 'unknown';
            element.querySelector('[data-ai-service-model]').textContent =
                [service.engine || service.model || '엔진 정보 없음', service.device].filter(Boolean).join(' · ');
            const event = service.last_result;
            element.querySelector('[data-ai-service-result]').textContent =
                resultLabel(event) + (event?.elapsed_ms == null ? '' : ` · ${duration(event.elapsed_ms)}`);
            element.querySelector('[data-ai-service-error]').textContent = service.stale ? '현재 상태 확인 대기' : (service.error || '');
            element.querySelector('[data-ai-service-requested]').textContent = `최근 요청 ${formatLastSeen(event?.started_at)}`;
            element.querySelector('[data-ai-service-checked]').textContent = `마지막 확인 ${formatLastSeen(service.checked_at)}`;
        });
        const gpuContainer = card.querySelector('[data-ai-gpus]');
        if (gpuContainer) {
            gpuContainer.replaceChildren();
            const gpus = ai.gpu?.status === 'available' ? ai.gpu.devices || [] : [];
            if (!gpus.length) {
                const message = document.createElement('small');
                message.textContent = ai.gpu?.error || '현재 GPU 데이터 없음';
                gpuContainer.append(message);
            }
            gpus.forEach((gpu) => {
                const row = document.createElement('div');
                row.className = 'iot-ai-gpu__device';
                const values = [gpu.name,
                    `사용률 ${gpu.utilization_percent == null ? '확인 불가' : `${Number(gpu.utilization_percent).toFixed(0)}%`}`,
                    `VRAM ${gpu.memory_used_mb == null || gpu.memory_total_mb == null ? '확인 불가' : `${Number(gpu.memory_used_mb).toFixed(0)} / ${Number(gpu.memory_total_mb).toFixed(0)} MiB`}`,
                    `온도 ${gpu.temperature_c == null ? '확인 불가' : `${Number(gpu.temperature_c).toFixed(0)}°C`}`];
                values.forEach((value, index) => {
                    const cell = document.createElement(index === 0 ? 'strong' : 'span');
                    cell.textContent = value;
                    row.append(cell);
                });
                gpuContainer.append(row);
            });
        }
        const history = Array.from(document.querySelectorAll('[data-ai-history]')).find((table) => table.dataset.aiHistory === card.dataset.nodeMonitor);
        if (history) {
            history.replaceChildren();
            (ai.recent || []).forEach((event) => {
                const row = document.createElement('tr');
                [formatLastSeen(event.started_at), event.request_id.slice(0, 8), event.stage.toUpperCase(),
                    event.route === 'automation_match' ? '자동화 직접 매칭' : '추론 서버',
                    { success: '성공', failed: '실패', processing: '처리 중', disabled: '비활성' }[event.status] || '확인 불가',
                    duration(event.elapsed_ms), event.error || ''].forEach((value) => {
                    const cell = document.createElement('td');
                    cell.textContent = value;
                    row.append(cell);
                });
                history.append(row);
            });
            if (!history.children.length) {
                const row = document.createElement('tr');
                const cell = document.createElement('td');
                cell.colSpan = 7;
                cell.textContent = '처리 기록 없음';
                row.append(cell);
                history.append(row);
            }
        }
    };

    const updateCard = (card, node) => {
        updateAI(card, node?.ai);
        const online = Boolean(node && node.online);
        const status = card.querySelector('[data-node-status]');
        const statusText = card.querySelector('[data-node-status-text]');
        const onlinePanel = card.querySelector('[data-node-online]');
        const offlinePanel = card.querySelector('[data-node-offline]');

        status?.classList.toggle('is-online', online);
        if (statusText) statusText.textContent = node?.transportError ? '확인 불가' : online ? 'ONLINE' : 'OFFLINE';
        if (onlinePanel) onlinePanel.hidden = !online;
        if (offlinePanel) offlinePanel.hidden = online;

        const lastSeen = card.querySelector('[data-node-last-seen]');
        if (lastSeen) lastSeen.textContent = formatLastSeen(node?.last_seen);
        if (!online || !node.current) return;

        const current = node.current;
        const cpu = clamp(current.cpu_percent, 0, 100);
        const memory = clamp(current.memory_percent, 0, 100);
        const storage = current.storage_percent == null ? null : clamp(current.storage_percent, 0, 100);

        const cpuGauge = card.querySelector('[data-cpu-gauge]');
        if (cpuGauge) cpuGauge.style.setProperty('--gauge-value', cpu.toFixed(1));
        const cpuValue = card.querySelector('[data-cpu-value]');
        if (cpuValue) cpuValue.textContent = cpu.toFixed(0);
        const cpuClockDetail = card.querySelector('[data-cpu-clock-detail]');
        if (cpuClockDetail) {
            const currentGhz = current.cpu_current_ghz;
            const maxGhz = current.cpu_max_ghz;
            if (currentGhz != null && maxGhz != null) {
                cpuClockDetail.textContent = `${Number(currentGhz).toFixed(1)} / ${Number(maxGhz).toFixed(1)} GHz`;
            } else if (currentGhz != null) {
                cpuClockDetail.textContent = `${Number(currentGhz).toFixed(1)} GHz`;
            } else {
                cpuClockDetail.textContent = '--';
            }
        }

        const memoryValue = card.querySelector('[data-memory-value]');
        if (memoryValue) memoryValue.textContent = memory.toFixed(0);
        const memoryBar = card.querySelector('[data-memory-bar]');
        if (memoryBar) memoryBar.style.width = `${memory.toFixed(1)}%`;
        const memoryDetail = card.querySelector('[data-memory-detail]');
        if (memoryDetail) {
            if (current.memory_used_gb != null && current.memory_total_gb != null) {
                memoryDetail.textContent = `${Number(current.memory_used_gb).toFixed(1)} / ${Number(current.memory_total_gb).toFixed(1)} GB`;
            } else {
                memoryDetail.textContent = '--';
            }
        }

        const storageValue = card.querySelector('[data-storage-value]');
        if (storageValue) storageValue.textContent = storage == null ? '--' : storage.toFixed(0);
        const storageArc = card.querySelector('[data-storage-arc]');
        if (storageArc) storageArc.setAttribute('stroke-dasharray', `${storage == null ? 0 : storage.toFixed(1)} 100`);
        const storageDetail = card.querySelector('[data-storage-detail]');
        if (storageDetail) {
            if (current.storage_used_gb != null && current.storage_total_gb != null) {
                storageDetail.textContent = `${Number(current.storage_used_gb).toFixed(0)} / ${Number(current.storage_total_gb).toFixed(0)} GB`;
            } else {
                storageDetail.textContent = '--';
            }
        }

        const downloadValue = card.querySelector('[data-download-value]');
        if (downloadValue) downloadValue.textContent = Number(current.download_mbps || 0).toFixed(1);
        const uploadValue = card.querySelector('[data-upload-value]');
        if (uploadValue) uploadValue.textContent = Number(current.upload_mbps || 0).toFixed(1);

        const style = getComputedStyle(document.documentElement);
        const accent = style.getPropertyValue('--iot-accent').trim() || '#00ffc8';
        const uploadColor = style.getPropertyValue('--iot-muted').trim() || '#b7b7b7';
        const maxValue = drawNetwork(
            card.querySelector('[data-network-chart]'),
            Array.isArray(node.history) ? node.history : [],
            accent,
            uploadColor,
        );
        const scale = card.querySelector('[data-network-scale]');
        if (scale && maxValue) scale.textContent = `${maxValue.toFixed(maxValue < 10 ? 1 : 0)} Mbps`;
    };

    const snapshots = new Map();
    const acceptSnapshot = (uid, node) => {
        const received = performance.now();
        snapshots.set(uid, {
            node, expires: received + (node.expires_in_seconds ?? 5) * 1000,
            services: Object.fromEntries(Object.entries(node.ai?.services || {}).map(([name, service]) => [
                name, received + (service.status === 'processing' ? 5 : service.expires_in_seconds || 0) * 1000,
            ])),
        });
        const card = cards.get(uid);
        if (card) updateCard(card, node);
    };
    try {
        const initial = document.getElementById('node-monitor-initial');
        if (initial) Object.entries(JSON.parse(initial.textContent)).forEach(([uid, node]) => acceptSnapshot(uid, node));
    } catch (error) { console.warn('초기 모니터링 데이터 읽기 실패:', error); }

    const expireSnapshots = () => {
        const now = performance.now();
        snapshots.forEach((entry, uid) => {
            const node = JSON.parse(JSON.stringify(entry.node));
            if (now >= entry.expires) {
                node.online = false;
                if (node.ai) node.ai.gpu = { status: 'unavailable', devices: [], error: '현재 GPU 데이터 없음' };
            }
            Object.entries(node.ai?.services || {}).forEach(([name, service]) => {
                if (service.status !== 'disabled' && (now >= entry.services[name] || (!node.online && service.status !== 'processing'))) {
                    service.status = 'unknown';
                    service.label = '확인 불가';
                    service.stale = true;
                }
            });
            const card = cards.get(uid);
            if (card) updateCard(card, node);
        });
    };

    let fetching = false;
    const refresh = async () => {
        if (fetching || document.hidden) return;
        fetching = true;
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 4000);
        try {
            const response = await fetch(endpoint, {
                method: 'GET',
                headers: { Accept: 'application/json' },
                cache: 'no-store',
                signal: controller.signal,
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            updateEntryStatus(data.entry_status);
            updateWeather(data.weather);
            Object.entries(data.nodes || {}).forEach(([uid, node]) => acceptSnapshot(uid, node));
        } catch (error) {
            console.warn('시스템 telemetry 갱신 실패:', error);
            snapshots.forEach((entry) => { entry.node.transportError = true; });
            expireSnapshots();
        } finally {
            window.clearTimeout(timeout);
            fetching = false;
        }
    };

    refresh();
    const timer = window.setInterval(refresh, 2000);
    const expirationTimer = window.setInterval(expireSnapshots, 1000);
    window.addEventListener('pagehide', () => {
        window.clearInterval(timer);
        window.clearInterval(expirationTimer);
    }, { once: true });
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) refresh();
    });
})();
