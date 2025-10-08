import platform
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple


@dataclass
class StepRecord:
    title: str
    icon: str
    status_label: str
    message: str
    duration: float


class StageHandle:
    def __init__(
        self,
        timeline: "StartupTimeline",
        title: str,
        icon: str,
        success_message: Optional[str],
    ) -> None:
        self.timeline = timeline
        self.title = title
        self.icon = icon
        self.message = success_message or ""
        self.status_icon = "✅"
        self.status_label = "Готово"
        self._explicit_status = False

    def success(self, message: Optional[str] = None) -> None:
        if message is not None:
            self.message = message
        self.status_icon = "✅"
        self.status_label = "Готово"
        self._explicit_status = True

    def warning(self, message: str) -> None:
        self.status_icon = "⚠️"
        self.status_label = "Предупреждение"
        self.message = message
        self._explicit_status = True

    def skip(self, message: str) -> None:
        self.status_icon = "⏭️"
        self.status_label = "Пропущено"
        self.message = message
        self._explicit_status = True

    def failure(self, message: str) -> None:
        self.status_icon = "❌"
        self.status_label = "Ошибка"
        self.message = message
        self._explicit_status = True

    def log(self, message: str, icon: str = "•") -> None:
        self.timeline.logger.info(f"┃ {icon} {message}")


class StartupTimeline:
    def __init__(self, logger: Any, app_name: str) -> None:
        self.logger = logger
        self.app_name = app_name
        self.steps: List[StepRecord] = []

    def _record_step(
        self, title: str, icon: str, status_label: str, message: str, duration: float
    ) -> None:
        self.steps.append(
            StepRecord(
                title=title,
                icon=icon,
                status_label=status_label,
                message=message,
                duration=duration,
            )
        )

    def log_banner(self, metadata: Optional[Sequence[Tuple[str, Any]]] = None) -> None:
        title_text = f"🚀 {self.app_name}"
        subtitle_parts = [f"Python {platform.python_version()}"]
        if metadata:
            for key, value in metadata:
                subtitle_parts.append(f"{key}: {value}")
        subtitle_text = " | ".join(subtitle_parts)

        width = max(len(title_text), len(subtitle_text))
        border = "╔" + "═" * (width + 2) + "╗"
        self.logger.info(border)
        self.logger.info("║ " + title_text.ljust(width) + " ║")
        self.logger.info("║ " + subtitle_text.ljust(width) + " ║")
        self.logger.info("╚" + "═" * (width + 2) + "╝")

    def log_section(self, title: str, lines: Iterable[str], icon: str = "📄") -> None:
        items = [f"{icon} {title}"] + [f"• {line}" for line in lines]
        width = max(len(item) for item in items)
        top = "┌ " + "─" * width + " ┐"
        middle = "├ " + "─" * width + " ┤"
        bottom = "└ " + "─" * width + " ┘"

        self.logger.info(top)
        self.logger.info("│ " + items[0].ljust(width) + " │")
        self.logger.info(middle)
        for item in items[1:]:
            self.logger.info("│ " + item.ljust(width) + " │")
        self.logger.info(bottom)

    def add_manual_step(
        self,
        title: str,
        icon: str,
        status_label: str,
        message: str,
    ) -> None:
        self.logger.info(f"┏ {icon} {title}")
        self.logger.info(f"┗ {icon} {title} — {status_label}: {message}")
        self._record_step(title, icon, status_label, message, 0.0)

    @asynccontextmanager
    async def stage(
        self,
        title: str,
        icon: str = "⚙️",
        description: Optional[str] = None,
        success_message: Optional[str] = "Готово",
    ):
        if description:
            self.logger.info(f"┏ {icon} {title} — {description}")
        else:
            self.logger.info(f"┏ {icon} {title}")

        handle = StageHandle(self, title, icon, success_message)
        start_time = time.perf_counter()
        try:
            yield handle
        except Exception as exc:
            message = str(exc)
            handle.failure(message)
            self.logger.exception(f"┣ ❌ {title} — ошибка: {message}")
            raise
        finally:
            duration = time.perf_counter() - start_time
            if not handle._explicit_status:
                handle.success(handle.message or "Готово")
            self.logger.info(
                f"┗ {handle.status_icon} {title} — {handle.message} [{duration:.2f}s]"
            )
            self._record_step(
                title=title,
                icon=handle.status_icon,
                status_label=handle.status_label,
                message=handle.message,
                duration=duration,
            )

    def log_summary(self) -> None:
        if not self.steps:
            return

        lines = []
        for step in self.steps:
            base = (
                f"{step.icon} {step.title} — {step.status_label}"
                f" [{step.duration:.2f}s]"
            )
            if step.message:
                base += f" :: {step.message}"
            lines.append(base)

        width = max(len(line) for line in lines)
        border_top = "┏" + "━" * (width + 2) + "┓"
        border_mid = "┣" + "━" * (width + 2) + "┫"
        border_bottom = "┗" + "━" * (width + 2) + "┛"
        title = "РЕЗЮМЕ ЗАПУСКА"

        self.logger.info(border_top)
        self.logger.info("┃ " + title.center(width) + " ┃")
        self.logger.info(border_mid)
        for line in lines:
            self.logger.info("┃ " + line.ljust(width) + " ┃")
        self.logger.info(border_bottom)

