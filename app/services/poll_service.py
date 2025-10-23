import asyncio
import logging
from types import SimpleNamespace
from typing import Iterable

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.user import add_user_balance
from app.database.models import (
    Poll,
    PollOption,
    PollQuestion,
    PollResponse,
    TransactionType,
    User,
)
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)


def _build_poll_invitation_text(poll: Poll, user: User) -> str:
    texts = get_texts(user.language)

    lines: list[str] = [f"🗳️ <b>{poll.title}</b>"]
    if poll.description:
        lines.append(poll.description)

    if poll.reward_enabled and poll.reward_amount_kopeks > 0:
        reward_line = texts.t(
            "POLL_INVITATION_REWARD",
            "🎁 За участие вы получите {amount}.",
        ).format(amount=settings.format_price(poll.reward_amount_kopeks))
        lines.append(reward_line)

    lines.append(
        texts.t(
            "POLL_INVITATION_START",
            "Нажмите кнопку ниже, чтобы пройти опрос.",
        )
    )

    return "\n\n".join(lines)


def build_start_keyboard(response_id: int, language: str) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.t("POLL_START_BUTTON", "📝 Пройти опрос"),
                    callback_data=f"poll_start:{response_id}",
                )
            ]
        ]
    )


async def send_poll_to_users(
    bot: Bot,
    db: AsyncSession,
    poll: Poll,
    users: Iterable[User],
) -> dict:
    sent = 0
    failed = 0
    skipped = 0

    poll_id = poll.id
    poll_snapshot = SimpleNamespace(
        title=poll.title,
        description=poll.description,
        reward_enabled=poll.reward_enabled,
        reward_amount_kopeks=poll.reward_amount_kopeks,
    )

    for index, user in enumerate(users, start=1):
        existing_response = await db.execute(
            select(PollResponse.id).where(
                and_(
                    PollResponse.poll_id == poll_id,
                    PollResponse.user_id == user.id,
                )
            )
        )
        if existing_response.scalar_one_or_none():
            skipped += 1
            continue

        response = PollResponse(
            poll_id=poll_id,
            user_id=user.id,
        )
        db.add(response)

        try:
            await db.flush()

            text = _build_poll_invitation_text(poll_snapshot, user)
            keyboard = build_start_keyboard(response.id, user.language)

            await bot.send_message(
                chat_id=user.telegram_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

            await db.commit()
            sent += 1

            if index % 20 == 0:
                await asyncio.sleep(1)
        except Exception as error:  # pragma: no cover - defensive logging
            failed += 1
            logger.error(
                "❌ Ошибка отправки опроса %s пользователю %s: %s",
                poll_id,
                user.telegram_id,
                error,
            )
            await db.rollback()

    return {
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "total": sent + failed + skipped,
    }


async def reward_user_for_poll(
    db: AsyncSession,
    response: PollResponse,
) -> int:
    poll = response.poll
    if not poll.reward_enabled or poll.reward_amount_kopeks <= 0:
        return 0

    if response.reward_given:
        return response.reward_amount_kopeks

    user = response.user
    description = f"Награда за участие в опросе \"{poll.title}\""

    success = await add_user_balance(
        db,
        user,
        poll.reward_amount_kopeks,
        description,
        transaction_type=TransactionType.POLL_REWARD,
    )

    if not success:
        return 0

    response.reward_given = True
    response.reward_amount_kopeks = poll.reward_amount_kopeks
    await db.commit()

    return poll.reward_amount_kopeks


async def get_next_question(response: PollResponse) -> tuple[int | None, PollQuestion | None]:
    if not response.poll or not response.poll.questions:
        return None, None

    answered_question_ids = {answer.question_id for answer in response.answers}
    ordered_questions = sorted(response.poll.questions, key=lambda q: q.order)

    for index, question in enumerate(ordered_questions, start=1):
        if question.id not in answered_question_ids:
            return index, question

    return None, None


async def get_question_option(question: PollQuestion, option_id: int) -> PollOption | None:
    for option in question.options:
        if option.id == option_id:
            return option
    return None
