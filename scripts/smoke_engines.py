# SPDX-License-Identifier: AGPL-3.0-only
"""Validate real pinned upstream parsers without a document upload or model download."""
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.engines import Request, check_version, command, limited_environment, private_config
ROOT = Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory(prefix='polyscholar-parser-') as temp:
        base = Path(temp)
        for name in ['babeldoc', 'pdfmathtranslate']:
            binary = ROOT / '.venvs' / name / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            req = Request(name, binary, base/'sample.pdf', base/'output',
                          'http://127.0.0.1:9999/v1', 'synthetic-model', pages='1-2')
            env = limited_environment()
            check_version(req, env)
            config = private_config(req, base/name, 'synthetic-test-token')
            module = 'babeldoc.main' if name == 'babeldoc' else 'pdf2zh.pdf2zh'
            code = ('import importlib,sys; '
                    'm=importlib.import_module(' + repr(module) + '); '
                    'a=m.create_parser().parse_args(sys.argv[1:]); '
                    'assert a.lang_in=="en" and a.lang_out=="zh" and a.pages=="1-2"; ')
            if name == 'babeldoc':
                code += 'assert a.openai_model=="synthetic-model"; assert a.openai_api_key=="synthetic-test-token"; '
            else:
                code += 'assert a.service=="openai"; '
            code += 'print("Pinned upstream parser passed")'
            args = command(req, config)
            subprocess.run([str(binary), '-I', '-c', code, *args[args.index(module)+1:]],
                           env=env, cwd=base, check=True, timeout=60)
            print(name, 'version and real argument parser passed; no translation executed')

if __name__ == '__main__':
    main()
