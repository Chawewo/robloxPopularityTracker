"""Windows timer: dispatch GitHub Actions using the existing Git credential helper.

Credentials remain in Windows Credential Manager; never written to files or logs.
"""
import json
import csv
import io
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
API = 'https://api.github.com/repos/Chawewo/robloxPopularityTracker'
GIT = r'C:\Program Files\Git\cmd\git.exe'


def request(path, token=None, payload=None):
    headers = {'User-Agent': 'RobloxTracker-WindowsScheduler', 'Accept': 'application/vnd.github+json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = json.dumps(payload).encode() if payload is not None else None
    with urlopen(Request(API + path, data=body, headers=headers), timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def credential():
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
    result = subprocess.run([GIT, '-c', f'safe.directory={ROOT.as_posix()}', 'credential', 'fill'],
        input='protocol=https\nhost=github.com\n\n', capture_output=True, text=True,
        cwd=ROOT, env=env, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode or not values.get('password'):
        raise RuntimeError('GitHub sign-in unavailable in Windows Git Credential Manager')
    return values['password']


def main():
    token = credential()
    if '--check-auth' in sys.argv:
        repo = request('', token)
        if not repo.get('permissions', {}).get('push'):
            raise RuntimeError('GitHub account does not have write access to this repository')
        return 'GitHub authentication verified; repository write access available'
    runs = request('/actions/workflows/track.yml/runs?per_page=10', token)['workflow_runs']
    if any(run['status'] != 'completed' for run in runs):
        return 'Skipped: workflow already active'
    # Use GitHub's repository data, not a potentially cached Pages response.
    import base64
    content = request('/contents/data/collections.csv?ref=main', token)
    rows = list(csv.DictReader(io.StringIO(base64.b64decode(content['content']).decode('utf-8'))))
    stamp = max(datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')) for row in rows)
    if (datetime.now(timezone.utc) - stamp).total_seconds() < 12 * 60:
        return 'Skipped: collection is less than 12 minutes old'
    request('/actions/workflows/track.yml/dispatches', token, {'ref': 'main'})
    return 'Dispatched GitHub collection workflow'


if __name__ == '__main__':
    code = 0
    try:
        message = main()
    except Exception as exc:
        # HTTP errors do not include request headers; never log credential-helper output.
        message = f'Failed: {type(exc).__name__}: {exc}'
        code = 1
    line = f'{datetime.now(timezone.utc).isoformat()} {message}'
    log = ROOT / '.scheduler.log'
    prior = log.read_text(encoding='utf-8').splitlines()[-499:] if log.exists() else []
    log.write_text('\n'.join(prior + [line]) + '\n', encoding='utf-8')
    if sys.stdout:
        print(line)
    sys.exit(code)
