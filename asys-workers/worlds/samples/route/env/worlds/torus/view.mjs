export function mount(element) {
  element.innerHTML = `<style>
    .torus{display:grid;grid-template-columns:minmax(280px,1.2fr) minmax(220px,1fr);gap:24px}.torus svg{width:100%;max-height:540px;background:#14242b;border-radius:8px}
    .torus p,.torus li{font-size:13px;color:#adbec5}.torus button{margin:3px}.torus pre{white-space:pre-wrap;font-size:12px}
    .torus [data-agent]{cursor:pointer}@media(max-width:700px){.torus{grid-template-columns:1fr}}
  </style><div class="torus"><section><h2>Local artifact world</h2><p>Edges wrap. Green: members. Blue: checked artifacts. Select a member to see its neighborhood.</p>
  <svg role="img" aria-label="Toroidal world"></svg></section><section><h2 class="selected">Select a member</h2><div class="members"></div>
  <p class="local"></p><ul></ul><h3>Observer leaderboard</h3><p>Global scores below are for the viewer; members receive local observations.</p><pre></pre></section></div>`;
  let current, selected;
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
    const scale = 40; svg.setAttribute('viewBox', `0 0 ${width * scale} ${height * scale}`); svg.replaceChildren();
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      svg.append(node('rect', {x: x * scale + 1, y: y * scale + 1, width: 38, height: 38,
        fill: distance({x, y}) <= radius ? '#29464a' : '#172d34', stroke: '#37505a', 'stroke-width': .5}));
    }
    const placed = new Map();
    for (const artifact of state.artifacts) {
      const key = `${artifact.position.x},${artifact.position.y}`;
      placed.set(key, (placed.get(key) ?? 0) + 1);
    }
    for (const [key, count] of placed) {
      const [x, y] = key.split(',').map(Number);
      svg.append(node('rect', {x: x * scale + 4, y: y * scale + 4, width: 13, height: 13, rx: 3, fill: '#76a9ef'}));
      const label = node('text', {x: x * scale + 20, y: y * scale + 14, fill: '#bdcff0', 'font-size': 9}); label.textContent = count; svg.append(label);
    }
    for (const [agent, cell] of Object.entries(state.positions)) {
      const marker = node('circle', {cx: cell.x * scale + 25, cy: cell.y * scale + 26, r: agent === selected ? 10 : 7,
        fill: '#73d8c4', stroke: agent === selected ? '#f0fffa' : '#173c36', 'stroke-width': 2, 'data-agent': agent});
      const title = node('title', {}); title.textContent = agent; marker.append(title); svg.append(marker);
    }
    buttons.replaceChildren();
    for (const agent of state.agents) { const button = document.createElement('button'); button.dataset.agent = agent; button.textContent = agent; buttons.append(button); }
    element.querySelector('.selected').textContent = `${selected} · (${position.x}, ${position.y})`;
    const nearby = state.artifacts.filter(item => distance(item.position) <= radius);
    element.querySelector('.local').textContent = `${nearby.length} nearby artifacts within wrapped Manhattan radius ${radius}. Up to ${state.settings.visible_artifacts} source slots are selected by score and recency.`;
    const list = element.querySelector('ul'); list.replaceChildren();
    for (const artifact of nearby) { const item = document.createElement('li'); item.textContent = `${artifact.id} · score ${artifact.score} · ${artifact.agent}`; list.append(item); }
    const sign = state.settings.direction === 'maximize' ? -1 : 1;
    const ranked = [...(state.baseline ? [state.baseline] : []), ...state.artifacts].sort((a, b) => sign * (a.score - b.score) || a.id.localeCompare(b.id));
    element.querySelector('pre').textContent = ranked.slice(0, 10).map((item, i) => `${i + 1}. ${item.score}  ${item.id}`).join('\n');
  }
  const click = event => { const item = event.target.closest('[data-agent]'); if (item) { selected = item.dataset.agent; draw(); } };
  element.addEventListener('click', click);
  return {update(frame) { current = frame; draw(); }, dispose() { element.removeEventListener('click', click); element.replaceChildren(); }};
}
