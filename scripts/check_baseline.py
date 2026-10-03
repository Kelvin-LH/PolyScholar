# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]

def main():
    for required in ['LICENSE', 'NOTICE', 'README.md', 'schemas/001_initial.sql',
                     'polyscholar/app.py', 'polyscholar/html_translate.py']:
        assert (ROOT / required).is_file(), required
    for path in [ROOT / 'README.md', ROOT / 'CONTRIBUTING.md', ROOT / 'SECURITY.md', ROOT / 'THIRD_PARTY_NOTICES.md', *sorted((ROOT / 'docs').glob('*.md'))]:
        for target in re.findall(r'\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            if '://' in target or target.startswith('#'):
                continue
            assert (path.parent / target.split('#')[0]).exists(), (path, target)
    con = sqlite3.connect(':memory:')
    con.executescript((ROOT / 'schemas/001_initial.sql').read_text())
    assert con.execute('PRAGMA foreign_key_check').fetchall() == []
    assert con.execute('PRAGMA user_version').fetchone()[0] == 1
    con.close()
    print('Baseline: local links and reference DocumentIR schema passed (not runtime migration)')

if __name__ == '__main__':
    main()
