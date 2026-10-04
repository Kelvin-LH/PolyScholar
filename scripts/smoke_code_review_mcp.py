# SPDX-License-Identifier: AGPL-3.0-only
"""Real stdio MCP transport, synthetic evidence, no model/public API.

实际 stdio 协议与 CLI 子进程；证据为合成数据，不等于真实科研 agent 验收。
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from integrations.github_snapshot import GitHubClient
from polyscholar.service import LocalService
from code_review_fixtures import CodeFixture, code_report
from verification_fixtures import REPOSITORY


async def check(data, document, review):
    parameters = StdioServerParameters(command=sys.executable,
        args=['-m', 'polyscholar.mcp_server'], cwd=str(ROOT),
        env={'POLYSCHOLAR_DATA_DIR': str(data), 'PYTHONIOENCODING': 'utf-8'})
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            initialization = await client.initialize()
            assert '用户未指定深度就询问基础/深入' in initialization.instructions
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {'cli_docs', 'cli_run', 'cli_execute', 'library_status'}
            status = await client.call_tool('library_status')
            assert status.structured_content['status']['lock'] == 'free'
            docs = await client.call_tool('cli_docs')
            assert 'begin-code' in ''.join(block.text for block in docs.content if block.type == 'text')

            async def execute(action, *args):
                result = await client.call_tool('cli_execute', {
                    'argv': ['verify', action, document['id'], *args, '--json'],
                }, read_timeout_seconds=30)
                assert not result.is_error, result
                wrapped = result.structured_content
                assert wrapped['success'] and not wrapped['truncated'], wrapped
                return wrapped['data']

            plan = await execute('plan')
            assert plan['confirmation_required']
            tree = await execute('code-tree', '--session', review['session_id'])
            assert tree['commit_sha'] == review['commit_sha']
            code = await execute('read-code', '--session', review['session_id'], '--path', 'train.py')
            assert 'seed = 42' in code['content']
            saved = await execute('import-code', '--payload', json.dumps(code_report(review), ensure_ascii=False))
            report = await execute('show', '--report', saved['id'])
            assert report['report']['findings'][0]['code_evidence'][0]['excerpt'] == 'seed = 42'
            state = await execute('code-status', '--session', review['session_id'])
            assert state['state'] == 'completed'


def main():
    with tempfile.TemporaryDirectory(prefix='polyscholar-code-mcp-') as directory:
        root = Path(directory)
        fixture = CodeFixture()
        resources = root / 'resources'
        fixture.write_worker(resources, root / 'requests.jsonl')
        source = root / 'paper.pdf'
        source.write_bytes(b'%PDF-1.7 synthetic MCP code review paper')
        service = LocalService(root / 'data', resources_dir=resources)
        try:
            document = service.import_pdf(source)
            base = service.store.save_verification(document['id'], GitHubClient(fixture).collect(REPOSITORY), document['sha256'])
            review = service.begin_code_review(document['id'], base['id'], depth='deep', authorized=True)
            service.read_code(document['id'], review['session_id'], 'train.py', 1, 2)
        finally:
            service.close()
        # The stdio client uses only MCP; no host file is needed for submission.
        # 客户端写回仅使用 MCP JSON 参数，无需创建宿主报告文件。
        asyncio.run(check(root / 'data', document, review))
    print('Code review: real stdio MCP -> CLI -> persisted report passed (synthetic evidence)')


if __name__ == '__main__':
    main()
