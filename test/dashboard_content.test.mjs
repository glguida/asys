import assert from 'node:assert/strict';
import test from 'node:test';
import {contentParts,outputRecords} from '../python/asys/dashboard_assets/content.mjs';
import {senateParticipants,senateSeats} from '../python/asys/dashboard_assets/senate.mjs';
import {workflowJob,workflowKind,workflowLabel,workflowLayout,workColor} from '../python/asys/dashboard_assets/dashboard.mjs';

test('embedded JSON shares structured rendering without swallowing surrounding prose or code',()=>{
  const value={assignment:'Use {braces} and an escaped "quote"',items:[false,0,null]};
  const source='Read this first.\nAssignment data:\n'+JSON.stringify(value,null,2)+'\nThen review.\n```json\n[1,2]\n```\nDone.';
  const parts=contentParts(source);
  assert.deepEqual(parts.filter(part=>part.kind==='data').map(part=>part.value),[value,[1,2]]);
  assert.match(parts.filter(part=>part.kind==='text').map(part=>part.value).join(''),/Read this first\.[\s\S]*Then review\.[\s\S]*Done\./);
  for(const text of ['```python\n{"code":true}\n```','```json\n{"unfinished":\n```','{not json}\nOrdinary text','[an ordinary Markdown link](https://example.com)'])
    assert.deepEqual(contentParts(text),[{kind:'text',value:text}]);
});

test('output starts with final and phase results while preserving optional raw logs',()=>{
  const payload={kind:'senate',data:{result:{approved:true,final:'Evidence accepted'},senate:{sessions:[{participant:'Numerical analyst',phase:'intervene',round:1,result:{final:'Measured 16'}}]},logs:{stdout:'{"type":"agent.message_delta","delta":"part"}'}}};
  const records=outputRecords(payload);
  assert.deepEqual(records.map(record=>record.id),['job-result','session-0','job-stdout']);
  assert.equal(records[0].data,payload.data.result);
  assert.match(records[1].title,/Numerical analyst.*intervene/);
  assert.equal(records[2].text,payload.data.logs.stdout);
});

test('Senate tiers distribute participants at small and large sizes and preserve configured names',()=>{
  for(const count of [0,1,3,8,15,40,100]) {
    const layout=senateSeats(count);
    assert.ok(layout.radii.length>=3);
    assert.equal(layout.seats.length,count);
    assert.equal(new Set(layout.seats.map(seat=>`${seat.x}/${seat.y}`)).size,count);
    for(const seat of layout.seats) {
      assert.ok(Math.abs(Math.hypot(seat.x-layout.center.x,seat.y-layout.center.y)-layout.radii[seat.row])<1e-8);
      assert.ok(seat.x>0&&seat.x<layout.width&&seat.y>0&&seat.y<layout.center.y);
    }
    if(count>=3)assert.ok(new Set(layout.seats.map(seat=>seat.row)).size>=3);
  }
  const people=senateParticipants({sessions:[{participant:'Numerical analyst',phase:'intervene',status:'done'}]},
    {princeps:{name:'Marcus'},senators:[{name:'Numerical analyst',prompt:'Check arithmetic'}]});
  assert.equal(people[0].name,'Marcus');assert.equal(people[0].role,'Princeps senatus');
  assert.equal(people[1].name,'Numerical analyst');assert.equal(people[1].sessions.length,1);
});

test('workflow loops retain separate ending branches and task colors use recorded bindings',()=>{
  const ids=['start','work','review','human','decision','revise','handover','accepted','stopped'];
  const nodes=ids.map(id=>({id,type:id==='decision'?'exclusiveGateway':['accepted','stopped'].includes(id)?'endEvent':'serviceTask'}));
  const edges=[['start','work'],['work','review'],['review','human'],['human','decision'],['decision','revise'],['revise','review'],['decision','handover'],['handover','accepted'],['decision','stopped']].map(([source,target])=>({source,target}));
  const layout=workflowLayout(nodes,edges),positions=Object.fromEntries(layout.nodes.map(node=>[node.id,node]));
  assert.equal(layout.edges.length,edges.length);
  assert.ok(positions.work.x<positions.review.x&&positions.review.x<positions.decision.x);
  assert.notEqual(positions.accepted.y,positions.stopped.y);
  assert.notEqual(positions.revise.y,positions.handover.y);
  assert.ok(layout.edges.every(edge=>edge.points.length>=2&&edge.points.every(point=>Number.isFinite(point.x)&&Number.isFinite(point.y))));
  for(const edge of layout.edges) {
    const source=positions[edge.source],target=positions[edge.target];
    assert.deepEqual(edge.points[0],{x:source.x,y:source.y});
    assert.deepEqual(edge.points.at(-1),{x:target.x,y:target.y});
    assert.ok(edge.points[1].x>source.x,'each task exits on its right');
    assert.ok(edge.points.at(-2).x<target.x,'each task receives work from its left');
  }
  const returning=layout.edges.find(edge=>edge.source==='revise'&&edge.target==='review');
  assert.equal(returning.returning,true);
  assert.match(returning.label,/Return to review/);
  assert.ok(Math.min(...returning.points.map(point=>point.y))<Math.min(...layout.nodes.map(node=>node.y))-50);
  assert.equal(workflowKind({type:'serviceTask',workerType:'program'}),'program');
  assert.equal(workflowKind({type:'serviceTask',workerType:'goal'}),'goal');
  assert.equal(workflowKind({type:'serviceTask',workerKind:'agent'}),'agent');
  assert.equal(workflowKind({type:'userTask'}, {kind:'program'}),'human');
  assert.equal(new Set(['agent','goal','senate','swarm','program','human'].map(workColor)).size,6);
  assert.deepEqual(workflowLabel('Goal: resolve human feedback'),['Goal: resolve human','feedback']);
});

test('multiple workflow returns and self-loops stay connected in distinct lanes',()=>{
  const nodes=['start','work','review','revise','end'].map(id=>({id,type:'serviceTask'}));
  const edges=[['start','work'],['work','review'],['review','revise'],['revise','work'],['review','review'],['review','end']].map(([source,target])=>({source,target}));
  const layout=workflowLayout(nodes,edges),returns=layout.edges.filter(edge=>edge.returning);
  assert.equal(returns.length,2);
  assert.equal(new Set(returns.map(edge=>Math.min(...edge.points.map(point=>point.y)))).size,2);
  for(const edge of returns) {
    assert.ok(edge.points[1].x>edge.points[0].x);
    assert.ok(edge.points.at(-2).x<edge.points.at(-1).x);
    assert.ok(edge.points.every(point=>point.x>=0&&point.x<layout.width&&point.y>=0&&point.y<layout.height));
  }
});

test('workflow selection uses durable activity identity before labels and follows the latest attempt',()=>{
  const node={id:'prepare',name:'review'};
  const original={id:'first',name:'prepare',metadata:{bpmn:{id:'prepare'}}};
  const collision={id:'unrelated',name:'review',metadata:{bpmn:{id:'review'}}};
  const retry={id:'retry',name:'prepare',metadata:{activity_id:'prepare'}};
  assert.equal(workflowJob(node,[original,collision]),original);
  assert.equal(workflowJob(node,[original,retry,collision]),retry);
  assert.equal(workflowJob(node,[collision]),undefined);
  assert.equal(workflowJob({id:'review',name:'review'},[original,collision]),collision);
  const legacy={id:'legacy',name:'review'};
  assert.equal(workflowJob(node,[legacy]),legacy);
  assert.equal(workflowJob(node,[original,legacy]),original);
});

test('timer and boundary event paths retain all connections through layout',()=>{
  const nodes=[['start','startEvent'],['wait','intermediateCatchEvent'],['call','callActivity'],
    ['deadline','boundaryEvent'],['end','endEvent']].map(([id,type])=>({id,type}));
  const edges=[['start','wait'],['wait','call'],['call','end'],['deadline','end']]
    .map(([source,target])=>({source,target}));
  const attachment={source:'call',target:'deadline',kind:'attachment'};
  const layout=workflowLayout(nodes,[...edges,attachment]);
  assert.equal(layout.nodes.length,nodes.length);
  assert.equal(layout.edges.length,edges.length+1);
  const placed=new Map(layout.nodes.map(node=>[node.id,node]));
  for(const edge of layout.edges) {
    assert.deepEqual(edge.points[0],{x:placed.get(edge.source).x,y:placed.get(edge.source).y});
    assert.deepEqual(edge.points.at(-1),{x:placed.get(edge.target).x,y:placed.get(edge.target).y});
  }
});
