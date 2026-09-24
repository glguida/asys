"""Exercise a real artifact handoff and one revision without model calls."""
import json
import os
from pathlib import Path
import sys


assignment = json.load(sys.stdin)
report = Path('deliverables/report.md')
role = sys.argv[1]

if role == 'implementer':
    report.parent.mkdir(parents=True, exist_ok=True)
    content = '# Report\n\n## Findings\n\nThis is a deterministic team fixture.\n'
    if report.exists():
        content += '\n## Evidence\n\nThe dummy worker generated this file; no real project was assessed.\n'
    content += '\n## Next steps\n\nUse the real agents environment to inspect a project.\n'
    report.write_text(content, encoding='utf-8')
    result = {'final': 'Wrote deliverables/report.md.', 'exception': None,
              'path': str(report)}
elif role == 'reviewer':
    content = report.read_text(encoding='utf-8')
    approved = all(section in content for section in ['## Findings', '## Evidence', '## Next steps'])
    result = {'final': 'Inspected the report sections.', 'exception': None,
              'approved': approved,
              'reason': 'All required sections are present.' if approved else 'Add the missing Evidence section.'}
else:
    raise SystemExit(f'Unknown role: {role}')

Path(os.environ['ASYS_RESULT']).write_text(json.dumps(result), encoding='utf-8')
