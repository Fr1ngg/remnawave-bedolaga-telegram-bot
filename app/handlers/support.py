import logging
from aiogram import Dispatcher, types, F
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_support_keyboard
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)


async def show_support_info(
    callback: types.CallbackQuery,
    db_user: User
):
    
    texts = get_texts(db_user.language)
    
    await callback.message.edit_text(
        texts.SUPPORT_INFO,
        reply_markup=get_support_keyboard(db_user.language)
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_support_info,
        F.data == "menu_support"
    )