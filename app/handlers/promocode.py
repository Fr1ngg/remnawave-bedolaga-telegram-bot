import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.states import PromoCodeStates
from app.database.models import User
from app.keyboards.inline import get_back_keyboard
from app.localization.texts import get_texts
from app.services.promocode_service import PromoCodeService
from app.services.admin_notification_service import AdminNotificationService
from app.utils.decorators import error_handler

logger = logging.getLogger(__name__)


@error_handler
async def show_promocode_menu(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    texts = get_texts(db_user.language)
    
    await callback.message.edit_text(
        texts.PROMOCODE_ENTER,
        reply_markup=get_back_keyboard(db_user.language)
    )
    
    await state.set_state(PromoCodeStates.waiting_for_code)
    await callback.answer()


@error_handler
async def process_promocode(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    code = message.text.strip()
    
    if not code:
        await message.answer(
            texts.t(
                "PROMOCODE_EMPTY_INPUT",
                "❌ Введите корректный промокод",
            ),
            reply_markup=get_back_keyboard(db_user.language)
        )
        return
    
    promocode_service = PromoCodeService()
    result = await promocode_service.activate_promocode(db, db_user.id, code)
    
    if result["success"]:
        await message.answer(
            texts.PROMOCODE_SUCCESS.format(description=result["description"]),
            reply_markup=get_back_keyboard(db_user.language)
        )
        logger.info(f"✅ Пользователь {db_user.telegram_id} активировал промокод {code}")

        try:
            notification_service = AdminNotificationService(message.bot)
            await notification_service.send_promocode_activation_notification(
                db,
                db_user,
                result.get("promocode", {"code": code}),
                result["description"],
            )
        except Exception as notify_error:
            logger.error(
                "Ошибка отправки админ уведомления об активации промокода %s: %s",
                code,
                notify_error,
            )
    else:
        error_messages = {
            "not_found": texts.PROMOCODE_INVALID,
            "expired": texts.PROMOCODE_EXPIRED,
            "used": texts.PROMOCODE_USED,
            "already_used_by_user": texts.PROMOCODE_USED,
            "server_error": texts.ERROR
        }
        
        error_text = error_messages.get(result["error"], texts.PROMOCODE_INVALID)
        await message.answer(
            error_text,
            reply_markup=get_back_keyboard(db_user.language)
        )
    
    await state.clear()


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_promocode_menu,
        F.data == "menu_promocode"
    )
    
    dp.message.register(
        process_promocode,
        PromoCodeStates.waiting_for_code
    )