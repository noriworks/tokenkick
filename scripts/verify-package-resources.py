"""Verify MCP documentation from built artifacts, outside the source checkout."""
import argparse
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def wheel_resources(wheel: Path, home: str) -> None:
    code = '''import sys
sys.path.insert(0, sys.argv[1])
from tokenkick import mcp_core
assert mcp_core.__file__.startswith(sys.argv[1]), mcp_core.__file__
for name in ('AGENT_PLAYBOOK.md', 'TOKENKICK_COMMANDS.md'):
    text = mcp_core._read_repo_text('docs/' + name)
    assert len(text) > 1000 and 'TokenKick' in text, name
'''
    subprocess.run([sys.executable, '-I', '-c', code, str(wheel.resolve())],
                   cwd=home, env={**os.environ, 'HOME': home}, check=True)


async def bundled_resources(runtime: Path, home: str) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=str(runtime.resolve()), args=['mcp', 'serve'],
                                  env={**os.environ, 'HOME': home, 'TK_NO_INTERACTIVE': '1'})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for uri in ('tokenkick://agent-playbook', 'tokenkick://commands'):
                result = await session.read_resource(uri)
                text = '\n'.join(getattr(item, 'text', '') for item in result.contents)
                assert len(text) > 1000 and 'TokenKick' in text, uri


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--runtime', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='tk-resource-check-') as home:
        wheel_resources(args.wheel, home)
        print('Wheel MCP resources: passed')
        if args.runtime:
            asyncio.run(asyncio.wait_for(bundled_resources(args.runtime, home), timeout=30))
            print('Bundled MCP resources over stdio: passed')


if __name__ == '__main__':
    main()
