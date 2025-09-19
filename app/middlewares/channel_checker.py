import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import TelegramObject, Update, Message, CallbackQuery
from aiogram.enums import ChatMemberStatus

from app.config import settings
from app.keyboards.inline import get_channel_sub_keyboard
from app.localization.loader import DEFAULT_LANGUAGE
from app.localization.texts import get_texts
from app.utils.check_reg_process import is_registration_process

logger = logging.getLogger(__name__)


class ChannelCheckerMiddleware(BaseMiddleware):
    def __init__(self):
        self.BAD_MEMBER_STATUS = (
            ChatMemberStatus.LEFT,
            ChatMemberStatus.KICKED,
            ChatMemberStatus.RESTRICTED
        )
        self.GOOD_MEMBER_STATUS = (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR
        )
        logger.info("🔧 ChannelCheckerMiddleware инициализирован")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        telegram_id = None
        if isinstance(event, (Message, CallbackQuery)):
            telegram_id = event.from_user.id
        elif isinstance(event, Update):
            if event.message:
                telegram_id = event.message.from_user.id
            elif event.callback_query:
                telegram_id = event.callback_query.from_user.id

        if telegram_id is None:
            logger.debug("❌ telegram_id не найден, пропускаем")
            return await handler(event, data)


        state: FSMContext = data.get('state')
        current_state = None

        if state:
            current_state = await state.get_state()


        is_reg_process = is_registration_process(event, current_state)

        if is_reg_process:
            logger.debug("✅ Событие разрешено (процесс регистрации), пропускаем проверку")
            return await handler(event, data)

        bot: Bot = data["bot"]

        channel_id = settings.CHANNEL_SUB_ID
        
        if not channel_id:
            logger.warning("⚠️ CHANNEL_SUB_ID не установлен, пропускаем проверку")
            return await handler(event, data)

        is_required = settings.CHANNEL_IS_REQUIRED_SUB
        
        if not is_required:
            logger.debug("⚠️ Обязательная подписка отключена, пропускаем проверку")
            return await handler(event, data)

        channel_link = settings.CHANNEL_LINK
        
        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=telegram_id)
            
            if member.status in self.GOOD_MEMBER_STATUS:
                return await handler(event, data)
            elif member.status in self.BAD_MEMBER_STATUS:
                logger.info(f"❌ Пользователь {telegram_id} не подписан на канал (статус: {member.status})")
                
                if isinstance(event, CallbackQuery) and event.data == "sub_channel_check":
                    await event.answer("❌ Вы еще не подписались на канал! Подпишитесь и попробуйте снова.", show_alert=True)
                    return 
                
                return await self._deny_message(event, bot, channel_link)
            else:
                logger.warning(f"⚠️ Неожиданный статус пользователя {telegram_id}: {member.status}")
                return await self._deny_message(event, bot, channel_link)
                
        except TelegramForbiddenError as e:
            logger.error(f"❌ Бот заблокирован в канале {channel_id}: {e}")
            return await self._deny_message(event, bot, channel_link)
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower():
                logger.error(f"❌ Канал {channel_id} не найден: {e}")
            elif "user not found" in str(e).lower():
                logger.error(f"❌ Пользователь {telegram_id} не найден: {e}")
            else:
                logger.error(f"❌ Ошибка запроса к каналу {channel_id}: {e}")
            return await self._deny_message(event, bot, channel_link)
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка при проверке подписки: {e}")
            return await handler(event, data)

    @staticmethod
    async def _deny_message(event: TelegramObject, bot: Bot, channel_link: str):
        logger.debug("🚫 Отправляем сообщение о необходимости подписки")

        user = None
        if isinstance(event, (Message, CallbackQuery)):
            user = getattr(event, "from_user", None)
        elif isinstance(event, Update):
            if event.message and event.message.from_user:
                user = event.message.from_user
            elif event.callback_query and event.callback_query.from_user:
                user = event.callback_query.from_user

        language = DEFAULT_LANGUAGE
        if user and user.language_code:
            language = user.language_code.split('-')[0]

        texts = get_texts(language)
        channel_sub_kb = get_channel_sub_keyboard(channel_link, language=language)
        text = texts.t(
            "CHANNEL_REQUIRED_TEXT",
            "🔒 Для использования бота подпишитесь на новостной канал, чтобы получать уведомления о новых возможностях и обновлениях бота. Спасибо!",
        )

        try:
            if isinstance(event, Message):
                return await event.answer(text, reply_markup=channel_sub_kb)
            elif isinstance(event, CallbackQuery):
                return await event.message.edit_text(text, reply_markup=channel_sub_kb)
            elif isinstance(event, Update) and event.message:
                return await bot.send_message(event.message.chat.id, text, reply_markup=channel_sub_kb)
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке сообщения о подписке: {e}")
