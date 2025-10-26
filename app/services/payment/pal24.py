"""Mixin для интеграции с PayPalych (Pal24)."""

from __future__ import annotations

import logging
from datetime import datetime
from importlib import import_module
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import PaymentMethod, TransactionType
from app.services.auto_purchase_service import try_auto_purchase_after_topup
from app.services.pal24_service import Pal24APIError
from app.services.user_cart_service import user_cart_service
from app.utils.user_utils import format_referrer_info

logger = logging.getLogger(__name__)


class Pal24PaymentMixin:
    """Mixin с созданием счетов Pal24, обработкой postback и запросом статуса."""

    async def create_pal24_payment(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        amount_kopeks: int,
        description: str,
        language: str,
        ttl_seconds: Optional[int] = None,
        payer_email: Optional[str] = None,
        payment_method: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Создаёт счёт в Pal24 и сохраняет локальную запись."""
        service = getattr(self, "pal24_service", None)
        if not service or not service.is_configured:
            logger.error("Pal24 сервис не инициализирован")
            return None

        if amount_kopeks < settings.PAL24_MIN_AMOUNT_KOPEKS:
            logger.warning(
                "Сумма Pal24 меньше минимальной: %s < %s",
                amount_kopeks,
                settings.PAL24_MIN_AMOUNT_KOPEKS,
            )
            return None

        if amount_kopeks > settings.PAL24_MAX_AMOUNT_KOPEKS:
            logger.warning(
                "Сумма Pal24 больше максимальной: %s > %s",
                amount_kopeks,
                settings.PAL24_MAX_AMOUNT_KOPEKS,
            )
            return None

        order_id = f"pal24_{user_id}_{uuid.uuid4().hex}"

        custom_payload = {
            "user_id": user_id,
            "amount_kopeks": amount_kopeks,
            "language": language,
        }

        normalized_payment_method = self._normalize_payment_method(payment_method)
        api_payment_method = self._map_api_payment_method(normalized_payment_method)

        payment_module = import_module("app.services.payment_service")

        try:
            response = await service.create_bill(
                amount_kopeks=amount_kopeks,
                user_id=user_id,
                order_id=order_id,
                description=description,
                ttl_seconds=ttl_seconds,
                custom_payload=custom_payload,
                payer_email=payer_email,
                payment_method=api_payment_method,
            )
        except Pal24APIError as error:
            logger.error("Ошибка Pal24 API при создании счета: %s", error)
            return None

        if not response.get("success", True):
            logger.error("Pal24 вернул ошибку при создании счета: %s", response)
            return None

        bill_id = response.get("bill_id")
        if not bill_id:
            logger.error("Pal24 не вернул bill_id: %s", response)
            return None

        def _pick_url(*keys: str) -> Optional[str]:
            for key in keys:
                value = response.get(key)
                if value:
                    return str(value)
            return None

        transfer_url = _pick_url(
            "transfer_url",
            "transferUrl",
            "transfer_link",
            "transferLink",
            "transfer",
            "sbp_url",
            "sbpUrl",
            "sbp_link",
            "sbpLink",
        )
        card_url = _pick_url(
            "link_url",
            "linkUrl",
            "link",
            "card_url",
            "cardUrl",
            "card_link",
            "cardLink",
            "payment_url",
            "paymentUrl",
            "url",
        )
        link_page_url = _pick_url(
            "link_page_url",
            "linkPageUrl",
            "page_url",
            "pageUrl",
        )

        primary_link = transfer_url or link_page_url or card_url
        secondary_link = link_page_url or card_url or transfer_url

        metadata_links = {
            key: value
            for key, value in {
                "sbp": transfer_url,
                "card": card_url,
                "page": link_page_url,
            }.items()
            if value
        }

        metadata_payload = {
            "user_id": user_id,
            "amount_kopeks": amount_kopeks,
            "description": description,
            "links": metadata_links,
            "raw_response": response,
            "selected_method": normalized_payment_method,
        }

        payment = await payment_module.create_pal24_payment(
            db,
            user_id=user_id,
            bill_id=bill_id,
            amount_kopeks=amount_kopeks,
            description=description,
            status=response.get("status", "NEW"),
            type_=response.get("type", "normal"),
            currency=response.get("currency", "RUB"),
            link_url=transfer_url or card_url,
            link_page_url=link_page_url or primary_link,
            order_id=order_id,
            ttl=ttl_seconds,
            metadata=metadata_payload,
        )

        logger.info(
            "Создан Pal24 счет %s для пользователя %s (%s₽)",
            bill_id,
            user_id,
            amount_kopeks / 100,
        )

        payment_status = getattr(payment, "status", response.get("status", "NEW"))

        return {
            "local_payment_id": payment.id,
            "bill_id": bill_id,
            "order_id": order_id,
            "amount_kopeks": amount_kopeks,
            "primary_url": primary_link,
            "secondary_url": secondary_link,
            "link_url": transfer_url,
            "card_url": card_url,
            "payment_method": normalized_payment_method,
            "metadata_links": metadata_links,
            "status": payment_status,
            "sbp_url": transfer_url,
            "transfer_url": transfer_url,
            "link_page_url": link_page_url,
            "payment_url": primary_link,
        }

    async def process_pal24_postback(
        self,
        db: AsyncSession,
        postback: Dict[str, Any],
    ) -> bool:
        """Обрабатывает postback от Pal24 и начисляет баланс при успехе."""
        try:
            payment_module = import_module("app.services.payment_service")

            def _first_non_empty(*values: Optional[str]) -> Optional[str]:
                for value in values:
                    if value:
                        return value
                return None

            payment_id = _first_non_empty(
                postback.get("id"),
                postback.get("TrsId"),
                postback.get("TrsID"),
            )
            bill_id = _first_non_empty(
                postback.get("bill_id"),
                postback.get("billId"),
                postback.get("BillId"),
                postback.get("BillID"),
            )
            order_id = _first_non_empty(
                postback.get("order_id"),
                postback.get("orderId"),
                postback.get("InvId"),
                postback.get("InvID"),
            )
            status = (postback.get("status") or postback.get("Status") or "").upper()

            if not bill_id and not order_id:
                logger.error("Pal24 postback без идентификаторов: %s", postback)
                return False

            payment = None
            if bill_id:
                payment = await payment_module.get_pal24_payment_by_bill_id(db, bill_id)
            if not payment and order_id:
                payment = await payment_module.get_pal24_payment_by_order_id(db, order_id)

            if not payment:
                logger.error("Pal24 платеж не найден: %s / %s", bill_id, order_id)
                return False

            if payment.is_paid:
                logger.info("Pal24 платеж %s уже обработан", payment.bill_id)
                return True

            if status in {"PAID", "SUCCESS", "OVERPAID"}:
                payment = await payment_module.update_pal24_payment_status(
                    db,
                    payment,
                    status=status,
                    is_paid=True,
                    paid_at=datetime.utcnow(),
                    postback_payload=postback,
                    payment_id=payment_id,
                    payment_status=postback.get("Status") or status,
                    payment_method=(
                        postback.get("payment_method")
                        or postback.get("PaymentMethod")
                        or (payment.metadata_json or {}).get("selected_method")
                        or getattr(payment, "payment_method", None)
                    ),
                    balance_amount=postback.get("BalanceAmount")
                    or postback.get("balance_amount"),
                    balance_currency=postback.get("BalanceCurrency")
                    or postback.get("balance_currency"),
                    payer_account=postback.get("AccountNumber")
                    or postback.get("account")
                    or postback.get("Account"),
                )

                return await self._finalize_pal24_payment(
                    db,
                    payment,
                    payment_id=payment_id,
                    trigger="postback",
                )

            await payment_module.update_pal24_payment_status(
                db,
                payment,
                status=status or "UNKNOWN",
                is_paid=False,
                postback_payload=postback,
                payment_id=payment_id,
                payment_status=postback.get("Status") or status,
                payment_method=(
                    postback.get("payment_method")
                    or postback.get("PaymentMethod")
                    or getattr(payment, "payment_method", None)
                ),
                balance_amount=postback.get("BalanceAmount")
                or postback.get("balance_amount"),
                balance_currency=postback.get("BalanceCurrency")
                or postback.get("balance_currency"),
                payer_account=postback.get("AccountNumber")
                or postback.get("account")
                or postback.get("Account"),
            )
            logger.info(
                "Обновили Pal24 платеж %s до статуса %s",
                payment.bill_id,
                status,
            )
            return True

        except Exception as error:
            logger.error("Ошибка обработки Pal24 postback: %s", error, exc_info=True)
            return False

    async def _finalize_pal24_payment(
        self,
        db: AsyncSession,
        payment: Any,
        *,
        payment_id: Optional[str],
        trigger: str,
    ) -> bool:
        """Создаёт транзакцию, начисляет баланс и отправляет уведомления."""

        payment_module = import_module("app.services.payment_service")

        if payment.transaction_id:
            logger.info(
                "Pal24 платеж %s уже привязан к транзакции (trigger=%s)",
                payment.bill_id,
                trigger,
            )
            return True

        user = await payment_module.get_user_by_id(db, payment.user_id)
        if not user:
            logger.error(
                "Пользователь %s не найден для Pal24 платежа %s (trigger=%s)",
                payment.user_id,
                payment.bill_id,
                trigger,
            )
            return False

        transaction = await payment_module.create_transaction(
            db,
            user_id=payment.user_id,
            type=TransactionType.DEPOSIT,
            amount_kopeks=payment.amount_kopeks,
            description=f"Пополнение через Pal24 ({payment_id or payment.bill_id})",
            payment_method=PaymentMethod.PAL24,
            external_id=str(payment_id) if payment_id else payment.bill_id,
            is_completed=True,
        )

        await payment_module.link_pal24_payment_to_transaction(db, payment, transaction.id)

        old_balance = user.balance_kopeks
        was_first_topup = not user.has_made_first_topup

        user.balance_kopeks += payment.amount_kopeks
        user.updated_at = datetime.utcnow()

        promo_group = getattr(user, "promo_group", None)
        subscription = getattr(user, "subscription", None)
        referrer_info = format_referrer_info(user)
        topup_status = "🆕 Первое пополнение" if was_first_topup else "🔄 Пополнение"

        await db.commit()

        try:
            from app.services.referral_service import process_referral_topup

            await process_referral_topup(
                db, user.id, payment.amount_kopeks, getattr(self, "bot", None)
            )
        except Exception as error:
            logger.error(
                "Ошибка обработки реферального пополнения Pal24: %s",
                error,
            )

        if was_first_topup and not user.has_made_first_topup:
            user.has_made_first_topup = True
            await db.commit()

        await db.refresh(user)
        await db.refresh(payment)

        if getattr(self, "bot", None):
            try:
                from app.services.admin_notification_service import (
                    AdminNotificationService,
                )

                notification_service = AdminNotificationService(self.bot)
                await notification_service.send_balance_topup_notification(
                    user,
                    transaction,
                    old_balance,
                    topup_status=topup_status,
                    referrer_info=referrer_info,
                    subscription=subscription,
                    promo_group=promo_group,
                    db=db,
                )
            except Exception as error:
                logger.error(
                    "Ошибка отправки админ уведомления Pal24: %s",
                    error,
                )

        if getattr(self, "bot", None):
            try:
                keyboard = await self.build_topup_success_keyboard(user)
                await self.bot.send_message(
                    user.telegram_id,
                    (
                        "✅ <b>Пополнение успешно!</b>\n\n"
                        f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}\n"
                        "🦊 Способ: PayPalych\n"
                        f"🆔 Транзакция: {transaction.id}\n\n"
                        "Баланс пополнен автоматически!"
                    ),
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            except Exception as error:
                logger.error(
                    "Ошибка отправки уведомления пользователю Pal24: %s",
                    error,
                )

        try:
            from app.services.user_cart_service import user_cart_service
            from aiogram import types

            has_saved_cart = await user_cart_service.has_user_cart(user.id)
            autopurchase_result = await try_auto_purchase_after_topup(db, user, getattr(self, "bot", None))
            if autopurchase_result.triggered:
                logger.info(
                    "Автопокупка после пополнения %s для пользователя %s",
                    "успешна" if autopurchase_result.success else "не выполнена",
                    user.id,
                )
                return True

            if has_saved_cart and getattr(self, "bot", None):
                from app.localization.texts import get_texts

                texts = get_texts(user.language)
                cart_message = texts.t(
                    "BALANCE_TOPUP_CART_REMINDER",
                    "У вас есть незавершенное оформление подписки. Вернуться?",
                )

                keyboard = types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            types.InlineKeyboardButton(
                                text=texts.t(
                                    "BALANCE_TOPUP_CART_BUTTON",
                                    "🛒 Продолжить оформление",
                                ),
                                callback_data="subscription_resume_checkout",
                            )
                        ],
                        [
                            types.InlineKeyboardButton(
                                text="🏠 Главное меню",
                                callback_data="back_to_menu",
                            )
                        ],
                    ]
                )

                await self.bot.send_message(
                    chat_id=user.telegram_id,
                    text=(
                        "✅ Баланс пополнен на "
                        f"{settings.format_price(payment.amount_kopeks)}!\n\n{cart_message}"
                    ),
                    reply_markup=keyboard,
                )
                logger.info(
                    "Отправлено уведомление с кнопкой возврата к оформлению подписки пользователю %s",
                    user.id,
                )
        except Exception as error:
            logger.error(
                "Ошибка при работе с сохраненной корзиной для пользователя %s: %s",
                user.id,
                error,
                exc_info=True,
            )

        logger.info(
            "✅ Обработан Pal24 платеж %s для пользователя %s (trigger=%s)",
            payment.bill_id,
            payment.user_id,
            trigger,
        )

        return True


    async def get_pal24_payment_status(
        self,
        db: AsyncSession,
        local_payment_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Запрашивает актуальный статус платежа у Pal24 и синхронизирует локальную запись."""
        try:
            payment_module = import_module("app.services.payment_service")

            payment = await payment_module.get_pal24_payment_by_id(db, local_payment_id)
            if not payment:
                return None

            remote_status: Optional[str] = None
            remote_payloads: Dict[str, Any] = {}
            payment_info_candidates: List[Dict[str, Optional[str]]] = []

            service = getattr(self, "pal24_service", None)
            if service and payment.bill_id:
                bill_id_str = str(payment.bill_id)
                try:
                    response = await service.get_bill_status(bill_id_str)
                except Pal24APIError as error:
                    logger.error("Ошибка Pal24 API при получении статуса счёта: %s", error)
                else:
                    if response:
                        remote_payloads["bill_status"] = response
                        status_value = response.get("status") or (response.get("bill") or {}).get("status")
                        if status_value:
                            remote_status = str(status_value).upper()
                        extracted = self._extract_remote_payment_info(response)
                        if extracted:
                            payment_info_candidates.append(extracted)

                if payment.payment_id:
                    payment_id_str = str(payment.payment_id)
                    try:
                        payment_response = await service.get_payment_status(payment_id_str)
                    except Pal24APIError as error:
                        logger.error("Ошибка Pal24 API при получении статуса платежа: %s", error)
                    else:
                        if payment_response:
                            remote_payloads["payment_status"] = payment_response
                            extracted = self._extract_remote_payment_info(payment_response)
                            if extracted:
                                payment_info_candidates.append(extracted)

                try:
                    payments_response = await service.get_bill_payments(bill_id_str)
                except Pal24APIError as error:
                    logger.error("Ошибка Pal24 API при получении списка платежей: %s", error)
                else:
                    if payments_response:
                        remote_payloads["bill_payments"] = payments_response
                        for candidate in self._collect_payment_candidates(payments_response):
                            extracted = self._extract_remote_payment_info(candidate)
                            if extracted:
                                payment_info_candidates.append(extracted)

            payment_info = self._select_best_payment_info(payment, payment_info_candidates)
            if payment_info:
                remote_payloads.setdefault("selected_payment", payment_info)

            bill_success = getattr(service, "BILL_SUCCESS_STATES", {"SUCCESS"}) if service else {"SUCCESS"}
            bill_failed = getattr(service, "BILL_FAILED_STATES", {"FAIL"}) if service else {"FAIL"}
            bill_pending = getattr(service, "BILL_PENDING_STATES", {"NEW", "PROCESS"}) if service else {"NEW", "PROCESS"}

            update_status = payment.status or "NEW"
            update_kwargs: Dict[str, Any] = {}
            is_paid_update: Optional[bool] = None

            if remote_status:
                update_status = remote_status
                if remote_status in bill_success:
                    is_paid_update = True
                elif remote_status in bill_failed:
                    is_paid_update = False
                elif remote_status in bill_pending and is_paid_update is None:
                    is_paid_update = False

            payment_status_code: Optional[str] = None
            if payment_info:
                payment_status_code = (payment_info.get("status") or "").upper() or None
                if payment_status_code:
                    existing_status = (getattr(payment, "payment_status", "") or "").upper()
                    if payment_status_code != existing_status:
                        update_kwargs["payment_status"] = payment_status_code

                payment_id_value = payment_info.get("id")
                if payment_id_value and payment_id_value != (payment.payment_id or ""):
                    update_kwargs["payment_id"] = payment_id_value

                method_value = payment_info.get("method")
                if method_value:
                    normalized_method = self._normalize_payment_method(method_value)
                    if normalized_method != (payment.payment_method or ""):
                        update_kwargs["payment_method"] = normalized_method

                balance_amount = payment_info.get("balance_amount")
                if balance_amount and balance_amount != (payment.balance_amount or ""):
                    update_kwargs["balance_amount"] = balance_amount

                balance_currency = payment_info.get("balance_currency")
                if balance_currency and balance_currency != (payment.balance_currency or ""):
                    update_kwargs["balance_currency"] = balance_currency

                payer_account = payment_info.get("account")
                if payer_account and payer_account != (payment.payer_account or ""):
                    update_kwargs["payer_account"] = payer_account

                if payment_status_code:
                    success_states = {"SUCCESS", "OVERPAID"}
                    failed_states = {"FAIL"}
                    pending_states = {"NEW", "PROCESS", "UNDERPAID"}
                    if payment_status_code in success_states:
                        is_paid_update = True
                    elif payment_status_code in failed_states and is_paid_update is not True:
                        is_paid_update = False
                    elif payment_status_code in pending_states and is_paid_update is None:
                        is_paid_update = False

            if not remote_status and payment_status_code:
                update_status = payment_status_code

            if is_paid_update is not None and is_paid_update != bool(payment.is_paid):
                update_kwargs["is_paid"] = is_paid_update
                if is_paid_update and not payment.paid_at:
                    update_kwargs.setdefault("paid_at", datetime.utcnow())

            current_status = payment.status or ""
            effective_status = update_status or current_status or "NEW"
            needs_update = bool(update_kwargs) or effective_status != current_status

            if needs_update:
                payment = await payment_module.update_pal24_payment_status(
                    db,
                    payment,
                    status=effective_status,
                    **update_kwargs,
                )

            remote_status_for_return = remote_status or payment_status_code
            remote_data = remote_payloads or None

            if payment.is_paid and not payment.transaction_id:
                try:
                    finalized = await self._finalize_pal24_payment(
                        db,
                        payment,
                        payment_id=getattr(payment, "payment_id", None),
                        trigger="status_check",
                    )
                    if finalized:
                        payment = await payment_module.get_pal24_payment_by_id(db, local_payment_id)
                except Exception as error:
                    logger.error(
                        "Ошибка автоматического начисления по Pal24 статусу: %s",
                        error,
                        exc_info=True,
                    )

            return {
                "payment": payment,
                "status": payment.status,
                "is_paid": payment.is_paid,
                "remote_status": remote_status_for_return,
                "remote_data": remote_data,
            }

        except Exception as error:
            logger.error("Ошибка получения статуса Pal24: %s", error, exc_info=True)
            return None


    @staticmethod
    def _extract_remote_payment_info(remote_data: Any) -> Dict[str, Optional[str]]:
        """Извлекает данные о платеже из ответа Pal24."""

        def _pick_candidate(value: Any) -> Optional[Dict[str, Any]]:
            if isinstance(value, dict):
                return value
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        return item
            return None

        def _normalize(candidate: Dict[str, Any]) -> Dict[str, Optional[str]]:
            def _stringify(value: Any) -> Optional[str]:
                if value is None:
                    return None
                return str(value)

            return {
                "id": _stringify(candidate.get("id") or candidate.get("payment_id")),
                "status": _stringify(candidate.get("status")),
                "method": _stringify(candidate.get("method") or candidate.get("payment_method")),
                "balance_amount": _stringify(
                    candidate.get("balance_amount")
                    or candidate.get("amount")
                    or candidate.get("BalanceAmount")
                ),
                "balance_currency": _stringify(
                    candidate.get("balance_currency") or candidate.get("BalanceCurrency")
                ),
                "account": _stringify(
                    candidate.get("account")
                    or candidate.get("payer_account")
                    or candidate.get("AccountNumber")
                ),
                "bill_id": _stringify(
                    candidate.get("bill_id")
                    or candidate.get("BillId")
                    or candidate.get("billId")
                ),
            }

        if not isinstance(remote_data, dict):
            return {}

        lower_keys = {str(key).lower() for key in remote_data.keys()}
        has_status = any(key in lower_keys for key in ("status", "payment_status"))
        has_identifier = any(
            key in lower_keys
            for key in ("payment_id", "from_card", "account_amount", "id")
        ) or "bill_id" in lower_keys

        if has_status and has_identifier and "bill" not in lower_keys:
            return _normalize(remote_data)

        search_spaces = [remote_data]
        bill_section = remote_data.get("bill") or remote_data.get("Bill")
        if isinstance(bill_section, dict):
            search_spaces.append(bill_section)

        for space in search_spaces:
            for key in ("payment", "Payment", "payment_info", "PaymentInfo"):
                candidate = _pick_candidate(space.get(key))
                if candidate:
                    return _normalize(candidate)
            for key in ("payments", "Payments"):
                candidate = _pick_candidate(space.get(key))
                if candidate:
                    return _normalize(candidate)

        data_section = remote_data.get("data") or remote_data.get("Data")
        candidate = _pick_candidate(data_section)
        if candidate:
            return _normalize(candidate)

        return {}

    @staticmethod
    def _collect_payment_candidates(remote_data: Any) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []

        def _visit(value: Any) -> None:
            if isinstance(value, dict):
                lower_keys = {str(key).lower() for key in value.keys()}
                has_status = any(key in lower_keys for key in ("status", "payment_status"))
                has_identifier = any(
                    key in lower_keys
                    for key in ("id", "payment_id", "bill_id", "from_card", "account_amount")
                )
                if has_status and has_identifier and value not in candidates:
                    candidates.append(value)
                for nested in value.values():
                    _visit(nested)
            elif isinstance(value, list):
                for item in value:
                    _visit(item)

        _visit(remote_data)
        return candidates

    @staticmethod
    def _select_best_payment_info(
        payment: Any,
        candidates: List[Dict[str, Optional[str]]],
    ) -> Dict[str, Optional[str]]:
        if not candidates:
            return {}

        payment_id = str(getattr(payment, "payment_id", "") or "")
        bill_id = str(getattr(payment, "bill_id", "") or "")

        for candidate in candidates:
            candidate_id = str(candidate.get("id") or "")
            if payment_id and candidate_id == payment_id:
                return candidate

        for candidate in candidates:
            candidate_bill = str(candidate.get("bill_id") or "")
            if bill_id and candidate_bill == bill_id:
                return candidate

        return candidates[0]

    @staticmethod
    def _normalize_payment_method(payment_method: Optional[str]) -> str:
        mapping = {
            "sbp": "sbp",
            "fast": "sbp",
            "fastpay": "sbp",
            "fast_payment": "sbp",
            "card": "card",
            "bank_card": "card",
            "bankcard": "card",
            "bank-card": "card",
        }

        if not payment_method:
            return "sbp"

        normalized = payment_method.strip().lower()
        return mapping.get(normalized, "sbp")

    @staticmethod
    def _map_api_payment_method(normalized_payment_method: str) -> Optional[str]:
        """Преобразует нормализованный метод оплаты в значение для Pal24 API."""

        api_mapping = {
            "sbp": "SBP",
            "card": "BANK_CARD",
        }

        return api_mapping.get(normalized_payment_method)
