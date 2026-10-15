const {test}=require('node:test');
const assert=require('node:assert/strict');
const layout=require('../STATIC/js/iotcore-graph-layout.js');
// Synthetic fixture with branch/merge topology matching a larger editor graph.
const fixture={"nodes":[{"key":"n0","type":"start","x":40,"y":20,"conditions":[],"actions":[]},{"key":"n1","type":"condition","x":40,"y":160,"conditions":[{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n2","type":"condition","x":40,"y":330,"conditions":[{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n3","type":"condition","x":40,"y":520,"conditions":[{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n4","type":"condition","x":40,"y":710,"conditions":[{"type":"sample","config":{"value":1}},{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n5","type":"condition","x":40,"y":900,"conditions":[{"type":"sample","config":{"value":1}},{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n6","type":"condition","x":390,"y":330,"conditions":[{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n7","type":"condition","x":740,"y":330,"conditions":[{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n8","type":"condition","x":740,"y":520,"conditions":[{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n9","type":"condition","x":740,"y":710,"conditions":[{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n10","type":"condition","x":1090,"y":330,"conditions":[{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n11","type":"condition","x":1440,"y":330,"conditions":[{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n12","type":"action","x":2140,"y":160,"conditions":[],"actions":[{"function":"sample"}]},{"key":"n13","type":"action","x":2140,"y":710,"conditions":[],"actions":[{"function":"sample"}]},{"key":"n14","type":"condition","x":1790,"y":520,"conditions":[{"type":"sample","config":{"value":1}},{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n15","type":"action","x":2140,"y":520,"conditions":[],"actions":[{"function":"sample"},{"function":"sample"}]},{"key":"n16","type":"condition","x":390,"y":1120,"conditions":[{"type":"sample","config":{"value":1}},{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n17","type":"action","x":740,"y":1120,"conditions":[],"actions":[{"function":"sample"}]},{"key":"n18","type":"delay","x":1090,"y":1120,"conditions":[],"actions":[]},{"key":"n19","type":"condition","x":1440,"y":1120,"conditions":[{"type":"sample","config":{"value":1}},{"type":"sample","config":{"value":1}}],"actions":[]},{"key":"n20","type":"action","x":1790,"y":1120,"conditions":[],"actions":[{"function":"sample"}]},{"key":"n21","type":"end","x":2490,"y":710,"conditions":[],"actions":[]}],"edges":[{"source":"n0","target":"n1","outcome":"always","order":1},{"source":"n1","target":"n2","outcome":"true","order":1},{"source":"n2","target":"n3","outcome":"false","order":1},{"source":"n2","target":"n6","outcome":"true","order":1},{"source":"n2","target":"n16","outcome":"true","order":2},{"source":"n3","target":"n4","outcome":"false","order":1},{"source":"n3","target":"n7","outcome":"true","order":1},{"source":"n4","target":"n5","outcome":"false","order":1},{"source":"n4","target":"n13","outcome":"true","order":1},{"source":"n5","target":"n21","outcome":"false","order":1},{"source":"n5","target":"n13","outcome":"true","order":1},{"source":"n6","target":"n13","outcome":"false","order":1},{"source":"n6","target":"n7","outcome":"true","order":1},{"source":"n7","target":"n8","outcome":"false","order":1},{"source":"n7","target":"n10","outcome":"true","order":1},{"source":"n8","target":"n9","outcome":"false","order":1},{"source":"n8","target":"n10","outcome":"true","order":1},{"source":"n9","target":"n13","outcome":"false","order":1},{"source":"n9","target":"n10","outcome":"true","order":1},{"source":"n10","target":"n12","outcome":"false","order":1},{"source":"n10","target":"n11","outcome":"true","order":1},{"source":"n11","target":"n14","outcome":"false","order":1},{"source":"n11","target":"n13","outcome":"false","order":2},{"source":"n11","target":"n12","outcome":"true","order":1},{"source":"n11","target":"n16","outcome":"true","order":2},{"source":"n12","target":"n21","outcome":"always","order":1},{"source":"n13","target":"n21","outcome":"always","order":1},{"source":"n14","target":"n15","outcome":"false","order":1},{"source":"n14","target":"n21","outcome":"true","order":1},{"source":"n15","target":"n21","outcome":"always","order":1},{"source":"n16","target":"n21","outcome":"false","order":1},{"source":"n16","target":"n17","outcome":"true","order":1},{"source":"n17","target":"n18","outcome":"always","order":1},{"source":"n18","target":"n19","outcome":"always","order":1},{"source":"n19","target":"n21","outcome":"false","order":1},{"source":"n19","target":"n20","outcome":"true","order":1},{"source":"n20","target":"n21","outcome":"always","order":1}]};
const dims=Object.fromEntries(fixture.nodes.map(n=>[n.key,{width:['start','end'].includes(n.type)?160:236,height:128+n.conditions.length*12+n.actions.length*12}]));
test('temperature graph layout preserves all semantics and stable execution order',()=>{
    const original=structuredClone(fixture);
    const positions=layout.arrange(fixture.nodes,fixture.edges,dims);
    const arranged={...fixture,nodes:fixture.nodes.map(n=>({...n,...positions[n.key]}))};
    assert.equal(layout.signature(arranged),layout.signature(fixture));
    assert.deepEqual(fixture,original);
    assert.deepEqual(layout.arrange(arranged.nodes,arranged.edges,dims),positions);
    const boxes=arranged.nodes.map(n=>({...positions[n.key],...dims[n.key]}));
    boxes.forEach((a,i)=>boxes.slice(i+1).forEach(b=>assert.equal(layout.overlaps(a,b,24),false)));
});
test('pinned nodes keep exact coordinates and movable nodes avoid them',()=>{
    const pinned=fixture.nodes[3];
    const positions=layout.arrange(fixture.nodes,fixture.edges,dims,new Set([pinned.key]));
    assert.deepEqual(positions[pinned.key],{x:pinned.x,y:pinned.y});
    const pinBox={...positions[pinned.key],...dims[pinned.key]};
    fixture.nodes.filter(n=>n.key!==pinned.key).forEach(n=>assert.equal(layout.overlaps(pinBox,{...positions[n.key],...dims[n.key]},24),false));
});
function geometry(nodes,edges){
    const byKey=new Map(nodes.map(n=>[n.key,n]));
    return edges.map(e=>{
        const source=byKey.get(e.source),target=byKey.get(e.target);
        return {...e,sourcePoint:{x:source.x+source.width,y:source.y+source.height*(e.outcome==='true'?.42:e.outcome==='false'?.76:.5)},
            targetPoint:{x:target.x,y:target.y+target.height/2}};
    });
}
test('orthogonal routes avoid unrelated nodes on the real temperature fixture',()=>{
    const positions=layout.arrange(fixture.nodes,fixture.edges,dims);
    const boxes=fixture.nodes.map(n=>({key:n.key,...positions[n.key],...dims[n.key]}));
    const start=performance.now();
    const routes=layout.routeEdges(boxes,geometry(boxes,fixture.edges));
    assert.equal(routes.length,fixture.edges.length);
    routes.forEach(({edge,points,fallback})=>{
        assert.equal(fallback,false);
        assert.deepEqual(points[0],geometry(boxes,[edge])[0].sourcePoint);
        points.slice(1).forEach((p,i)=>{
            assert.ok(p.x===points[i].x || p.y===points[i].y);
            boxes.filter(b=>b.key!==edge.source && b.key!==edge.target).forEach(b=>assert.equal(layout.segmentHits(points[i],p,b),false));
        });
    });
    console.log(`temperature routing: ${(performance.now()-start).toFixed(1)} ms, ${routes.length} edges`);
});
test('manual obstacles, backward edges and overlaps have bounded routing',()=>{
    const boxes=[{key:'a',x:600,y:0,width:236,height:140},{key:'b',x:0,y:300,width:236,height:140},{key:'obstacle',x:270,y:100,width:236,height:140}];
    const [r]=layout.routeEdges(boxes,geometry(boxes,[{source:'a',target:'b',outcome:'always',order:1}]));
    assert.equal(r.fallback,false);
    r.points.slice(1).forEach((p,i)=>assert.equal(layout.segmentHits(r.points[i],p,boxes[2]),false));
    assert.throws(()=>layout.arrange([{key:'a'},{key:'b'}],[{source:'a',target:'b',outcome:'always',order:1},{source:'b',target:'a',outcome:'always',order:1}]),/순환/);
});
test('large DAG geometry stays bounded and separates tall nodes',()=>{
    const nodes=Array.from({length:100},(_,i)=>({key:`n${String(i).padStart(3,'0')}`,type:'action',x:0,y:0}));
    const edges=nodes.slice(1).map((n,i)=>({source:nodes[Math.floor(i/3)].key,target:n.key,outcome:'always',order:i%3+1}));
    const dimensions=Object.fromEntries(nodes.map(n=>[n.key,{width:236,height:300}]));
    const start=performance.now();
    const positions=layout.arrange(nodes,edges,dimensions);
    const boxes=nodes.map(n=>({key:n.key,...positions[n.key],...dimensions[n.key]}));
    const routes=layout.routeEdges(boxes,geometry(boxes,edges));
    assert.equal(routes.length,99);
    assert.ok(performance.now()-start<3000,'100-node geometry must finish within a generous 3-second CI budget');
    console.log(`100-node layout+routing: ${(performance.now()-start).toFixed(1)} ms`);
});
