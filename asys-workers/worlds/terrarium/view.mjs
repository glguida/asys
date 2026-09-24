/** Habitat graphics; the dashboard owns execution, evidence and inspection. */
export function mount(element, context = {}) {
  const theme = {carta:'#EFE7D6',inchiostro:'#201A12',verde:'#3E7C5A',campo:'#E1D9C8',rail:'#928B7E',...context.theme};
  element.innerHTML = `<div class="habitat-view"><div class="habitat-layout"><section>
    <svg class="habitat-map" tabindex="0" role="group" aria-label="Habitat map. Arrow keys select a cell; Enter inspects it. Member marks open their saved decision history."></svg>
    <div class="habitat-legend" aria-label="Habitat legend. Activate an item for its rules.">
      <button type="button" data-legend="member"><b class="inhabitant" aria-hidden="true"></b>Member</button>
      <button type="button" data-legend="collector"><b aria-hidden="true">C</b>Collector</button>
      <button type="button" data-legend="garden"><b aria-hidden="true">G</b>Garden</button>
      <button type="button" data-legend="channel"><b aria-hidden="true">+</b>Channel</button>
      <button type="button" data-legend="resource"><b aria-hidden="true">M</b>Material</button>
      <button type="button" data-legend="neighborhood"><b class="area" aria-hidden="true"></b>Local view</button>
    </div><p class="habitat-visibility"></p></section><section>
    <label class="habitat-selector">Member history<select><option value="">Inspect cells</option></select></label>
    <h4>Healthy gardens by turn</h4><svg class="habitat-history" role="group" aria-label="Healthy gardens by turn. Select a point to inspect its measurement."></svg>
    <div class="habitat-legend" aria-label="Measurement legend">
      <button type="button" data-legend="garden"><b class="line" aria-hidden="true"></b>Healthy gardens</button>
      <button type="button" data-legend="target"><b class="line dashed" aria-hidden="true"></b>Target</button>
      <button type="button" data-legend="drought"><b class="area" aria-hidden="true"></b>Drought</button>
    </div><p class="habitat-measurement"></p><p class="habitat-phase"></p></section></div>
    <section class="habitat-cooperation"><h4>Recorded interactions</h4>
    <svg class="habitat-relations" role="group" aria-label="Recorded interactions from origin members to receiving members. Select a relation for evidence or a member for its decision history."></svg>
    <div class="habitat-legend" aria-label="Interaction legend">
      <button type="button" data-legend="reuse"><b class="line" aria-hidden="true"></b>Construction reuse</button>
      <button type="button" data-legend="message"><b class="line dashed" aria-hidden="true"></b>Delivered message</button>
      <button type="button" data-legend="member"><b class="inhabitant" aria-hidden="true"></b>Member history</button>
    </div><p class="habitat-relation-summary"></p></section>
    <section class="habitat-fallback" hidden aria-live="polite"><h4></h4><p></p><dl></dl></section></div>`;
  const $ = selector => element.querySelector(selector), ns = 'http://www.w3.org/2000/svg';
  const map = $('.habitat-map'), selector = $('.habitat-selector select');
  let current, signature, selected = {x:0,y:0}, member = null, historyTurn = null, relationKey = null, disposed = false;
  const node = (tag, attributes = {}, text) => {
    const result = document.createElementNS(ns, tag);
    for (const [key,value] of Object.entries(attributes)) result.setAttribute(key,value);
    if (text != null) result.textContent = text;
    return result;
  };
  const label = id => String(id).replace(/^agent[-_]?/, 'A');
  const text = (selector, value) => { $(selector).textContent = value; };
  const stateOf = frame => frame?.state ?? frame?.world ?? frame;
  const activeState = () => stateOf(current);
  const allMembers = state => ({...state.retired_agents,...state.agents});
  const targetOf = state => current?.metrics?.targetGardens ?? state.objective?.healthyGardens;
  const buttonAttributes = (key, description) => ({role:'button',tabindex:0,'aria-label':description,'data-focus':key});
  function inspect(value) {
    if (typeof context.inspect === 'function') { context.inspect(value); return; }
    const section = $('.habitat-fallback'); section.hidden = false;
    section.querySelector('h4').textContent = value.title;
    section.querySelector('p').textContent = value.description ?? '';
    const fields = section.querySelector('dl'); fields.replaceChildren();
    for (const [key,data] of Object.entries(value.data ?? {})) {
      if (data == null) continue;
      const term=document.createElement('dt'), detail=document.createElement('dd');
      term.textContent=key;
      detail.textContent=Array.isArray(data)?`${data.length} recorded entries`:typeof data==='object'?'Recorded details':String(data);
      fields.append(term,detail);
    }
  }
  function memberDetails(state,id) {
    const agent=allMembers(state)[id]; if(!agent)return null;
    return {title:id,description:state.agents?.[id]?'Present in the habitat.':'Removed before the drought; this is its last recorded position.',member:id,
      data:{Position:`${agent.x}, ${agent.y}`,Material:agent.stock,Constructions:agent.built,'Observation radius':state.rules.observationRadius,'Received messages':agent.inbox??[]}};
  }
  function chooseMember(id) {
    const state=activeState(), agent=state&&allMembers(state)[id]; if(!agent)return;
    member=id;selected={x:agent.x,y:agent.y};render();
    if(typeof context.selectMember==='function') {
      try { Promise.resolve(context.selectMember(id)).catch(()=>{if(!disposed&&member===id)inspect(memberDetails(state,id));}); }
      catch { if(!disposed&&member===id)inspect(memberDetails(state,id)); }
    } else inspect(memberDetails(state,id));
  }
  function inspectCell(state) {
    if(!state?.cells||!state?.rules)return;
    const cell=state.cells.find(cell=>cell.x===selected.x&&cell.y===selected.y);if(!cell)return;
    const neighbors=state.cells.filter(other=>Math.abs(other.x-cell.x)+Math.abs(other.y-cell.y)===1&&other.kind!=='ground');
    inspect({title:`${cell.kind[0].toUpperCase()+cell.kind.slice(1)} · ${cell.x}, ${cell.y}`,
      description:cell.built_by?`Built by ${cell.built_by} at turn ${cell.built_turn}.`:'Recorded habitat cell.',
      ...(cell.built_by?{member:cell.built_by}:{}),data:{Kind:cell.kind,Position:`${cell.x}, ${cell.y}`,Builder:cell.built_by,'Built at turn':cell.built_turn,
        'Water stored':cell.kind==='collector'?`${cell.water??0} / ${state.rules.collectorCapacity}`:undefined,
        'Garden health':cell.kind==='garden'?`${cell.health} / 100`:undefined,
        'Healthy threshold':cell.kind==='garden'?50:undefined,'Water this turn':cell.kind==='garden'?(cell.supplied?'Supplied':'Not supplied'):undefined,
        'Material available':cell.amount,'Last repaired by':cell.last_repaired_by,
        Neighbors:neighbors.map(item=>({position:`${item.x}, ${item.y}`,kind:item.kind,builder:item.built_by})),
        Members:Object.values(state.agents??{}).filter(agent=>agent.x===cell.x&&agent.y===cell.y).map(agent=>agent.id)}});
  }
  function inspectLegend(kind) {
    const state=activeState();if(!state?.rules)return;
    const descriptions={
      member:['Members','The member marks represent inhabitants. Select a mark or the member selector to inspect its saved decisions in the console.'],
      collector:['Collectors','Collectors store rainwater for gardens. The filled height shows stored water; select a collector to inspect its capacity and builder.'],
      garden:['Gardens','The bar beneath a garden shows health. A health value of at least 50 counts toward the healthy-garden measurement.'],
      channel:['Channels','Channels connect constructions so stored water can reach gardens. Select a channel to inspect its builder and adjacent cells.'],
      resource:['Construction material','Material cells supply construction stock to inhabitants. Select a cell for the remaining amount.'],
      neighborhood:['Local observation','Shading shows cells within the selected active member’s Manhattan observation radius. The observer can inspect the whole habitat.'],
      target:['Measured target','The dotted line shows the requested number of healthy gardens. The world checks their survival during the drought.'],
      drought:['Drought interval','The shaded interval begins after all members leave and rainfall stops. Constructions must maintain gardens without further member decisions.'],
      reuse:['Construction reuse','A solid relation records a new construction functionally adjoining another member’s construction. Multiple records between the same members are grouped.'],
      message:['Delivered local messages','A dashed relation records delivery to a nearby member. It establishes delivery, not whether the recipient used the message.']};
    const [title,description]=descriptions[kind]??['Habitat','Select a cell or recorded measurement.'];
    const data=kind==='member'?{Active:Object.keys(state.agents??{}).length,Retired:Object.keys(state.retired_agents??{}).length}:
      kind==='target'?{'Healthy gardens requested':targetOf(state)??'Exploration; no garden threshold'}:
      kind==='drought'?{'Construction turns':state.rules.discoveryTurns,'Drought turns':state.rules.droughtTurns,'Elapsed drought turns':state.drought?.elapsed??0}:
      kind==='neighborhood'?{Member:member??'Select a member',Radius:state.rules.observationRadius}:
      ['reuse','message'].includes(kind)?{'Recorded relations':(state.links??[]).filter(link=>link.kind===kind).length}:
      {Cells:state.cells.filter(cell=>cell.kind===kind).length};
    inspect({title,description,data});
  }
  function drawMap(state) {
    const size=38, radius=state.rules.observationRadius, agent=state.agents?.[member];
    map.setAttribute('viewBox',`0 0 ${state.width*size} ${state.height*size}`);
    const fragment=document.createDocumentFragment();
    for(const cell of state.cells) {
      const description=`Cell ${cell.x}, ${cell.y}: ${cell.kind}. Inspect ${cell.kind==='ground'?'cell':'construction or resource'}.`;
      const group=node('g',{'data-cell':`${cell.x},${cell.y}`,'data-focus':`cell-${cell.x}-${cell.y}`,transform:`translate(${cell.x*size},${cell.y*size})`,role:'button',tabindex:-1,'aria-label':description});
      const nearby=agent&&Math.abs(cell.x-agent.x)+Math.abs(cell.y-agent.y)<=radius;
      group.append(node('rect',{x:0,y:0,width:size,height:size,fill:nearby?theme.campo:theme.carta,stroke:theme.rail,'stroke-width':.5}));
      group.append(node('title',{},description));
      if(cell.kind==='collector') {
        group.append(node('path',{d:'M8 10V28H30V10',fill:'none',stroke:theme.inchiostro,'stroke-width':1.5}));
        const height=14*Math.max(0,Math.min(1,(cell.water??0)/state.rules.collectorCapacity));
        group.append(node('rect',{x:11,y:25-height,width:16,height,fill:theme.inchiostro}));
      } else if(cell.kind==='garden') {
        for(const y of [12,18,24])group.append(node('line',{x1:9,x2:29,y1:y,y2:y,stroke:theme.inchiostro,'stroke-width':1}));
        group.append(node('line',{x1:9,x2:9+20*Math.max(0,Math.min(1,cell.health/100)),y1:30,y2:30,stroke:theme.inchiostro,'stroke-width':3}));
      } else if(cell.kind==='channel') {
        group.append(node('path',{d:'M0 17H38M0 21H38M17 0V38M21 0V38',fill:'none',stroke:theme.inchiostro,'stroke-width':.8}));
      } else if(cell.kind==='resource') {
        for(let x=9;x<30;x+=6)group.append(node('line',{x1:x,y1:12,x2:x,y2:27,stroke:theme.inchiostro,'stroke-width':2}));
      }
      const letters={collector:'C',garden:'G',channel:'+',resource:'M'};
      if(letters[cell.kind])group.append(node('text',{x:3,y:8,fill:theme.inchiostro,'font-size':7,'font-weight':700},letters[cell.kind]));
      if(!member&&cell.x===selected.x&&cell.y===selected.y)group.append(node('rect',{x:2,y:2,width:size-4,height:size-4,fill:'none',stroke:theme.inchiostro,'stroke-width':2}));
      fragment.append(group);
    }
    const groups=new Map();
    for(const agent of Object.values(state.agents??{})) {
      const key=`${agent.x},${agent.y}`;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(agent);
    }
    for(const agents of groups.values())agents.forEach((agent,index)=>{
      const x=agent.x*size+25+(index-(agents.length-1)/2)*7,y=agent.y*size+13;
      const group=node('g',{...buttonAttributes(`map-${agent.id}`,`Open ${agent.id} member console`),'data-member':agent.id,transform:`translate(${x},${y})`});
      group.append(node('path',{d:theme.taskPath??'M0 1L1 1L.5 0Z',transform:'translate(-6,-10.4) scale(12,10.4)','vector-effect':'non-scaling-stroke',fill:theme.verde,stroke:member===agent.id?theme.inchiostro:theme.carta,'stroke-width':1.4}));
      group.append(node('title',{},agent.id));fragment.append(group);
    });
    map.replaceChildren(fragment);
  }
  function drawHistory(state) {
    const chart=$('.habitat-history'), history=state.history??[], target=targetOf(state);
    chart.setAttribute('viewBox','0 0 430 230');chart.replaceChildren();
    const maxTurn=Math.max(1,state.turn),maxHealthy=Math.max(1,target??0,...history.map(row=>row.healthyGardens));
    const x=turn=>36+turn/maxTurn*374,y=value=>188-value/maxHealthy*145;
    if(state.turn>=state.rules.discoveryTurns)chart.append(node('rect',{x:x(state.rules.discoveryTurns),y:24,width:x(maxTurn)-x(state.rules.discoveryTurns),height:164,fill:theme.campo,'data-legend':'drought'}));
    chart.append(node('path',{d:'M36 24V188H410',fill:'none',stroke:theme.rail,'stroke-width':1}));
    if(target!=null) {
      chart.append(node('line',{x1:36,x2:410,y1:y(target),y2:y(target),stroke:theme.inchiostro,'stroke-width':1,'stroke-dasharray':'2 4','data-legend':'target'}));
      chart.append(node('text',{x:410,y:y(target)-7,'text-anchor':'end',fill:theme.inchiostro,'font-size':10},`Target ${target}`));
    }
    if(history.length)chart.append(node('polyline',{points:history.map(row=>`${x(row.turn)},${y(row.healthyGardens)}`).join(' '),fill:'none',stroke:theme.inchiostro,'stroke-width':2}));
    else chart.append(node('text',{x:223,y:105,'text-anchor':'middle',fill:theme.inchiostro,'font-size':12},'No completed turns yet'));
    for(const row of history) {
      const point=node('g',{...buttonAttributes(`history-${row.turn}`,`Turn ${row.turn}: ${row.healthyGardens} healthy gardens`),'data-history':row.turn});
      point.append(node('circle',{cx:x(row.turn),cy:y(row.healthyGardens),r:8,fill:'transparent'}));
      point.append(node('circle',{cx:x(row.turn),cy:y(row.healthyGardens),r:historyTurn===row.turn?4:2.5,fill:theme.inchiostro}));
      point.append(node('title',{},`Turn ${row.turn}: ${row.healthyGardens} healthy gardens`));chart.append(point);
    }
    for(const [value,px,py,anchor] of [[maxHealthy,27,44,'end'],[0,27,191,'end'],['Turn 0',36,214,'start'],[`Turn ${state.turn}`,410,214,'end']])chart.append(node('text',{x:px,y:py,'text-anchor':anchor,fill:theme.inchiostro,'font-size':10},value));
    const healthy=state.cells.filter(cell=>cell.kind==='garden'&&cell.health>=50).length;
    text('.habitat-measurement',`${healthy} healthy gardens now. Select a point to inspect the recorded water and garden measurements.`);
  }
  function relations(state) {
    const groups=new Map();
    for(const link of state.links??[]) {
      const key=`${link.kind}:${link.source}:${link.target}`;
      if(!groups.has(key))groups.set(key,{key,kind:link.kind,source:link.source,target:link.target,records:[]});
      groups.get(key).records.push(link);
    }
    return [...groups.values()];
  }
  function drawRelations(state) {
    const chart=$('.habitat-relations'), grouped=relations(state), ids=Object.keys(allMembers(state)).sort();
    const height=Math.max(110,ids.length*30+62), positions=new Map(ids.map((id,index)=>[id,46+index*30]));
    chart.setAttribute('viewBox',`0 0 640 ${height}`);chart.replaceChildren();
    chart.append(node('text',{x:20,y:16,fill:theme.inchiostro,'font-size':11},'Origin'));
    chart.append(node('text',{x:620,y:16,'text-anchor':'end',fill:theme.inchiostro,'font-size':11},'Receiver'));
    for(const [index,edge] of grouped.entries()) {
      if(!positions.has(edge.source)||!positions.has(edge.target))continue;
      const middle=160+(index+.5)/Math.max(1,grouped.length)*320;
      const y1=positions.get(edge.source),y2=positions.get(edge.target);
      const path=`M106 ${y1} H${middle} V${y2} H534`;
      const description=`${edge.source} to ${edge.target}: ${edge.records.length} ${edge.kind==='reuse'?'construction reuse':'message delivery'} records`;
      const group=node('g',{...buttonAttributes(`relation-${edge.key}`,description),'data-relation':edge.key});
      group.append(node('path',{d:path,fill:'none',stroke:'transparent','stroke-width':12}));
      group.append(node('path',{d:path,fill:'none',stroke:theme.inchiostro,'stroke-width':relationKey===edge.key?2.5:1.2,'stroke-dasharray':edge.kind==='message'?'4 4':'none',opacity:relationKey&&relationKey!==edge.key?0.35:1}));
      group.append(node('title',{},description));chart.append(group);
    }
    for(const [side,x] of [['source',96],['target',544]]) {
      chart.append(node('path',{d:`M${x} 28V${height-24}`,fill:'none',stroke:theme.rail,'stroke-width':.7}));
      for(const id of ids) {
        const y=positions.get(id), group=node('g',{...buttonAttributes(`${side}-${id}`,`Open ${id} member console`),'data-member':id});
        group.append(node('rect',{x:side==='source'?0:x-8,y:y-14,width:104,height:27,fill:'transparent'}));
        group.append(node('path',{d:theme.taskPath??'M0 1L1 1L.5 0Z',transform:`translate(${x-6},${y-6.4}) scale(12,10.4)`,'vector-effect':'non-scaling-stroke',fill:theme.verde,stroke:member===id?theme.inchiostro:'none','stroke-width':1.4}));
        group.append(node('text',{x:side==='source'?x-13:x+13,y:y+4,'text-anchor':side==='source'?'end':'start',fill:theme.inchiostro,'font-size':11},label(id)));
        chart.append(group);
      }
    }
    if(!grouped.length)chart.append(node('text',{x:320,y:height/2,'text-anchor':'middle',fill:theme.inchiostro,'font-size':12},'No interactions recorded'));
    text('.habitat-relation-summary',`${(state.links??[]).length} recorded interactions across ${grouped.length} relations. Select a line for its turns and participants.`);
  }
  function render() {
    if(disposed)return;const state=activeState();if(!state?.cells||!state?.rules)return;
    const focused=element.contains(document.activeElement)?document.activeElement?.dataset?.focus:null;
    selected.x=Math.max(0,Math.min(state.width-1,selected.x));selected.y=Math.max(0,Math.min(state.height-1,selected.y));
    const phase=state.phase==='discovery'?'Rain and construction':state.phase==='drought'?'Drought · members have left':'Drought evaluation complete';
    text('.habitat-phase',`${phase} · turn ${state.turn}. ${state.phase==='discovery'?`${state.rules.discoveryTurns} construction turns precede ${state.rules.droughtTurns} rainless turns.`:`${state.drought.elapsed} of ${state.rules.droughtTurns} rainless turns measured.`}`);
    const agents=allMembers(state),ids=Object.keys(agents).sort();if(member&&!agents[member])member=null;
    if(selector.dataset.members!==ids.join('\n')) {
      selector.dataset.members=ids.join('\n');selector.replaceChildren();
      const empty=document.createElement('option');empty.value='';empty.textContent='Inspect cells';selector.append(empty);
      for(const id of ids){const option=document.createElement('option');option.value=id;option.textContent=id;selector.append(option);}
    }
    selector.value=member??'';
    text('.habitat-visibility',member?(state.agents?.[member]?`${member} · shaded cells show its local observation area. The observer sees the whole habitat.`:`${member} has left. Its saved decisions and final position remain inspectable.`):`Cell ${selected.x}, ${selected.y} selected. Click a cell or use arrow keys and Enter. The observer sees the whole habitat.`);
    drawMap(state);drawHistory(state);drawRelations(state);
    if(focused)[...element.querySelectorAll('[data-focus]')].find(item=>item.dataset.focus===focused)?.focus({preventScroll:true});
  }
  function choose(event) {
    const target=event.target.closest('[data-member],[data-cell],[data-history],[data-relation],[data-legend]');
    if(!target||!element.contains(target))return;
    const state=activeState();if(!state?.cells)return;
    if(target.dataset.member){chooseMember(target.dataset.member);return;}
    if(target.dataset.cell){const [x,y]=target.dataset.cell.split(',').map(Number);selected={x,y};member=null;render();inspectCell(state);return;}
    if(target.dataset.history) {
      historyTurn=Number(target.dataset.history);
      const row=state.history?.find(item=>item.turn===historyTurn);if(!row)return;
      const description=row.turn===state.rules.discoveryTurns
        ? 'End of construction. This measurement includes the final rainfall; members were then removed before the next turn.'
        : row.phase==='discovery'?'Measured while rainfall and inhabitants were active.':'Drought measurement after rainfall stopped and inhabitants left.';
      render();inspect({title:`Garden measurement · turn ${row.turn}`,description,
        data:{Turn:row.turn,'Healthy gardens':row.healthyGardens,'Stored water':row.water,Phase:row.phase,Target:targetOf(state)}});return;
    }
    if(target.dataset.relation){relationKey=target.dataset.relation;const relation=relations(state).find(item=>item.key===relationKey);if(!relation)return;render();inspect({title:`${label(relation.source)} → ${label(relation.target)}`,description:relation.kind==='reuse'?'Recorded neighboring construction reuse.':'Recorded delivery of a local message.',data:{Origin:relation.source,Receiver:relation.target,Kind:relation.kind,Records:relation.records.length,Turns:relation.records.map(item=>item.turn)},member:relation.target});return;}
    if(target.dataset.legend)inspectLegend(target.dataset.legend);
  }
  function keys(event) {
    if(!activeState()?.cells)return;
    if(map.contains(event.target)) {
      const offsets={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]};
      if(offsets[event.key]) {
        event.preventDefault();member=null;selected={x:selected.x+offsets[event.key][0],y:selected.y+offsets[event.key][1]};render();
        map.querySelector(`[data-cell="${selected.x},${selected.y}"]`)?.focus({preventScroll:true});inspectCell(activeState());return;
      }
      if(event.target===map&&(event.key==='Enter'||event.key===' ')){event.preventDefault();inspectCell(activeState());return;}
    }
    if(event.target.closest('[role=button]')&&(event.key==='Enter'||event.key===' ')){event.preventDefault();choose(event);}
  }
  function select() { if(selector.value)chooseMember(selector.value);else{member=null;render();inspectCell(activeState());} }
  element.addEventListener('click',choose);element.addEventListener('keydown',keys);selector.addEventListener('change',select);
  return {update(frame){const next=JSON.stringify(frame);current=frame;if(next!==signature){signature=next;render();}},dispose(){disposed=true;element.removeEventListener('click',choose);element.removeEventListener('keydown',keys);selector.removeEventListener('change',select);element.replaceChildren();}};
}
