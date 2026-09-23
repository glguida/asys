export function mount(element) {
  element.innerHTML = `<style>
    .leader{display:grid;grid-template-columns:minmax(260px,1fr) minmax(260px,1fr);gap:24px}
    .leader table{width:100%;border-collapse:collapse}.leader td,.leader th{text-align:left;padding:9px;border-bottom:1px solid #2d4047;font-size:13px}
    .leader tbody tr{cursor:pointer}.leader tbody tr:hover{background:#213840}.leader svg{width:100%;height:280px;background:#14242b;border-radius:8px}
    .leader pre{white-space:pre-wrap;font-size:12px}.leader small{color:#9bb0b8}@media(max-width:700px){.leader{grid-template-columns:1fr}}
  </style><div class="leader"><section><h2>Shared leaderboard</h2><small class="policy"></small>
  <table><thead><tr><th>Rank</th><th>Artifact</th><th>Score</th><th>Author</th></tr></thead><tbody></tbody></table></section>
  <section><h2 class="selection">Selected artifact</h2><svg viewBox="0 0 360 280" role="img" aria-label="Measured route"></svg><pre></pre></section></div>`;
  let current, selected;
  const table = element.querySelector('tbody'), preview = element.querySelector('pre'), svg = element.querySelector('svg');
  const node = (name, attributes) => {
    const item = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (const [key, value] of Object.entries(attributes)) item.setAttribute(key, value);
    return item;
  };
  function draw() {
    const state = current?.state;
    if (!Array.isArray(state?.artifacts)) return;
    const sign = state.settings.direction === 'maximize' ? -1 : 1;
    const items = [...(state.baseline ? [state.baseline] : []), ...state.artifacts].sort((a, b) => sign * (a.score - b.score) || a.id.localeCompare(b.id));
    if (!items.length) { table.replaceChildren(); svg.hidden = true; preview.textContent = 'Waiting for an independently accepted candidate.'; return; }
    const chosen = items.find(item => item.id === selected) ?? items[0];
    selected = chosen.id;
    element.querySelector('.policy').textContent = state.settings.top_k === 0
      ? 'Members see every checked submission. Click a row to inspect it.'
      : `Members see the best ${state.settings.top_k} checked submissions, plus the initial candidate.`;
    table.replaceChildren();
    for (const [index, item] of items.entries()) {
      const row = document.createElement('tr'); row.dataset.id = item.id;
      for (const text of [index + 1, item.id === 'baseline' ? 'Initial candidate' : item.id, item.score, item.agent ?? '—']) {
        const cell = document.createElement('td'); cell.textContent = text; row.append(cell);
      }
      if (item.id === selected) row.style.background = '#29464a';
      table.append(row);
    }
    element.querySelector('.selection').textContent = `${chosen.id} · ${chosen.score}`;
    preview.textContent = JSON.stringify({candidate: chosen.candidate, parent: chosen.parent, measurement: chosen.details}, null, 2);
    svg.replaceChildren();
    const cities = state.problem.cities, tour = chosen.candidate?.tour;
    svg.hidden = !Array.isArray(cities) || !Array.isArray(tour);
    if (svg.hidden) return;
    const xs = cities.map(point => point[0]), ys = cities.map(point => point[1]);
    const x0 = Math.min(...xs), y0 = Math.min(...ys), dx = Math.max(...xs) - x0 || 1, dy = Math.max(...ys) - y0 || 1;
    const point = index => [35 + (cities[index][0] - x0) / dx * 285, 35 + (cities[index][1] - y0) / dy * 205];
    const route = [...tour, tour[0]].map(point), corners = [route[0]];
    for (let index = 1; index < route.length; index++) corners.push([route[index][0], route[index - 1][1]], route[index]);
    const path = corners.map(pair => pair.join(',')).join(' ');
    svg.append(node('polyline', {points: path, fill: 'none', stroke: '#73d8c4', 'stroke-width': 3}));
    cities.forEach((_, index) => {
      const [x, y] = point(index); svg.append(node('circle', {cx: x, cy: y, r: 6, fill: '#e5efe9'}));
      const label = node('text', {x: x + 9, y: y - 8, fill: '#d7e7e3', 'font-size': 13}); label.textContent = index; svg.append(label);
    });
  }
  const click = event => { const row = event.target.closest('tr[data-id]'); if (row) { selected = row.dataset.id; draw(); } };
  table.addEventListener('click', click);
  return {update(frame) { current = frame; draw(); }, dispose() { table.removeEventListener('click', click); element.replaceChildren(); }};
}
