"""Provider interface plus the shared base for executable-backed providers."""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from openapply.providers._process import ProcessResult, ProcessRunner, run_process
from openapply.providers.models import AgentResponse

Which = Callable[[str], str | None]


class AgentProvider(ABC):
    name: str
    display_name: str
    supports_generation: bool = True

    @abstractmethod
    async def is_installed(self) -> bool: ...

    @abstractmethod
    async def get_version(self) -> str | None: ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Installed *and* ready to answer a prompt (authenticated / running)."""

    @abstractmethod
    async def generate(self, prompt: str, *, timeout: float | None = None) -> AgentResponse: ...

    async def status_detail(self) -> str:
        return ""


class CliProvider(AgentProvider):
    """Provider that drives an installed command-line tool as a subprocess."""

    executable: str

    def __init__(
        self,
        *,
        runner: ProcessRunner | None = None,
        which: Which = shutil.which,
        model: str | None = None,
    ) -> None:
        self._runner = runner or run_process
        self._which = which
        self.model = model

    def executable_path(self) -> str | None:
        return self._which(self.executable)

    async def is_installed(self) -> bool:
        return self.executable_path() is not None

    async def _run(
        self,
        args: list[str],
        *,
        stdin_text: str | None = None,
        timeout: float | None = None,
        cwd: Path | None = None,
    ) -> ProcessResult:
        path = self.executable_path()
        if path is None:
            raise RuntimeError(f"{self.executable} not found")
        return await self._runner(
            [path, *args], provider=self.name, stdin_text=stdin_text, timeout=timeout, cwd=cwd
        )

    async def get_version(self) -> str | None:
        if not await self.is_installed():
            return None
        try:
            result = await self._run(["--version"], timeout=15)
        except Exception:
            return None
        if result.return_code != 0:
            return None
        lines = result.stdout.strip().splitlines()
        return lines[0].strip() if lines else None
