export function mount(element, context = {}) {
  const theme={carta:"#EFE7D6",inchiostro:"#201A12",verde:"#3E7C5A",campo:"#E1D9C8",rail:"#928B7E",...context.theme};
  element.innerHTML = `<div class="leader"><section><h2>Shared leaderboard</h2><small class="policy"></small>
  <div class="leader-list"><table><thead><tr><th>Rank</th><th>Artifact</th><th>Score</th><th>Author</th></tr></thead><tbody></tbody></table></div></section>
  <section><h2>Measured candidates</h2><svg viewBox="0 0 360 280" role="group" aria-label="Measured scores by round. Select a candidate for evidence."></svg>
  <button type="button" class="leader-legend">Checked candidate · outline marks selection</button><p class="selection"></p><p>Select a candidate to inspect its data, or an author to inspect its saved decision history.</p></section></div>`;
  let current, selected, signature;
  const table = element.querySelector('tbody'), preview = element.querySelector('.selection'), svg = element.querySelector('svg');
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
    if (!items.length) { table.replaceChildren(); svg.hidden = true; preview.textContent = 'No accepted candidates recorded.'; return; }
    const chosen = items.find(item => item.id === selected) ?? items[0];
    selected = chosen.id;
    element.querySelector('.policy').textContent = state.settings.top_k === 0
      ? 'Members see every checked submission. Click a row to inspect it.'
      : `Members see the best ${state.settings.top_k} checked submissions, plus the initial candidate.`;
    element.querySelector('.policy').textContent += state.settings.direction === 'maximize' ? ' Higher scores are better.' : ' Lower scores are better.';
    table.replaceChildren();
    for (const [index, item] of items.entries()) {
      const row = document.createElement('tr'); row.dataset.id = item.id;
      for (const [column, text] of [index + 1, item.id === 'baseline' ? 'Initial candidate' : item.id, item.score, item.agent ?? '—'].entries()) {
        const cell = document.createElement('td');
        if (column === 1 || column === 3 && item.agent) {
          const button = document.createElement('button'); button.type = 'button'; button.textContent = text;
          button.dataset[column === 1 ? 'id' : 'member'] = column === 1 ? item.id : item.agent; cell.append(button);
        } else cell.textContent = text;
        row.append(cell);
      }
      row.setAttribute('aria-selected', String(item.id === selected));
      table.append(row);
    }
    preview.textContent = `${chosen.id} · score ${chosen.score} · round ${chosen.round ?? 0}${chosen.agent ? ' · ' + chosen.agent : ''}`;
    svg.replaceChildren();
    svg.hidden = false;
    const low = Math.min(...items.map(item => item.score)), high = Math.max(...items.map(item => item.score));
    const span = high - low || 1, rounds = Math.max(1, ...items.map(item => item.round));
    svg.append(node('path', {d: 'M40,25 V240 H330', fill: 'none', stroke: theme.inchiostro}));
    for (const item of items) {
      const x = 40 + item.round / rounds * 285, y = 225 - (item.score - low) / span * 185;
      const marker = node('path', {d:theme.taskPath??"M0 1L1 1L.5 0Z", transform:`translate(${x-5},${y-6}) scale(10,10)`, "vector-effect":"non-scaling-stroke",
        fill: theme.inchiostro, stroke: theme.inchiostro, 'stroke-width': item.id === selected ? 2 : .5,
        'data-id': item.id, role: 'button', tabindex: 0, 'aria-label': `Inspect ${item.id}, score ${item.score}, round ${item.round}`});
      const title = node('title', {}); title.textContent = `${item.id}: ${item.score}`; marker.append(title); svg.append(marker);
    }
    for (const [text, x, y] of [[high, 4, 42], [low, 4, 230], ['Round 0', 38, 263], [`Round ${rounds}`, 273, 263]]) {
      const label = node('text', {x, y, fill: theme.inchiostro, 'font-size': 11}); label.textContent = text; svg.append(label);
    }
  }
  const click = event => {
    const author = event.target.closest('[data-member]');
    if (author) { context.selectMember?.(author.dataset.member); return; }
    if (event.target.closest('.leader-legend')) {
      context.inspect?.({title:'Checked candidates',description:'Each mark is a candidate accepted by the world evaluator. Horizontal position is its submission round; vertical position is the measured score. The heavier outline marks your selection. Acceptance does not prove optimality.',data:{direction:current?.state?.settings?.direction}}); return;
    }
    const row = event.target.closest('[data-id]');
    if (row) {
      selected = row.dataset.id;
      const chosen = [...(current.state.baseline ? [current.state.baseline] : []), ...current.state.artifacts].find(item => item.id === selected);
      // Change selection styles without replacing a focused button or chart marker.
      for (const item of table.children) item.setAttribute('aria-selected', String(item.dataset.id === selected));
      for (const item of svg.querySelectorAll('[data-id]')) item.setAttribute('stroke-width', item.dataset.id === selected ? 2 : .5);
      preview.textContent = `${chosen.id} · score ${chosen.score} · round ${chosen.round ?? 0}${chosen.agent ? ' · ' + chosen.agent : ''}`;
      context.inspect?.({title:chosen.id === 'baseline' ? 'Initial candidate' : chosen.id,
        description:`Checked score ${chosen.score} at round ${chosen.round ?? 0}.${chosen.parent ? ' Declared parent: ' + chosen.parent + '.' : ''}`,
        data:{score:chosen.score,round:chosen.round,author:chosen.agent,parent:chosen.parent,candidate:chosen.candidate,measurement:chosen.details}});
    }
  };
  const keys = event => { if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('path[data-id]')) { event.preventDefault(); click(event); } };
  element.addEventListener('click', click); element.addEventListener('keydown', keys);
  return {update(frame) { const next=JSON.stringify(frame); if(next===signature)return; signature=next; current = frame; draw(); },
    dispose() { element.removeEventListener('click', click); element.removeEventListener('keydown', keys); element.replaceChildren(); }};
}
