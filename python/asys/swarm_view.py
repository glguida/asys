"""Loopback UI for saved swarm runs, using only runtime channel files."""
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import parse_qs, unquote, urlsplit

from asys_runtime.channel import Reader, Writer, direction_root
from asys_runtime.files import read_json


CHANNEL = 'swarm'
TERMINAL = {'completed', 'failed', 'cancelled'}
FALLBACK = b'''<!doctype html><meta charset="utf-8"><title>Asys swarm</title>
<style>body{font:16px system-ui;background:#101922;color:#d9e6ed;margin:3em}
button{padding:.6em 1em;margin-right:.6em}pre{white-space:pre-wrap}</style>
<h1>Asys swarm</h1><p id="status">Connecting...</p>
<button onclick="control('pause')">Pause</button><button onclick="control('resume')">Resume</button>
<button onclick="control('cancel')">Cancel</button><pre id="state"></pre>
<script>async function control(type){let r=await fetch('/api/control',{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify({type})});if(!r.ok)alert((await r.json()).error)}
async function refresh(){try{let r=await(await fetch('/api/run')).json();
document.getElementById('status').textContent=r.name+' / '+r.status;
document.getElementById('state').textContent=JSON.stringify(await(await fetch('/api/state')).json(),null,2)
}catch(e){document.getElementById('status').textContent=e.message}}
refresh();setInterval(refresh,500)</script>'''


def run_record(directory):
    record = read_json(Path(directory) / 'run.json')
    if not isinstance(record, dict) or record.get('manager') != 'swarm':
        raise ValueError('Select an asys-swarm run')
    return record


def control(directory, kind):
    if kind not in {'pause', 'resume', 'cancel'}:
        raise ValueError('Control type must be pause, resume, or cancel')
    record = run_record(directory)
    if record.get('status') in TERMINAL:
        raise ValueError(f"Run is already {record['status']}")
    return Writer(direction_root(Path(directory) / 'runtime', CHANNEL, 'in')).send(kind, {'id': record['id']})


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
        self.outbound = Reader(direction_root(self.directory / 'runtime', CHANNEL, 'out'))
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
                        return self.respond(200, viewer.state())
                    if target.path == '/api/events':
                        after = int(parse_qs(target.query).get('after', ['0'])[0])
                        if after < 0:
                            raise ValueError('after must be non-negative')
                        events = viewer.outbound.read(after, limit=500)
                        return self.respond(200, {'events': events, 'after': events[-1]['sequence'] if events else after})
                    if target.path.startswith('/api/'):
                        return self.respond(404, {'error': 'Unknown endpoint'})
                    if viewer.view is None:
                        if target.path != '/':
                            return self.respond(404, {'error': 'Not found'})
                        return self.respond(200, FALLBACK, 'text/html; charset=utf-8')
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

    def state(self):
        for sequence in reversed(self.outbound.sequences()):
            try:
                event = self.outbound.event(sequence)
            except FileNotFoundError:
                continue
            if event['type'] == 'swarm.snapshot':
                return event['data']
        record = run_record(self.directory)
        state_directory = (self.directory / record.get('swarm_state', 'swarm')).resolve()
        if not state_directory.is_relative_to(self.directory):
            raise ValueError('Saved swarm state must remain inside the run')
        checkpoint = state_directory / 'checkpoint.json'
        if not checkpoint.is_file():
            return {}
        value = read_json(checkpoint)
        evaluation = value.get('evaluation', {})
        config = value.get('config', {})
        return {'runId': value.get('id'), 'turn': value.get('turn'), 'state': value.get('world', {}),
                'metrics': evaluation.get('metrics', {}), 'evaluation': evaluation,
                'status': value.get('status'), 'decisions': value.get('decisions', 0),
                'usage': value.get('usage', {}), 'events': [],
                'mission': config.get('mission'), 'objective': config.get('objective')}

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
