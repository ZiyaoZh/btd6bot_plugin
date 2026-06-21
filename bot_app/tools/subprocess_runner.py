from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class AsyncSubprocessRunner(Protocol):
    async def run(
        self,
        cmd: list[str],
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        timeout: float,
    ) -> ProcessResult: ...


class AsyncioSubprocessRunner:
    async def run(
        self,
        cmd: list[str],
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        timeout: float,
    ) -> ProcessResult:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return ProcessResult(
            returncode=int(proc.returncode or 0),
            stdout=stdout.decode("utf-8", errors="ignore"),
            stderr=stderr.decode("utf-8", errors="ignore"),
        )
