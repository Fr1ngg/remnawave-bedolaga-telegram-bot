import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    Subscription,
    SubscriptionStatus,
    User,
    SubscriptionServer,
    PromoGroup,
)
from app.database.crud.notification import clear_notifications
from app.utils.pricing_utils import calculate_months_from_days, get_remaining_months
from app.config import settings

logger = logging.getLogger(__name__)


async def get_subscription_by_user_id(db: AsyncSession, user_id: int) -> Optional[Subscription]:
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.user))
        .where(Subscription.user_id == user_id)
        .order_by(Subscription.created_at.desc())
        .limit(1) 
    )
    subscription = result.scalar_one_or_none()
    
    if subscription:
        subscription = await check_and_update_subscription_status(db, subscription)
    
    return subscription


async def create_trial_subscription(
    db: AsyncSession,
    user_id: int,
    duration_days: int = None,
    traffic_limit_gb: int = None,
    device_limit: int = None,
    squad_uuid: str = None,
    connected_squads: Optional[List[str]] = None,
    traffic_reset_strategy: Optional[str] = None
) -> Subscription:

    duration_days = settings.TRIAL_DURATION_DAYS if duration_days is None else duration_days
    traffic_limit_gb = settings.TRIAL_TRAFFIC_LIMIT_GB if traffic_limit_gb is None else traffic_limit_gb
    device_limit = settings.TRIAL_DEVICE_LIMIT if device_limit is None else device_limit
    default_squad = squad_uuid or settings.TRIAL_SQUAD_UUID

    squads: List[str] = []
    if connected_squads is not None:
        squads = [uuid for uuid in connected_squads if uuid]
    elif default_squad:
        squads = [default_squad]

    reset_strategy = (traffic_reset_strategy or settings.DEFAULT_TRAFFIC_RESET_STRATEGY or "NO_RESET").upper()
    if reset_strategy not in {"NO_RESET", "DAY", "WEEK", "MONTH"}:
        reset_strategy = "NO_RESET"

    end_date = datetime.utcnow() + timedelta(days=duration_days)

    subscription = Subscription(
        user_id=user_id,
        status=SubscriptionStatus.ACTIVE.value,
        is_trial=True,
        start_date=datetime.utcnow(),
        end_date=end_date,
        traffic_limit_gb=traffic_limit_gb,
        device_limit=device_limit,
        connected_squads=squads,
        traffic_reset_strategy=reset_strategy
    )
    
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"🎁 Создана триальная подписка для пользователя {user_id}")
    return subscription


async def create_paid_subscription(
    db: AsyncSession,
    user_id: int,
    duration_days: int,
    traffic_limit_gb: int = 0, 
    device_limit: int = 1,
    connected_squads: List[str] = None
) -> Subscription:
    
    end_date = datetime.utcnow() + timedelta(days=duration_days)
    
    subscription = Subscription(
        user_id=user_id,
        status=SubscriptionStatus.ACTIVE.value,
        is_trial=False,
        start_date=datetime.utcnow(),
        end_date=end_date,
        traffic_limit_gb=traffic_limit_gb,
        device_limit=device_limit,
        connected_squads=connected_squads or [],
        traffic_reset_strategy=settings.DEFAULT_TRAFFIC_RESET_STRATEGY.upper()
    )
    
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"💎 Создана платная подписка для пользователя {user_id}")
    return subscription


async def extend_subscription(
    db: AsyncSession,
    subscription: Subscription,
    days: int
) -> Subscription:
    current_time = datetime.utcnow()
    
    logger.info(f"🔄 Продление подписки {subscription.id} на {days} дней")
    logger.info(f"📊 Текущие параметры: статус={subscription.status}, окончание={subscription.end_date}")
    
    if subscription.end_date > current_time:
        subscription.end_date = subscription.end_date + timedelta(days=days)
        logger.info(f"📅 Подписка активна, добавляем {days} дней к текущей дате окончания")
    else:
        subscription.end_date = current_time + timedelta(days=days)
        logger.info(f"📅 Подписка истекла, устанавливаем новую дату окончания")
    
    if subscription.status == SubscriptionStatus.EXPIRED.value:
        subscription.status = SubscriptionStatus.ACTIVE.value
        logger.info(f"🔄 Статус изменён с EXPIRED на ACTIVE")
    
    subscription.updated_at = current_time
    
    await db.commit()
    await db.refresh(subscription)
    await clear_notifications(db, subscription.id)

    logger.info(f"✅ Подписка продлена до: {subscription.end_date}")
    logger.info(f"📊 Новые параметры: статус={subscription.status}, окончание={subscription.end_date}")

    return subscription


async def add_subscription_traffic(
    db: AsyncSession,
    subscription: Subscription,
    gb: int
) -> Subscription:
    
    subscription.add_traffic(gb)
    subscription.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"📈 К подписке пользователя {subscription.user_id} добавлено {gb} ГБ трафика")
    return subscription


async def add_subscription_devices(
    db: AsyncSession,
    subscription: Subscription,
    devices: int
) -> Subscription:
    
    subscription.device_limit += devices
    subscription.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"📱 К подписке пользователя {subscription.user_id} добавлено {devices} устройств")
    return subscription


async def add_subscription_squad(
    db: AsyncSession,
    subscription: Subscription,
    squad_uuid: str
) -> Subscription:
    
    if squad_uuid not in subscription.connected_squads:
        subscription.connected_squads = subscription.connected_squads + [squad_uuid]
        subscription.updated_at = datetime.utcnow()
        
        await db.commit()
        await db.refresh(subscription)
        
        logger.info(f"🌍 К подписке пользователя {subscription.user_id} добавлен сквад {squad_uuid}")
    
    return subscription


async def remove_subscription_squad(
    db: AsyncSession,
    subscription: Subscription,
    squad_uuid: str
) -> Subscription:
    
    if squad_uuid in subscription.connected_squads:
        squads = subscription.connected_squads.copy()
        squads.remove(squad_uuid)
        subscription.connected_squads = squads
        subscription.updated_at = datetime.utcnow()
        
        await db.commit()
        await db.refresh(subscription)
        
        logger.info(f"🚫 Из подписки пользователя {subscription.user_id} удален сквад {squad_uuid}")
    
    return subscription


async def update_subscription_autopay(
    db: AsyncSession,
    subscription: Subscription,
    enabled: bool,
    days_before: int = 3
) -> Subscription:
    
    subscription.autopay_enabled = enabled
    subscription.autopay_days_before = days_before
    subscription.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(subscription)
    
    status = "включен" if enabled else "выключен"
    logger.info(f"💳 Автоплатеж для подписки пользователя {subscription.user_id} {status}")
    return subscription


async def deactivate_subscription(
    db: AsyncSession,
    subscription: Subscription
) -> Subscription:
    
    subscription.status = SubscriptionStatus.DISABLED.value
    subscription.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"❌ Подписка пользователя {subscription.user_id} деактивирована")
    return subscription


async def get_expiring_subscriptions(
    db: AsyncSession,
    days_before: int = 3
) -> List[Subscription]:
    
    threshold_date = datetime.utcnow() + timedelta(days=days_before)
    
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.user))
        .where(
            and_(
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.end_date <= threshold_date,
                Subscription.end_date > datetime.utcnow()
            )
        )
    )
    return result.scalars().all()


async def get_expired_subscriptions(db: AsyncSession) -> List[Subscription]:
    
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.user))
        .where(
            and_(
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.end_date <= datetime.utcnow()
            )
        )
    )
    return result.scalars().all()


async def get_subscriptions_for_autopay(db: AsyncSession) -> List[Subscription]:
    current_time = datetime.utcnow()
    
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.user))
        .where(
            and_(
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.autopay_enabled == True,
                Subscription.is_trial == False 
            )
        )
    )
    all_autopay_subscriptions = result.scalars().all()
    
    ready_for_autopay = []
    for subscription in all_autopay_subscriptions:
        days_until_expiry = (subscription.end_date - current_time).days
        
        if days_until_expiry <= subscription.autopay_days_before and subscription.end_date > current_time:
            ready_for_autopay.append(subscription)
    
    return ready_for_autopay


async def get_subscriptions_statistics(db: AsyncSession) -> dict:
    
    total_result = await db.execute(select(func.count(Subscription.id)))
    total_subscriptions = total_result.scalar()
    
    active_result = await db.execute(
        select(func.count(Subscription.id))
        .where(Subscription.status == SubscriptionStatus.ACTIVE.value)
    )
    active_subscriptions = active_result.scalar()
    
    trial_result = await db.execute(
        select(func.count(Subscription.id))
        .where(
            and_(
                Subscription.is_trial == True,
                Subscription.status == SubscriptionStatus.ACTIVE.value
            )
        )
    )
    trial_subscriptions = trial_result.scalar()
    
    paid_subscriptions = active_subscriptions - trial_subscriptions
    
    today = datetime.utcnow().date()
    today_result = await db.execute(
        select(func.count(Subscription.id))
        .where(
            and_(
                Subscription.created_at >= today,
                Subscription.is_trial == False
            )
        )
    )
    purchased_today = today_result.scalar()
    
    week_ago = datetime.utcnow() - timedelta(days=7)
    week_result = await db.execute(
        select(func.count(Subscription.id))
        .where(
            and_(
                Subscription.created_at >= week_ago,
                Subscription.is_trial == False
            )
        )
    )
    purchased_week = week_result.scalar()
    
    month_ago = datetime.utcnow() - timedelta(days=30)
    month_result = await db.execute(
        select(func.count(Subscription.id))
        .where(
            and_(
                Subscription.created_at >= month_ago,
                Subscription.is_trial == False
            )
        )
    )
    purchased_month = month_result.scalar()
    
    try:
        from app.database.crud.subscription_conversion import get_conversion_statistics
        conversion_stats = await get_conversion_statistics(db)
        
        trial_to_paid_conversion = conversion_stats.get("conversion_rate", 0)
        renewals_count = conversion_stats.get("month_conversions", 0)
        
        logger.info(f"📊 Статистика конверсии из таблицы conversions:")
        logger.info(f"   Общее количество конверсий: {conversion_stats.get('total_conversions', 0)}")
        logger.info(f"   Процент конверсии: {trial_to_paid_conversion}%")
        logger.info(f"   Конверсий за месяц: {renewals_count}")
        
    except ImportError:
        logger.warning("⚠️ Таблица subscription_conversions не найдена, используем старую логику")
        
        users_with_paid_result = await db.execute(
            select(func.count(User.id))
            .where(User.has_had_paid_subscription == True)
        )
        users_with_paid = users_with_paid_result.scalar()
        
        total_users_result = await db.execute(select(func.count(User.id)))
        total_users = total_users_result.scalar()
        
        if total_users > 0:
            trial_to_paid_conversion = round((users_with_paid / total_users) * 100, 1)
        else:
            trial_to_paid_conversion = 0
            
        renewals_count = 0
    
    return {
        "total_subscriptions": total_subscriptions,
        "active_subscriptions": active_subscriptions,
        "trial_subscriptions": trial_subscriptions,
        "paid_subscriptions": paid_subscriptions,
        "purchased_today": purchased_today,
        "purchased_week": purchased_week,
        "purchased_month": purchased_month,
        "trial_to_paid_conversion": trial_to_paid_conversion, 
        "renewals_count": renewals_count 
    }

async def update_subscription_usage(
    db: AsyncSession,
    subscription: Subscription,
    used_gb: float
) -> Subscription:
    subscription.traffic_used_gb = used_gb
    subscription.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(subscription)
    
    return subscription

async def get_all_subscriptions(
    db: AsyncSession, 
    page: int = 1, 
    limit: int = 10
) -> Tuple[List[Subscription], int]:
    count_result = await db.execute(
        select(func.count(Subscription.id))
    )
    total_count = count_result.scalar()
    
    offset = (page - 1) * limit
    
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.user))
        .order_by(Subscription.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    
    subscriptions = result.scalars().all()
    
    return subscriptions, total_count

async def add_subscription_servers(
    db: AsyncSession,
    subscription: Subscription,
    server_squad_ids: List[int],
    paid_prices: List[int] = None
) -> Subscription:
    await db.refresh(subscription)
    
    if paid_prices is None:
        months_remaining = get_remaining_months(subscription.end_date)
        paid_prices = []
        
        from app.database.models import ServerSquad
        for server_id in server_squad_ids:
            result = await db.execute(
                select(ServerSquad.price_kopeks)
                .where(ServerSquad.id == server_id)
            )
            server_price_per_month = result.scalar() or 0
            total_price_for_period = server_price_per_month * months_remaining
            paid_prices.append(total_price_for_period)
    
    for i, server_id in enumerate(server_squad_ids):
        subscription_server = SubscriptionServer(
            subscription_id=subscription.id,  
            server_squad_id=server_id,
            paid_price_kopeks=paid_prices[i] if i < len(paid_prices) else 0
        )
        db.add(subscription_server)
    
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"🌐 К подписке {subscription.id} добавлено {len(server_squad_ids)} серверов с ценами: {paid_prices}")
    return subscription

async def get_server_monthly_price(
    db: AsyncSession,
    server_squad_id: int
) -> int:
    from app.database.models import ServerSquad
    
    result = await db.execute(
        select(ServerSquad.price_kopeks)
        .where(ServerSquad.id == server_squad_id)
    )
    return result.scalar() or 0


async def get_servers_monthly_prices(
    db: AsyncSession,
    server_squad_ids: List[int]
) -> List[int]:
    prices = []
    for server_id in server_squad_ids:
        price = await get_server_monthly_price(db, server_id)
        prices.append(price)
    return prices

def _get_discount_percent(
    user: Optional[User],
    promo_group: Optional[PromoGroup],
    category: str,
    *,
    period_days: Optional[int] = None,
) -> int:
    if user is not None:
        try:
            return user.get_promo_discount(category, period_days)
        except AttributeError:
            pass

    if promo_group is not None:
        return promo_group.get_discount_percent(category, period_days)

    return 0


async def calculate_subscription_total_cost(
    db: AsyncSession,
    period_days: int,
    traffic_gb: int,
    server_squad_ids: List[int],
    devices: int,
    *,
    user: Optional[User] = None,
    promo_group: Optional[PromoGroup] = None,
) -> Tuple[int, dict]:
    from app.config import PERIOD_PRICES
    
    months_in_period = calculate_months_from_days(period_days)
    
    base_price = PERIOD_PRICES.get(period_days, 0)
    
    promo_group = promo_group or (user.promo_group if user else None)

    traffic_price_per_month = settings.get_traffic_price(traffic_gb)
    traffic_discount_percent = _get_discount_percent(
        user,
        promo_group,
        "traffic",
        period_days=period_days,
    )
    traffic_discount_per_month = traffic_price_per_month * traffic_discount_percent // 100
    discounted_traffic_per_month = traffic_price_per_month - traffic_discount_per_month
    total_traffic_price = discounted_traffic_per_month * months_in_period
    total_traffic_discount = traffic_discount_per_month * months_in_period

    servers_prices = await get_servers_monthly_prices(db, server_squad_ids)
    servers_price_per_month = sum(servers_prices)
    servers_discount_percent = _get_discount_percent(
        user,
        promo_group,
        "servers",
        period_days=period_days,
    )
    servers_discount_per_month = servers_price_per_month * servers_discount_percent // 100
    discounted_servers_per_month = servers_price_per_month - servers_discount_per_month
    total_servers_price = discounted_servers_per_month * months_in_period
    total_servers_discount = servers_discount_per_month * months_in_period

    additional_devices = max(0, devices - settings.DEFAULT_DEVICE_LIMIT)
    devices_price_per_month = additional_devices * settings.PRICE_PER_DEVICE
    devices_discount_percent = _get_discount_percent(
        user,
        promo_group,
        "devices",
        period_days=period_days,
    )
    devices_discount_per_month = devices_price_per_month * devices_discount_percent // 100
    discounted_devices_per_month = devices_price_per_month - devices_discount_per_month
    total_devices_price = discounted_devices_per_month * months_in_period
    total_devices_discount = devices_discount_per_month * months_in_period

    total_cost = base_price + total_traffic_price + total_servers_price + total_devices_price

    details = {
        'base_price': base_price,
        'traffic_price_per_month': traffic_price_per_month,
        'traffic_discount_percent': traffic_discount_percent,
        'traffic_discount_total': total_traffic_discount,
        'total_traffic_price': total_traffic_price,
        'servers_price_per_month': servers_price_per_month,
        'servers_discount_percent': servers_discount_percent,
        'servers_discount_total': total_servers_discount,
        'total_servers_price': total_servers_price,
        'devices_price_per_month': devices_price_per_month,
        'devices_discount_percent': devices_discount_percent,
        'devices_discount_total': total_devices_discount,
        'total_devices_price': total_devices_price,
        'months_in_period': months_in_period,
        'servers_individual_prices': [
            (price - (price * servers_discount_percent // 100)) * months_in_period
            for price in servers_prices
        ]
    }

    logger.info(f"📊 Расчет стоимости подписки на {period_days} дней ({months_in_period} мес):")
    logger.info(f"   Базовый период: {base_price/100}₽")
    if total_traffic_price > 0:
        message = (
            f"   Трафик: {traffic_price_per_month/100}₽/мес × {months_in_period} = {total_traffic_price/100}₽"
        )
        if total_traffic_discount > 0:
            message += (
                f" (скидка {traffic_discount_percent}%: -{total_traffic_discount/100}₽)"
            )
        logger.info(message)
    if total_servers_price > 0:
        message = (
            f"   Серверы: {servers_price_per_month/100}₽/мес × {months_in_period} = {total_servers_price/100}₽"
        )
        if total_servers_discount > 0:
            message += (
                f" (скидка {servers_discount_percent}%: -{total_servers_discount/100}₽)"
            )
        logger.info(message)
    if total_devices_price > 0:
        message = (
            f"   Устройства: {devices_price_per_month/100}₽/мес × {months_in_period} = {total_devices_price/100}₽"
        )
        if total_devices_discount > 0:
            message += (
                f" (скидка {devices_discount_percent}%: -{total_devices_discount/100}₽)"
            )
        logger.info(message)
    logger.info(f"   ИТОГО: {total_cost/100}₽")
    
    return total_cost, details
    
async def get_subscription_server_ids(
    db: AsyncSession,
    subscription_id: int
) -> List[int]:
    
    result = await db.execute(
        select(SubscriptionServer.server_squad_id)
        .where(SubscriptionServer.subscription_id == subscription_id)
    )
    return [row[0] for row in result.fetchall()]


async def get_subscription_servers(
    db: AsyncSession,
    subscription_id: int
) -> List[dict]:
    
    from app.database.models import ServerSquad
    
    result = await db.execute(
        select(SubscriptionServer, ServerSquad)
        .join(ServerSquad, SubscriptionServer.server_squad_id == ServerSquad.id)
        .where(SubscriptionServer.subscription_id == subscription_id)
    )
    
    servers_info = []
    for sub_server, server_squad in result.fetchall():
        servers_info.append({
            'server_id': server_squad.id,
            'squad_uuid': server_squad.squad_uuid,
            'display_name': server_squad.display_name,
            'country_code': server_squad.country_code,
            'paid_price_kopeks': sub_server.paid_price_kopeks,
            'connected_at': sub_server.connected_at,
            'is_available': server_squad.is_available
        })
    
    return servers_info

async def remove_subscription_servers(
    db: AsyncSession,
    subscription_id: int,
    server_squad_ids: List[int]
) -> bool:
    try:
        from app.database.models import SubscriptionServer
        from sqlalchemy import delete
        
        await db.execute(
            delete(SubscriptionServer)
            .where(
                SubscriptionServer.subscription_id == subscription_id,
                SubscriptionServer.server_squad_id.in_(server_squad_ids)
            )
        )
        
        await db.commit()
        logger.info(f"🗑️ Удалены серверы {server_squad_ids} из подписки {subscription_id}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка удаления серверов из подписки: {e}")
        await db.rollback()
        return False


async def get_subscription_renewal_cost(
    db: AsyncSession,
    subscription_id: int,
    period_days: int,
    *,
    user: Optional[User] = None,
    promo_group: Optional[PromoGroup] = None,
) -> int:
    try:
        from app.config import PERIOD_PRICES

        months_in_period = calculate_months_from_days(period_days)

        base_price = PERIOD_PRICES.get(period_days, 0)

        result = await db.execute(
            select(Subscription)
            .options(
                selectinload(Subscription.user).selectinload(User.promo_group),
            )
            .where(Subscription.id == subscription_id)
        )
        subscription = result.scalar_one_or_none()
        if not subscription:
            return base_price

        if user is None:
            user = subscription.user
        promo_group = promo_group or (user.promo_group if user else None)

        servers_info = await get_subscription_servers(db, subscription_id)
        servers_price_per_month = 0
        for server_info in servers_info:
            from app.database.models import ServerSquad
            result = await db.execute(
                select(ServerSquad.price_kopeks)
                .where(ServerSquad.id == server_info['server_id'])
            )
            current_server_price = result.scalar() or 0
            servers_price_per_month += current_server_price

        servers_discount_percent = _get_discount_percent(
            user,
            promo_group,
            "servers",
            period_days=period_days,
        )
        servers_discount_per_month = servers_price_per_month * servers_discount_percent // 100
        discounted_servers_per_month = servers_price_per_month - servers_discount_per_month
        total_servers_cost = discounted_servers_per_month * months_in_period
        total_servers_discount = servers_discount_per_month * months_in_period

        traffic_price_per_month = settings.get_traffic_price(subscription.traffic_limit_gb)
        traffic_discount_percent = _get_discount_percent(
            user,
            promo_group,
            "traffic",
            period_days=period_days,
        )
        traffic_discount_per_month = traffic_price_per_month * traffic_discount_percent // 100
        discounted_traffic_per_month = traffic_price_per_month - traffic_discount_per_month
        total_traffic_cost = discounted_traffic_per_month * months_in_period
        total_traffic_discount = traffic_discount_per_month * months_in_period

        additional_devices = max(0, subscription.device_limit - settings.DEFAULT_DEVICE_LIMIT)
        devices_price_per_month = additional_devices * settings.PRICE_PER_DEVICE
        devices_discount_percent = _get_discount_percent(
            user,
            promo_group,
            "devices",
            period_days=period_days,
        )
        devices_discount_per_month = devices_price_per_month * devices_discount_percent // 100
        discounted_devices_per_month = devices_price_per_month - devices_discount_per_month
        total_devices_cost = discounted_devices_per_month * months_in_period
        total_devices_discount = devices_discount_per_month * months_in_period

        total_cost = base_price + total_servers_cost + total_traffic_cost + total_devices_cost

        logger.info(f"💰 Расчет продления подписки {subscription_id} на {period_days} дней ({months_in_period} мес):")
        logger.info(f"   📅 Период: {base_price/100}₽")
        if total_servers_cost > 0:
            message = (
                f"   🌍 Серверы: {servers_price_per_month/100}₽/мес × {months_in_period} = {total_servers_cost/100}₽"
            )
            if total_servers_discount > 0:
                message += (
                    f" (скидка {servers_discount_percent}%: -{total_servers_discount/100}₽)"
                )
            logger.info(message)
        if total_traffic_cost > 0:
            message = (
                f"   📊 Трафик: {traffic_price_per_month/100}₽/мес × {months_in_period} = {total_traffic_cost/100}₽"
            )
            if total_traffic_discount > 0:
                message += (
                    f" (скидка {traffic_discount_percent}%: -{total_traffic_discount/100}₽)"
                )
            logger.info(message)
        if total_devices_cost > 0:
            message = (
                f"   📱 Устройства: {devices_price_per_month/100}₽/мес × {months_in_period} = {total_devices_cost/100}₽"
            )
            if total_devices_discount > 0:
                message += (
                    f" (скидка {devices_discount_percent}%: -{total_devices_discount/100}₽)"
                )
            logger.info(message)
        logger.info(f"   💎 ИТОГО: {total_cost/100}₽")
        
        return total_cost
        
    except Exception as e:
        logger.error(f"Ошибка расчета стоимости продления: {e}")
        from app.config import PERIOD_PRICES
        return PERIOD_PRICES.get(period_days, 0)

async def calculate_addon_cost_for_remaining_period(
    db: AsyncSession,
    subscription: Subscription,
    additional_traffic_gb: int = 0,
    additional_devices: int = 0,
    additional_server_ids: List[int] = None,
    *,
    user: Optional[User] = None,
    promo_group: Optional[PromoGroup] = None,
) -> int:
    if additional_server_ids is None:
        additional_server_ids = []

    months_to_pay = get_remaining_months(subscription.end_date)
    period_hint_days = months_to_pay * 30 if months_to_pay > 0 else None

    total_cost = 0

    if user is None:
        user = getattr(subscription, "user", None)
    promo_group = promo_group or (user.promo_group if user else None)

    if additional_traffic_gb > 0:
        traffic_price_per_month = settings.get_traffic_price(additional_traffic_gb)
        traffic_discount_percent = _get_discount_percent(
            user,
            promo_group,
            "traffic",
            period_days=period_hint_days,
        )
        traffic_discount_per_month = traffic_price_per_month * traffic_discount_percent // 100
        discounted_traffic_per_month = traffic_price_per_month - traffic_discount_per_month
        traffic_total_cost = discounted_traffic_per_month * months_to_pay
        total_cost += traffic_total_cost
        message = (
            f"Трафик +{additional_traffic_gb}ГБ: {traffic_price_per_month/100}₽/мес × {months_to_pay} = {traffic_total_cost/100}₽"
        )
        if traffic_discount_per_month > 0:
            message += (
                f" (скидка {traffic_discount_percent}%: -{traffic_discount_per_month * months_to_pay/100}₽)"
            )
        logger.info(message)

    if additional_devices > 0:
        devices_price_per_month = additional_devices * settings.PRICE_PER_DEVICE
        devices_discount_percent = _get_discount_percent(
            user,
            promo_group,
            "devices",
            period_days=period_hint_days,
        )
        devices_discount_per_month = devices_price_per_month * devices_discount_percent // 100
        discounted_devices_per_month = devices_price_per_month - devices_discount_per_month
        devices_total_cost = discounted_devices_per_month * months_to_pay
        total_cost += devices_total_cost
        message = (
            f"Устройства +{additional_devices}: {devices_price_per_month/100}₽/мес × {months_to_pay} = {devices_total_cost/100}₽"
        )
        if devices_discount_per_month > 0:
            message += (
                f" (скидка {devices_discount_percent}%: -{devices_discount_per_month * months_to_pay/100}₽)"
            )
        logger.info(message)

    if additional_server_ids:
        from app.database.models import ServerSquad
        for server_id in additional_server_ids:
            result = await db.execute(
                select(ServerSquad.price_kopeks, ServerSquad.display_name)
                .where(ServerSquad.id == server_id)
            )
            server_data = result.first()
            if server_data:
                server_price_per_month, server_name = server_data
                servers_discount_percent = _get_discount_percent(
                    user,
                    promo_group,
                    "servers",
                    period_days=period_hint_days,
                )
                server_discount_per_month = server_price_per_month * servers_discount_percent // 100
                discounted_server_per_month = server_price_per_month - server_discount_per_month
                server_total_cost = discounted_server_per_month * months_to_pay
                total_cost += server_total_cost
                message = (
                    f"Сервер {server_name}: {server_price_per_month/100}₽/мес × {months_to_pay} = {server_total_cost/100}₽"
                )
                if server_discount_per_month > 0:
                    message += (
                        f" (скидка {servers_discount_percent}%: -{server_discount_per_month * months_to_pay/100}₽)"
                    )
                logger.info(message)
    
    logger.info(f"💰 Итого доплата за {months_to_pay} мес: {total_cost/100}₽")
    return total_cost

async def expire_subscription(
    db: AsyncSession,
    subscription: Subscription
) -> Subscription:
    
    subscription.status = SubscriptionStatus.EXPIRED.value
    subscription.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"⏰ Подписка пользователя {subscription.user_id} помечена как истёкшая")
    return subscription


async def check_and_update_subscription_status(
    db: AsyncSession,
    subscription: Subscription
) -> Subscription:
    
    current_time = datetime.utcnow()
    
    if (subscription.status == SubscriptionStatus.ACTIVE.value and 
        subscription.end_date <= current_time):
        
        subscription.status = SubscriptionStatus.EXPIRED.value
        subscription.updated_at = current_time
        
        await db.commit()
        await db.refresh(subscription)
        
        logger.info(f"⏰ Статус подписки пользователя {subscription.user_id} изменен на 'expired'")
    
    return subscription

async def create_subscription(
    db: AsyncSession,
    user_id: int,
    status: str = "trial",
    is_trial: bool = True,
    end_date: datetime = None,
    traffic_limit_gb: int = 10,
    traffic_used_gb: float = 0.0,
    device_limit: int = 1,
    connected_squads: list = None,
    remnawave_short_uuid: str = None,
    subscription_url: str = ""
) -> Subscription:
    
    if end_date is None:
        end_date = datetime.utcnow() + timedelta(days=3)
    
    if connected_squads is None:
        connected_squads = []
    
    subscription = Subscription(
        user_id=user_id,
        status=status,
        is_trial=is_trial,
        end_date=end_date,
        traffic_limit_gb=traffic_limit_gb,
        traffic_used_gb=traffic_used_gb,
        device_limit=device_limit,
        connected_squads=connected_squads,
        remnawave_short_uuid=remnawave_short_uuid,
        subscription_url=subscription_url
    )
    
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"✅ Создана подписка для пользователя {user_id}")
    return subscription
