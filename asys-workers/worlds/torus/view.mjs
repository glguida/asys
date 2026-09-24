/** Pack every member separately below the artifact strip in its actual cell. */
export function memberLayout(positions) {
  const cells=new Map(),result=[];
  for(const [id,cell]of Object.entries(positions).sort(([a],[b])=>a<b?-1:a>b?1:0)) {
    const key=`${cell.x},${cell.y}`;
    if(!cells.has(key))cells.set(key,[]);
    cells.get(key).push({id,cell});
  }
  for(const members of cells.values()) {
    const count=members.length;let columns=1,rows=count,spacing=0,empty=Infinity;
    for(let candidate=1;candidate<=count;candidate++) {
      const candidateRows=Math.ceil(count/candidate),fit=Math.min(32/candidate,18/candidateRows),unused=candidate*candidateRows-count;
      if(fit>spacing||(fit===spacing&&unused<empty)){columns=candidate;rows=candidateRows;spacing=fit;empty=unused;}
    }
    const stepX=32/columns,stepY=18/rows,size=Math.min(14,spacing*.72);
    members.forEach(({id,cell},index)=>{
      const row=Math.floor(index/columns),column=index%columns,inRow=Math.min(columns,count-row*columns);
      result.push({id,cell,count,size,x:cell.x*40+4+(columns-inRow)*stepX/2+(column+.5)*stepX,y:cell.y*40+18+(row+.5)*stepY});
    });
  }
  return result;
}

export function mount(element, context = {}) {
  const theme={carta:"#EFE7D6",inchiostro:"#201A12",verde:"#3E7C5A",campo:"#E1D9C8",rail:"#928B7E",...context.theme};
  element.innerHTML = `<div class="torus"><section><h2>Local artifact world</h2><svg role="group" aria-label="Toroidal world. Select a member or artifact cell to inspect it."></svg>
  <div class="torus-legend"><button type="button" data-legend="agent"><i class="agent"></i>Member</button><button type="button" data-legend="artifact"><i></i>Checked artifacts</button><button type="button" data-legend="area"><i class="area"></i>Local observation area</button></div>
  <p>Opposite edges connect. Select a member to see its local area and saved decision history. Members sharing a cell have separate marks; ×N gives their count.</p><p class="cell-selection"></p></section><section><h2 class="selected">Select a member</h2><div class="members"></div>
  <p class="local"></p><ul></ul><h3>Observer leaderboard</h3><p>Global scores below are for the viewer; members receive local observations.</p><ol></ol></section></div>`;
  let current, selected, signature;
  const svg = element.querySelector('svg'), buttons = element.querySelector('.members');
  const node = (name, attributes) => {
    const item = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (const [key, value] of Object.entries(attributes)) item.setAttribute(key, value);
    return item;
  };
  function draw() {
    const state = current?.state;
    if (!state?.positions) return;
    selected = state.positions[selected] ? selected : state.agents[0];
    const {width, height, radius} = state.settings, position = state.positions[selected];
    const distance = cell => Math.min(Math.abs(cell.x - position.x), width - Math.abs(cell.x - position.x))
      + Math.min(Math.abs(cell.y - position.y), height - Math.abs(cell.y - position.y));
    const layout=memberLayout(state.positions),occupancy=new Map();
    for(const member of layout)occupancy.set(`${member.cell.x},${member.cell.y}`,member.count);
    const scale = 40; svg.setAttribute('viewBox', `0 0 ${width * scale} ${height * scale}`); svg.replaceChildren();
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      svg.append(node('rect', {x: x * scale + 1, y: y * scale + 1, width: 38, height: 38,
        fill: distance({x, y}) <= radius ? theme.campo : theme.carta, stroke: theme.rail, 'stroke-width': .5,
        'data-cell': `${x},${y}`, role:'button', tabindex:0, 'aria-label':`Inspect cell ${x}, ${y}; ${occupancy.get(`${x},${y}`)??0} members`}));
    }
    const placed = new Map();
    for (const artifact of state.artifacts) {
      const key = `${artifact.position.x},${artifact.position.y}`;
      placed.set(key, (placed.get(key) ?? 0) + 1);
    }
    for (const [key, count] of placed) {
      const [x, y] = key.split(',').map(Number);
      svg.append(node('rect', {x: x * scale + 4, y: y * scale + 5, width: 6, height: 6, fill: theme.inchiostro,
        'data-cell':key,'data-artifact-marker':'true',role:'button',tabindex:0,'aria-label':`${count} artifacts at ${x}, ${y}`}));
      const label = node('text', {x: x * scale + 12, y: y * scale + 12, fill: theme.inchiostro, 'font-size': 7,
        textLength:Math.min(12,String(count).length*4.2),lengthAdjust:'spacingAndGlyphs','data-cell':key,role:'button',tabindex:0,'aria-label':`${count} artifacts at ${x}, ${y}`});
      label.textContent = count; svg.append(label);
    }
    for(const [key,count]of occupancy)if(count>1) {
      const [x,y]=key.split(',').map(Number),text=`×${count}`;
      const label=node('text',{x:x*scale+37,y:y*scale+12,fill:theme.inchiostro,'font-size':7,'text-anchor':'end',
        textLength:Math.min(12,text.length*4.2),lengthAdjust:'spacingAndGlyphs','data-cell':key,'data-occupancy':count,
        role:'button',tabindex:0,'aria-label':`${count} members share cell ${x}, ${y}`});
      label.textContent=text;svg.append(label);
    }
    for (const {id:agent,cell,x,y,size,count} of layout) {
      const half=size/2;
      const marker = node('path', {d:theme.taskPath??"M0 1L1 1L.5 0Z", transform:`translate(${x-half},${y-half}) scale(${size},${size})`, "vector-effect":"non-scaling-stroke",
        fill: theme.verde, stroke: theme.inchiostro, 'stroke-width': Math.min(agent===selected?1.4:.5,size*.16), 'data-agent': agent,
        role:'button',tabindex:0,'aria-pressed':String(agent===selected),
        'aria-label':`Open ${agent} saved decisions; cell ${cell.x}, ${cell.y}; ${count} members in this cell`});
      const title = node('title', {}); title.textContent = `${agent} · (${cell.x}, ${cell.y})`; marker.append(title); svg.append(marker);
    }
    buttons.replaceChildren();
    for (const agent of state.agents) { const button = document.createElement('button'); button.type='button'; button.dataset.agent = agent; button.textContent = agent; button.setAttribute('aria-pressed',String(agent===selected)); buttons.append(button); }
    element.querySelector('.selected').textContent = `${selected} · (${position.x}, ${position.y})`;
    const nearby = state.artifacts.filter(item => distance(item.position) <= radius);
    element.querySelector('.local').textContent = `${nearby.length} nearby artifacts within wrapped Manhattan radius ${radius}. Up to ${state.settings.visible_artifacts} source slots are selected by score and recency.`;
    const list = element.querySelector('ul'); list.replaceChildren();
    for (const artifact of nearby) { const item = document.createElement('li'), button=document.createElement('button'); button.type='button';button.dataset.artifact=artifact.id;button.textContent = `${artifact.id} · score ${artifact.score} · ${artifact.agent}`;item.append(button);list.append(item); }
    if (!nearby.length) { const item=document.createElement('li');item.textContent='No artifacts in this local area.';list.append(item); }
    const sign = state.settings.direction === 'maximize' ? -1 : 1;
    const ranked = [...(state.baseline ? [state.baseline] : []), ...state.artifacts].sort((a, b) => sign * (a.score - b.score) || a.id.localeCompare(b.id));
    const ranking=element.querySelector('ol');ranking.replaceChildren();
    for(const [index,artifact]of ranked.entries()) { const item=document.createElement('li'),button=document.createElement('button');button.type='button';button.dataset.artifact=artifact.id;button.textContent=`${index+1}. ${artifact.score} · ${artifact.id}`;item.append(button);ranking.append(item); }
  }
  const click = event => {
    const item = event.target.closest('[data-agent],[data-cell],[data-artifact],[data-legend]'); if (!item) return;
    if (item.dataset.agent) {
      const hadFocus=element.ownerDocument.activeElement===item,tag=item.tagName;
      selected = item.dataset.agent; draw();
      if(hadFocus)[...element.querySelectorAll('[data-agent]')].find(node=>node.dataset.agent===selected&&node.tagName===tag)?.focus({preventScroll:true});
      context.selectMember?.(selected);return;
    }
    const state=current?.state;
    if(item.dataset.artifact) {
      const artifact=[...(state.baseline?[state.baseline]:[]),...state.artifacts].find(artifact=>artifact.id===item.dataset.artifact);
      context.inspect?.({title:artifact.id==='baseline'?'Initial candidate':artifact.id,description:`Checked score ${artifact.score} at round ${artifact.round??0}.${artifact.parent?' Declared parent: '+artifact.parent+'.':''}`,data:{score:artifact.score,round:artifact.round,author:artifact.agent,parent:artifact.parent,position:artifact.position,candidate:artifact.candidate,measurement:artifact.details}});return;
    }
    if(item.dataset.cell) {
      const [x,y]=item.dataset.cell.split(',').map(Number),members=Object.entries(state.positions).filter(([,p])=>p.x===x&&p.y===y).map(([id])=>id),artifacts=state.artifacts.filter(a=>a.position.x===x&&a.position.y===y);
      element.querySelector('.cell-selection').textContent=`Cell ${x}, ${y} · ${members.length} members · ${artifacts.length} artifacts`;
      context.inspect?.({title:`Cell ${x}, ${y}`,description:`${members.length} members and ${artifacts.length} checked artifacts. Artifacts stay where they were published.`,data:{position:{x,y},members,artifacts}});return;
    }
    const descriptions={agent:'The member marks identify swarm agents. Select one to inspect its saved decisions in the console. The shaded area follows the selected member. Members sharing a cell remain separate marks; ×N gives their count.',artifact:'Ink squares mark cells containing checked artifacts; the number gives their count. Select a square to inspect the cell, or a named artifact to inspect its candidate.',area:'Shading marks the selected member’s local observation area. Manhattan distance wraps across opposite edges. The observer’s global rankings are not sent to members.'};
    context.inspect?.({title:{agent:'Member',artifact:'Checked artifacts',area:'Local observation area'}[item.dataset.legend],description:descriptions[item.dataset.legend],data:state?.settings});
  };
  const keys=event=>{if((event.key==='Enter'||event.key===' ')&&event.target.matches('svg [role=button]')){event.preventDefault();click(event);}};
  element.addEventListener('click', click);
  element.addEventListener('keydown',keys);
  return {update(frame) { const next=JSON.stringify(frame);if(next===signature)return;signature=next;current = frame; draw(); },
    dispose() { element.removeEventListener('click', click);element.removeEventListener('keydown',keys);element.replaceChildren(); }};
}
