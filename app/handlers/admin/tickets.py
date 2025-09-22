import logging
import re
from typing import List, Dict, Any
from aiogram import Dispatcher, types, F, Bot
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_
from datetime import datetime, timedelta
import time

from app.database.models import User, Ticket, TicketStatus
from app.database.crud.ticket import TicketCRUD, TicketMessageCRUD
from app.states import TicketStates, AdminTicketStates
from app.keyboards.inline import (
    get_admin_tickets_keyboard,
    get_admin_ticket_view_keyboard,
    get_admin_ticket_reply_cancel_keyboard
)
from app.localization.texts import get_texts
from app.utils.pagination import paginate_list, get_pagination_info
from app.services.admin_notification_service import AdminNotificationService
from app.config import settings
from app.utils.cache import RateLimitCache

logger = logging.getLogger(__name__)



def extract_id_from_callback(callback_data: str) -> int:
    """Извлекает числовой идентификатор из callback_data."""
    match = re.search(r"(\d+)$", callback_data or "")
    if not match:
        raise ValueError(f"ID not found in callback data: {callback_data}")
    return int(match.group(1))


 


async def show_admin_tickets(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Показать все тикеты для админов"""
    texts = get_texts(db_user.language)
    
    # Определяем текущую страницу и scope
    current_page = 1
    scope = "open"
    data_str = callback.data
    if data_str == "admin_tickets_scope_open":
        scope = "open"
    elif data_str == "admin_tickets_scope_closed":
        scope = "closed"
    elif data_str.startswith("admin_tickets_page_"):
        try:
            parts = data_str.split("_")
            # format: admin_tickets_page_{scope}_{page}
            if len(parts) >= 5:
                scope = parts[3]
                current_page = int(parts[4])
            else:
                current_page = int(data_str.replace("admin_tickets_page_", ""))
        except ValueError:
            current_page = 1
    statuses = [TicketStatus.OPEN.value, TicketStatus.ANSWERED.value] if scope == "open" else [TicketStatus.CLOSED.value]
    page_size = 10
    # total count for proper pagination
    total_count = await TicketCRUD.count_tickets_by_statuses(db, statuses)
    total_pages = max(1, (total_count + page_size - 1) // page_size) if total_count > 0 else 1
    if current_page > total_pages:
        current_page = total_pages
    offset = (current_page - 1) * page_size
    tickets = await TicketCRUD.get_tickets_by_statuses(db, statuses=statuses, limit=page_size, offset=offset)
    
    # Даже если тикетов нет, показываем переключатели разделов
    
    # Формируем данные для клавиатуры
    ticket_data = []
    for ticket in tickets:
        user_name = ticket.user.full_name if ticket.user else "Unknown"
        ticket_data.append({
            'id': ticket.id,
            'title': ticket.title,
            'status_emoji': ticket.status_emoji,
            'priority_emoji': ticket.priority_emoji,
            'user_name': user_name,
            'is_closed': ticket.is_closed,
            'locked_emoji': ("🔒" if ticket.is_user_reply_blocked else "")
        })
    
    # Итоговые страницы уже посчитаны выше
    await callback.message.edit_text(
        texts.t("ADMIN_TICKETS_TITLE", "🎫 Все тикеты поддержки:"),
        reply_markup=get_admin_tickets_keyboard(ticket_data, current_page=current_page, total_pages=total_pages, language=db_user.language, scope=scope)
    )
    await callback.answer()


async def view_admin_ticket(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    """Показать детали тикета для админа"""
    texts = get_texts(db_user.language)
    try:
        ticket_id = extract_id_from_callback(callback.data)
    except ValueError:
        await callback.answer(
            texts.t("TICKET_NOT_FOUND", "Тикет не найден."),
            show_alert=True
        )
        return

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=True)

    if not ticket:
        await callback.answer(
            texts.t("TICKET_NOT_FOUND", "Тикет не найден."),
            show_alert=True
        )
        return
    
    texts = get_texts(db_user.language)
    
    # Формируем текст тикета
    status_text = {
        TicketStatus.OPEN.value: texts.t("TICKET_STATUS_OPEN", "Открыт"),
        TicketStatus.ANSWERED.value: texts.t("TICKET_STATUS_ANSWERED", "Отвечен"),
        TicketStatus.CLOSED.value: texts.t("TICKET_STATUS_CLOSED", "Закрыт"),
        TicketStatus.PENDING.value: texts.t("TICKET_STATUS_PENDING", "В ожидании")
    }.get(ticket.status, ticket.status)
    
    user_name = ticket.user.full_name if ticket.user else "Unknown"
    
    ticket_text = f"🎫 Тикет #{ticket.id}\n\n"
    ticket_text += f"👤 Пользователь: {user_name}\n"
    ticket_text += f"📝 Заголовок: {ticket.title}\n"
    ticket_text += f"📊 Статус: {ticket.status_emoji} {status_text}\n"
    ticket_text += f"📅 Создан: {ticket.created_at.strftime('%d.%m.%Y %H:%M')}\n"
    ticket_text += f"🔄 Обновлен: {ticket.updated_at.strftime('%d.%m.%Y %H:%M')}\n\n"
    
    if ticket.is_user_reply_blocked:
        if ticket.user_reply_block_permanent:
            ticket_text += "🚫 Пользователь заблокирован навсегда для ответов в этом тикете\n"
        elif ticket.user_reply_block_until:
            ticket_text += f"⏳ Блок до: {ticket.user_reply_block_until.strftime('%d.%m.%Y %H:%M')}\n"
    
    if ticket.messages:
        ticket_text += f"💬 Сообщения ({len(ticket.messages)}):\n\n"
        
        for msg in ticket.messages:
            sender = "👤 Пользователь" if msg.is_user_message else "🛠️ Поддержка"
            ticket_text += f"{sender} ({msg.created_at.strftime('%d.%m %H:%M')}):\n"
            ticket_text += f"{msg.message_text}\n\n"
            if getattr(msg, "has_media", False) and getattr(msg, "media_type", None) == "photo":
                ticket_text += "📎 Вложение: фото\n\n"
    
    # Добавим кнопку "Вложения", если есть фото
    has_photos = any(getattr(m, "has_media", False) and getattr(m, "media_type", None) == "photo" for m in ticket.messages or [])
    keyboard = get_admin_ticket_view_keyboard(
        ticket_id, 
        ticket.is_closed, 
        db_user.language
    )
    if has_photos:
        try:
            keyboard.inline_keyboard.insert(0, [types.InlineKeyboardButton(text=texts.t("TICKET_ATTACHMENTS", "📎 Вложения"), callback_data=f"admin_ticket_attachments_{ticket_id}")])
        except Exception:
            pass

    # Сначала пробуем отредактировать; если не вышло — удалим и отправим новое
    try:
        await callback.message.edit_text(
            ticket_text,
            reply_markup=keyboard,
        )
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            ticket_text,
            reply_markup=keyboard,
        )
    # сохраняем id для дальнейших действий (ответ/статусы)
    await state.update_data(ticket_id=ticket_id)
    await callback.answer()


async def reply_to_admin_ticket(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User
):
    """Начать ответ на тикет от админа"""
    try:
        ticket_id = extract_id_from_callback(callback.data)
    except ValueError:
        texts = get_texts(db_user.language)
        await callback.answer(
            texts.t("TICKET_NOT_FOUND", "Тикет не найден."),
            show_alert=True
        )
        return

    await state.update_data(ticket_id=ticket_id, reply_mode=True)
    texts = get_texts(db_user.language)
    await callback.message.edit_text(
        texts.t("ADMIN_TICKET_REPLY_INPUT", "Введите ответ от поддержки:"),
        reply_markup=get_admin_ticket_reply_cancel_keyboard(db_user.language)
    )

    await state.set_state(AdminTicketStates.waiting_for_reply)
    await callback.answer()


async def handle_admin_ticket_reply(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    # Проверяем, что пользователь в правильном состоянии
    current_state = await state.get_state()
    if current_state != AdminTicketStates.waiting_for_reply:
        return

    # Анти-спам: одно сообщение за короткое окно по конкретному тикету
    try:
        data_rl = await state.get_data()
        rl_ticket_id = data_rl.get("ticket_id") or "admin_reply"
        limited = await RateLimitCache.is_rate_limited(db_user.id, f"admin_ticket_reply_{rl_ticket_id}", limit=1, window=2)
        if limited:
            return
    except Exception:
        pass
    try:
        data_rl = await state.get_data()
        last_ts = data_rl.get("admin_rl_ts_reply")
        now_ts = time.time()
        if last_ts and (now_ts - float(last_ts)) < 2:
            return
        await state.update_data(admin_rl_ts_reply=now_ts)
    except Exception:
        pass

    """Обработать ответ админа на тикет"""
    # Поддержка фото вложений в ответе админа
    reply_text = (message.text or message.caption or "").strip()
    if len(reply_text) > 400:
        reply_text = reply_text[:400]
    media_type = None
    media_file_id = None
    media_caption = None
    if message.photo:
        media_type = "photo"
        media_file_id = message.photo[-1].file_id
        media_caption = message.caption

    if len(reply_text) < 1 and not media_file_id:
        texts = get_texts(db_user.language)
        await message.answer(
            texts.t("TICKET_REPLY_TOO_SHORT", "Ответ должен содержать минимум 5 символов. Попробуйте еще раз:")
        )
        return

    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    try:
        ticket_id = int(ticket_id) if ticket_id is not None else None
    except (TypeError, ValueError):
        ticket_id = None

    if not ticket_id:
        texts = get_texts(db_user.language)
        await message.answer(
            texts.t("TICKET_REPLY_ERROR", "Ошибка: не найден ID тикета.")
        )
        await state.clear()
        return

    try:
        # Если это режим ввода длительности блокировки
        if not data.get("reply_mode"):
            try:
                minutes = int(reply_text)
                minutes = max(1, min(60*24*365, minutes))
            except ValueError:
                await message.answer("❌ Введите целое число минут")
                return
            until = datetime.utcnow() + timedelta(minutes=minutes)
            ok = await TicketCRUD.set_user_reply_block(db, ticket_id, permanent=False, until=until)
            if ok:
                await message.answer(f"✅ Пользователь заблокирован на {minutes} минут")
            else:
                await message.answer("❌ Ошибка блокировки")
            await state.clear()
            return

        # Обычный режим ответа админа
        ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=False)
        if not ticket:
            texts = get_texts(db_user.language)
            await message.answer(
                texts.t("TICKET_NOT_FOUND", "Тикет не найден.")
            )
            await state.clear()
            return

        # Добавляем сообщение от админа (внутри add_message статус станет ANSWERED)
        await TicketMessageCRUD.add_message(
            db,
            ticket_id,
            db_user.id,
            reply_text,
            is_from_admin=True,
            media_type=media_type,
            media_file_id=media_file_id,
            media_caption=media_caption,
        )

        texts = get_texts(db_user.language)

        await message.answer(
            texts.t("ADMIN_TICKET_REPLY_SENT", "✅ Ответ отправлен!"),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(
                    text=texts.t("VIEW_TICKET", "👁️ Посмотреть тикет"),
                    callback_data=f"admin_view_ticket_{ticket_id}"
                )],
                [types.InlineKeyboardButton(
                    text=texts.t("BACK_TO_TICKETS", "⬅️ К тикетам"),
                    callback_data="admin_tickets"
                )]
            ])
        )

        await state.clear()

        # Уведомляем пользователя о новом ответе
        await notify_user_about_ticket_reply(message.bot, ticket, reply_text, db)
        # Админ-уведомления о ответе в тикет отключены по требованию

    except Exception as e:
        logger.error(f"Error adding admin ticket reply: {e}")
        texts = get_texts(db_user.language)
        await message.answer(
            texts.t("TICKET_REPLY_ERROR", "❌ Произошла ошибка при отправке ответа. Попробуйте позже.")
        )


async def mark_ticket_as_answered(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Отметить тикет как отвеченный"""
    try:
        ticket_id = extract_id_from_callback(callback.data)
    except ValueError:
        texts = get_texts(db_user.language)
        await callback.answer(
            texts.t("TICKET_UPDATE_ERROR", "❌ Ошибка при обновлении тикета."),
            show_alert=True
        )
        return
    
    try:
        success = await TicketCRUD.update_ticket_status(
            db, ticket_id, TicketStatus.ANSWERED.value
        )
        
        if success:
            texts = get_texts(db_user.language)
            await callback.answer(
                texts.t("TICKET_MARKED_ANSWERED", "✅ Тикет отмечен как отвеченный."),
                show_alert=True
            )
            
            # Обновляем сообщение
            await view_admin_ticket(callback, db_user, db)
        else:
            texts = get_texts(db_user.language)
            await callback.answer(
                texts.t("TICKET_UPDATE_ERROR", "❌ Ошибка при обновлении тикета."),
                show_alert=True
            )
            
    except Exception as e:
        logger.error(f"Error marking ticket as answered: {e}")
        texts = get_texts(db_user.language)
        await callback.answer(
            texts.t("TICKET_UPDATE_ERROR", "❌ Ошибка при обновлении тикета."),
            show_alert=True
        )


async def close_admin_ticket(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Закрыть тикет админом"""
    try:
        ticket_id = extract_id_from_callback(callback.data)
    except ValueError:
        texts = get_texts(db_user.language)
        await callback.answer(
            texts.t("TICKET_CLOSE_ERROR", "❌ Ошибка при закрытии тикета."),
            show_alert=True
        )
        return

    try:
        success = await TicketCRUD.close_ticket(db, ticket_id)
        
        if success:
            texts = get_texts(db_user.language)
            await callback.answer(
                texts.t("TICKET_CLOSED", "✅ Тикет закрыт."),
                show_alert=True
            )
            
            # Обновляем inline-клавиатуру в текущем сообщении без кнопок действий
            await callback.message.edit_reply_markup(
                reply_markup=get_admin_ticket_view_keyboard(ticket_id, True, db_user.language)
            )
        else:
            texts = get_texts(db_user.language)
            await callback.answer(
                texts.t("TICKET_CLOSE_ERROR", "❌ Ошибка при закрытии тикета."),
                show_alert=True
            )
            
    except Exception as e:
        logger.error(f"Error closing admin ticket: {e}")
        texts = get_texts(db_user.language)
        await callback.answer(
            texts.t("TICKET_CLOSE_ERROR", "❌ Ошибка при закрытии тикета."),
            show_alert=True
        )


async def cancel_admin_ticket_reply(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User
):
    """Отменить ответ админа на тикет"""
    await state.clear()
    
    texts = get_texts(db_user.language)
    
    await callback.message.edit_text(
        texts.t("TICKET_REPLY_CANCELLED", "Ответ отменен."),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(
                text=texts.t("BACK_TO_TICKETS", "⬅️ К тикетам"),
                callback_data="admin_tickets"
            )]
        ])
    )
    await callback.answer()


async def block_user_in_ticket(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    try:
        ticket_id = extract_id_from_callback(callback.data)
    except ValueError:
        await callback.answer(texts.t("TICKET_NOT_FOUND", "Тикет не найден."), show_alert=True)
        return
    await callback.message.edit_text(
        texts.t("ENTER_BLOCK_MINUTES", "Введите количество минут для блокировки пользователя (например, 15):"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(
                text=texts.t("CANCEL_REPLY", "❌ Отменить ответ"),
                callback_data="cancel_admin_ticket_reply"
            )]
        ])
    )
    await state.update_data(ticket_id=ticket_id)
    await state.set_state(AdminTicketStates.waiting_for_block_duration)
    await callback.answer()


async def handle_admin_block_duration_input(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    # Проверяем состояние
    current_state = await state.get_state()
    if current_state != AdminTicketStates.waiting_for_block_duration:
        return
    
    reply_text = message.text.strip()
    if len(reply_text) < 1:
        await message.answer("❌ Введите целое число минут")
        return
    
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    try:
        minutes = int(reply_text)
        minutes = max(1, min(60*24*365, minutes))  # максимум 1 год
    except ValueError:
        await message.answer("❌ Введите целое число минут")
        return
    
    if not ticket_id:
        texts = get_texts(db_user.language)
        await message.answer(texts.t("TICKET_REPLY_ERROR", "Ошибка: не найден ID тикета."))
        await state.clear()
        return
    
    try:
        ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=False)
        if not ticket:
            texts = get_texts(db_user.language)
            await message.answer(texts.t("TICKET_NOT_FOUND", "Тикет не найден."))
            await state.clear()
            return
        
        until = datetime.utcnow() + timedelta(minutes=minutes)
        ok = await TicketCRUD.set_user_reply_block(db, ticket_id, permanent=False, until=until)
        if ok:
            await message.answer(f"✅ Пользователь заблокирован на {minutes} минут")
        else:
            await message.answer("❌ Ошибка блокировки")
        await state.clear()
        await message.answer(
            "✅ Блокировка установлена. Откройте тикет заново для обновления состояния.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="👁️ Посмотреть тикет", callback_data=f"admin_view_ticket_{ticket_id}")]])
        )
    except Exception as e:
        logger.error(f"Error setting block duration: {e}")
        texts = get_texts(db_user.language)
        await message.answer(texts.t("TICKET_REPLY_ERROR", "❌ Произошла ошибка. Попробуйте позже."))


 


 

async def unblock_user_in_ticket(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    try:
        ticket_id = extract_id_from_callback(callback.data)
    except ValueError:
        texts = get_texts(db_user.language)
        await callback.answer(texts.t("TICKET_NOT_FOUND", "Тикет не найден."), show_alert=True)
        return
    ok = await TicketCRUD.set_user_reply_block(db, ticket_id, permanent=False, until=None)
    if ok:
        await callback.answer("✅ Блок снят")
        await view_admin_ticket(callback, db_user, db, FSMContext(callback.bot, callback.from_user.id))
    else:
        await callback.answer("❌ Ошибка", show_alert=True)


async def block_user_permanently(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    try:
        ticket_id = extract_id_from_callback(callback.data)
    except ValueError:
        texts = get_texts(db_user.language)
        await callback.answer(texts.t("TICKET_NOT_FOUND", "Тикет не найден."), show_alert=True)
        return
    ok = await TicketCRUD.set_user_reply_block(db, ticket_id, permanent=True, until=None)
    if ok:
        await callback.answer("✅ Пользователь заблокирован навсегда")
        await view_admin_ticket(callback, db_user, db, FSMContext(callback.bot, callback.from_user.id))
    else:
        await callback.answer("❌ Ошибка", show_alert=True)


async def notify_user_about_ticket_reply(bot: Bot, ticket: Ticket, reply_text: str, db: AsyncSession):
    """Уведомить пользователя о новом ответе в тикете"""
    try:
        from app.localization.texts import get_texts
        
        # Получаем тикет с пользователем
        ticket_with_user = await TicketCRUD.get_ticket_by_id(db, ticket.id, load_user=True)
        if not ticket_with_user or not ticket_with_user.user:
            logger.error(f"User not found for ticket #{ticket.id}")
            return
        
        texts = get_texts(ticket_with_user.user.language)
        
        # Формируем уведомление
        base_text = texts.t(
            "TICKET_REPLY_NOTIFICATION", 
            "🎫 Получен ответ по тикету #{ticket_id}\n\n{reply_preview}\n\nНажмите кнопку ниже, чтобы перейти к тикету:"
        ).format(
            ticket_id=ticket.id,
            reply_preview=reply_text[:100] + "..." if len(reply_text) > 100 else reply_text
        )
        # Если было фото в последнем ответе админа — отправим как фото
        last_message = await TicketMessageCRUD.get_last_message(db, ticket.id)
        if last_message and last_message.has_media and last_message.media_type == "photo" and last_message.is_from_admin:
            caption = base_text
            try:
                await bot.send_photo(
                    chat_id=ticket_with_user.user.telegram_id,
                    photo=last_message.media_file_id,
                    caption=caption,
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(text=texts.t("VIEW_TICKET", "👁️ Посмотреть тикет"), callback_data=f"view_ticket_{ticket.id}")],
                        [types.InlineKeyboardButton(text=texts.t("CLOSE_NOTIFICATION", "❌ Закрыть уведомление"), callback_data=f"close_ticket_notification_{ticket.id}")]
                    ])
                )
                return
            except Exception as e:
                logger.error(f"Не удалось отправить фото-уведомление: {e}")
        # Фоллбек: текстовое уведомление
        await bot.send_message(
            chat_id=ticket_with_user.user.telegram_id,
            text=base_text,
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text=texts.t("VIEW_TICKET", "👁️ Посмотреть тикет"), callback_data=f"view_ticket_{ticket.id}")],
                [types.InlineKeyboardButton(text=texts.t("CLOSE_NOTIFICATION", "❌ Закрыть уведомление"), callback_data=f"close_ticket_notification_{ticket.id}")]
            ])
        )
        
        logger.info(f"Ticket #{ticket.id} reply notification sent to user {ticket_with_user.user.telegram_id}")
        
    except Exception as e:
        logger.error(f"Error notifying user about ticket reply: {e}")


def register_handlers(dp: Dispatcher):
    """Регистрация админских обработчиков тикетов"""
    
    # Просмотр тикетов
    dp.callback_query.register(show_admin_tickets, F.data == "admin_tickets")
    dp.callback_query.register(show_admin_tickets, F.data == "admin_tickets_scope_open")
    dp.callback_query.register(show_admin_tickets, F.data == "admin_tickets_scope_closed")
    
    dp.callback_query.register(view_admin_ticket, F.data.startswith("admin_view_ticket_"))
    
    # Ответы на тикеты
    dp.callback_query.register(
        reply_to_admin_ticket,
        F.data.startswith("admin_reply_ticket_")
    )
    
    dp.message.register(handle_admin_ticket_reply, AdminTicketStates.waiting_for_reply)
    dp.message.register(handle_admin_block_duration_input, AdminTicketStates.waiting_for_block_duration)
    
    # Управление статусами: явная кнопка больше не используется (статус меняется автоматически)
    
    dp.callback_query.register(
        close_admin_ticket,
        F.data.startswith("admin_close_ticket_")
    )
    dp.callback_query.register(block_user_in_ticket, F.data.startswith("admin_block_user_ticket_"))
    dp.callback_query.register(unblock_user_in_ticket, F.data.startswith("admin_unblock_user_ticket_"))
    dp.callback_query.register(block_user_permanently, F.data.startswith("admin_block_user_perm_ticket_"))
    
    # Отмена операций
    dp.callback_query.register(
        cancel_admin_ticket_reply,
        F.data == "cancel_admin_ticket_reply"
    )
    
    # Пагинация админских тикетов
    dp.callback_query.register(show_admin_tickets, F.data.startswith("admin_tickets_page_"))

    # Управление компоновкой ответа — (отключено)

    # Вложения в тикете (админ)
    async def send_admin_ticket_attachments(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
    ):
        texts = get_texts(db_user.language)
        try:
            ticket_id = extract_id_from_callback(callback.data)
        except ValueError:
            await callback.answer(texts.t("TICKET_NOT_FOUND", "Тикет не найден."), show_alert=True)
            return
        ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True)
        if not ticket:
            await callback.answer(texts.t("TICKET_NOT_FOUND", "Тикет не найден."), show_alert=True)
            return
        photos = [m.media_file_id for m in ticket.messages if getattr(m, "has_media", False) and getattr(m, "media_type", None) == "photo" and m.media_file_id]
        if not photos:
            await callback.answer(texts.t("NO_ATTACHMENTS", "Вложений нет."), show_alert=True)
            return
        from aiogram.types import InputMediaPhoto
        chunks = [photos[i:i+10] for i in range(0, len(photos), 10)]
        last_group_message = None
        for chunk in chunks:
            media = [InputMediaPhoto(media=pid) for pid in chunk]
            try:
                messages = await callback.message.bot.send_media_group(chat_id=callback.from_user.id, media=media)
                if messages:
                    last_group_message = messages[-1]
            except Exception:
                pass
        # После отправки добавим кнопку удалить под последним сообщением группы
        if last_group_message:
            try:
                kb = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text=texts.t("DELETE_MESSAGE", "🗑 Удалить"), callback_data=f"admin_delete_message_{last_group_message.message_id}")]])
                await callback.message.bot.send_message(chat_id=callback.from_user.id, text=texts.t("ATTACHMENTS_SENT", "Вложения отправлены."), reply_markup=kb)
            except Exception:
                await callback.answer(texts.t("ATTACHMENTS_SENT", "Вложения отправлены."))
        else:
            await callback.answer(texts.t("ATTACHMENTS_SENT", "Вложения отправлены."))

    dp.callback_query.register(send_admin_ticket_attachments, F.data.startswith("admin_ticket_attachments_"))

    async def admin_delete_message(
        callback: types.CallbackQuery
    ):
        try:
            msg_id = extract_id_from_callback(callback.data)
        except ValueError:
            await callback.answer("❌")
            return
        try:
            await callback.message.bot.delete_message(chat_id=callback.from_user.id, message_id=msg_id)
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer("✅")

    dp.callback_query.register(admin_delete_message, F.data.startswith("admin_delete_message_"))

