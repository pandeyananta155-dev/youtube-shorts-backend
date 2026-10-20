import math
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from app.config import Settings


class ScriptProvider(Protocol):
    def generate(self, title: str, commentary: str | None, category: str | None) -> str: ...


class CaptionProvider(Protocol):
    def generate(self, script: str, duration_seconds: int) -> str: ...


class NarrationProvider(Protocol):
    def generate(self, text: str, output_path: Path) -> dict[str, str | int]: ...


class TemplateScriptProvider:
    def __init__(self, templates: dict[str, str] | None = None):
        self.templates = templates or {}

    def generate(self, title: str, commentary: str | None, category: str | None) -> str:
        original_commentary = (commentary or f"A brief original overview of {title}.").strip()
        template = self.templates.get((category or "").lower(), "Original commentary: {commentary}")
        rendered = template.replace("{title}", title).replace("{commentary}", original_commentary)
        if not rendered.strip():
            raise ValueError("Configured category template produced an empty script")
        return rendered.strip()


class TemplateCaptionProvider:
    def __init__(self, words_per_caption: int = 7):
        self.words_per_caption = words_per_caption

    def generate(self, script: str, duration_seconds: int) -> str:
        words = script.split()
        if not words:
            raise ValueError("Cannot create captions from an empty script")
        groups = [words[index:index + self.words_per_caption] for index in range(0, len(words), self.words_per_caption)]
        interval = duration_seconds / len(groups)
        rows = []
        for index, group in enumerate(groups):
            start = timedelta(seconds=interval * index)
            end = timedelta(seconds=interval * (index + 1))
            rows.extend([str(index + 1), f"{self._timestamp(start)} --> {self._timestamp(end)}", " ".join(group), ""])
        return "\n".join(rows)

    @staticmethod
    def _timestamp(value: timedelta) -> str:
        total_ms = max(0, int(value.total_seconds() * 1000))
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, milliseconds = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


class EspeakNarrationProvider:
    def __init__(self, settings: Settings, runner=subprocess.run):
        self.settings = settings
        self.runner = runner

    def generate(self, text: str, output_path: Path) -> dict[str, str | int]:
        binary = shutil.which(self.settings.espeak_binary)
        if not binary:
            raise RuntimeError(
                "Local narration was requested, but eSpeak NG is not installed. "
                "Install espeak-ng or disable narration."
            )
        command = [
            binary,
            "-v", self.settings.narration_voice,
            "-s", str(self.settings.narration_speed),
            "-w", str(output_path),
            text,
        ]
        result = self.runner(command, capture_output=True, text=True, timeout=60, check=False)
        if result.returncode != 0 or not output_path.is_file():
            raise RuntimeError("eSpeak NG could not generate narration")
        return {
            "provider": "espeak-ng",
            "voice": self.settings.narration_voice,
            "speed": self.settings.narration_speed,
        }