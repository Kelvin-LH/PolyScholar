# SPDX-License-Identifier: AGPL-3.0-only
"""Unsigned source integrity inventory. Not licensing or commercial-use detection."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def inventory():
    candidates = subprocess.check_output(
        ['git', '-C', str(ROOT), 'ls-files', '-z', '--cached', '--others', '--exclude-standard']
    ).decode().split('\0')
    result = {}
    for name in sorted(set(candidates) - {''}):
        path = ROOT / name
        if path.is_symlink():
            raise ValueError('Source inventory does not accept symlinks')
        if path.is_file():
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result

def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    g = sub.add_parser('generate')
    g.add_argument('--output', required=True)
    v = sub.add_parser('verify')
    v.add_argument('manifest')
    args = p.parse_args()
    if args.action == 'generate':
        out = Path(args.output).resolve()
        if out == ROOT or ROOT in out.parents:
            p.error('Manifest output must be outside the repository')
        commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True)
        data = {'format': 1, 'project': 'PolyScholar', 'license': 'AGPL-3.0-only',
                'commit': commit.stdout.strip() if commit.returncode == 0 else None,
                'signed': False, 'files': inventory()}
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print('Unsigned inventory written')
    else:
        data = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
        if data.get('format') != 1 or data.get('files') != inventory():
            raise SystemExit('Inventory mismatch: files changed, added or removed')
        print('Inventory matches; authenticity and commercial purpose are not verified')

if __name__ == '__main__':
    main()
