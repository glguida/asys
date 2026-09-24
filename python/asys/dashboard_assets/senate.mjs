import {readTheme,markAttributes} from './design.mjs';

export function senateParticipants(checkpoint = {}, config = {}) {
  config = checkpoint.config ?? config;
  const sessions = checkpoint.sessions ?? [];
  const people = [];
  if (config.princeps) people.push({...config.princeps, id: 'princeps', role: 'Princeps senatus'});
  for (const [index, participant] of (config.senators ?? []).entries())
    people.push({...participant, id: `senator-${index + 1}`, role: 'Senator'});
  for (const person of people) {
    person.sessions = sessions.filter(session => session.participant === person.name);
    const latest = person.sessions.at(-1);
    person.status = latest?.status ?? 'waiting';
    person.phase = latest?.phase ?? '';
    person.round = latest?.round;
  }
  return people;
}

export function senateSeats(count) {
  const capacities=[3,5,7];
  while(capacities.reduce((sum,size)=>sum+size,0)<count)capacities.push(capacities.at(-1)+2);
  const rows=capacities.map(()=>0);
  for(let i=0,remaining=count;remaining>0;i=(i+1)%rows.length) {
    if(rows[i]<capacities[i]){rows[i]++;remaining--;}
  }
  const radii=rows.map((_,row)=>230+row*110),outer=radii.at(-1);
  const center={x:outer+90,y:outer+70};
  const positions=rows.map((size,row)=>Array.from({length:size},(_,index)=>{
    const angle=Math.PI*(size===1?(row+.8)/(rows.length+.6):(index+.5+(row%2?.12:-.12))/size);
    return {x:center.x-radii[row]*Math.cos(angle),y:center.y-radii[row]*Math.sin(angle),row};
  }));
  const seats=[];
  for(let column=0;seats.length<count;column++)for(const row of positions)if(row[column])seats.push(row[column]);
  return {center,seats,radii,width:center.x*2,height:center.y+95};
}

// Retain participant DOM and keyboard focus while checkpoint state advances.
export function mountSenate(element, {onSelect = () => {}} = {}) {
  const document = element.ownerDocument;
  const theme=readTheme(document);
  const make = (tag, text, cls) => { const node = document.createElement(tag); if (text) node.textContent = text; if (cls) node.className = cls; return node; };
  const svg = (tag, attrs = {}, text) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text) node.textContent = text;
    return node;
  };
  const heading = make('div', null, 'senate-heading'), status = make('p', '', 'quiet');
  const all = make('button', 'Whole Senate'); all.type = 'button'; all.addEventListener('click', () => onSelect(null));
  heading.append(status, all);
  const help = make('p', 'Select a participant to inspect their role, contributions, and saved conversation.', 'senate-help');
  const viewport = make('div', null, 'senate-viewport');
  const map = svg('svg', {role: 'group', 'aria-label': 'Senate participants arranged in a semicircle'});
  const guides = svg('g', {'aria-hidden': 'true'}), members = svg('g'); map.append(guides, members); viewport.append(map);
  const legend = make('p', 'Agent · outline: selected participant · ring: speaking', 'senate-legend');
  const history = make('details', null, 'senate-history'), summary = make('summary', 'About this layout');
  const note = make('p', 'This semicircle is a schematic of the agents. The Curia Julia seated senators on stepped banks to the left and right. The centered Princeps senatus shows the coordinating role in asys. ');
  const link = make('a', 'Curia Julia'); link.href = 'https://colosseo.it/mirabilia/curia-iulia/'; link.target = '_blank'; link.rel = 'noopener noreferrer'; note.append(link);
  history.append(summary, note); element.append(heading, help, viewport, legend, history);
  const nodes = new Map(); let layoutSignature = '';
  return {
    update(checkpoint, config, selected) {
      const people = senateParticipants(checkpoint, config), senators = people.filter(person => person.id !== 'princeps');
      const layout = senateSeats(senators.length);
      map.setAttribute('viewBox', `0 0 ${layout.width} ${layout.height}`);
      map.style.minWidth = `${Math.min(layout.width, people.length>16?1000:600)}px`;
      const sig = JSON.stringify(layout.radii);
      if (sig !== layoutSignature) {
        layoutSignature = sig; guides.replaceChildren();
        for (const radius of layout.radii) guides.append(svg('path', {d: `M ${layout.center.x-radius} ${layout.center.y} A ${radius} ${radius} 0 0 1 ${layout.center.x+radius} ${layout.center.y}`, class: 'senate-arc'}));
        guides.append(svg('line', {x1: layout.center.x-60, x2: layout.center.x+60, y1: layout.center.y+62, y2: layout.center.y+62, class: 'senate-dais'}));
      }
      status.textContent = [checkpoint.status ?? 'Waiting', `Round ${checkpoint.round ?? 0}`, `${senators.length} senators`].join(' · ');
      all.setAttribute('aria-pressed', String(!selected));
      const wanted = new Set(people.map(person => person.id));
      for (const [id, group] of nodes) if (!wanted.has(id)) { group.remove(); nodes.delete(id); }
      let index = 0;
      for (const person of people) {
        const position = person.id === 'princeps' ? layout.center : layout.seats[index++];
        let group = nodes.get(person.id);
        if (!group) {
          group = svg('g', {class: 'senate-person', role: 'button', tabindex: 0, 'data-participant': person.id});
          group.append(svg('rect', {x: -82, y: -37, width: 164, height: 96, class: 'senate-hit'}),
            svg('circle', {r: 26, cy: -7, class: 'senate-speaking'}),
            svg('path', {...markAttributes(theme,{x:-16,y:-20,width:32,height:29}),class:'senate-marker'}),
            svg('text', {y: 29, 'text-anchor': 'middle', class: 'senate-name'}),
            svg('text', {y: 46, 'text-anchor': 'middle', class: 'senate-role'}),svg('title'));
          const choose = () => onSelect(group._person);
          group.addEventListener('click', choose);
          group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(); } });
          nodes.set(person.id, group); members.append(group);
        }
        group._person = person;
        group.setAttribute('transform', `translate(${position.x},${position.y})`);
        group.setAttribute('aria-pressed', String(selected === person.name));
        group.setAttribute('aria-label', `${person.name}, ${person.role}, ${person.status}${person.phase ? ', '+person.phase : ''}`);
        group.classList.toggle('is-speaking', person.status === 'running');
        group.querySelector('.senate-name').textContent = person.name.length > 24 ? person.name.slice(0,23)+'…' : person.name;
        group.querySelector('.senate-role').textContent = `${person.role} · ${person.status}`;
        group.querySelector('title').textContent = `${person.name}\n${person.role}\n${person.prompt ?? ''}\n${person.phase || 'No contribution yet'}`;
      }
      const ratio=layout.width/(map.getBoundingClientRect().width||layout.width);
      for(const node of nodes.values()){node.querySelector('.senate-name').style.fontSize=`${12*ratio}px`;node.querySelector('.senate-role').style.fontSize=`${11*ratio}px`;}
    },
    dispose() { nodes.clear(); element.replaceChildren(); },
  };
}
