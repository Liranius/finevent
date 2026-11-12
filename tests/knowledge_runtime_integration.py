"""Run with the project's installed Basic Memory Python to exercise the real MCP writer."""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def materialized(notes: Path, expected: bytes) -> Path:
    async with asyncio.timeout(20):
        while True:
            for file in notes.rglob("*.md"):
                if expected in file.read_bytes():
                    return file
            await asyncio.sleep(0.05)


async def verify(directory: Path) -> None:
    project = directory / "project"
    cache = directory / "cache"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    cache.mkdir()
    for name in ("knowledge.mjs", "knowledge-runtime.py", "knowledge-privacy.mjs"):
        shutil.copy(ROOT / "scripts" / name, scripts)
    (project / ".codex").mkdir()
    shutil.copy(ROOT / ".codex/basic-memory.json", project / ".codex")
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
    # Share the installed interpreter. All project settings, notes and the DB are disposable.
    subprocess.run(
        [
            "node", "-e",
            "require('node:fs').symlinkSync(process.argv[1], process.argv[2], 'junction')",
            sys.prefix, str(cache / "runtime-0.24.1"),
        ],
        check=True,
    )
    server = StdioServerParameters(
        command="node", args=[str(scripts / "knowledge.mjs"), "mcp"], cwd=project,
        env={**os.environ, "FINEVENT_MEMORY_CACHE": str(cache), "HF_HUB_OFFLINE": "1"},
    )
    async with stdio_client(server) as streams, ClientSession(*streams) as session:
        await session.initialize()
        note = dict(
            title="Portable", directory="notes", project="finevent",
            content="safe reference\nSCHEME//threads/example", metadata={"embed": False},
        )
        rejected = await session.call_tool("write_note", {
            **note, "metadata": {"session_id": "private-value"},
        })
        assert rejected.is_error, f"MCP accepted private frontmatter: {rejected.content}"
        notes = project / "docs/knowledge"
        assert not list(notes.rglob("*.md")), "rejected note reached disk"

        created = await session.call_tool("write_note", note)
        assert not created.is_error, f"clean retry must create the same note: {created.content}"
        file = await materialized(notes, b"safe reference")
        before = file.read_bytes()
        unsafe = "codex://threads/private-value"
        reference = {"project": "finevent", "identifier": "notes/portable"}
        for tool, arguments in [
            ("write_note", {**note, "overwrite": True, "content": unsafe}),
            ("edit_note", {**reference, "operation": "append", "content": unsafe}),
            ("edit_note", {**reference, "operation": "find_replace", "find_text": "SCHEME", "content": "codex:"}),
        ]:
            rejected = await session.call_tool(tool, arguments)
            assert rejected.is_error, f"{tool} accepted private content: {rejected.content}"
            assert "private-value" not in str(rejected.content)
            assert file.read_bytes() == before, f"{tool} modified the original file"
        read = await session.call_tool("read_note", reference)
        assert not read.is_error and "safe reference" in str(read.content)
        assert "private-value" not in str(read.content), "rejected edit reached the database"
        edited = await session.call_tool(
            "edit_note", {**reference, "operation": "append", "content": "second reference"},
        )
        assert not edited.is_error, f"clean edit failed: {edited.content}"
        assert await materialized(notes, b"second reference") == file


if __name__ == "__main__":
    destination = ROOT / ".validation"
    destination.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination) as temp:
        asyncio.run(verify(Path(temp)))
    print("Passed MCP create, overwrite, edit, readback and clean retry.")
