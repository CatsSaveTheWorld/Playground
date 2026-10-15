(() => {
    "use strict";

    const form = document.getElementById("automation-graph-form");
    if (!form) return;

    const readJson = (id, fallback) => {
        const element = document.getElementById(id);
        if (!element) return fallback;
        try { return JSON.parse(element.textContent || "null") ?? fallback; }
        catch (_error) { return fallback; }
    };

    const initial = readJson("automation-graph-data", {nodes: [], edges: [], revision: 0});
    const devices = readJson("automation-graph-devices", []);
    const automations = readJson("automation-graph-automations", []);
    const actionRegistry = readJson("automation-graph-action-registry", {});

    const viewport = document.getElementById("automation-graph-viewport");
    const world = document.getElementById("automation-graph-world");
    const nodesLayer = document.getElementById("automation-graph-nodes");
    const edgesLayer = document.getElementById("automation-graph-edges");
    const inspector = document.getElementById("automation-graph-inspector");
    const hiddenInput = document.getElementById("automation-graph-json");
    const statusElement = document.getElementById("graph-status");
    const saveState = document.getElementById("graph-save-state");
    const zoomLabel = document.getElementById("graph-zoom-label");

    const layout = window.IoTGraphLayout;
    if (!layout) {
        statusElement.textContent = "그래프 정리 기능을 불러오지 못했습니다. 새로고침해 주세요.";
        form.addEventListener("submit", event => event.preventDefault());
        return;
    }
    const layoutMessage = document.getElementById("graph-layout-message");
    const pinStorageKey = `iotcore-graph-pins:${location.pathname}`;
    let storedPins = [];
    try { storedPins = JSON.parse(localStorage.getItem(pinStorageKey) || "[]"); } catch (_error) { /* optional preference */ }
    const pinnedNodes = new Set(Array.isArray(storedPins) ? storedPins : []);
    let edgeFrame = null;
    let edgeGeometry = null;

    const NODE_WIDTH = {start: 160, end: 160, condition: 236, action: 236, delay: 236};
    const TYPE_LABEL = {start: "START", condition: "CONDITION", action: "ACTION", delay: "DELAY", end: "END"};
    const CONDITION_LABEL = {schedule: "날짜 / 시간", device_state: "기기 상태 / 변화", mqtt_event: "MQTT 이벤트", weather: "현재 날씨"};
    const OP_LABEL = {eq: "=", ne: "≠", gt: ">", gte: "≥", lt: "<", lte: "≤", changed: "변경됨", changed_to: "지정 값으로 변경"};

    const state = {
        revision: Number(initial.revision || 0),
        nodes: (initial.nodes || []).map(node => ({
            ...node,
            key: String(node.key),
            x: Number(node.x || 0),
            y: Number(node.y || 0),
            enabled: node.enabled !== false,
            delay_seconds: Number(node.delay_seconds || 0),
            conditions: Array.isArray(node.conditions) ? node.conditions.map(item => ({
                type: item.type || item.condition_type || "device_state",
                config: {...(item.config || {})},
            })) : [],
            actions: Array.isArray(node.actions) ? node.actions.map(item => ({
                action_type: item.action_type || "device",
                device_id: item.device_id ?? null,
                function: item.function || "",
                parameter: item.parameter ?? null,
                target_automation_id: item.target_automation_id ?? null,
                delay: Number(item.delay || 0),
                delay_position: item.delay_position || "after",
            })) : [],
        })),
        edges: (initial.edges || []).map((edge, index) => ({
            id: `edge-${index}-${String(edge.source)}-${String(edge.target)}-${edge.outcome || "always"}`,
            source: String(edge.source),
            target: String(edge.target),
            outcome: edge.outcome || "always",
            order: Number(edge.order || 1),
        })),
        selectedNodeKey: null,
        selectedEdgeId: null,
        layoutPreview: null,
        layoutUndo: null,
        pendingConnection: null,
        zoom: 1,
        panX: 24,
        panY: 24,
        dirty: false,
    };

    const deviceById = new Map(devices.map(device => [String(device.id), device]));
    const automationById = new Map(automations.map(item => [String(item.id), item]));
    const nodeByKey = key => state.nodes.find(node => node.key === String(key));

    const uuid = () => {
        if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
        return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, char => {
            const value = Math.random() * 16 | 0;
            return (char === "x" ? value : (value & 0x3 | 0x8)).toString(16);
        });
    };

    const escapeHtml = value => String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");

    const formatValue = value => {
        if (value === null || value === undefined) return "";
        if (typeof value === "string") return value;
        try { return JSON.stringify(value); } catch (_error) { return String(value); }
    };

    const parseSmartValue = raw => {
        const value = String(raw ?? "").trim();
        if (!value) return null;
        try { return JSON.parse(value); } catch (_error) { return value; }
    };

    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

    function nodeTitle(node) {
        if (node.title) return node.title;
        if (node.type === "condition") return "조건";
        if (node.type === "action") return "동작";
        if (node.type === "delay") return "지연";
        return TYPE_LABEL[node.type] || node.type;
    }

    function conditionSummary(item) {
        const config = item.config || {};
        if (item.type === "schedule") {
            if (config.schedule_type === "once") return `한 번 · ${config.run_at || "시각 미설정"}`;
            if (config.schedule_type === "interval") return `${config.every || "?"}${{minutes:"분",hours:"시간",days:"일"}[config.unit] || ""} 간격`;
            if (config.schedule_type === "weekly") {
                const weekdays = ["월","화","수","목","금","토","일"];
                const days = (config.weekdays || []).map(value => weekdays[Number(value)]).filter(Boolean).join("·") || "요일 미설정";
                if ((config.time_mode || "at") === "window") return `${days} ${config.start || "?"}~${config.end || "종료 없음"}`;
                return `${days} ${config.time || "시각 미설정"}`;
            }
            return "날짜 / 시간 설정 필요";
        }
        if (item.type === "device_state") {
            const device = deviceById.get(String(config.device_id));
            return `${device?.name || config.device_name || "기기 미선택"} · ${config.key || "상태 키"} ${OP_LABEL[config.operator] || config.operator || "?"} ${formatValue(config.value)}`;
        }
        if (item.type === "mqtt_event") return `${config.topic || "토픽 미설정"} · ${config.field || "value"} ${OP_LABEL[config.operator] || config.operator || "received"}`;
        if (item.type === "weather") return `${{temperature:"기온",humidity:"습도",precipitation_probability:"강수확률"}[config.metric] || "날씨"} ${OP_LABEL[config.operator] || config.operator || "?"} ${formatValue(config.value)}`;
        return CONDITION_LABEL[item.type] || "조건";
    }

    function actionSummary(item) {
        if ((item.action_type || "device") === "automation") {
            const target = automationById.get(String(item.target_automation_id));
            return `자동화 · ${target?.name || item.target_automation_name || "미선택"}`;
        }
        const device = deviceById.get(String(item.device_id));
        const definition = (actionRegistry[device?.device_type] || []).find(action => action.code === item.function);
        const name = definition?.name || item.function || "동작 미선택";
        return `${device?.name || "기기 미선택"} · ${name}`;
    }

    function applyTransform() {
        world.style.transform = `translate(${state.panX}px, ${state.panY}px) scale(${state.zoom})`;
        zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
    }

    function markDirty(message = "변경 사항 있음") {
        state.dirty = true;
        saveState.textContent = message;
        validateAndReport(false);
        updateLayoutControls();
    }

    function defaultNode(type) {
        const count = state.nodes.length;
        const base = {
            key: uuid(), type, title: "", x: 300 + count * 18, y: 180 + count * 18,
            enabled: true, delay_seconds: 0, conditions: [], actions: [],
        };
        if (type === "condition") {
            base.title = "조건";
            base.conditions = [{type: "device_state", config: {operator: "eq", value: true}}];
        } else if (type === "action") {
            base.title = "동작";
            base.actions = [{action_type: "device", device_id: null, function: "", parameter: null, target_automation_id: null, delay: 0, delay_position: "after"}];
        } else if (type === "delay") {
            base.title = "지연";
            base.delay_seconds = 1;
        }
        return base;
    }

    function renderNodes() {
        nodesLayer.replaceChildren();
        state.nodes.forEach(node => {
            const element = document.createElement("article");
            element.className = `iot-graph-node iot-graph-node--${node.type}${state.selectedNodeKey === node.key ? " is-selected" : ""}${node.enabled === false ? " is-disabled" : ""}`;
            element.dataset.nodeKey = node.key;
            element.classList.toggle("is-pinned", pinnedNodes.has(node.key));
            element.title = nodeTitle(node) + (pinnedNodes.has(node.key) ? " · 자동 정리 위치 고정" : "");
            element.style.left = `${node.x}px`;
            element.style.top = `${node.y}px`;

            const header = document.createElement("div");
            header.className = "iot-graph-node__header";
            header.innerHTML = `<span class="iot-graph-node__kind">${TYPE_LABEL[node.type] || escapeHtml(node.type)}</span><span class="iot-graph-node__title">${escapeHtml(nodeTitle(node))}</span>`;
            element.appendChild(header);

            const body = document.createElement("div");
            body.className = "iot-graph-node__body";
            if (node.type === "condition") {
                if (!node.conditions.length) body.innerHTML = '<div class="iot-graph-node__muted">조건 설정 필요</div>';
                node.conditions.forEach((item, index) => {
                    if (index) {
                        const join = document.createElement("div");
                        join.className = "iot-graph-node__join";
                        join.textContent = "AND";
                        body.appendChild(join);
                    }
                    const row = document.createElement("div");
                    row.className = "iot-graph-node__row";
                    row.textContent = conditionSummary(item);
                    body.appendChild(row);
                });
            } else if (node.type === "action") {
                if (!node.actions.length) body.innerHTML = '<div class="iot-graph-node__muted">동작 설정 필요</div>';
                node.actions.forEach((item, index) => {
                    const row = document.createElement("div");
                    row.className = "iot-graph-node__row";
                    row.textContent = `${index + 1}. ${actionSummary(item)}`;
                    body.appendChild(row);
                });
            } else if (node.type === "delay") {
                body.textContent = `${Number(node.delay_seconds || 0)}초 대기`;
            } else if (node.type === "start") {
                body.textContent = "자동화 시작점";
            } else if (node.type === "end") {
                body.textContent = "이 경로 종료";
            }
            element.appendChild(body);

            if (node.type !== "start") {
                const input = document.createElement("button");
                input.type = "button";
                input.className = "iot-graph-port iot-graph-port--input";
                input.dataset.port = "input";
                input.title = "입력";
                element.appendChild(input);
            }
            if (node.type === "condition") {
                ["true", "false"].forEach(outcome => {
                    const port = document.createElement("button");
                    port.type = "button";
                    port.className = `iot-graph-port iot-graph-port--${outcome}${state.pendingConnection?.source === node.key && state.pendingConnection?.outcome === outcome ? " is-connecting" : ""}`;
                    port.dataset.port = "output";
                    port.dataset.outcome = outcome;
                    port.title = `${outcome.toUpperCase()} 출력`;
                    element.appendChild(port);
                    const label = document.createElement("span");
                    label.className = `iot-graph-port-label iot-graph-port-label--${outcome}`;
                    label.textContent = outcome.toUpperCase();
                    element.appendChild(label);
                });
            } else if (["start", "action", "delay"].includes(node.type)) {
                const port = document.createElement("button");
                port.type = "button";
                port.className = `iot-graph-port iot-graph-port--always${state.pendingConnection?.source === node.key ? " is-connecting" : ""}`;
                port.dataset.port = "output";
                port.dataset.outcome = "always";
                port.title = "다음 Node 연결";
                element.appendChild(port);
            }

            element.addEventListener("click", event => {
                if (event.target.closest(".iot-graph-port")) return;
                selectNode(node.key);
            });
            bindNodeDrag(header, element, node);
            bindPorts(element, node);
            nodesLayer.appendChild(element);
        });
        requestEdgeRender();
        updateLayoutControls();
    }

    function portPoint(nodeKey, kind, outcome = null) {
        const nodeElement = nodesLayer.querySelector(`[data-node-key="${CSS.escape(String(nodeKey))}"]`);
        if (!nodeElement) return null;
        let selector = kind === "input" ? '[data-port="input"]' : `[data-port="output"][data-outcome="${outcome}"]`;
        const port = nodeElement.querySelector(selector);
        if (!port) return null;
        const node = nodeByKey(nodeKey);
        return {
            x: node.x + port.offsetLeft + port.offsetWidth / 2,
            y: node.y + port.offsetTop + port.offsetHeight / 2,
        };
    }

    function orderedEdges() {
        return layout.orderedEdges(state.edges);
    }

    function captureLayout() {
        return {positions:Object.fromEntries(state.nodes.map(n=>[n.key,{x:n.x,y:n.y}])),
            zoom:state.zoom,panX:state.panX,panY:state.panY,signature:layout.signature(serialize())};
    }

    function restoreLayout(snapshot) {
        state.nodes.forEach(node=> {
            if (snapshot.positions[node.key]) Object.assign(node,snapshot.positions[node.key]);
        });
        state.zoom=snapshot.zoom;state.panX=snapshot.panX;state.panY=snapshot.panY;
    }

    function updateLayoutControls() {
        const preview=Boolean(state.layoutPreview);
        document.getElementById("graph-layout-preview").hidden=!preview;
        nodesLayer.inert=preview;
        inspector.inert=preview;
        document.querySelectorAll("[data-add-node]").forEach(button=>{button.disabled=preview;});
        document.getElementById("graph-arrange").disabled=preview || Boolean(state.pendingConnection);
        document.getElementById("graph-layout-undo").disabled=preview || !state.layoutUndo
            || state.layoutUndo.signature!==layout.signature(serialize());
    }

    function arrangePreview() {
        if(state.layoutPreview || state.pendingConnection) return;
        const errors=validateGraph();
        if(errors.length) {layoutMessage.textContent=errors[0];return;}
        try {
            const before=captureLayout();
            const dimensions=Object.fromEntries([...nodesLayer.children].map(el=>[el.dataset.nodeKey,
                {width:el.offsetWidth,height:el.offsetHeight}]));
            const positions=layout.arrange(state.nodes,state.edges,dimensions,pinnedNodes);
            state.layoutPreview=before;
            state.nodes.forEach(node=>Object.assign(node,positions[node.key]));
            if(layout.signature(serialize())!==before.signature) throw Error("실행 구조가 달라져 정리를 취소했습니다.");
            layoutMessage.textContent="미리보기를 확인한 뒤 배치 적용 또는 취소를 선택하세요.";
            renderAll();
            requestAnimationFrame(fitGraph);
        } catch(error) {
            if(state.layoutPreview) restoreLayout(state.layoutPreview);
            state.layoutPreview=null;
            renderAll();
            layoutMessage.textContent=error.message;
        }
    }

    function applyLayoutPreview() {
        const previous=state.layoutPreview;
        if(!previous) return;
        if(previous.signature!==layout.signature(serialize())) {
            cancelLayoutPreview();
            layoutMessage.textContent="편집 내용이 달라져 정리를 취소했습니다.";
            return;
        }
        state.layoutUndo=previous;
        state.layoutPreview=null;
        markDirty("정리된 배치 적용됨 · 저장 전");
        layoutMessage.textContent="배치를 적용했습니다. 실행 순서는 유지됩니다. 저장 전 되돌릴 수 있습니다.";
        renderAll();
    }

    function cancelLayoutPreview() {
        if(!state.layoutPreview) return;
        restoreLayout(state.layoutPreview);
        state.layoutPreview=null;
        layoutMessage.textContent="미리보기를 취소하고 이전 배치로 돌아왔습니다.";
        renderAll();
    }

    function undoLayout() {
        if(!state.layoutUndo || state.layoutPreview || state.layoutUndo.signature!==layout.signature(serialize())) return;
        restoreLayout(state.layoutUndo);
        state.layoutUndo=null;
        markDirty("이전 배치로 복원됨 · 저장 전");
        layoutMessage.textContent="자동 정리 전 배치로 돌아왔습니다.";
        renderAll();
    }

    function moveEdge(edge, delta) {
        const group=orderedEdges().filter(e=>e.source===edge.source && e.outcome===edge.outcome);
        const index=group.findIndex(e=>e.id===edge.id),next=index+delta;
        if(index<0 || next<0 || next>=group.length) return;
        [group[index],group[next]]=[group[next],group[index]];
        group.forEach((e,i)=>{state.edges.find(original=>original.id===e.id).order=i+1;});
        markDirty("분기 실행 순서 변경됨");
        renderAll();
    }

    function applyRouteHighlight() {
        const selected=state.selectedNodeKey;
        const ancestors=new Set(selected?[selected]:[]),descendants=new Set(ancestors);
        for(const [set,reverse] of [[ancestors,true],[descendants,false]]) {
            const queue=[...set];
            while(queue.length) {
                const key=queue.shift();
                state.edges.forEach(edge=> {
                    if((reverse?edge.target:edge.source)!==key) return;
                    const next=reverse?edge.source:edge.target;
                    if(!set.has(next)) {set.add(next);queue.push(next);}
                });
            }
        }
        const active=new Set(state.edges.filter(e=>state.selectedEdgeId ? e.id===state.selectedEdgeId
            : (ancestors.has(e.source)&&ancestors.has(e.target)) || (descendants.has(e.source)&&descendants.has(e.target))).map(e=>e.id));
        edgesLayer.querySelectorAll("[data-edge-id]").forEach(el=> {
            el.classList.toggle("is-dimmed",Boolean(selected)&&!active.has(el.dataset.edgeId));
            el.classList.toggle("is-highlighted",Boolean(selected)&&active.has(el.dataset.edgeId));
        });
    }

    function requestEdgeRender() {
        if(edgeFrame) return;
        edgeFrame=requestAnimationFrame(()=>{edgeFrame=null;renderEdges();});
    }

    function renderEdges() {
        const rectangles=[],ports=new Map();
        [...nodesLayer.children].forEach(el=> {
            const node=nodeByKey(el.dataset.nodeKey);
            if(!node) return;
            rectangles.push({key:node.key,x:node.x,y:node.y,width:el.offsetWidth,height:el.offsetHeight});
            el.querySelectorAll("[data-port]").forEach(port=>ports.set(`${node.key}|${port.dataset.port}|${port.dataset.outcome||''}`,
                {x:node.x+port.offsetLeft+port.offsetWidth/2,y:node.y+port.offsetTop+port.offsetHeight/2}));
        });
        const edges=orderedEdges().map(edge=>({...edge,
            sourcePoint:ports.get(`${edge.source}|output|${edge.outcome}`),targetPoint:ports.get(`${edge.target}|input|`)}))
            .filter(e=>e.sourcePoint && e.targetPoint);
        const geometry=JSON.stringify({rectangles,edges});
        if(geometry===edgeGeometry) {applyRouteHighlight();return;}
        edgeGeometry=geometry;
        const routes=layout.routeEdges(rectangles,edges);
        edgesLayer.replaceChildren();
        const maxX=Math.max(1000,...rectangles.map(r=>r.x+r.width),...routes.flatMap(r=>r.points.map(p=>p.x)))+100;
        const maxY=Math.max(800,...rectangles.map(r=>r.y+r.height),...routes.flatMap(r=>r.points.map(p=>p.y)))+100;
        world.style.width=nodesLayer.style.width=`${maxX}px`;
        world.style.height=nodesLayer.style.height=`${maxY}px`;
        edgesLayer.setAttribute("width",maxX);edgesLayer.setAttribute("height",maxY);
        edgesLayer.style.width=`${maxX}px`;edgesLayer.style.height=`${maxY}px`;
        routes.forEach(({edge,d,fallback})=> {
            const path=document.createElementNS("http://www.w3.org/2000/svg","path");
            path.setAttribute("d",d);
            path.setAttribute("class",`iot-graph-edge iot-graph-edge--${edge.outcome}${fallback?' is-unrouted':''}`);
            path.dataset.edgeId=edge.id;
            const hit=path.cloneNode();
            hit.setAttribute("class","iot-graph-edge-hit");
            const title=document.createElementNS("http://www.w3.org/2000/svg","title");
            title.textContent=`${nodeTitle(nodeByKey(edge.source))} → ${nodeTitle(nodeByKey(edge.target))} · ${edge.outcome.toUpperCase()} #${edge.order}`
                +(fallback?" · 경로가 혼잡합니다. 자동 정리 또는 Node 이동을 사용하세요.":"");
            hit.appendChild(title);
            hit.addEventListener("pointerdown",event=>event.stopPropagation());
            hit.addEventListener("click",event=> {
                event.stopPropagation();
                if(state.layoutPreview) return;
                selectNode(edge.source);state.selectedEdgeId=edge.id;renderInspector();applyRouteHighlight();
            });
            edgesLayer.append(path,hit);
            const label=document.createElementNS("http://www.w3.org/2000/svg","text");
            label.setAttribute("x",edge.sourcePoint.x+14+(edge.order-1)*12);
            label.setAttribute("y",edge.sourcePoint.y-10-(edge.order-1)*14);
            label.setAttribute("class",`iot-graph-edge-label iot-graph-edge-label--${edge.outcome}`);
            label.dataset.edgeId=edge.id;
            label.textContent=`${edge.outcome==='always'?'':edge.outcome.toUpperCase()+' · '}${edge.order}`;
            edgesLayer.appendChild(label);
        });
        if(routes.some(r=>r.fallback)) layoutMessage.textContent="일부 선의 경로가 혼잡합니다. 자동 정리 또는 Node 이동으로 여백을 확보하세요.";
        applyRouteHighlight();
    }


    function bindNodeDrag(handle, element, node) {
        handle.addEventListener("pointerdown", event => {
            if (event.button !== 0 || state.layoutPreview) return;
            event.stopPropagation();
            selectNode(node.key);
            const startX = event.clientX;
            const startY = event.clientY;
            const originX = node.x;
            const originY = node.y;
            let moved = false;
            try { handle.setPointerCapture(event.pointerId); } catch (_error) { /* synthetic/ended pointer */ }
            const move = moveEvent => {
                node.x = Math.round(originX + (moveEvent.clientX - startX) / state.zoom);
                node.y = Math.round(originY + (moveEvent.clientY - startY) / state.zoom);
                element.style.left = `${node.x}px`;
                element.style.top = `${node.y}px`;
                moved = true;
                requestEdgeRender();
                state.dirty = true;
            };
            const up = () => {
                handle.removeEventListener("pointermove", move);
                handle.removeEventListener("pointerup", up);
                handle.removeEventListener("pointercancel", up);
                if (moved) markDirty("Node 위치 변경됨");
                renderInspector();
            };
            handle.addEventListener("pointermove", move);
            handle.addEventListener("pointerup", up);
            handle.addEventListener("pointercancel", up);
        });
    }

    function bindPorts(element, node) {
        element.querySelectorAll('[data-port="output"]').forEach(port => {
            port.addEventListener("click", event => {
                event.stopPropagation();
                const outcome = port.dataset.outcome;
                if (state.pendingConnection?.source === node.key && state.pendingConnection?.outcome === outcome) state.pendingConnection = null;
                else state.pendingConnection = {source: node.key, outcome};
                renderNodes();
                setStatus(state.pendingConnection ? "연결할 대상 Node의 입력 포트를 선택하세요." : "연결 취소됨", false);
            });
        });
        const input = element.querySelector('[data-port="input"]');
        if (input) input.addEventListener("click", event => {
            event.stopPropagation();
            if (!state.pendingConnection) {
                selectNode(node.key);
                setStatus("먼저 출발 Node의 출력 포트를 선택하세요.", false);
                return;
            }
            const added = addEdge(state.pendingConnection.source, node.key, state.pendingConnection.outcome);
            state.pendingConnection = null;
            if (added) renderAll();
            else renderNodes();
        });
    }

    function wouldCreateCycle(source, target) {
        const adjacency = new Map();
        state.nodes.forEach(node => adjacency.set(node.key, []));
        state.edges.forEach(edge => adjacency.get(edge.source)?.push(edge.target));
        adjacency.get(source)?.push(target);
        const visiting = new Set();
        const visited = new Set();
        const visit = key => {
            if (visiting.has(key)) return true;
            if (visited.has(key)) return false;
            visiting.add(key);
            for (const next of adjacency.get(key) || []) if (visit(next)) return true;
            visiting.delete(key);
            visited.add(key);
            return false;
        };
        return state.nodes.some(node => visit(node.key));
    }

    function addEdge(source, target, outcome) {
        const sourceNode = nodeByKey(source);
        const targetNode = nodeByKey(target);
        if (!sourceNode || !targetNode) return false;
        if (source === target) { setStatus("자기 자신에게 연결할 수 없습니다.", true); return false; }
        if (targetNode.type === "start") { setStatus("START Node에는 연결할 수 없습니다.", true); return false; }
        if (sourceNode.type === "end") { setStatus("END Node에서는 연결할 수 없습니다.", true); return false; }
        const allowed = sourceNode.type === "condition" ? ["true", "false"] : ["always"];
        if (!allowed.includes(outcome)) { setStatus("이 Node에서 사용할 수 없는 출력입니다.", true); return false; }
        if (state.edges.some(edge => edge.source === source && edge.target === target && edge.outcome === outcome)) { setStatus("이미 같은 연결이 있습니다.", true); return false; }
        if (wouldCreateCycle(source, target)) { setStatus("순환 경로는 만들 수 없습니다.", true); return false; }
        const order = 1 + Math.max(0, ...orderedEdges().filter(e => e.source === source && e.outcome === outcome).map(e => e.order));
        state.edges = orderedEdges();
        state.edges.push({id: `edge-${uuid()}`, source, target, outcome, order});
        markDirty("Edge 추가됨");
        return true;
    }

    function removeEdge(edge) {
        const index = state.edges.findIndex(item => item.source === edge.source && item.target === edge.target && item.outcome === edge.outcome);
        if (index >= 0) state.edges.splice(index, 1);
        markDirty("Edge 삭제됨");
        renderAll();
    }

    function selectNode(key) {
        state.selectedNodeKey = key;
        state.selectedEdgeId = null;
        // Preserve the live drag handle and its pointer capture when selecting.
        // Rebuilding nodes here detaches the element receiving pointer events.
        nodesLayer.querySelectorAll(".iot-graph-node").forEach(element => {
            element.classList.toggle("is-selected", element.dataset.nodeKey === key);
        });
        renderInspector();
        applyRouteHighlight();
    }

    function removeSelectedNode() {
        const node = nodeByKey(state.selectedNodeKey);
        if (!node || ["start", "end"].includes(node.type)) return;
        if (!window.confirm(`'${nodeTitle(node)}' Node를 삭제할까요? 연결된 Edge도 함께 삭제됩니다.`)) return;
        state.nodes = state.nodes.filter(item => item.key !== node.key);
        state.edges = state.edges.filter(edge => edge.source !== node.key && edge.target !== node.key);
        state.selectedNodeKey = null;
        markDirty("Node 삭제됨");
        renderAll();
    }

    function createInput(type, value = "") {
        const input = document.createElement("input");
        input.type = type;
        input.value = value ?? "";
        return input;
    }

    function createSelect(options, value) {
        const select = document.createElement("select");
        options.forEach(([key, label]) => {
            const option = document.createElement("option");
            option.value = String(key);
            option.textContent = label;
            if (String(value ?? "") === String(key)) option.selected = true;
            select.appendChild(option);
        });
        return select;
    }

    function addField(container, labelText, control) {
        const wrapper = document.createElement("div");
        wrapper.className = "iot-graph-inspector__field";
        if (labelText) {
            const label = document.createElement("label");
            label.textContent = labelText;
            wrapper.appendChild(label);
        }
        wrapper.appendChild(control);
        container.appendChild(wrapper);
        return wrapper;
    }

    function bind(control, callback, eventName = "input") {
        control.addEventListener(eventName, () => {
            callback(control);
            markDirty();
            renderNodes();
            renderInspector();
        });
    }

    function renderInspector() {
        const node = nodeByKey(state.selectedNodeKey);
        inspector.replaceChildren();
        if (!node) {
            inspector.innerHTML = '<div class="iot-graph-inspector__empty"><strong>Node를 선택하세요</strong><p>선택한 조건·동작·지연의 상세 설정이 여기에 표시됩니다.</p></div>';
            return;
        }

        const heading = document.createElement("div");
        heading.innerHTML = `<h2>${escapeHtml(TYPE_LABEL[node.type] || node.type)}</h2><p>${escapeHtml(nodeTitle(node))}</p>`;
        inspector.appendChild(heading);
        const pinLabel = document.createElement("label");
        pinLabel.className = "iot-graph-inspector__label iot-graph-pin-label";
        const pin = document.createElement("input");
        pin.type = "checkbox";
        pin.checked = pinnedNodes.has(node.key);
        pinLabel.append(pin, document.createTextNode(" 자동 정리 시 위치 고정"));
        pinLabel.title = "이 브라우저에서 기억합니다. 직접 드래그해서 이동할 수 있습니다.";
        pin.addEventListener("change", () => {
            if (pin.checked) pinnedNodes.add(node.key); else pinnedNodes.delete(node.key);
            try { localStorage.setItem(pinStorageKey, JSON.stringify([...pinnedNodes])); } catch (_error) { /* storage may be blocked */ }
            renderNodes();
            layoutMessage.textContent = "위치 고정 설정은 이 브라우저에서 기억합니다.";
        });
        inspector.appendChild(pinLabel);

        if (!["start", "end"].includes(node.type)) {
            const title = createInput("text", node.title || "");
            title.addEventListener("input", () => { node.title = title.value; state.dirty = true; renderNodes(); });
            title.addEventListener("change", () => { markDirty("Node 이름 변경됨"); renderInspector(); });
            addField(inspector, "Node 이름", title);

            const enabledWrap = document.createElement("label");
            enabledWrap.className = "iot-graph-inspector__label";
            const enabled = document.createElement("input");
            enabled.type = "checkbox";
            enabled.checked = node.enabled !== false;
            enabledWrap.append(enabled, document.createTextNode(" 이 Node 활성화"));
            enabled.addEventListener("change", () => { node.enabled = enabled.checked; markDirty(); renderNodes(); });
            inspector.appendChild(enabledWrap);
        }

        if (node.type === "condition") renderConditionInspector(node);
        else if (node.type === "action") renderActionInspector(node);
        else if (node.type === "delay") renderDelayInspector(node);
        else {
            const note = document.createElement("div");
            note.className = "iot-graph-inspector__section";
            note.innerHTML = node.type === "start"
                ? "<p>START에 직접 연결된 조건 Node는 자동화를 깨우는 시작 조건으로 동작합니다.</p>"
                : "<p>END에 도달하면 해당 실행 경로가 종료됩니다.</p>";
            inspector.appendChild(note);
        }

        renderOutgoingInspector(node);

        if (!["start", "end"].includes(node.type)) {
            const section = document.createElement("div");
            section.className = "iot-graph-inspector__section iot-graph-inspector__actions";
            const button = document.createElement("button");
            button.type = "button";
            button.className = "iot-button iot-button--danger";
            button.textContent = "Node 삭제";
            button.addEventListener("click", removeSelectedNode);
            section.appendChild(button);
            inspector.appendChild(section);
        }
    }

    function itemHeader(title, onRemove, onUp, onDown) {
        const head = document.createElement("div");
        head.className = "iot-graph-inspector__item-head";
        const strong = document.createElement("strong");
        strong.textContent = title;
        const buttons = document.createElement("div");
        [["↑", onUp], ["↓", onDown], ["삭제", onRemove]].forEach(([label, handler], index) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = `iot-graph-mini-button${index === 2 ? " iot-graph-mini-button--danger" : ""}`;
            button.textContent = label;
            button.addEventListener("click", handler);
            buttons.appendChild(button);
        });
        head.append(strong, buttons);
        return head;
    }

    function moveItem(items, index, delta) {
        const next = index + delta;
        if (next < 0 || next >= items.length) return;
        [items[index], items[next]] = [items[next], items[index]];
        markDirty("항목 순서 변경됨");
        renderAll();
    }

    function renderConditionInspector(node) {
        const section = document.createElement("div");
        section.className = "iot-graph-inspector__section";
        section.innerHTML = "<h3>조건</h3><p>한 Node에 여러 조건을 넣으면 모두 AND로 평가됩니다.</p>";
        node.conditions.forEach((item, index) => {
            if (index) {
                const and = document.createElement("div");
                and.className = "iot-graph-inspector__and";
                and.textContent = "AND";
                section.appendChild(and);
            }
            const card = document.createElement("div");
            card.className = "iot-graph-inspector__item";
            card.appendChild(itemHeader(
                `조건 ${index + 1}`,
                () => { node.conditions.splice(index, 1); markDirty("조건 삭제됨"); renderAll(); },
                () => moveItem(node.conditions, index, -1),
                () => moveItem(node.conditions, index, 1),
            ));
            const type = createSelect([
                ["device_state", "기기 상태 / 변화"], ["schedule", "날짜 / 시간"], ["mqtt_event", "MQTT 이벤트"], ["weather", "현재 날씨"],
            ], item.type);
            type.addEventListener("change", () => {
                item.type = type.value;
                item.config = defaultConditionConfig(item.type);
                markDirty("조건 종류 변경됨");
                renderAll();
            });
            addField(card, "조건 종류", type);
            renderConditionFields(card, item);
            section.appendChild(card);
        });
        const add = document.createElement("button");
        add.type = "button";
        add.className = "iot-button";
        add.textContent = "+ 조건 추가 (AND)";
        add.addEventListener("click", () => {
            node.conditions.push({type: "device_state", config: defaultConditionConfig("device_state")});
            markDirty("조건 추가됨");
            renderAll();
        });
        section.appendChild(add);
        inspector.appendChild(section);
    }

    function defaultConditionConfig(type) {
        if (type === "schedule") return {schedule_type: "weekly", weekdays: [0,1,2,3,4], time_mode: "at", time: "08:00"};
        if (type === "device_state") return {device_id: null, key: "", operator: "eq", value: true};
        if (type === "mqtt_event") return {topic: "", field: "value", operator: "received", value: null};
        if (type === "weather") return {metric: "temperature", operator: "gte", value: 25};
        return {};
    }

    function renderConditionFields(card, item) {
        const config = item.config || (item.config = {});
        if (item.type === "device_state") {
            const device = createSelect([["", "기기 선택"], ...devices.map(value => [value.id, `${value.location ? value.location + " · " : ""}${value.name}`])], config.device_id);
            device.addEventListener("change", () => { config.device_id = device.value ? Number(device.value) : null; markDirty(); renderNodes(); });
            addField(card, "기기", device);
            const key = createInput("text", config.key || "");
            key.placeholder = "예: power, contact, occupancy";
            key.addEventListener("input", () => { config.key = key.value; state.dirty = true; renderNodes(); });
            addField(card, "상태 키", key);
            const op = createSelect([
                ["eq", "값이 같음"], ["ne", "값이 다름"], ["gt", "보다 큼"], ["gte", "이상"], ["lt", "보다 작음"], ["lte", "이하"], ["changed", "값이 변경됨"], ["changed_to", "지정한 값으로 변경됨"],
            ], config.operator || "eq");
            op.addEventListener("change", () => { config.operator = op.value; markDirty(); renderNodes(); renderInspector(); });
            addField(card, "비교", op);
            if (op.value !== "changed") {
                const value = createInput("text", formatValue(config.value));
                value.placeholder = 'true / 25 / "cool" / 문자열';
                value.addEventListener("input", () => { config.value = parseSmartValue(value.value); state.dirty = true; renderNodes(); });
                addField(card, "값", value);
            }
            return;
        }
        if (item.type === "mqtt_event") {
            const topic = createInput("text", config.topic || "");
            topic.placeholder = "iotcore/devices/+/state";
            topic.addEventListener("input", () => { config.topic = topic.value; state.dirty = true; renderNodes(); });
            addField(card, "MQTT Topic", topic);
            const field = createInput("text", config.field || "value");
            field.addEventListener("input", () => { config.field = field.value || "value"; state.dirty = true; renderNodes(); });
            addField(card, "Payload 필드", field);
            const op = createSelect([
                ["received", "메시지를 수신함"], ["eq", "값이 같음"], ["ne", "값이 다름"], ["gt", "보다 큼"], ["gte", "이상"], ["lt", "보다 작음"], ["lte", "이하"], ["changed", "값이 변경됨"], ["changed_to", "지정한 값으로 변경됨"],
            ], config.operator || "received");
            op.addEventListener("change", () => { config.operator = op.value; markDirty(); renderNodes(); renderInspector(); });
            addField(card, "비교", op);
            if (!['received', 'changed'].includes(op.value)) {
                const value = createInput("text", formatValue(config.value));
                value.addEventListener("input", () => { config.value = parseSmartValue(value.value); state.dirty = true; renderNodes(); });
                addField(card, "값", value);
            }
            return;
        }
        if (item.type === "weather") {
            const metric = createSelect([["temperature","현재 기온"],["humidity","현재 습도"],["precipitation_probability","강수 확률"]], config.metric || "temperature");
            metric.addEventListener("change", () => { config.metric = metric.value; markDirty(); renderNodes(); });
            addField(card, "날씨 항목", metric);
            const op = createSelect([["eq","같음"],["ne","다름"],["gt","보다 큼"],["gte","이상"],["lt","보다 작음"],["lte","이하"]], config.operator || "gte");
            op.addEventListener("change", () => { config.operator = op.value; markDirty(); renderNodes(); });
            addField(card, "비교", op);
            const value = createInput("number", config.value ?? "");
            value.step = "any";
            value.addEventListener("input", () => { config.value = value.value === "" ? null : Number(value.value); state.dirty = true; renderNodes(); });
            addField(card, "값", value);
            return;
        }
        if (item.type === "schedule") renderScheduleFields(card, config);
    }

    function renderScheduleFields(card, config) {
        const type = createSelect([["once","한 번 실행"],["weekly","요일 선택 반복"],["interval","일정 간격"]], config.schedule_type || "weekly");
        type.addEventListener("change", () => {
            Object.assign(config, defaultConditionConfig("schedule"), {schedule_type: type.value});
            if (type.value === "once") { config.run_at = ""; delete config.weekdays; delete config.time; }
            if (type.value === "interval") { config.every = 5; config.unit = "minutes"; delete config.weekdays; delete config.time; }
            markDirty(); renderNodes(); renderInspector();
        });
        addField(card, "반복 방식", type);
        if (type.value === "once") {
            const runAt = createInput("datetime-local", String(config.run_at || "").slice(0,16));
            runAt.addEventListener("input", () => { config.run_at = runAt.value; state.dirty = true; renderNodes(); });
            addField(card, "실행 시각", runAt);
            return;
        }
        if (type.value === "interval") {
            const every = createInput("number", config.every || 1);
            every.min = "1";
            every.addEventListener("input", () => { config.every = Number(every.value || 0); state.dirty = true; renderNodes(); });
            addField(card, "간격", every);
            const unit = createSelect([["minutes","분"],["hours","시간"],["days","일"]], config.unit || "minutes");
            unit.addEventListener("change", () => { config.unit = unit.value; markDirty(); renderNodes(); });
            addField(card, "단위", unit);
            return;
        }
        const weekdaysWrap = document.createElement("div");
        weekdaysWrap.className = "iot-graph-weekdays";
        const selected = new Set((config.weekdays || []).map(Number));
        ["월","화","수","목","금","토","일"].forEach((label, index) => {
            const wrapper = document.createElement("label");
            const input = document.createElement("input");
            input.type = "checkbox";
            input.checked = selected.has(index);
            input.addEventListener("change", () => {
                if (input.checked) selected.add(index); else selected.delete(index);
                config.weekdays = [...selected].sort((a,b) => a-b);
                markDirty(); renderNodes();
            });
            wrapper.append(input, document.createTextNode(label));
            weekdaysWrap.appendChild(wrapper);
        });
        addField(card, "요일", weekdaysWrap);
        const mode = createSelect([["at","지정 시각"],["window","시간대"]], config.time_mode || "at");
        mode.addEventListener("change", () => { config.time_mode = mode.value; markDirty(); renderNodes(); renderInspector(); });
        addField(card, "시간 방식", mode);
        if (mode.value === "window") {
            const start = createInput("time", config.start || "08:00");
            start.addEventListener("input", () => { config.start = start.value; state.dirty = true; renderNodes(); });
            addField(card, "시작", start);
            const end = createInput("time", config.end || "");
            end.addEventListener("input", () => { config.end = end.value || null; state.dirty = true; renderNodes(); });
            addField(card, "종료 (비우면 없음)", end);
        } else {
            const time = createInput("time", config.time || "08:00");
            time.addEventListener("input", () => { config.time = time.value; state.dirty = true; renderNodes(); });
            addField(card, "실행 시각", time);
        }
    }

    function renderActionInspector(node) {
        const section = document.createElement("div");
        section.className = "iot-graph-inspector__section";
        section.innerHTML = "<h3>동작</h3><p>같은 Node 안의 동작은 표시된 순서대로 기존 기기별 Queue에 등록됩니다. Graph 흐름은 Queue 등록 직후 계속되므로, 다음 조건에서 실제 기기 상태를 확인해야 한다면 사이에 지연 Node를 두세요.</p>";
        node.actions.forEach((item, index) => {
            const card = document.createElement("div");
            card.className = "iot-graph-inspector__item";
            card.appendChild(itemHeader(
                `동작 ${index + 1}`,
                () => { node.actions.splice(index, 1); markDirty("동작 삭제됨"); renderAll(); },
                () => moveItem(node.actions, index, -1),
                () => moveItem(node.actions, index, 1),
            ));
            const actionType = createSelect([["device","개별 기기 동작"],["automation","다른 자동화 실행"]], item.action_type || "device");
            actionType.addEventListener("change", () => {
                item.action_type = actionType.value;
                item.device_id = null; item.function = ""; item.parameter = null; item.target_automation_id = null;
                markDirty(); renderAll();
            });
            addField(card, "실행 종류", actionType);
            if (actionType.value === "automation") {
                const target = createSelect([["","자동화 선택"], ...automations.map(value => [value.id, value.name])], item.target_automation_id);
                target.addEventListener("change", () => { item.target_automation_id = target.value ? Number(target.value) : null; markDirty(); renderNodes(); });
                addField(card, "실행할 자동화", target);
            } else {
                const controllable = devices.filter(value => value.controllable);
                const device = createSelect([["","기기 선택"], ...controllable.map(value => [value.id, `${value.location ? value.location + " · " : ""}${value.name}`])], item.device_id);
                device.addEventListener("change", () => {
                    item.device_id = device.value ? Number(device.value) : null;
                    item.function = "";
                    item.parameter = null;
                    markDirty(); renderNodes(); renderInspector();
                });
                addField(card, "기기", device);
                const selectedDevice = deviceById.get(String(item.device_id));
                const actions = actionRegistry[selectedDevice?.device_type] || [];
                const fn = createSelect([["","동작 선택"], ...actions.map(value => [value.code, value.name])], item.function);
                fn.addEventListener("change", () => { item.function = fn.value; item.parameter = null; markDirty(); renderNodes(); renderInspector(); });
                addField(card, "동작", fn);
                const definition = actions.find(value => value.code === item.function);
                if (definition?.parameter_key || item.parameter !== null) {
                    const parameter = document.createElement("textarea");
                    parameter.value = item.parameter === null ? "" : JSON.stringify(item.parameter, null, 2);
                    parameter.placeholder = definition?.parameter_key ? `{\n  "${definition.parameter_key}": 값\n}` : "{}";
                    parameter.addEventListener("input", () => {
                        const raw = parameter.value.trim();
                        if (!raw) { item.parameter = null; parameter.setCustomValidity(""); }
                        else {
                            try { item.parameter = JSON.parse(raw); parameter.setCustomValidity(""); }
                            catch (_error) { parameter.setCustomValidity("올바른 JSON을 입력하세요."); }
                        }
                        state.dirty = true;
                    });
                    addField(card, "파라미터(JSON)", parameter);
                }
            }

            const legacy = document.createElement("details");
            legacy.innerHTML = "<summary style=\"font-size:.7rem;cursor:pointer;opacity:.7\">Legacy 동작 지연</summary>";
            const delay = createInput("number", item.delay || 0);
            delay.min = "0";
            delay.addEventListener("input", () => { item.delay = Number(delay.value || 0); state.dirty = true; });
            addField(legacy, "지연(초)", delay);
            const delayPosition = createSelect([["before","동작 전"],["after","동작 후"]], item.delay_position || "after");
            delayPosition.addEventListener("change", () => { item.delay_position = delayPosition.value; markDirty(); });
            addField(legacy, "지연 위치", delayPosition);
            const note = document.createElement("p");
            note.textContent = "신규 구성에서는 독립 DELAY Node 사용을 권장합니다.";
            legacy.appendChild(note);
            card.appendChild(legacy);
            section.appendChild(card);
        });
        const add = document.createElement("button");
        add.type = "button";
        add.className = "iot-button";
        add.textContent = "+ 동작 추가";
        add.addEventListener("click", () => {
            node.actions.push({action_type:"device", device_id:null, function:"", parameter:null, target_automation_id:null, delay:0, delay_position:"after"});
            markDirty("동작 추가됨");
            renderAll();
        });
        section.appendChild(add);
        inspector.appendChild(section);
    }

    function renderDelayInspector(node) {
        const section = document.createElement("div");
        section.className = "iot-graph-inspector__section";
        section.innerHTML = "<h3>지연</h3><p>이 Node에 도달한 실행 경로를 지정 시간 동안 대기시킵니다.</p>";
        const seconds = createInput("number", node.delay_seconds || 0);
        seconds.min = "0";
        seconds.step = "1";
        seconds.addEventListener("input", () => { node.delay_seconds = Math.max(0, Number(seconds.value || 0)); state.dirty = true; renderNodes(); });
        addField(section, "대기 시간(초)", seconds);
        inspector.appendChild(section);
    }

    function renderOutgoingInspector(node) {
        const section = document.createElement("div");
        section.className = "iot-graph-inspector__section";
        section.innerHTML = "<h3>연결</h3><p>같은 출력의 실행 순서는 ↑·↓로 변경합니다. Node 위치와는 독립적입니다.</p>";
        const list = document.createElement("div");
        list.className = "iot-graph-edge-list";
        const edges = orderedEdges().filter(edge => edge.source === node.key);
        if (!edges.length) {
            const empty = document.createElement("p");
            empty.textContent = node.type === "end" ? "END에는 출력 연결이 없습니다." : "연결된 다음 Node가 없습니다.";
            list.appendChild(empty);
        }
        edges.forEach(edge => {
            const row = document.createElement("div");
            row.className = "iot-graph-edge-row";
            row.classList.toggle("is-selected", state.selectedEdgeId === edge.id);
            const outcome = document.createElement("span");
            outcome.className = "iot-graph-edge-row__outcome";
            outcome.textContent = edge.outcome === "always" ? `#${edge.order}` : `${edge.outcome.toUpperCase()} #${edge.order}`;
            const target = document.createElement("span");
            target.className = "iot-graph-edge-row__target";
            target.textContent = nodeTitle(nodeByKey(edge.target) || {type:"?", title:edge.target});
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "iot-graph-mini-button iot-graph-mini-button--danger";
            remove.textContent = "삭제";
            remove.addEventListener("click", () => removeEdge(edge));
            const controls = document.createElement("div");
            controls.className = "iot-graph-edge-controls";
            const group = edges.filter(e => e.outcome === edge.outcome);
            const index = group.findIndex(e => e.id === edge.id);
            [["↑", -1], ["↓", 1]].forEach(([label, delta]) => {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "iot-graph-mini-button";
                button.textContent = label;
                button.setAttribute("aria-label", `${target.textContent} 실행 순서 ${delta < 0 ? '앞으로' : '뒤로'}`);
                button.disabled = index + delta < 0 || index + delta >= group.length;
                button.addEventListener("click", () => moveEdge(edge, delta));
                controls.appendChild(button);
            });
            controls.appendChild(remove);
            row.append(outcome, target, controls);
            list.appendChild(row);
        });
        section.appendChild(list);
        inspector.appendChild(section);
    }

    function addNode(type) {
        const node = defaultNode(type);
        const centerX = (-state.panX + viewport.clientWidth * .5) / state.zoom;
        const centerY = (-state.panY + viewport.clientHeight * .5) / state.zoom;
        node.x = Math.round(centerX - (NODE_WIDTH[type] || 236) / 2 + state.nodes.length * 8);
        node.y = Math.round(centerY - 60 + state.nodes.length * 8);
        state.nodes.push(node);
        state.selectedNodeKey = node.key;
        markDirty(`${TYPE_LABEL[type]} Node 추가됨`);
        renderAll();
    }

    function serialize() {
        return {
            version: 1,
            revision: state.revision,
            nodes: state.nodes.map(node => ({
                key: node.key,
                type: node.type,
                title: node.title || "",
                x: Math.round(node.x),
                y: Math.round(node.y),
                enabled: node.enabled !== false,
                delay_seconds: Math.max(0, Math.round(Number(node.delay_seconds || 0))),
                conditions: node.type === "condition" ? node.conditions.map((item, index) => ({order:index + 1, type:item.type, config:{...(item.config || {})}})) : [],
                actions: node.type === "action" ? node.actions.map((item, index) => ({order:index + 1, ...item})) : [],
            })),
            edges: orderedEdges().map(edge => ({source:edge.source, target:edge.target, outcome:edge.outcome, order:edge.order})),
        };
    }

    function validateGraph() {
        const errors = [];
        const nodes = state.nodes;
        const starts = nodes.filter(node => node.type === "start");
        if (starts.length !== 1) errors.push("START Node는 정확히 하나여야 합니다.");
        if (!nodes.some(node => node.type === "end")) errors.push("END Node가 하나 이상 필요합니다.");
        if (!nodes.some(node => node.type === "action")) errors.push("동작 Node가 하나 이상 필요합니다.");
        nodes.forEach(node => {
            if (node.type === "condition") {
                if (!node.conditions.length) errors.push(`${nodeTitle(node)}: 조건이 하나 이상 필요합니다.`);
                if (node.conditions.filter(item => item.type === "schedule").length > 1) errors.push(`${nodeTitle(node)}: 날짜/시간 조건은 한 Node에 하나만 둘 수 있습니다.`);
            }
            if (node.type === "action" && !node.actions.length) errors.push(`${nodeTitle(node)}: 동작이 하나 이상 필요합니다.`);
            if (node.type === "delay" && Number(node.delay_seconds) < 0) errors.push(`${nodeTitle(node)}: 지연 시간은 0초 이상이어야 합니다.`);
        });
        const keys = new Set(nodes.map(node => node.key));
        const seen = new Set();
        state.edges.forEach(edge => {
            if (!keys.has(edge.source) || !keys.has(edge.target)) errors.push("존재하지 않는 Node를 연결한 Edge가 있습니다.");
            const duplicate = `${edge.source}|${edge.target}|${edge.outcome}`;
            if (seen.has(duplicate)) errors.push("동일한 Edge가 중복되었습니다.");
            seen.add(duplicate);
            const source = nodeByKey(edge.source);
            if (source?.type === "condition" && !["true","false"].includes(edge.outcome)) errors.push("조건 Node는 TRUE/FALSE Edge만 사용할 수 있습니다.");
            if (["start","action","delay"].includes(source?.type) && edge.outcome !== "always") errors.push("START/ACTION/DELAY는 ALWAYS Edge만 사용할 수 있습니다.");
            if (source?.type === "end") errors.push("END에서는 Edge가 나갈 수 없습니다.");
        });
        if (starts.length === 1) {
            const adjacency = new Map(nodes.map(node => [node.key, []]));
            const indegree = new Map(nodes.map(node => [node.key, 0]));
            state.edges.forEach(edge => {
                adjacency.get(edge.source)?.push(edge.target);
                indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1);
            });
            const queue = [...indegree].filter(([,value]) => value === 0).map(([key]) => key);
            let visited = 0;
            while (queue.length) {
                const key = queue.shift(); visited += 1;
                (adjacency.get(key) || []).forEach(target => { const next = indegree.get(target) - 1; indegree.set(target, next); if (next === 0) queue.push(target); });
            }
            if (visited !== nodes.length) errors.push("순환 경로는 허용되지 않습니다.");
            const reachable = new Set([starts[0].key]);
            const stack = [starts[0].key];
            while (stack.length) {
                const key = stack.pop();
                (adjacency.get(key) || []).forEach(target => { if (!reachable.has(target)) { reachable.add(target); stack.push(target); } });
            }
            if (reachable.size !== nodes.length) errors.push("START에서 도달할 수 없는 Node가 있습니다.");
        }
        return [...new Set(errors)];
    }

    function setStatus(message, isError = false, valid = false) {
        statusElement.textContent = message;
        statusElement.classList.toggle("is-error", isError);
        statusElement.classList.toggle("is-valid", valid);
    }

    function validateAndReport(showFirstError = true) {
        const errors = validateGraph();
        if (errors.length) {
            setStatus(errors[0], true, false);
            saveState.textContent = `검증 필요 · ${errors.length}건`;
            if (showFirstError) console.warn("Automation graph validation", errors);
            return errors;
        }
        setStatus(`Node ${state.nodes.length}개 · Edge ${state.edges.length}개 · 저장 가능`, false, true);
        saveState.textContent = state.dirty ? "변경 사항 저장 가능" : "그래프 정상";
        return [];
    }

    function renderAll() {
        applyTransform();
        renderNodes();
        renderInspector();
        validateAndReport(false);
        updateLayoutControls();
    }

    function fitGraph() {
        if (!state.nodes.length) return;
        const minX = Math.min(...state.nodes.map(node => node.x));
        const minY = Math.min(...state.nodes.map(node => node.y));
        const maxX = Math.max(...state.nodes.map(node => node.x + (NODE_WIDTH[node.type] || 236)));
        const maxY = Math.max(...state.nodes.map(node => node.y + (nodesLayer.querySelector(`[data-node-key="${CSS.escape(node.key)}"]`)?.offsetHeight || 160)));
        const width = Math.max(1, maxX - minX + 120);
        const height = Math.max(1, maxY - minY + 120);
        const availableWidth = Math.max(200, viewport.clientWidth);
        const availableHeight = Math.max(200, viewport.clientHeight);
        state.zoom = clamp(Math.min(availableWidth / width, availableHeight / height), .12, 1.35);
        state.panX = (availableWidth - (maxX - minX) * state.zoom) / 2 - minX * state.zoom;
        state.panY = (availableHeight - (maxY - minY) * state.zoom) / 2 - minY * state.zoom;
        applyTransform();
    }

    document.querySelectorAll("[data-add-node]").forEach(button => button.addEventListener("click", () => addNode(button.dataset.addNode)));
    document.getElementById("graph-fit").addEventListener("click", fitGraph);
    document.getElementById("graph-readable").addEventListener("click", () => {
        const node = nodeByKey(state.selectedNodeKey) || state.nodes.find(n => n.type === "start");
        if (!node) return;
        state.zoom = .8;
        state.panX = viewport.clientWidth / 2 - (node.x + (NODE_WIDTH[node.type] || 236) / 2) * state.zoom;
        state.panY = viewport.clientHeight / 2 - (node.y + 64) * state.zoom;
        applyTransform();
    });
    document.getElementById("graph-arrange").addEventListener("click", arrangePreview);
    document.getElementById("graph-layout-apply").addEventListener("click", applyLayoutPreview);
    document.getElementById("graph-layout-cancel").addEventListener("click", cancelLayoutPreview);
    document.getElementById("graph-layout-undo").addEventListener("click", undoLayout);
    document.getElementById("graph-zoom-in").addEventListener("click", () => { state.zoom = clamp(state.zoom + .1, .12, 1.8); applyTransform(); });
    document.getElementById("graph-zoom-out").addEventListener("click", () => { state.zoom = clamp(state.zoom - .1, .12, 1.8); applyTransform(); });

    viewport.addEventListener("wheel", event => {
        event.preventDefault();
        const rect = viewport.getBoundingClientRect();
        const mouseX = event.clientX - rect.left;
        const mouseY = event.clientY - rect.top;
        const worldX = (mouseX - state.panX) / state.zoom;
        const worldY = (mouseY - state.panY) / state.zoom;
        const nextZoom = clamp(state.zoom * (event.deltaY < 0 ? 1.1 : .9), .12, 1.8);
        state.panX = mouseX - worldX * nextZoom;
        state.panY = mouseY - worldY * nextZoom;
        state.zoom = nextZoom;
        applyTransform();
    }, {passive:false});

    viewport.addEventListener("pointerdown", event => {
        if (event.button !== 0 || event.target.closest(".iot-graph-node, .iot-graph-edge-hit")) return;
        const startX = event.clientX;
        const startY = event.clientY;
        const originX = state.panX;
        const originY = state.panY;
        viewport.classList.add("is-panning");
        try { viewport.setPointerCapture(event.pointerId); } catch (_error) { /* synthetic/ended pointer */ }
        const move = moveEvent => {
            state.panX = originX + moveEvent.clientX - startX;
            state.panY = originY + moveEvent.clientY - startY;
            applyTransform();
        };
        const up = () => {
            viewport.classList.remove("is-panning");
            viewport.removeEventListener("pointermove", move);
            viewport.removeEventListener("pointerup", up);
        };
        viewport.addEventListener("pointermove", move);
        viewport.addEventListener("pointerup", up);
    });

    document.addEventListener("keydown", event => {
        if (state.layoutPreview) {
            if (event.key === "Escape") { event.preventDefault(); cancelLayoutPreview(); }
            return;
        }
        if (event.key === "Escape" && !state.pendingConnection) selectNode(null);
        if ((event.key === "Delete" || event.key === "Backspace") && state.selectedNodeKey && !event.target.matches("input, textarea, select")) {
            event.preventDefault();
            removeSelectedNode();
        }
        if (event.key === "Escape" && state.pendingConnection) {
            state.pendingConnection = null;
            renderNodes();
            setStatus("연결 취소됨", false);
        }
    });

    form.addEventListener("submit", event => {
        if (state.layoutPreview) {
            event.preventDefault();
            layoutMessage.textContent = "미리보기에서 배치 적용 또는 취소를 먼저 선택하세요.";
            return;
        }
        const errors = validateGraph();
        if (errors.length) {
            event.preventDefault();
            window.alert(`그래프를 저장할 수 없습니다.\n\n${errors.join("\n")}`);
            setStatus(errors[0], true, false);
            return;
        }
        hiddenInput.value = JSON.stringify(serialize());
        saveState.textContent = "저장 중…";
    });

    window.addEventListener("beforeunload", event => {
        if (!state.dirty && !state.layoutPreview) return;
        event.preventDefault();
        event.returnValue = "";
    });
    form.addEventListener("submit", event => {
        if (!event.defaultPrevented) state.dirty = false;
    });

    form.addEventListener("input", event => {
        if (event.target.closest(".iot-graph-settings")) state.dirty = true;
    });
    renderAll();
    requestAnimationFrame(fitGraph);
})();
