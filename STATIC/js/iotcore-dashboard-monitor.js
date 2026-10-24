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

    const updateRouting = (card, node) => {
        if (!node?.ai) return;
        const routingPanel = card.closest('[data-monitor-layout]')?.querySelector('[data-ai-routing-panel]') || card;
        const rows = node.ai.routing || [];
        routingPanel.querySelectorAll('[data-ai-route]').forEach((element) => {
            const route = rows.find((row) => row.service === element.dataset.aiRoute);
            if (!route) return;
            element.dataset.routeState = route.state;
            const badge = element.querySelector('[data-route-status]');
            if (badge) {
                badge.textContent = route.status_label || '확인 불가';
                badge.dataset.aiState = route.status || 'unknown';
            }
            const values = {
                'active': route.active?.name || '미지정',
                'standby': route.standby?.name || '미지정',
                'note': route.note || '',
                'model': route.model || '',
                'usage': route.usage || '',
                'error': route.error || '',
            };
            Object.entries(values).forEach(([key, value]) => {
                const field = element.querySelector(`[data-route-${key}]`);
                if (!field) return;
                field.textContent = value;
                if (['model', 'usage', 'error'].includes(key)) field.hidden = !value;
            });
        });
        const freshness = routingPanel.querySelector('[data-route-freshness]');
        if (freshness) {
            freshness.hidden = !node?.transportError;
            freshness.textContent = node?.transportError ? '배치 갱신 실패 · 마지막으로 확인한 설정입니다.' : '';
        }
    };
    const gpuNumber = (value) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
    const drawGPUHistory = (canvas, points, key, maximum, color) => {
        const rect = canvas.getBoundingClientRect();
        const width = Math.max(1, rect.width);
        const height = Math.max(1, rect.height);
        const dpr = window.devicePixelRatio || 1;
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
        const ctx = canvas.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);
        const pad = 3;
        const plotW = Math.max(1, width - pad * 2);
        const plotH = Math.max(1, height - pad * 2);
        ctx.strokeStyle = 'rgba(183,183,183,.14)';
        ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i += 1) {
            const y = pad + plotH * i / 4;
            ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(width - pad, y); ctx.stroke();
        }
        for (let i = 0; i <= 6; i += 1) {
            const x = pad + plotW * i / 6;
            ctx.beginPath(); ctx.moveTo(x, pad); ctx.lineTo(x, height - pad); ctx.stroke();
        }
        const end = points.length ? points[points.length - 1].at : Date.now();
        const start = end - 60000;
        let drawing = false;
        let previousAt = 0;
        let last = null;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.7;
        ctx.beginPath();
        points.forEach((point) => {
            const value = gpuNumber(point.gpu?.[key]);
            if (value === null || point.at < start || point.at > end) {
                drawing = false;
                return;
            }
            const x = pad + (point.at - start) / 60000 * plotW;
            const y = pad + plotH - Math.min(value, maximum) / maximum * plotH;
            if (!drawing || point.at - previousAt > 5000) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
            drawing = true;
            previousAt = point.at;
            last = { x, y };
        });
        ctx.stroke();
        if (last) {
            ctx.fillStyle = color;
            ctx.beginPath(); ctx.arc(last.x, last.y, 2, 0, Math.PI * 2); ctx.fill();
        }
    };

    const updateGPUCharts = (card, ai, history) => {
        const container = card.querySelector('[data-ai-gpus]');
        if (!container) return;
        container.replaceChildren();
        const devices = ai.gpu?.status === 'available' ? ai.gpu.devices || [] : [];
        const displayed = devices.length ? devices : [null];
        displayed.forEach((gpu, index) => {
            const device = document.createElement('div');
            device.className = 'iot-gpu-device';
            const name = document.createElement('strong');
            name.className = 'iot-gpu-device__name';
            name.textContent = gpu?.name || '현재 GPU 데이터 없음';
            device.append(name);
            const grid = document.createElement('div');
            grid.className = 'iot-gpu-charts';
            device.append(grid);
            container.append(device);
            const points = (history || []).map((point) => ({
                at: Date.parse(point.timestamp),
                gpu: gpu && point.gpu?.[index]?.name === gpu.name ? point.gpu[index] : null,
            })).filter((point) => Number.isFinite(point.at));
            const totals = points.map((point) => gpuNumber(point.gpu?.memory_total_mb) || 0);
            const total = gpuNumber(gpu?.memory_total_mb);
            const used = gpuNumber(gpu?.memory_used_mb);
            const utilization = gpuNumber(gpu?.utilization_percent);
            const temperature = gpuNumber(gpu?.temperature_c);
            const temperatureMax = Math.max(100, niceMax(Math.max(0, ...points.map((point) => gpuNumber(point.gpu?.temperature_c) || 0), temperature || 0)));
            const vramMax = Math.max(1024, total || 0, used || 0, ...totals);
            const charts = [
                { key: 'utilization_percent', title: '사용률', color: '#00e6b8', maximum: 100,
                  value: utilization === null ? '--' : `${utilization.toFixed(0)}%`, scale: '100%' },
                { key: 'memory_used_mb', title: 'VRAM', color: '#69b9ff', maximum: vramMax,
                  value: used === null ? '--' : `${(used / 1024).toFixed(1)} / ${total === null ? '--' : (total / 1024).toFixed(1)} GiB`, scale: `${(vramMax / 1024).toFixed(1)} GiB` },
                { key: 'temperature_c', title: '온도', color: '#f2c66d', maximum: temperatureMax,
                  value: temperature === null ? '--' : `${temperature.toFixed(0)}°C`, scale: `${temperatureMax}°C` },
            ];
            charts.forEach((metric) => {
                const panel = document.createElement('div');
                panel.className = 'iot-gpu-chart';
                panel.dataset.gpuChartMetric = metric.key;
                panel.style.setProperty('--gpu-color', metric.color);
                const head = document.createElement('div');
                head.className = 'iot-gpu-chart__head';
                const title = document.createElement('span'); title.textContent = metric.title;
                const value = document.createElement('strong'); value.textContent = metric.value;
                head.append(title, value);
                const scale = document.createElement('div');
                scale.className = 'iot-gpu-chart__scale';
                const minimum = document.createElement('span'); minimum.textContent = '0';
                const maximum = document.createElement('span'); maximum.textContent = metric.scale;
                scale.append(minimum, maximum);
                const canvas = document.createElement('canvas');
                canvas.setAttribute('role', 'img');
                canvas.setAttribute('aria-label', `${gpu?.name || 'GPU'} ${metric.title} 최근 60초 그래프 · 현재 ${metric.value}`);
                const time = document.createElement('div');
                time.className = 'iot-gpu-chart__time';
                const from = document.createElement('span'); from.textContent = '-60초';
                const to = document.createElement('span'); to.textContent = '현재';
                time.append(from, to);
                panel.append(head, scale, canvas, time);
                grid.append(panel);
                drawGPUHistory(canvas, points, metric.key, metric.maximum, metric.color);
                if (!points.some((point) => gpuNumber(point.gpu?.[metric.key]) !== null)) {
                    const empty = document.createElement('p');
                    empty.className = 'iot-gpu-chart__empty';
                    empty.textContent = '수신된 그래프 데이터 없음';
                    panel.append(empty);
                }
            });
        });
    };

    const updateAI = (card, ai, history) => {
        if (!ai) return;
        updateGPUCharts(card, ai, history);

    };

    const updateCard = (card, node) => {
        const resourceName = card.querySelector('[data-ai-resource-name]');
        if (resourceName) resourceName.textContent = node?.name || node?.uid || '확인 불가';
        updateRouting(card, node);
        updateAI(card, node?.ai, node?.history || []);
        const online = Boolean(node && node.online);
        const status = card.querySelector('[data-node-status]');
        const statusText = card.querySelector('[data-node-status-text]');
        const onlinePanel = card.querySelector('[data-node-online]');
        const offlinePanel = card.querySelector('[data-node-offline]');

        status?.classList.toggle('is-online', online);
        if (statusText) statusText.textContent = node?.transportError ? '확인 불가' : online ? 'ONLINE' : 'OFFLINE';
        if (onlinePanel) onlinePanel.hidden = !online && !node?.ai;
        if (offlinePanel) offlinePanel.hidden = online;

        const lastSeen = card.querySelector('[data-node-last-seen]');
        if (lastSeen) lastSeen.textContent = formatLastSeen(node?.last_seen);
        if (!online || !node.current) {
            if (node?.ai) {
                ['cpu-value', 'memory-value', 'storage-value', 'download-value', 'upload-value',
                 'cpu-clock-detail', 'memory-detail', 'storage-detail', 'network-scale'].forEach((name) => {
                    const field = card.querySelector(`[data-${name}]`);
                    if (field) field.textContent = '--';
                });
                card.querySelector('[data-cpu-gauge]')?.style.setProperty('--gauge-value', '0');
                const bar = card.querySelector('[data-memory-bar]');
                if (bar) bar.style.width = '0%';
                card.querySelector('[data-storage-arc]')?.setAttribute('stroke-dasharray', '0 100');
                drawNetwork(card.querySelector('[data-network-chart]'), [], '#00ffc8', '#b7b7b7');
            }
            return;
        }

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
        document.querySelectorAll('[data-ai-node-tab]').forEach((tab) => {
            if (tab.dataset.aiNodeTab === uid) tab.textContent = node.name || uid;
        });
        const received = performance.now();
        snapshots.set(uid, {
            node, expires: received + (node.expires_in_seconds ?? 5) * 1000,
            routes: Object.fromEntries((node.ai?.routing || []).map((route) => [
                route.service, received + (route.expires_in_seconds || 0) * 1000,
            ])),
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
            (node.ai?.routing || []).forEach((route) => {
                if (!['pending', 'disabled'].includes(route.state) && now >= entry.routes[route.service]) {
                    route.status = 'unknown';
                    route.status_label = '확인 불가';
                    route.usage = '현재 처리량 확인 대기';
                    route.error = '';
                }
            });
            const card = cards.get(uid);
            if (card) updateCard(card, node);
        });
    };

    const selectResource = (card, tab) => {
        const uid = tab.dataset.aiNodeTab;
        cards.delete(card.dataset.nodeMonitor);
        card.dataset.nodeMonitor = uid;
        cards.set(uid, card);
        card.querySelectorAll('[data-ai-node-tab]').forEach((button) => {
            const selected = button === tab;
            button.setAttribute('aria-selected', String(selected));
            button.tabIndex = selected ? 0 : -1;
        });
        card.querySelector('[role="tabpanel"]')?.setAttribute('aria-labelledby', tab.id);
        try { sessionStorage.setItem('iotcore-ai-resource-node', uid); } catch (_) {}
        expireSnapshots();
    };
    document.querySelectorAll('.iot-node-monitor--ai').forEach((card) => {
        const tabs = Array.from(card.querySelectorAll('[data-ai-node-tab]'));
        tabs.forEach((tab, index) => {
            tab.addEventListener('click', () => selectResource(card, tab));
            tab.addEventListener('keydown', (event) => {
                let next = index;
                if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
                else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
                else if (event.key === 'Home') next = 0;
                else if (event.key === 'End') next = tabs.length - 1;
                else return;
                event.preventDefault();
                selectResource(card, tabs[next]);
                tabs[next].focus();
            });
        });
        try {
            const saved = sessionStorage.getItem('iotcore-ai-resource-node');
            const tab = tabs.find((button) => button.dataset.aiNodeTab === saved);
            if (tab) selectResource(card, tab);
        } catch (_) {}
    });

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
