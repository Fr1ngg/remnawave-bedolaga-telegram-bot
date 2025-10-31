import logging
from typing import Optional, TYPE_CHECKING

from aiogram import types
from aiogram.fsm.context import FSMContext

from app.localization.texts import get_texts
from app.services.subscription_checkout_service import save_subscription_checkout_draft
from app.states import SubscriptionStates
from app.keyboards.inline import get_subscription_confirm_keyboard

if TYPE_CHECKING:  # pragma: no cover - only for type checking
    from .pricing import _prepare_subscription_summary


logger = logging.getLogger(__name__)


async def present_subscription_summary(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user,
        texts: Optional = None,
) -> bool:
    """Render the subscription purchase summary and switch to the confirmation state.

    Returns ``True`` when the summary is shown successfully and ``False`` if
    calculation failed (an error is shown to the user in this case).
    """

    if texts is None:
        texts = get_texts(db_user.language)

    data = await state.get_data()

    from .pricing import _prepare_subscription_summary

    try:
        summary_text, prepared_data = await _prepare_subscription_summary(db_user, data, texts)
    except ValueError as exc:
        logger.error(
            "Ошибка в расчете цены подписки для пользователя %s: %s",
            db_user.telegram_id,
            exc,
        )
        await callback.answer("Ошибка расчета цены. Обратитесь в поддержку.", show_alert=True)
        return False

    await state.set_data(prepared_data)
    await save_subscription_checkout_draft(db_user.id, prepared_data)

    await callback.message.edit_text(
        summary_text,
        reply_markup=get_subscription_confirm_keyboard(db_user.language),
        parse_mode="HTML",
    )

    await state.set_state(SubscriptionStates.confirming_purchase)
    return True
