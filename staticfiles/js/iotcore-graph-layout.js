/* Pure editor geometry. Never evaluates a condition or changes graph edges. */
(function (root, factory) {
    "use strict";
    const api = factory();
    if (typeof module === "object" && module.exports) module.exports = api;
    else root.IoTGraphLayout = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    "use strict";
    const compare = (a, b) => String(a).localeCompare(String(b), "en");
    const groupKey = edge => `${edge.source}|${edge.outcome}`;

    function orderedEdges(edges) {
        const groups = new Map();
        edges.forEach((edge, index) => {
            const key = groupKey(edge);
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push({edge, index});
        });
        const result = [];
        groups.forEach(group => {
            group.sort((a, b) => a.edge.order - b.edge.order || a.index - b.index);
            group.forEach(({edge}, index) => result.push({...edge, order: index + 1}));
        });
        return result;
    }

    function signature(graph) {
        const nodes = graph.nodes.map(({x, y, ...node}) => node).sort((a, b) => compare(a.key, b.key));
        const edges = orderedEdges(graph.edges).map(({id, ...edge}) => edge)
            .sort((a, b) => compare(groupKey(a), groupKey(b)) || a.order - b.order || compare(a.target, b.target));
        return JSON.stringify({nodes, edges});
    }

    const overlaps = (a, b, gap = 0) => a.x < b.x + b.width + gap && a.x + a.width + gap > b.x
        && a.y < b.y + b.height + gap && a.y + a.height + gap > b.y;

    function arrange(nodes, edges, dimensions = {}, pinned = new Set()) {
        if (nodes.length > 400 || edges.length > 1600) throw Error("자동 정리는 Node 400개, 연결 1600개까지 지원합니다.");
        const byKey = new Map(nodes.map(n => [n.key, n]));
        const incoming = new Map(nodes.map(n => [n.key, []]));
        const outgoing = new Map(nodes.map(n => [n.key, []]));
        orderedEdges(edges).forEach(edge => {
            if (!byKey.has(edge.source) || !byKey.has(edge.target)) throw Error("존재하지 않는 Node 연결을 먼저 수정하세요.");
            incoming.get(edge.target).push(edge.source);
            outgoing.get(edge.source).push(edge);
        });
        outgoing.forEach(list => list.sort((a,b) => compare(a.outcome, b.outcome) || a.order - b.order || compare(a.target,b.target)));
        const rank = new Map(nodes.map(n => [n.key, 0]));
        const degree = new Map(nodes.map(n => [n.key, incoming.get(n.key).length]));
        const queue = nodes.filter(n => !degree.get(n.key)).map(n => n.key).sort(compare);
        const topo = [];
        while (queue.length) {
            const key = queue.shift();
            topo.push(key);
            outgoing.get(key).forEach(edge => {
                rank.set(edge.target, Math.max(rank.get(edge.target), rank.get(key) + 1));
                degree.set(edge.target, degree.get(edge.target) - 1);
                if (!degree.get(edge.target)) queue.push(edge.target);
            });
            queue.sort(compare);
        }
        if (topo.length !== nodes.length) throw Error("순환 연결이 있어 자동 정리를 할 수 없습니다.");
        const layers = [];
        topo.forEach(key => (layers[rank.get(key)] ||= []).push(key));
        const order = new Map();
        const updateOrder = () => layers.forEach(layer => layer.forEach((key, i) => order.set(key, i)));
        updateOrder();
        // Stable barycentre sweeps reduce crossings without touching execution order.
        for (let pass = 0; pass < 4; pass++) {
            const forward = pass % 2 === 0;
            const indices = layers.map((_, i) => i);
            if (!forward) indices.reverse();
            for (const i of indices) {
                const score = key => {
                    const neighbours = forward ? incoming.get(key) : outgoing.get(key).map(e => e.target);
                    return neighbours.length ? neighbours.reduce((sum,k) => sum + order.get(k),0) / neighbours.length : order.get(key);
                };
                layers[i].sort((a,b) => score(a) - score(b) || compare(a,b));
                updateOrder();
            }
        }
        const size = n => ({width: dimensions[n.key]?.width || (n.type === "start" || n.type === "end" ? 160 : 236),
            height: dimensions[n.key]?.height || 128});
        const positions = {};
        const occupied = [];
        for (const n of nodes) if (pinned.has(n.key)) {
            positions[n.key] = {x:n.x,y:n.y};
            occupied.push({...positions[n.key],...size(n)});
        }
        let x = 40;
        layers.forEach(layer => {
            let y = 40;
            for (const key of layer) {
                const n = byKey.get(key), box = size(n);
                if (pinned.has(key)) continue;
                let candidate = {x,y,...box};
                let collision;
                while ((collision = occupied.find(other => overlaps(candidate,other,36)))) {
                    candidate.y = collision.y + collision.height + 56;
                }
                positions[key] = {x:candidate.x,y:candidate.y};
                occupied.push(candidate);
                y = candidate.y + box.height + 72;
            }
            x += Math.max(...layer.map(key => size(byKey.get(key)).width)) + 124;
        });
        return positions;
    }

    function segmentHits(a, b, rect) {
        if (a.x === b.x) return a.x > rect.x && a.x < rect.x + rect.width
            && Math.max(a.y,b.y) > rect.y && Math.min(a.y,b.y) < rect.y + rect.height;
        if (a.y === b.y) return a.y > rect.y && a.y < rect.y + rect.height
            && Math.max(a.x,b.x) > rect.x && Math.min(a.x,b.x) < rect.x + rect.width;
        return true;
    }

    function simplify(points) {
        const result = [];
        points.forEach(p => {
            if (result.length && result.at(-1).x === p.x && result.at(-1).y === p.y) return;
            while (result.length > 1) {
                const a=result.at(-2), b=result.at(-1);
                if ((a.x===b.x && b.x===p.x) || (a.y===b.y && b.y===p.y)) result.pop();
                else break;
            }
            result.push(p);
        });
        return result;
    }

    const length = points => points.slice(1).reduce((sum,p,i) => sum + Math.abs(p.x-points[i].x)+Math.abs(p.y-points[i].y),0);
    const clear = (points, boxes) => points.slice(1).every((p,i) => !boxes.some(box => segmentHits(points[i],p,box)));

    function detour(start, end, boxes) {
        // Bounded visibility-grid fallback for manually placed/pinned obstacles.
        if (boxes.length > 100) return null;
        const xs=[...new Set([start.x,end.x,...boxes.flatMap(b=>[b.x-4,b.x+b.width+4])])].sort((a,b)=>a-b);
        const ys=[...new Set([start.y,end.y,...boxes.flatMap(b=>[b.y-4,b.y+b.height+4])])].sort((a,b)=>a-b);
        const key=(x,y)=>`${x},${y}`, initial=key(xs.indexOf(start.x),ys.indexOf(start.y));
        const open=[{ix:xs.indexOf(start.x),iy:ys.indexOf(start.y),g:0,f:0,key:initial}];
        const costs=new Map([[initial,0]]), previous=new Map();
        const point = value => ({x:xs[value.ix],y:ys[value.iy]});
        for(let budget=0;open.length && budget<2400;budget++) {
            let best=0;
            for(let i=1;i<open.length;i++) if(open[i].f<open[best].f) best=i;
            const current=open.splice(best,1)[0], a=point(current);
            if(current.g!==costs.get(current.key)) continue;
            if(a.x===end.x && a.y===end.y) {
                const points=[a]; let k=current.key;
                while(previous.has(k)) {const item=previous.get(k);points.push(item.point);k=item.key;}
                return simplify(points.reverse());
            }
            for(const [dx,dy] of [[1,0],[0,1],[-1,0],[0,-1]]) {
                const ix=current.ix+dx,iy=current.iy+dy;
                if(ix<0 || ix>=xs.length || iy<0 || iy>=ys.length) continue;
                const b={x:xs[ix],y:ys[iy]};
                if(boxes.some(box=>segmentHits(a,b,box))) continue;
                const id=key(ix,iy),g=current.g+Math.abs(a.x-b.x)+Math.abs(a.y-b.y);
                if(g >= (costs.get(id)??Infinity)) continue;
                costs.set(id,g);previous.set(id,{key:current.key,point:a});
                open.push({ix,iy,g,key:id,f:g+Math.abs(b.x-end.x)+Math.abs(b.y-end.y)});
            }
        }
        return null;
    }

    function routeEdges(rectangles, edges) {
        const boxes = rectangles.map(r => ({x:r.x-12,y:r.y-12,width:r.width+24,height:r.height+24}));
        const horizontal = [...new Set(boxes.flatMap(b=>[b.y-10,b.y+b.height+10]))];
        const vertical = [...new Set(boxes.flatMap(b=>[b.x-10,b.x+b.width+10]))];
        const used = new Map();
        return orderedEdges(edges).map(edge => {
            const source=edge.sourcePoint,target=edge.targetPoint;
            const start={x:source.x+28+((edge.order-1)%8)*8,y:source.y};
            const end={x:target.x-28,y:target.y};
            const candidates=[];
            const xs=[(start.x+end.x)/2,start.x,end.x,...vertical];
            const ys=[(start.y+end.y)/2,start.y,end.y,...horizontal];
            const add = points => {
                points=simplify(points);
                if(!clear(points,boxes)) return;
                let congestion=0;
                points.slice(1).forEach((p,i)=> {
                    const a=points[i],id=a.x===p.x?`x:${a.x}`:`y:${a.y}`;
                    congestion+=(used.get(id)||0)*18;
                });
                candidates.push({points,score:length(points)+(points.length-2)*24+congestion});
            };
            xs.forEach(x=>add([start,{x,y:start.y},{x,y:end.y},end]));
            ys.forEach(y=>add([start,{x:start.x,y},{x:end.x,y},end]));
            candidates.sort((a,b)=>a.score-b.score);
            let points=candidates[0]?.points || detour(start,end,boxes);
            const fallback=!points;
            if(!points) {
                const y=Math.min(source.y,target.y,...boxes.map(b=>b.y))-32;
                points=[start,{x:start.x,y},{x:end.x,y},end];
            }
            points=simplify([source,...points,target]);
            points.slice(1).forEach((p,i)=> {
                const a=points[i],id=a.x===p.x?`x:${a.x}`:`y:${a.y}`;
                used.set(id,(used.get(id)||0)+1);
            });
            return {edge,points,fallback,d:points.map((p,i)=>`${i?'L':'M'} ${p.x} ${p.y}`).join(' ')};
        });
    }
    return {orderedEdges,signature,arrange,routeEdges,segmentHits,overlaps};
});
