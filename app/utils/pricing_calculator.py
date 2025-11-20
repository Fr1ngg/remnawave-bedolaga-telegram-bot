"""
Unified pricing calculator module for subscription pricing.

This module provides a centralized way to calculate subscription prices
including all components: period, traffic, servers, and devices.
All calculations consider discounts from promo groups and offers.
"""

from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict
from datetime import datetime, date

from app.database.models import User
from app.config import settings, PERIOD_PRICES
from app.database.crud.server_squad import get_servers_monthly_prices
from app.database.crud.promo import _get_discount_percent


@dataclass
class PricingDetails:
    """Detailed breakdown of pricing calculation."""
    
    # Base components
    base_price_original: int = 0  # Original price before discounts
    base_price_discounted: int = 0  # Price after discounts
    base_discount_percent: int = 0
    base_discount_total: int = 0
    
    # Traffic components
    traffic_price_per_month_original: int = 0
    traffic_price_per_month_discounted: int = 0
    traffic_discount_percent: int = 0
    traffic_discount_total: int = 0
    total_traffic_price: int = 0
    
    # Servers components
    servers_price_per_month_original: int = 0
    servers_price_per_month_discounted: int = 0
    servers_discount_percent: int = 0
    servers_discount_total: int = 0
    total_servers_price: int = 0
    servers_individual_prices: List[int] = None
    
    # Devices components
    devices_price_per_month_original: int = 0
    devices_price_per_month_discounted: int = 0
    devices_discount_percent: int = 0
    devices_discount_total: int = 0
    total_devices_price: int = 0
    
    # Summary
    months_in_period: int = 0
    total_cost: int = 0
    total_discount: int = 0
    total_original_cost: int = 0


@dataclass
class PricingResult:
    """Result of pricing calculation."""
    total_price: int
    details: PricingDetails


def calculate_months_from_days(period_days: int) -> int:
    """Convert days to months for pricing calculations."""
    return max(1, round(period_days / 30.44))  # Average days in month


async def calculate_subscription_total_cost(
    db,
    period_days: int,
    traffic_gb: int,
    server_squad_ids: List[int],
    devices: int,
    *,
    user: Optional[User] = None,
    promo_group=None
) -> PricingResult:
    """
    Calculate total subscription cost including all components with discounts.

    Args:
        db: Database session
        period_days: Subscription period in days
        traffic_gb: Traffic limit in GB (0 for unlimited)
        server_squad_ids: List of server squad IDs
        devices: Number of allowed devices
        user: User object for discount calculation
        promo_group: Promo group for discount calculation

    Returns:
        PricingResult with total price and detailed breakdown
    """
    months_in_period = calculate_months_from_days(period_days)

    # Calculate base period price
    base_price_original = PERIOD_PRICES.get(period_days, 0)
    period_discount_percent = _get_discount_percent(
        user,
        promo_group,
        "period",
        period_days=period_days,
    )
    base_discount_total = base_price_original * period_discount_percent // 100
    base_price_discounted = base_price_original - base_discount_total

    # Calculate traffic price
    traffic_price_per_month_original = settings.get_traffic_price(traffic_gb)
    traffic_discount_percent = _get_discount_percent(
        user,
        promo_group,
        "traffic",
        period_days=period_days,
    )
    traffic_discount_per_month = traffic_price_per_month_original * traffic_discount_percent // 100
    traffic_price_per_month_discounted = traffic_price_per_month_original - traffic_discount_per_month
    total_traffic_price = traffic_price_per_month_discounted * months_in_period
    total_traffic_discount = traffic_discount_per_month * months_in_period

    # Calculate servers price
    servers_prices = await get_servers_monthly_prices(db, server_squad_ids)
    servers_price_per_month_original = sum(servers_prices)
    servers_discount_percent = _get_discount_percent(
        user,
        promo_group,
        "servers",
        period_days=period_days,
    )
    servers_discount_per_month = servers_price_per_month_original * servers_discount_percent // 100
    servers_price_per_month_discounted = servers_price_per_month_original - servers_discount_per_month
    total_servers_price = servers_price_per_month_discounted * months_in_period
    total_servers_discount = servers_discount_per_month * months_in_period

    # Calculate devices price
    additional_devices = max(0, devices - settings.DEFAULT_DEVICE_LIMIT)
    devices_price_per_month_original = additional_devices * settings.PRICE_PER_DEVICE
    devices_discount_percent = _get_discount_percent(
        user,
        promo_group,
        "devices",
        period_days=period_days,
    )
    devices_discount_per_month = devices_price_per_month_original * devices_discount_percent // 100
    devices_price_per_month_discounted = devices_price_per_month_original - devices_discount_per_month
    total_devices_price = devices_price_per_month_discounted * months_in_period
    total_devices_discount = devices_discount_per_month * months_in_period

    # Calculate total cost
    total_cost = base_price_discounted + total_traffic_price + total_servers_price + total_devices_price
    total_original_cost = base_price_original + (traffic_price_per_month_original * months_in_period) + \
                          (servers_price_per_month_original * months_in_period) + \
                          (devices_price_per_month_original * months_in_period)
    total_discount = total_original_cost - total_cost

    # Prepare details
    details = PricingDetails(
        base_price_original=base_price_original,
        base_price_discounted=base_price_discounted,
        base_discount_percent=period_discount_percent,
        base_discount_total=base_discount_total,
        
        traffic_price_per_month_original=traffic_price_per_month_original,
        traffic_price_per_month_discounted=traffic_price_per_month_discounted,
        traffic_discount_percent=traffic_discount_percent,
        traffic_discount_total=total_traffic_discount,
        total_traffic_price=total_traffic_price,
        
        servers_price_per_month_original=servers_price_per_month_original,
        servers_price_per_month_discounted=servers_price_per_month_discounted,
        servers_discount_percent=servers_discount_percent,
        servers_discount_total=total_servers_discount,
        total_servers_price=total_servers_price,
        servers_individual_prices=[
            (price - (price * servers_discount_percent // 100)) * months_in_period
            for price in servers_prices
        ],
        
        devices_price_per_month_original=devices_price_per_month_original,
        devices_price_per_month_discounted=devices_price_per_month_discounted,
        devices_discount_percent=devices_discount_percent,
        devices_discount_total=total_devices_discount,
        total_devices_price=total_devices_price,
        
        months_in_period=months_in_period,
        total_cost=total_cost,
        total_discount=total_discount,
        total_original_cost=total_original_cost
    )

    return PricingResult(
        total_price=total_cost,
        details=details
    )


def calculate_period_price(
    period_days: int,
    user: Optional[User] = None,
    promo_group=None
) -> Tuple[int, int]:
    """
    Calculate just the period price with applicable discounts.
    
    Args:
        period_days: Subscription period in days
        user: User object for discount calculation
        promo_group: Promo group for discount calculation
    
    Returns:
        Tuple of (final_price, original_price)
    """
    base_price_original = PERIOD_PRICES.get(period_days, 0)
    period_discount_percent = _get_discount_percent(
        user,
        promo_group,
        "period",
        period_days=period_days,
    )
    base_discount_total = base_price_original * period_discount_percent // 100
    base_price_discounted = base_price_original - base_discount_total
    
    return base_price_discounted, base_price_original


def calculate_traffic_price(
    traffic_gb: int,
    period_days: int,
    user: Optional[User] = None,
    promo_group=None
) -> Tuple[int, int]:
    """
    Calculate traffic price for the entire period with applicable discounts.
    
    Args:
        traffic_gb: Traffic limit in GB (0 for unlimited)
        period_days: Subscription period in days
        user: User object for discount calculation
        promo_group: Promo group for discount calculation
    
    Returns:
        Tuple of (final_price, original_price)
    """
    months_in_period = calculate_months_from_days(period_days)
    traffic_price_per_month_original = settings.get_traffic_price(traffic_gb)
    traffic_discount_percent = _get_discount_percent(
        user,
        promo_group,
        "traffic",
        period_days=period_days,
    )
    traffic_discount_per_month = traffic_price_per_month_original * traffic_discount_percent // 100
    traffic_price_per_month_discounted = traffic_price_per_month_original - traffic_discount_per_month
    total_traffic_price = traffic_price_per_month_discounted * months_in_period
    
    total_traffic_price_original = traffic_price_per_month_original * months_in_period
    
    return total_traffic_price, total_traffic_price_original


async def calculate_servers_price(
    db,
    server_squad_ids: List[int],
    period_days: int,
    user: Optional[User] = None,
    promo_group=None
) -> Tuple[int, int, List[int]]:
    """
    Calculate servers price for the entire period with applicable discounts.
    
    Args:
        db: Database session
        server_squad_ids: List of server squad IDs
        period_days: Subscription period in days
        user: User object for discount calculation
        promo_group: Promo group for discount calculation
    
    Returns:
        Tuple of (final_price, original_price, individual_server_prices)
    """
    months_in_period = calculate_months_from_days(period_days)
    servers_prices = await get_servers_monthly_prices(db, server_squad_ids)
    servers_price_per_month_original = sum(servers_prices)
    servers_discount_percent = _get_discount_percent(
        user,
        promo_group,
        "servers",
        period_days=period_days,
    )
    servers_discount_per_month = servers_price_per_month_original * servers_discount_percent // 100
    servers_price_per_month_discounted = servers_price_per_month_original - servers_discount_per_month
    total_servers_price = servers_price_per_month_discounted * months_in_period
    
    total_servers_price_original = servers_price_per_month_original * months_in_period
    
    # Calculate individual server prices after discount
    individual_server_prices = [
        (price - (price * servers_discount_percent // 100)) * months_in_period
        for price in servers_prices
    ]
    
    return total_servers_price, total_servers_price_original, individual_server_prices


def calculate_devices_price(
    devices: int,
    period_days: int,
    user: Optional[User] = None,
    promo_group=None
) -> Tuple[int, int]:
    """
    Calculate devices price for the entire period with applicable discounts.
    
    Args:
        devices: Number of devices
        period_days: Subscription period in days
        user: User object for discount calculation
        promo_group: Promo group for discount calculation
    
    Returns:
        Tuple of (final_price, original_price)
    """
    months_in_period = calculate_months_from_days(period_days)
    additional_devices = max(0, devices - settings.DEFAULT_DEVICE_LIMIT)
    devices_price_per_month_original = additional_devices * settings.PRICE_PER_DEVICE
    devices_discount_percent = _get_discount_percent(
        user,
        promo_group,
        "devices",
        period_days=period_days,
    )
    devices_discount_per_month = devices_price_per_month_original * devices_discount_percent // 100
    devices_price_per_month_discounted = devices_price_per_month_original - devices_discount_per_month
    total_devices_price = devices_price_per_month_discounted * months_in_period
    
    total_devices_price_original = devices_price_per_month_original * months_in_period
    
    return total_devices_price, total_devices_price_original


async def calculate_subscription_total_cost_basic(
    db,
    period_days: int,
    traffic_gb: int,
    server_squad_ids: List[int],
    devices: int,
    *,
    user: Optional[User] = None,
    promo_group=None
) -> int:
    """
    Simple wrapper that returns only the total price without details.
    
    Args:
        db: Database session
        period_days: Subscription period in days
        traffic_gb: Traffic limit in GB (0 for unlimited)
        server_squad_ids: List of server squad IDs
        devices: Number of allowed devices
        user: User object for discount calculation
        promo_group: Promo group for discount calculation

    Returns:
        Total subscription price
    """
    result = await calculate_subscription_total_cost(
        db, period_days, traffic_gb, server_squad_ids, devices,
        user=user, promo_group=promo_group
    )
    return result.total_price