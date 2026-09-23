export function mount(element) {
  element.innerHTML = `<style>
    .leader{display:grid;grid-template-columns:minmax(260px,1fr) minmax(260px,1fr);gap:24px}
    .leader table{width:100%;border-collapse:collapse}.leader td,.leader th{text-align:left;padding:9px;border-bottom:1px solid #2d4047;font-size:13px}
    .leader tbody tr{cursor:pointer}.leader tbody tr:hover{background:#213840}.leader svg{width:100%;height:280px;background:#14242b;border-radius:8px}
    .leader pre{white-space:pre-wrap;font-size:12px}.leader small{color:#9bb0b8}@media(max-width:700px){.leader{grid-template-columns:1fr}}
  </style><div class="leader"><section><h2>Shared leaderboard</h2><small class="policy"></small>
  <table><thead><tr><th>Rank</th><th>Artifact</th><th>Score</th><th>Author</th></tr></thead><tbody></tbody></table></section>
  <section><h2 class="selection">Selected artifact</h2><svg viewBox="0 0 360 280" role="img" aria-label="Measured scores by round"></svg><pre></pre></section></div>`;
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
    svg.hidden = false;
    const low = Math.min(...items.map(item => item.score)), high = Math.max(...items.map(item => item.score));
    const span = high - low || 1, rounds = Math.max(1, ...items.map(item => item.round));
    svg.append(node('path', {d: 'M40,25 V240 H330', fill: 'none', stroke: '#52717b'}));
    for (const item of items) {
      const marker = node('circle', {cx: 40 + item.round / rounds * 285, cy: 225 - (item.score - low) / span * 185,
        r: item.id === selected ? 7 : 4, fill: item.id === selected ? '#e5efe9' : '#73d8c4'});
      const title = node('title', {}); title.textContent = `${item.id}: ${item.score}`; marker.append(title); svg.append(marker);
    }
    for (const [text, x, y] of [[high, 4, 42], [low, 4, 230], ['Round 0', 38, 263], [`Round ${rounds}`, 273, 263]]) {
      const label = node('text', {x, y, fill: '#b6cacd', 'font-size': 11}); label.textContent = text; svg.append(label);
    }
  }
  const click = event => { const row = event.target.closest('tr[data-id]'); if (row) { selected = row.dataset.id; draw(); } };
  table.addEventListener('click', click);
  return {update(frame) { current = frame; draw(); }, dispose() { table.removeEventListener('click', click); element.replaceChildren(); }};
}
