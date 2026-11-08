#!/usr/bin/env python3
"""Install a per-user launchd job for the bounded daily discovery runner."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
LABEL = 'ai.playlattice.daily-discovery'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--uninstall', action='store_true')
    args = parser.parse_args()
    if args.install == args.uninstall:
        parser.error('Choose --install or --uninstall')
    plist = Path.home() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
    domain = f'gui/{os.getuid()}'
    if args.uninstall:
        subprocess.run(['launchctl', 'bootout', domain, str(plist)], check=False)
        plist.unlink(missing_ok=True)
        print('Daily discovery schedule removed; saved outputs retained.')
        return
    node = shutil.which('node')
    tsx = ROOT / 'integrations/naive/node_modules/tsx/dist/cli.mjs'
    if not node or not tsx.is_file():
        raise SystemExit('Install Node and integration dependencies first.')
    data = ROOT / 'data/naive'
    data.mkdir(parents=True, exist_ok=True)
    policy = data / 'daily-discovery-policy.json'
    if not policy.exists():
        raise SystemExit('Create daily-discovery-policy.json before installing.')
    config = json.loads(policy.read_text())
    if config.get('target_per_day') != 5:
        raise SystemExit('This schedule requires the approved five-video daily limit.')
    job = {
        'Label': LABEL,
        'ProgramArguments': [node, str(tsx), str(ROOT / 'integrations/naive/src/daily-discovery.ts'), '--once'],
        'WorkingDirectory': str(ROOT / 'integrations/naive'),
        'RunAtLoad': True,
        'StartInterval': 300,
        'ProcessType': 'Background',
        'StandardOutPath': str(data / 'daily-discovery.stdout.log'),
        'StandardErrorPath': str(data / 'daily-discovery.stderr.log'),
        'EnvironmentVariables': {'PATH': os.pathsep.join(dict.fromkeys([str(Path(node).parent), str(ROOT / '.venv/bin'), '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin'])), 'PLAYLATTICE_PYTHON': str(ROOT / '.venv/bin/python')},
        'ExitTimeOut': 30,
    }
    plist.parent.mkdir(parents=True, exist_ok=True)
    with plist.open('wb') as handle:
        plistlib.dump(job, handle)
    os.chmod(plist, 0o600)
    subprocess.run(['plutil', '-lint', str(plist)], check=True)
    subprocess.run(['launchctl', 'bootout', domain, str(plist)], capture_output=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)
    print(f'Installed {LABEL}: bounded tick every five minutes, five new videos per local calendar day.')


if __name__ == '__main__':
    main()
