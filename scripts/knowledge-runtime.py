"""Project entry point for the pinned local Basic Memory runtime."""

import asyncio
import os
import subprocess
from functools import wraps
from pathlib import Path

from basic_memory import file_utils

VALIDATOR = Path(__file__).with_name("knowledge-privacy.mjs")
NOTES = VALIDATOR.parent.parent / "docs" / "knowledge"


async def validate(content: str | bytes) -> None:
    result = await asyncio.to_thread(
        subprocess.run,
        [os.environ["FINEVENT_NODE"], str(VALIDATOR)],
        input=content if isinstance(content, bytes) else content.encode("utf-8"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        raise ValueError(
            "Knowledge note rejected. Check Markdown, workstation paths and chat metadata."
        )


def guard_file(write):
    @wraps(write)
    async def checked(path, content):
        target = Path(path).resolve()
        if target.suffix.lower() == ".md" and target.is_relative_to(NOTES):
            await validate(content)
        return await write(path, content)

    return checked


def guard_preparation(prepare):
    @wraps(prepare)
    async def checked(*args, **kwargs):
        prepared = await prepare(*args, **kwargs)
        await validate(prepared.markdown_content)
        return prepared

    return checked


if __name__ == "__main__":
    # Install before other modules import the atomic writers (imports and file materialization).
    file_utils.write_file_atomic = guard_file(file_utils.write_file_atomic)
    file_utils.write_file_atomic_bytes = guard_file(file_utils.write_file_atomic_bytes)

    from basic_memory.services.note_preparation import NotePreparation
    from basic_memory.cli.main import app

    # In 0.24.1 note mutations accept the composed Markdown into SQLite before writing the file.
    for operation in ("create", "update", "edit", "move"):
        method = f"prepare_{operation}_entity_content"
        setattr(NotePreparation, method, guard_preparation(getattr(NotePreparation, method)))
    app()
