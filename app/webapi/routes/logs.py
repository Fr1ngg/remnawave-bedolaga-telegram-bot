from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from app.config import settings

from ..dependencies import require_api_token
from ..schemas.logs import LogEntry, LogListResponse


router = APIRouter(prefix="/logs", tags=["logs"])


def _candidates_from_settings() -> list[Path]:
    paths: list[Path] = []
    try:
        configured = (settings.LOG_FILE or "").strip()
        if configured:
            p = Path(configured)
            paths.append(p)
            try:
                paths.append(p.resolve())
            except Exception:
                pass
    except Exception:
        pass
    return paths

LOG_FILE_CANDIDATES = (
    _candidates_from_settings()
    + [Path("app/logs/bot.log"), Path("logs/bot.log"), Path("/logs/bot.log")]  # common locations
)

LINE_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - (?P<logger>[^ ]+) - (?P<level>[A-Z]+) - (?P<message>.*)$")


def _find_log_file(explicit: str | None = None) -> Path | None:
    ordered = []
    if explicit:
        ordered.append(Path(explicit))
        try:
            ordered.append(Path(explicit).resolve())
        except Exception:
            pass
    ordered.extend(LOG_FILE_CANDIDATES)

    for candidate in ordered:
        if candidate.exists():
            return candidate
    # last-resort discovery
    try:
        for base in (Path("."), Path("/app")):
            for found in base.rglob("bot.log"):
                if found.is_file():
                    return found
        # scan locales for any .log files
        for base in (Path("logs"), Path("/logs")):
            if base.exists():
                for found in base.rglob("*.log"):
                    if found.is_file():
                        return found
    except Exception:
        pass
    return None


def _parse_line(line: str) -> LogEntry | None:
    m = LINE_RE.match(line.strip())
    if not m:
        return None
    ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S,%f")
    return LogEntry(
        time=ts,
        logger=m.group("logger"),
        level=m.group("level"),
        message=m.group("message"),
        raw=line.rstrip("\n"),
    )


@router.get("", response_model=LogListResponse)
async def list_logs(
    _: Any = Security(require_api_token),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    level: str | None = Query(None, description="Фильтр по уровню: INFO, WARNING, ERROR"),
    q: str | None = Query(None, description="Поиск по подстроке"),
    path: str | None = Query(None, description="Переопределить путь к файлу лога"),
) -> LogListResponse:
    log_path = _find_log_file(path)
    if not log_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл лога не найден")

    lines: list[str] = []
    try:
        with log_path.open("r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Не удалось прочитать лог: {exc}")

    entries: list[LogEntry] = []
    for line in reversed(lines):  # последние записи первыми
        entry = _parse_line(line)
        if not entry:
            continue
        if level and entry.level.upper() != level.upper():
            continue
        if q and q.lower() not in entry.raw.lower():
            continue
        entries.append(entry)

    total = len(entries)
    slice_items = entries[offset:offset + limit]
    return LogListResponse(items=slice_items, total=total)


