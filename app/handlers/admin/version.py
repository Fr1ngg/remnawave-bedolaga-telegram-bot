import logging
from aiogram import Dispatcher, types, F
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services.version_service import version_service
from app.utils.decorators import admin_required, error_handler
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def cmd_check_updates(
    message: types.Message,
    db_user: User,
    db: AsyncSession
):
    """Принудительно проверяет обновления (только для админов)"""
    
    texts = get_texts(db_user.language)
    
    # Показываем что проверяем
    checking_msg = await message.answer("🔍 Проверяем обновления...")
    
    # Проверяем обновления
    update_available = await version_service.check_for_updates()
    version_info = version_service.get_version_info()
    
    # Удаляем сообщение о проверке
    await checking_msg.delete()
    
    # Формируем результат
    if update_available:
        result_text = f"🔄 <b>Обновление найдено!</b>\n\n"
        result_text += f"Текущая версия: v{version_info['current_version']}\n"
        result_text += f"Новая версия: v{version_info['latest_version']}\n\n"
        result_text += f"Рекомендуется обновить бота."
        
        if version_info["changelog"]:
            # Обрезаем changelog до разумного размера
            changelog = version_info["changelog"][:800]
            if len(version_info["changelog"]) > 800:
                changelog += "..."
            result_text += f"\n\n📝 <b>Что нового:</b>\n{changelog}"
    else:
        result_text = f"✅ <b>Обновления не найдены</b>\n\n"
        result_text += f"Текущая версия: v{version_info['current_version']}\n"
        result_text += f"Бот актуален!"
    
    await message.answer(result_text, parse_mode="HTML")


@admin_required
@error_handler
async def cmd_version_info(
    message: types.Message,
    db_user: User,
    db: AsyncSession
):
    """Показывает подробную информацию о версии (только для админов)"""
    
    texts = get_texts(db_user.language)
    
    # Получаем информацию о версии
    version_info = version_service.get_version_info()
    
    # Проверяем обновления если не проверяли недавно
    if not version_info["latest_version"]:
        await version_service.check_for_updates()
        version_info = version_service.get_version_info()
    
    # Формируем сообщение
    version_text = f"🤖 <b>Информация о версии</b>\n\n"
    version_text += f"📦 <b>Текущая версия:</b> v{version_info['current_version']}\n"
    
    if version_info["latest_version"]:
        version_text += f"🆕 <b>Последняя версия:</b> v{version_info['latest_version']}\n"
        
        if version_info["update_available"]:
            version_text += f"\n🔄 <b>Доступно обновление!</b>\n"
            version_text += f"Рекомендуется обновить бота до версии v{version_info['latest_version']}\n"
            
            if version_info["changelog"]:
                # Обрезаем changelog до разумного размера
                changelog = version_info["changelog"][:1000]
                if len(version_info["changelog"]) > 1000:
                    changelog += "..."
                version_text += f"\n📝 <b>Что нового:</b>\n{changelog}"
        else:
            version_text += f"\n✅ <b>Версия актуальна</b>\n"
    else:
        version_text += f"\n⚠️ <b>Не удалось проверить обновления</b>\n"
    
    # Добавляем информацию о системе
    version_text += f"\n🔧 <b>Системная информация:</b>\n"
    version_text += f"• Python: 3.11+\n"
    version_text += f"• aiogram: 3.7.0\n"
    version_text += f"• SQLAlchemy: 2.0.25\n"
    version_text += f"• PostgreSQL: 15+\n"
    version_text += f"• Redis: 7+\n"
    
    await message.answer(version_text, parse_mode="HTML")


def register_handlers(dp: Dispatcher):
    """Регистрирует обработчики команд версии для админов"""
    
    dp.message.register(
        cmd_check_updates,
        Command("checkupdates")
    )
    logger.info("✅ Зарегистрирована админская команда /checkupdates")
    
    dp.message.register(
        cmd_version_info,
        Command("version")
    )
    logger.info("✅ Зарегистрирована админская команда /version")
