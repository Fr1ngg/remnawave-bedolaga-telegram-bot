import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import PromoGroup, User


def _normalize_period_discounts(period_discounts: Optional[Dict[int, int]]) -> Dict[int, int]:
    if not period_discounts:
        return {}

    normalized: Dict[int, int] = {}

    for key, value in period_discounts.items():
        try:
            period = int(key)
            percent = int(value)
        except (TypeError, ValueError):
            continue

        normalized[period] = max(0, min(100, percent))

    return normalized

logger = logging.getLogger(__name__)


async def get_promo_groups_with_counts(
    db: AsyncSession,
    *,
    offset: int = 0,
    limit: Optional[int] = None,
) -> List[Tuple[PromoGroup, int]]:
    query = (
        select(PromoGroup, func.count(User.id))
        .outerjoin(User, User.promo_group_id == PromoGroup.id)
        .group_by(PromoGroup.id)
        .order_by(PromoGroup.is_default.desc(), PromoGroup.name)
    )

    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)

    result = await db.execute(query)
    return result.all()


async def get_auto_assign_promo_groups(db: AsyncSession) -> List[PromoGroup]:
    result = await db.execute(
        select(PromoGroup)
        .where(PromoGroup.auto_assign_total_spent_kopeks.is_not(None))
        .where(PromoGroup.auto_assign_total_spent_kopeks > 0)
        .order_by(PromoGroup.auto_assign_total_spent_kopeks, PromoGroup.id)
    )
    return result.scalars().all()


async def has_auto_assign_promo_groups(db: AsyncSession) -> bool:
    result = await db.execute(
        select(func.count(PromoGroup.id))
        .where(PromoGroup.auto_assign_total_spent_kopeks.is_not(None))
        .where(PromoGroup.auto_assign_total_spent_kopeks > 0)
    )
    return bool(result.scalar_one())


async def get_promo_group_by_id(db: AsyncSession, group_id: int) -> Optional[PromoGroup]:
    return await db.get(PromoGroup, group_id)


async def count_promo_groups(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(PromoGroup.id)))
    return int(result.scalar_one())


async def get_default_promo_group(db: AsyncSession) -> Optional[PromoGroup]:
    result = await db.execute(
        select(PromoGroup).where(PromoGroup.is_default.is_(True))
    )
    return result.scalars().first()


async def create_promo_group(
    db: AsyncSession,
    name: str,
    *,
    server_discount_percent: int,
    traffic_discount_percent: int,
    device_discount_percent: int,
    period_discounts: Optional[Dict[int, int]] = None,
    auto_assign_total_spent_kopeks: Optional[int] = None,
    apply_discounts_to_addons: bool = True,
    is_default: bool = False,
) -> PromoGroup:
    normalized_period_discounts = _normalize_period_discounts(period_discounts)

    auto_assign_total_spent_kopeks = (
        max(0, auto_assign_total_spent_kopeks)
        if auto_assign_total_spent_kopeks is not None
        else None
    )

    existing_default = await get_default_promo_group(db)
    should_be_default = existing_default is None or is_default

    promo_group = PromoGroup(
        name=name.strip(),
        server_discount_percent=max(0, min(100, server_discount_percent)),
        traffic_discount_percent=max(0, min(100, traffic_discount_percent)),
        device_discount_percent=max(0, min(100, device_discount_percent)),
        period_discounts=normalized_period_discounts or None,
        auto_assign_total_spent_kopeks=auto_assign_total_spent_kopeks,
        apply_discounts_to_addons=bool(apply_discounts_to_addons),
        is_default=should_be_default,
    )

    db.add(promo_group)
    await db.flush()

    if should_be_default and existing_default and existing_default.id != promo_group.id:
        await db.execute(
            update(PromoGroup)
            .where(PromoGroup.id != promo_group.id)
            .values(is_default=False)
        )

    await db.commit()
    await db.refresh(promo_group)

    logger.info(
        "Создана промогруппа '%s' (default=%s) с скидками (servers=%s%%, traffic=%s%%, devices=%s%%, periods=%s) и порогом автоприсвоения %s₽, скидки на доп. услуги: %s",
        promo_group.name,
        promo_group.is_default,
        promo_group.server_discount_percent,
        promo_group.traffic_discount_percent,
        promo_group.device_discount_percent,
        normalized_period_discounts,
        (auto_assign_total_spent_kopeks or 0) / 100,
        "on" if promo_group.apply_discounts_to_addons else "off",
    )

    return promo_group


async def update_promo_group(
    db: AsyncSession,
    group: PromoGroup,
    *,
    name: Optional[str] = None,
    server_discount_percent: Optional[int] = None,
    traffic_discount_percent: Optional[int] = None,
    device_discount_percent: Optional[int] = None,
    period_discounts: Optional[Dict[int, int]] = None,
    auto_assign_total_spent_kopeks: Optional[int] = None,
    apply_discounts_to_addons: Optional[bool] = None,
    is_default: Optional[bool] = None,
) -> PromoGroup:
    if name is not None:
        group.name = name.strip()
    if server_discount_percent is not None:
        group.server_discount_percent = max(0, min(100, server_discount_percent))
    if traffic_discount_percent is not None:
        group.traffic_discount_percent = max(0, min(100, traffic_discount_percent))
    if device_discount_percent is not None:
        group.device_discount_percent = max(0, min(100, device_discount_percent))
    if period_discounts is not None:
        normalized_period_discounts = _normalize_period_discounts(period_discounts)
        group.period_discounts = normalized_period_discounts or None
    if auto_assign_total_spent_kopeks is not None:
        group.auto_assign_total_spent_kopeks = max(0, auto_assign_total_spent_kopeks)
    if apply_discounts_to_addons is not None:
        group.apply_discounts_to_addons = bool(apply_discounts_to_addons)

    if is_default is not None:
        if is_default:
            group.is_default = True
            await db.flush()
            await db.execute(
                update(PromoGroup)
                .where(PromoGroup.id != group.id)
                .values(is_default=False)
            )
        else:
            if group.is_default:
                group.is_default = False
                await db.flush()
                replacement = await db.execute(
                    select(PromoGroup)
                    .where(PromoGroup.id != group.id)
                    .order_by(PromoGroup.id)
                    .limit(1)
                )
                new_default = replacement.scalars().first()
                if new_default:
                    await db.execute(
                        update(PromoGroup)
                        .where(PromoGroup.id == new_default.id)
                        .values(is_default=True)
                    )
                else:
                    # Не допускаем состояния без базовой промогруппы
                    group.is_default = True

    await db.commit()
    await db.refresh(group)

    logger.info(
        "Обновлена промогруппа '%s' (id=%s)",
        group.name,
        group.id,
    )
    return group


async def delete_promo_group(db: AsyncSession, group: PromoGroup) -> bool:
    if group.is_default:
        logger.warning("Попытка удалить базовую промогруппу запрещена")
        return False

    default_group = await get_default_promo_group(db)
    if not default_group:
        logger.error("Не найдена базовая промогруппа для reassignment")
        return False


    await db.execute(
        update(User)
        .where(User.promo_group_id == group.id)
        .values(promo_group_id=default_group.id)
    )
    await db.delete(group)
    await db.commit()

    logger.info(
        "Промогруппа '%s' (id=%s) удалена, пользователи переведены в '%s'",
        group.name,
        group.id,
        default_group.name,
    )
    return True


async def get_promo_group_members(
    db: AsyncSession,
    group_id: int,
    *,
    offset: int = 0,
    limit: int = 20,
) -> List[User]:
    result = await db.execute(
        select(User)
        .options(selectinload(User.subscription))
        .where(User.promo_group_id == group_id)
        .order_by(User.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


async def count_promo_group_members(db: AsyncSession, group_id: int) -> int:
    result = await db.execute(
        select(func.count(User.id)).where(User.promo_group_id == group_id)
    )
    return result.scalar_one()
