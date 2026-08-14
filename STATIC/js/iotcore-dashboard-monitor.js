(() => {
    const endpoint = window.IOTCORE_NODE_METRICS_URL;
    if (!endpoint) return;

    const cards = new Map(
        Array.from(document.querySelectorAll('[data-node-monitor]')).map((card) => [
            card.dataset.nodeMonitor,
            card,
        ])
    );

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

    const updateCard = (card, node) => {
        const online = Boolean(node && node.online);
        const status = card.querySelector('[data-node-status]');
        const statusText = card.querySelector('[data-node-status-text]');
        const onlinePanel = card.querySelector('[data-node-online]');
        const offlinePanel = card.querySelector('[data-node-offline]');

        status?.classList.toggle('is-online', online);
        if (statusText) statusText.textContent = online ? 'ONLINE' : 'OFFLINE';
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

    let fetching = false;
    const refresh = async () => {
        if (fetching || document.hidden) return;
        fetching = true;
        try {
            const response = await fetch(endpoint, {
                method: 'GET',
                headers: { Accept: 'application/json' },
                cache: 'no-store',
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            Object.entries(data.nodes || {}).forEach(([uid, node]) => {
                const card = cards.get(uid);
                if (card) updateCard(card, node);
            });
        } catch (error) {
            console.warn('시스템 telemetry 갱신 실패:', error);
        } finally {
            fetching = false;
        }
    };

    refresh();
    const timer = window.setInterval(refresh, 3000);
    window.addEventListener('pagehide', () => window.clearInterval(timer), { once: true });
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) refresh();
    });
})();
