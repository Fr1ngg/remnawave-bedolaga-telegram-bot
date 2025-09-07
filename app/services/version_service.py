import logging
import asyncio
import aiohttp
from typing import Optional, Dict, Any
from pathlib import Path
from packaging import version

from app.config import settings

logger = logging.getLogger(__name__)


class VersionService:
    """Сервис для работы с версиями бота"""
    
    def __init__(self):
        self.current_version = self.get_current_version()
        self.latest_version: Optional[str] = None
        self.update_available = False
        self.changelog: Optional[str] = None
        
    def get_current_version(self) -> str:
        """Получает текущую версию из файла VERSION"""
        try:
            version_file = Path("VERSION")
            if version_file.exists():
                return version_file.read_text().strip()
            else:
                # Fallback - попробуем получить из git
                return self._get_version_from_git()
        except Exception as e:
            logger.error(f"Ошибка получения версии: {e}")
            return "unknown"
    
    def _get_version_from_git(self) -> str:
        """Получает версию из git тегов"""
        try:
            import subprocess
            result = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            logger.debug(f"Не удалось получить версию из git: {e}")
        return "dev"
    
    async def check_for_updates(self) -> bool:
        """Проверяет наличие обновлений"""
        try:
            # Проверяем GitHub API для получения последнего релиза
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.github.com/repos/fr1ngg/remnawave-bedolaga-telegram-bot/releases/latest",
                    timeout=10
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.latest_version = data.get("tag_name", "").lstrip("v")
                        self.changelog = data.get("body", "")
                        
                        # Сравниваем версии
                        if self.latest_version and self.current_version != "unknown":
                            try:
                                current = version.parse(self.current_version)
                                latest = version.parse(self.latest_version)
                                self.update_available = latest > current
                                
                                if self.update_available:
                                    logger.info(f"Доступно обновление: {self.current_version} -> {self.latest_version}")
                                else:
                                    logger.info(f"Версия актуальна: {self.current_version}")
                                    
                            except Exception as e:
                                logger.error(f"Ошибка сравнения версий: {e}")
                                self.update_available = False
                        
                        return self.update_available
                        
        except Exception as e:
            logger.error(f"Ошибка проверки обновлений: {e}")
            return False
    
    def get_version_info(self) -> Dict[str, Any]:
        """Возвращает информацию о версии"""
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "changelog": self.changelog
        }
    
    def get_version_display(self) -> str:
        """Возвращает отформатированную строку версии"""
        if self.update_available and self.latest_version:
            return f"v{self.current_version} → v{self.latest_version} 🔄"
        else:
            return f"v{self.current_version} ✅"
    
    async def start_version_monitoring(self, interval_hours: int = 24):
        """Запускает мониторинг версий"""
        logger.info(f"Запуск мониторинга версий (интервал: {interval_hours}ч)")
        
        while True:
            try:
                await self.check_for_updates()
                await asyncio.sleep(interval_hours * 3600)  # Конвертируем часы в секунды
            except Exception as e:
                logger.error(f"Ошибка в мониторинге версий: {e}")
                await asyncio.sleep(3600)  # Ждем час при ошибке


# Глобальный экземпляр сервиса
version_service = VersionService()
