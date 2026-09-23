"""Generic world viewer: embedded renderer modules and durable recorded frames."""
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import parse_qs, unquote, urlsplit

from asys_runtime.channel import Reader, Writer, direction_root
from asys_runtime.files import read_json


CHANNEL = 'swarm'
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'done'}
FALLBACK = b'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Asys world</title>
<link rel="stylesheet" href="/viewer.css">
<header><a class="brand" href="/">asys</a><span>WORLD OBSERVATORY</span><strong id="status">Connecting</strong></header>
<main><section class="heading"><div><h1 id="name">Swarm run</h1><p id="request"></p></div>
<div class="controls"><button data-control="pause">Pause</button><button data-control="resume">Resume</button><button data-control="cancel">Cancel</button></div></section>
<p id="error" role="alert"></p><div id="metrics"></div>
<section class="timeline"><button id="live">Live</button><label for="frame">Recorded frame</label>
<input id="frame" type="range" min="0" max="0" value="0" disabled><output id="frame-label">Waiting for a frame</output></section>
<section id="world" aria-label="World renderer"><pre id="fallback-state"></pre></section>
<section class="journal"><div><h2>Journal</h2><p>Saved events remain available after execution ends.</p><ol id="events"></ol></div>
<div><h2>Event details</h2><pre id="event-detail">Select an event.</pre></div></section></main>
<script type="module" src="/viewer.mjs"></script></html>'''

STYLE = b'''*{box-sizing:border-box}body{margin:0;background:#0f1c23;color:#e1ebe9;font:14px system-ui,sans-serif}
header{padding:18px 4vw;border-bottom:1px solid #30434a;display:flex;align-items:center;gap:24px}header span{font-size:10px;letter-spacing:2px;color:#9cafb5}
header strong{margin-left:auto;font-size:12px;color:#73d8c4}.brand{font-size:27px;font-weight:750;text-decoration:none;color:#e1ebe9;letter-spacing:-2px}
main{max-width:1350px;padding:30px 4vw;margin:auto}.heading{display:flex;justify-content:space-between;align-items:center;gap:24px}h1{font-size:28px;margin:0 0 9px}h2{font-size:17px;font-weight:600}h3{font-size:14px}
p{color:#9cafb5;line-height:1.6}button{background:#1c333b;color:#dfefeb;border:1px solid #3d5960;border-radius:5px;padding:8px 12px;cursor:pointer;font:inherit}button:hover{background:#2c4b51}button:disabled{opacity:.45;cursor:default}
.controls{display:flex;gap:6px}#error{color:#ffb399;min-height:1em}#metrics{display:flex;flex-wrap:wrap;gap:10px;margin:15px 0 24px}.metric{min-width:125px;padding:14px 17px;border:1px solid #30434a;border-radius:7px}.metric strong{font-size:24px;display:block}.metric span{font-size:11px;color:#9cafb5}
.timeline{display:flex;align-items:center;gap:15px;padding:14px 0;border-top:1px solid #30434a;border-bottom:1px solid #30434a;margin-bottom:22px}.timeline label,.timeline output{font-size:12px;color:#adbec5}input[type=range]{flex:1;accent-color:#73d8c4;min-width:60px}
#world{min-height:280px;padding:8px 0 22px}.journal{display:grid;grid-template-columns:1fr 1fr;gap:24px;border-top:1px solid #30434a;padding-top:20px}pre{font:12px ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:480px;overflow:auto}#events{padding:0;list-style:none;max-height:420px;overflow:auto}#events button{width:100%;text-align:left;border:0;border-bottom:1px solid #263d45;border-radius:0;font-size:12px;background:transparent}
@media(max-width:700px){.heading{display:block}.controls{margin-top:16px}.journal{grid-template-columns:1fr}.timeline{flex-wrap:wrap}.timeline label{display:none}header span{display:none}}'''

SCRIPT = r'''const $ = selector => document.querySelector(selector);
let renderer, record, frames = [], after = 0, live = true, selected = null, stopped = false;
const retained = [];
async function get(path) { const response = await fetch(path); const value = await response.json(); if (!response.ok) throw Error(value.error ?? response.statusText); return value; }
function metric(label, value) { const item = document.createElement('div'); item.className = 'metric'; const number = document.createElement('strong'); number.textContent = value ?? '\u2014'; const caption = document.createElement('span'); caption.textContent = label; item.append(number, caption); return item; }
async function display(frame) {
  $('#frame-label').textContent = `${live ? 'Live' : 'Replay'} \u00b7 turn ${frame.turn ?? 0}`;
  $('#metrics').replaceChildren(metric('Turn', frame.turn ?? 0), metric('Tokens', frame.usage?.totalTokens ?? 0),
    ...Object.entries(frame.metrics ?? {}).slice(0, 6).map(([key, value]) => metric(key, typeof value === 'object' ? JSON.stringify(value) : value)));
  if (renderer) await renderer.update(frame); else $('#fallback-state').textContent = JSON.stringify(frame.state ?? {}, null, 2);
}
async function choose(index) { if (!frames[index]) return; live = false; selected = frames[index].sequence; $('#live').textContent = 'Return to live'; await display(await get(`/api/state?sequence=${selected}`)); }
$('#frame').addEventListener('input', event => choose(Number(event.target.value)).catch(showError));
$('#live').addEventListener('click', () => { live = true; selected = null; $('#live').textContent = 'Live'; refresh().catch(showError); });
function showError(error) { $('#error').textContent = error.message; }
for (const button of document.querySelectorAll('[data-control]')) button.addEventListener('click', async () => {
  try { const response = await fetch('/api/control', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({type: button.dataset.control})}); const value = await response.json(); if (!response.ok) throw Error(value.error); $('#error').textContent = ''; } catch (error) { showError(error); }
});
function journal(events) {
  retained.push(...events); if (retained.length > 200) retained.splice(0, retained.length - 200);
  const list = $('#events'); list.replaceChildren();
  for (const event of [...retained].reverse()) {
    const item = document.createElement('li'), button = document.createElement('button');
    button.textContent = `#${event.sequence}  ${event.type}${event.data?.turn == null ? '' : ' / turn ' + event.data.turn}`;
    button.addEventListener('click', () => {
      const data = {...event.data}; if (data.state) data.state = '[Select its recorded frame to inspect world state]';
      $('#event-detail').textContent = JSON.stringify({...event, data}, null, 2);
      if (event.type === 'swarm.snapshot') { const index = frames.findIndex(frame => frame.sequence === event.sequence); if (index >= 0) { $('#frame').value = index; choose(index).catch(showError); } }
    });
    item.append(button); list.append(item);
  }
}
let refreshing = false;
async function refresh() {
  if (refreshing || stopped) return; refreshing = true;
  try {
    const [run, frame, page, timeline] = await Promise.all([get('/api/run'), get('/api/state'), get(`/api/events?after=${after}`), get('/api/frames')]);
    record = run; frames = timeline.frames; after = page.after;
    $('#name').textContent = run.worker_name ?? run.name ?? 'Swarm run'; $('#request').textContent = frame.mission ?? run.request ?? '';
    $('#status').textContent = run.status ?? frame.status ?? 'starting';
    const terminal = ['completed','failed','cancelled','interrupted','done'].includes(run.status);
    for (const button of document.querySelectorAll('[data-control]')) button.disabled = terminal || !run.swarm_state && run.manager !== 'swarm';
    $('#frame').disabled = !frames.length; $('#frame').max = Math.max(0, frames.length - 1);
    if (live) { $('#frame').value = Math.max(0, frames.length - 1); await display(frame); }
    else if (selected != null) $('#frame').value = Math.max(0, frames.findIndex(frame => frame.sequence === selected));
    if (page.events.length) journal(page.events);
  } finally { refreshing = false; }
}
async function start() {
  const [run, view] = await Promise.all([get('/api/run'), get('/api/view')]); record = run;
  if (view.module) { const module = await import(view.module); if (typeof module.mount !== 'function') throw Error('Renderer must export mount(element, context)'); $('#world').replaceChildren(); renderer = await module.mount($('#world'), {runId: run.id, metadata: run}); if (!renderer || typeof renderer.update !== 'function' || typeof renderer.dispose !== 'function') throw Error('Renderer mount must return update(frame) and dispose()'); }
  await refresh();
  const poll = async () => { if (stopped) return; try { await refresh(); } catch (error) { showError(error); } if (!stopped) setTimeout(poll, 700); }; setTimeout(poll, 700);
}
window.addEventListener('pagehide', () => { stopped = true; renderer?.dispose(); });
start().catch(showError);
'''.encode()


def run_record(directory):
    record = read_json(Path(directory) / 'run.json')
    if not isinstance(record, dict) or not (record.get('manager') == 'swarm' or record.get('swarm_state')):
        raise ValueError('Select a run containing a swarm world')
    return record


def control(directory, kind):
    if kind not in {'pause', 'resume', 'cancel'}:
        raise ValueError('Control type must be pause, resume, or cancel')
    record = run_record(directory)
    if record.get('status') in TERMINAL:
        raise ValueError(f"Run is already {record['status']}")
    channel = record.get('control_channel', CHANNEL)
    identity = (record.get('swarm_run_id', record.get('job_id', record['id']))
                if record.get('manager') == 'run' else record['id'])
    return Writer(direction_root(Path(directory) / 'runtime', channel, 'in')).send(kind, {'id': identity})


class Viewer:
    def __init__(self, directory, *, port=0):
        self.directory = Path(directory).resolve()
        record = run_record(self.directory)
        # Old runs retained a package; worker-owned runs retain only static
        # assets. Keep both view formats readable without importing either.
        self.package = (self.directory / record.get('view_directory', 'package')).resolve()
        if not self.package.is_relative_to(self.directory):
            raise ValueError('Saved view directory must remain inside the run')
        self.view = None
        if record.get('view'):
            candidate = (self.package / record['view']).resolve(strict=True)
            if not candidate.is_relative_to(self.package.resolve()) or not candidate.is_file():
                raise ValueError('Saved view must be a file inside the swarm package')
            self.view = candidate
        self.format = ('module' if record.get('view_format') == 'module' or self.view is not None
                       and self.view.suffix in {'.mjs', '.js'} else 'legacy' if self.view is not None else 'none')
        self.outbound = Reader(direction_root(self.directory / 'runtime', record.get('control_channel', CHANNEL), 'out'))
        self.state_directory = (self.directory / record.get('swarm_state', 'swarm')).resolve()
        if not self.state_directory.is_relative_to(self.directory):
            raise ValueError('Saved swarm state must remain inside the run')
        self.journal = self.state_directory / 'events.jsonl'
        self._index = {}
        self._position = 0
        self._inode = None
        self._lock = threading.RLock()
        viewer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, code, body, content_type='application/json; charset=utf-8'):
                if not isinstance(body, bytes):
                    body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

            def host_allowed(self):
                return self.headers.get('Host') in viewer.hosts

            def do_GET(self):
                if not self.host_allowed():
                    return self.respond(403, {'error': 'Local viewer host required'})
                target = urlsplit(self.path)
                try:
                    if target.path == '/api/run':
                        return self.respond(200, run_record(viewer.directory))
                    if target.path == '/api/state':
                        sequence = parse_qs(target.query).get('sequence', [None])[0]
                        return self.respond(200, viewer.state(None if sequence is None else int(sequence)))
                    if target.path == '/api/view':
                        return self.respond(200, {'format': viewer.format, 'module':
                            '/view/' + viewer.view.relative_to(viewer.package).as_posix() if viewer.format == 'module' else None})
                    if target.path == '/api/frames':
                        return self.respond(200, {'frames': viewer.frames()})
                    if target.path == '/api/events':
                        after = int(parse_qs(target.query).get('after', ['0'])[0])
                        if after < 0:
                            raise ValueError('after must be non-negative')
                        events = viewer.events(after)
                        return self.respond(200, {'events': events, 'after': events[-1]['sequence'] if events else after})
                    if target.path.startswith('/api/'):
                        return self.respond(404, {'error': 'Unknown endpoint'})
                    if viewer.format != 'legacy':
                        if target.path == '/':
                            return self.respond(200, FALLBACK, 'text/html; charset=utf-8')
                        if target.path == '/viewer.mjs':
                            return self.respond(200, SCRIPT, 'text/javascript; charset=utf-8')
                        if target.path == '/viewer.css':
                            return self.respond(200, STYLE, 'text/css; charset=utf-8')
                        if viewer.format != 'module' or not target.path.startswith('/view/'):
                            return self.respond(404, {'error': 'Not found'})
                        path = (viewer.package / unquote(target.path[len('/view/'):])).resolve()
                        if not path.is_relative_to(viewer.package) or not path.is_file():
                            return self.respond(404, {'error': 'Not found'})
                        mime = 'text/javascript' if path.suffix in {'.mjs', '.js'} else mimetypes.guess_type(path)[0]
                        return self.respond(200, path.read_bytes(), mime or 'application/octet-stream')
                    relative = unquote(target.path).lstrip('/')
                    path = viewer.view if not relative else (viewer.view.parent / relative).resolve()
                    if not path.is_relative_to(viewer.view.parent) or not path.is_file():
                        return self.respond(404, {'error': 'Not found'})
                    return self.respond(200, path.read_bytes(), mimetypes.guess_type(path)[0] or 'application/octet-stream')
                except (OSError, ValueError, TypeError) as error:
                    self.respond(400, {'error': str(error)})

            def do_POST(self):
                if not self.host_allowed():
                    return self.respond(403, {'error': 'Local viewer host required'})
                origin = self.headers.get('Origin')
                if origin is not None and origin != f"http://{self.headers.get('Host')}":
                    return self.respond(403, {'error': 'Same-origin control required'})
                if self.headers.get('Sec-Fetch-Site') not in {None, 'same-origin', 'none'}:
                    return self.respond(403, {'error': 'Same-origin control required'})
                if urlsplit(self.path).path != '/api/control':
                    return self.respond(404, {'error': 'Unknown endpoint'})
                if self.headers.get_content_type() != 'application/json':
                    return self.respond(415, {'error': 'Use application/json'})
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if length < 1 or length > 4096:
                        raise ValueError('Expected a JSON control object of at most 4096 bytes')
                    value = json.loads(self.rfile.read(length))
                    if not isinstance(value, dict):
                        raise ValueError('Expected a JSON control object')
                    return self.respond(202, control(viewer.directory, value.get('type')))
                except (OSError, ValueError, TypeError) as error:
                    self.respond(400, {'error': str(error)})

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.hosts = {f'127.0.0.1:{self.port}', f'localhost:{self.port}'}
        self.url = f'http://127.0.0.1:{self.port}/'
        self.thread = None

    def _scan_journal(self):
        """Index complete durable lines without retaining large state payloads."""
        with self._lock:
            if not self.journal.is_file():
                return False
            stat = self.journal.stat()
            inode = (stat.st_dev, stat.st_ino)
            if inode != self._inode or stat.st_size < self._position:
                self._index, self._position, self._inode = {}, 0, inode
            with self.journal.open('rb') as stream:
                stream.seek(self._position)
                while True:
                    offset = stream.tell()
                    line = stream.readline(8 * 1024 * 1024 + 1)
                    if len(line) > 8 * 1024 * 1024:
                        raise ValueError('Journal event exceeds 8 MiB')
                    if not line or not line.endswith(b'\n'):
                        break  # an interrupted final append is not a frame
                    row = json.loads(line)
                    if not isinstance(row, dict) or not isinstance(row.get('data'), dict):
                        raise ValueError('Invalid journal event')
                    sequence = row.get('sequence')
                    if type(sequence) is not int or sequence <= next(reversed(self._index), 0):
                        raise ValueError('Invalid journal sequence')
                    data = row.get('data', {})
                    self._index[sequence] = {'offset': offset, 'length': len(line), 'sequence': sequence,
                        'type': row.get('type'), 'time': row.get('time'), 'turn': data.get('turn'),
                        'status': data.get('status')}
                    self._position = stream.tell()
            return True

    def _journal_event(self, sequence):
        row = self._index[sequence]
        with self.journal.open('rb') as stream:
            stream.seek(row['offset'])
            return json.loads(stream.read(row['length']))

    def events(self, after=0):
        with self._lock:
            if not self._scan_journal():
                return self.outbound.read(after, limit=500)
            rows, total = [], 0
            for sequence, metadata in self._index.items():
                if sequence <= after:
                    continue
                if rows and (len(rows) >= 500 or total + metadata['length'] > 4 * 1024 * 1024):
                    break
                rows.append(self._journal_event(sequence))
                total += metadata['length']
            return rows

    def frames(self):
        with self._lock:
            if self._scan_journal():
                return [{key: value for key, value in row.items() if key not in {'offset', 'length', 'type'}}
                        for row in self._index.values() if row['type'] == 'swarm.snapshot']
            return [{'sequence': event['sequence'], 'time': event['time'], 'turn': event['data'].get('turn'),
                     'status': event['data'].get('status')} for event in self.outbound.read(0)
                    if event['type'] == 'swarm.snapshot']

    def state(self, sequence=None):
        if sequence is not None:
            with self._lock:
                if self._scan_journal():
                    if sequence not in self._index or self._index[sequence]['type'] != 'swarm.snapshot':
                        raise ValueError('Select a recorded snapshot sequence')
                    return self._journal_event(sequence)['data']
                event = self.outbound.event(sequence)
                if event['type'] != 'swarm.snapshot':
                    raise ValueError('Select a recorded snapshot sequence')
                return event['data']
        # New runs have an authoritative checkpoint in the outer job. Prefer it
        # to a channel whose old snapshots may have been pruned.
        checkpoint = self.state_directory / 'checkpoint.json'
        if checkpoint.is_file():
            value = read_json(checkpoint)
            evaluation = value.get('evaluation', {})
            config = value.get('config', {})
            return {'runId': value.get('id'), 'turn': value.get('turn'), 'state': value.get('world', {}),
                'metrics': evaluation.get('metrics', {}), 'evaluation': evaluation,
                'status': value.get('status'), 'decisions': value.get('decisions', 0),
                'usage': value.get('usage', {}), 'events': [],
                'mission': config.get('mission'), 'objective': config.get('objective')}
        frames = self.frames()
        if frames:
            return self.state(frames[-1]['sequence'])
        for sequence in reversed(self.outbound.sequences()):
            try:
                event = self.outbound.event(sequence)
            except FileNotFoundError:
                continue
            if event['type'] == 'swarm.snapshot':
                return event['data']
        return {}

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def close(self):
        if self.thread is not None:
            self.server.shutdown()
            self.thread.join(timeout=5)
            self.thread = None
        self.server.server_close()
