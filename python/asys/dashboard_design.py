"""Load a local dashboard design package without coupling it to run data."""
from html import escape
import json
from pathlib import Path
from urllib.parse import quote

DEFAULT_DESIGN = Path(__file__).resolve().parents[2] / 'designs/default'
ASSET_TYPES = {'.css', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.ico', '.woff', '.woff2', '.ttf', '.otf'}


def asset(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Design assets must stay inside the design package')
    root = Path(root).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or target.suffix.lower() not in ASSET_TYPES:
        raise ValueError('Expected a CSS, image or font file inside the design package')
    if not target.is_file():
        raise FileNotFoundError(f'Design asset does not exist: {relative}')
    return target


class Design:
    def __init__(self, directory=None):
        self.root = (Path(directory).expanduser() if directory is not None else DEFAULT_DESIGN).resolve()
        self.custom = self.root != DEFAULT_DESIGN.resolve()
        self.config = json.loads((self.root / 'design.json').read_text())
        if not isinstance(self.config, dict) or type(self.config.get('version')) is not int or self.config['version'] != 1:
            raise ValueError('Dashboard design.json must be a version-1 object')
        unknown = set(self.config) - {'version', 'name', 'stylesheet', 'logo', 'wordmark', 'title'}
        if unknown:
            raise ValueError(f'Unknown dashboard design fields: {", ".join(sorted(unknown))}')
        for field in ('name', 'stylesheet'):
            if not isinstance(self.config.get(field), str) or not self.config[field].strip():
                raise ValueError(f'Dashboard design requires {field}')
        for field in ('logo', 'wordmark', 'title'):
            if field in self.config and not isinstance(self.config[field], str):
                raise ValueError(f'Dashboard design {field} must be text')
        if asset(self.root, self.config['stylesheet']).suffix != '.css':
            raise ValueError('Dashboard design stylesheet must be CSS')
        if self.config.get('logo'):
            asset(self.root, self.config['logo'])

    def html(self, template):
        result = template
        if self.custom:
            url = '/design/custom/' + quote(self.config['stylesheet'], safe='/')
            result = result.replace('<!-- custom-design -->', f'<link rel="stylesheet" href="{escape(url, quote=True)}">')
        if self.config.get('logo'):
            prefix = '/design/custom/' if self.custom else '/design/default/'
            url = prefix + quote(self.config['logo'], safe='/')
            result = result.replace('/design/default/logo.svg', escape(url, quote=True))
        if 'wordmark' in self.config:
            result = result.replace('<span class="wordmark">asys</span>', '<span class="wordmark">' + escape(self.config['wordmark']) + '</span>')
        if 'title' in self.config:
            result = result.replace('<title>asys · dashboard</title>', '<title>' + escape(self.config['title']) + '</title>')
        return result.encode()
