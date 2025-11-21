import logging
from decimal import Decimal, ROUND_HALF_UP
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.payment_service import PaymentService
from app.external.telegram_stars import TelegramStarsService
from app.database.crud.user import get_user_by_telegram_id
from app.localization.loader import DEFAULT_LANGUAGE
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)


async def handle_pre_checkout_query(query: types.PreCheckoutQuery):
    texts = get_texts(DEFAULT_LANGUAGE)

    try:
        logger.info(
            f"📋 Pre-checkout query от {query.from_user.id}: {query.total_amount} XTR, payload: {query.invoice_payload}"
        )

        allowed_prefixes = ("balance_", "admin_stars_test_", "simple_sub_")

        if not query.invoice_payload or not query.invoice_payload.startswith(allowed_prefixes):
            logger.warning(f"Невалидный payload: {query.invoice_payload}")
            await query.answer(
                ok=False,
                error_message=texts.t(
                    "STARS_PRECHECK_INVALID_PAYLOAD",
                    "Ошибка валидации платежа. Попробуйте еще раз.",
                ),
            )
            return

        try:
            from app.database.database import get_db

            async for db in get_db():
                user = await get_user_by_telegram_id(db, query.from_user.id)
                if not user:
                    logger.warning(f"Пользователь {query.from_user.id} не найден в БД")
                    await query.answer(
                        ok=False,
                        error_message=texts.t(
                            "STARS_PRECHECK_USER_NOT_FOUND",
                            "Пользователь не найден. Обратитесь в поддержку.",
                        ),
                    )
                    return
                texts = get_texts(user.language or DEFAULT_LANGUAGE)
                break
        except Exception as db_error:
            logger.error(f"Ошибка подключения к БД в pre_checkout_query: {db_error}")
            await query.answer(
                ok=False,
                error_message=texts.t(
                    "STARS_PRECHECK_TECHNICAL_ERROR",
                    "Техническая ошибка. Попробуйте позже.",
                ),
            )
            return

        await query.answer(ok=True)
        logger.info(f"✅ Pre-checkout одобрен для пользователя {query.from_user.id}")

    except Exception as e:
        logger.error(f"Ошибка в pre_checkout_query: {e}", exc_info=True)
        await query.answer(
            ok=False,
            error_message=texts.t(
                "STARS_PRECHECK_TECHNICAL_ERROR",
                "Техническая ошибка. Попробуйте позже.",
            ),
        )


async def handle_successful_payment(
    message: types.Message,
    db: AsyncSession,
    state: FSMContext,
    **kwargs
):
    texts = get_texts(DEFAULT_LANGUAGE)

    try:
        payment = message.successful_payment
        user_id = message.from_user.id

        logger.info(
            f"💳 Успешный Stars платеж от {user_id}: "
            f"{payment.total_amount} XTR, "
            f"payload: {payment.invoice_payload}, "
            f"charge_id: {payment.telegram_payment_charge_id}"
        )

        user = await get_user_by_telegram_id(db, user_id)
        texts = get_texts(user.language if user and user.language else DEFAULT_LANGUAGE)

        if not user:
            logger.error(f"Пользователь {user_id} не найден при обработке Stars платежа")
            await message.answer(
                texts.t(
                    "STARS_PAYMENT_USER_NOT_FOUND",
                    "❌ Ошибка: пользователь не найден. Обратитесь в поддержку.",
                )
            )
            return

        payment_service = PaymentService(message.bot)

        state_data = await state.get_data()
        prompt_message_id = state_data.get("stars_prompt_message_id")
        prompt_chat_id = state_data.get("stars_prompt_chat_id", message.chat.id)
        invoice_message_id = state_data.get("stars_invoice_message_id")
        invoice_chat_id = state_data.get("stars_invoice_chat_id", message.chat.id)

        for chat_id, message_id, label in [
            (prompt_chat_id, prompt_message_id, "запрос суммы"),
            (invoice_chat_id, invoice_message_id, "инвойс Stars"),
        ]:
            if message_id:
                try:
                    await message.bot.delete_message(chat_id, message_id)
                except Exception as delete_error:  # pragma: no cover - зависит от прав бота
                    logger.warning(
                        "Не удалось удалить сообщение %s после оплаты Stars: %s",
                        label,
                        delete_error,
                    )

        success = await payment_service.process_stars_payment(
            db=db,
            user_id=user.id,
            stars_amount=payment.total_amount,
            payload=payment.invoice_payload,
            telegram_payment_charge_id=payment.telegram_payment_charge_id
        )

        await state.update_data(
            stars_prompt_message_id=None,
            stars_prompt_chat_id=None,
            stars_invoice_message_id=None,
            stars_invoice_chat_id=None,
        )

        if success:
            rubles_amount = TelegramStarsService.calculate_rubles_from_stars(payment.total_amount)
            amount_kopeks = int((rubles_amount * Decimal(100)).to_integral_value(rounding=ROUND_HALF_UP))
            amount_text = settings.format_price(amount_kopeks).replace(" ₽", "")

            keyboard = await payment_service.build_topup_success_keyboard(user)

            transaction_id_short = payment.telegram_payment_charge_id[:8]

            await message.answer(
                texts.t(
                    "STARS_PAYMENT_SUCCESS",
                    "🎉 <b>Платеж успешно обработан!</b>\n\n"
                    "⭐ Потрачено звезд: {stars_spent}\n"
                    "💰 Зачислено на баланс: {amount} ₽\n"
                    "🆔 ID транзакции: {transaction_id}...\n\n"
                    "⚠️ <b>Важно:</b> Пополнение баланса не активирует подписку автоматически. "
                    "Обязательно активируйте подписку отдельно!\n\n"
                    "🔄 При наличии сохранённой корзины подписки и включенной автопокупке, "
                    "подписка будет приобретена автоматически после пополнения баланса.\n\n"
                    "Спасибо за пополнение! 🚀",
                ).format(
                    stars_spent=payment.total_amount,
                    amount=amount_text,
                    transaction_id=transaction_id_short,
                ),
                parse_mode="HTML",
                reply_markup=keyboard,
            )

            logger.info(
                "✅ Stars платеж успешно обработан: пользователь %s, %s звезд → %s",
                user.id,
                payment.total_amount,
                settings.format_price(amount_kopeks),
            )
        else:
            logger.error(f"Ошибка обработки Stars платежа для пользователя {user.id}")
            await message.answer(
                texts.t(
                    "STARS_PAYMENT_ENROLLMENT_ERROR",
                    "❌ Произошла ошибка при зачислении средств. "
                    "Обратитесь в поддержку, платеж будет проверен вручную.",
                )
            )

    except Exception as e:
        logger.error(f"Ошибка в successful_payment: {e}", exc_info=True)
        await message.answer(
            texts.t(
                "STARS_PAYMENT_PROCESSING_ERROR",
                "❌ Техническая ошибка при обработке платежа. "
                "Обратитесь в поддержку для решения проблемы.",
            )
        )


def register_stars_handlers(dp: Dispatcher):

    dp.pre_checkout_query.register(
        handle_pre_checkout_query,
        F.currency == "XTR"
    )

    dp.message.register(
        handle_successful_payment,
        F.successful_payment
    )

    logger.info("🌟 Зарегистрированы обработчики Telegram Stars платежей")
