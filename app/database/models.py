from datetime import datetime, timedelta
from typing import Optional, List, Dict
from enum import Enum

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Boolean,
    Text,
    ForeignKey,
    Float,
    JSON,
    BigInteger,
    UniqueConstraint,
    Index,
    Table,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.sql import func


Base = declarative_base()


server_squad_promo_groups = Table(
    "server_squad_promo_groups",
    Base.metadata,
    Column(
        "server_squad_id",
        Integer,
        ForeignKey("server_squads.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "promo_group_id",
        Integer,
        ForeignKey("promo_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class UserStatus(Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    DELETED = "deleted"


class SubscriptionStatus(Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    EXPIRED = "expired"
    DISABLED = "disabled"


class TransactionType(Enum):
    DEPOSIT = "deposit"  
    WITHDRAWAL = "withdrawal"  
    SUBSCRIPTION_PAYMENT = "subscription_payment"  
    REFUND = "refund" 
    REFERRAL_REWARD = "referral_reward"  


class PromoCodeType(Enum):
    BALANCE = "balance" 
    SUBSCRIPTION_DAYS = "subscription_days"  
    TRIAL_SUBSCRIPTION = "trial_subscription"  


class PaymentMethod(Enum):
    TELEGRAM_STARS = "telegram_stars"
    TRIBUTE = "tribute"
    YOOKASSA = "yookassa"
    CRYPTOBOT = "cryptobot"
    MULENPAY = "mulenpay"
    PAL24 = "pal24"
    MANUAL = "manual"


class MainMenuButtonActionType(Enum):
    URL = "url"
    MINI_APP = "mini_app"


class MainMenuButtonVisibility(Enum):
    ALL = "all"
    ADMINS = "admins"
    SUBSCRIBERS = "subscribers"

class YooKassaPayment(Base):
    __tablename__ = "yookassa_payments"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    yookassa_payment_id = Column(String(255), unique=True, nullable=False, index=True)
    amount_kopeks = Column(Integer, nullable=False)
    currency = Column(String(3), default="RUB", nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(50), nullable=False)  
    is_paid = Column(Boolean, default=False)
    is_captured = Column(Boolean, default=False)
    confirmation_url = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    payment_method_type = Column(String(50), nullable=True) 
    refundable = Column(Boolean, default=False)
    test_mode = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    yookassa_created_at = Column(DateTime, nullable=True) 
    captured_at = Column(DateTime, nullable=True) 
    user = relationship("User", backref="yookassa_payments")
    transaction = relationship("Transaction", backref="yookassa_payment")
    
    @property
    def amount_rubles(self) -> float:
        return self.amount_kopeks / 100
    
    @property
    def is_pending(self) -> bool:
        return self.status == "pending"
    
    @property
    def is_succeeded(self) -> bool:
        return self.status == "succeeded" and self.is_paid
    
    @property
    def is_failed(self) -> bool:
        return self.status in ["canceled", "failed"]
    
    @property
    def can_be_captured(self) -> bool:
        return self.status == "waiting_for_capture"
    
    def __repr__(self):
        return f"<YooKassaPayment(id={self.id}, yookassa_id={self.yookassa_payment_id}, amount={self.amount_rubles}₽, status={self.status})>"

class CryptoBotPayment(Base):
    __tablename__ = "cryptobot_payments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    invoice_id = Column(String(255), unique=True, nullable=False, index=True)
    amount = Column(String(50), nullable=False)
    asset = Column(String(10), nullable=False)
    
    status = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    payload = Column(Text, nullable=True)
    
    bot_invoice_url = Column(Text, nullable=True)
    mini_app_invoice_url = Column(Text, nullable=True)
    web_app_invoice_url = Column(Text, nullable=True)
    
    paid_at = Column(DateTime, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    user = relationship("User", backref="cryptobot_payments")
    transaction = relationship("Transaction", backref="cryptobot_payment")
    
    @property
    def amount_float(self) -> float:
        try:
            return float(self.amount)
        except (ValueError, TypeError):
            return 0.0
    
    @property
    def is_paid(self) -> bool:
        return self.status == "paid"
    
    @property
    def is_pending(self) -> bool:
        return self.status == "active"
    
    @property
    def is_expired(self) -> bool:
        return self.status == "expired"
    
    def __repr__(self):
        return f"<CryptoBotPayment(id={self.id}, invoice_id={self.invoice_id}, amount={self.amount} {self.asset}, status={self.status})>"


class MulenPayPayment(Base):
    __tablename__ = "mulenpay_payments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    mulen_payment_id = Column(Integer, nullable=True, index=True)
    uuid = Column(String(255), unique=True, nullable=False, index=True)
    amount_kopeks = Column(Integer, nullable=False)
    currency = Column(String(10), nullable=False, default="RUB")
    description = Column(Text, nullable=True)

    status = Column(String(50), nullable=False, default="created")
    is_paid = Column(Boolean, default=False)
    paid_at = Column(DateTime, nullable=True)

    payment_url = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    callback_payload = Column(JSON, nullable=True)

    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    user = relationship("User", backref="mulenpay_payments")
    transaction = relationship("Transaction", backref="mulenpay_payment")

    @property
    def amount_rubles(self) -> float:
        return self.amount_kopeks / 100

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            "<MulenPayPayment(id={0}, mulen_id={1}, amount={2}₽, status={3})>".format(
                self.id,
                self.mulen_payment_id,
                self.amount_rubles,
                self.status,
            )
        )


class Pal24Payment(Base):
    __tablename__ = "pal24_payments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    bill_id = Column(String(255), unique=True, nullable=False, index=True)
    order_id = Column(String(255), nullable=True, index=True)
    amount_kopeks = Column(Integer, nullable=False)
    currency = Column(String(10), nullable=False, default="RUB")
    description = Column(Text, nullable=True)
    type = Column(String(20), nullable=False, default="normal")

    status = Column(String(50), nullable=False, default="NEW")
    is_active = Column(Boolean, default=True)
    is_paid = Column(Boolean, default=False)
    paid_at = Column(DateTime, nullable=True)
    last_status = Column(String(50), nullable=True)
    last_status_checked_at = Column(DateTime, nullable=True)

    link_url = Column(Text, nullable=True)
    link_page_url = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    callback_payload = Column(JSON, nullable=True)

    payment_id = Column(String(255), nullable=True, index=True)
    payment_status = Column(String(50), nullable=True)
    payment_method = Column(String(50), nullable=True)
    balance_amount = Column(String(50), nullable=True)
    balance_currency = Column(String(10), nullable=True)
    payer_account = Column(String(255), nullable=True)

    ttl = Column(Integer, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    user = relationship("User", backref="pal24_payments")
    transaction = relationship("Transaction", backref="pal24_payment")

    @property
    def amount_rubles(self) -> float:
        return self.amount_kopeks / 100

    @property
    def is_pending(self) -> bool:
        return self.status in {"NEW", "PROCESS"}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            "<Pal24Payment(id={0}, bill_id={1}, amount={2}₽, status={3})>".format(
                self.id,
                self.bill_id,
                self.amount_rubles,
                self.status,
            )
        )


class PromoGroup(Base):
    __tablename__ = "promo_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    server_discount_percent = Column(Integer, nullable=False, default=0)
    traffic_discount_percent = Column(Integer, nullable=False, default=0)
    device_discount_percent = Column(Integer, nullable=False, default=0)
    period_discounts = Column(JSON, nullable=True, default=dict)
    auto_assign_total_spent_kopeks = Column(Integer, nullable=True, default=None)
    apply_discounts_to_addons = Column(Boolean, nullable=False, default=True)
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    users = relationship("User", back_populates="promo_group")
    server_squads = relationship(
        "ServerSquad",
        secondary=server_squad_promo_groups,
        back_populates="allowed_promo_groups",
        lazy="selectin",
    )

    def _get_period_discounts_map(self) -> Dict[int, int]:
        raw_discounts = self.period_discounts or {}

        if isinstance(raw_discounts, dict):
            items = raw_discounts.items()
        else:
            items = []

        normalized: Dict[int, int] = {}

        for key, value in items:
            try:
                period = int(key)
                percent = int(value)
            except (TypeError, ValueError):
                continue

            normalized[period] = max(0, min(100, percent))

        return normalized

    def _get_period_discount(self, period_days: Optional[int]) -> int:
        if not period_days:
            return 0

        discounts = self._get_period_discounts_map()

        if period_days in discounts:
            return discounts[period_days]

        if self.is_default:
            try:
                from app.config import settings

                if settings.is_base_promo_group_period_discount_enabled():
                    config_discounts = settings.get_base_promo_group_period_discounts()
                    return config_discounts.get(period_days, 0)
            except Exception:
                return 0

        return 0

    def get_discount_percent(self, category: str, period_days: Optional[int] = None) -> int:
        if category == "period":
            return max(0, min(100, self._get_period_discount(period_days)))

        mapping = {
            "servers": self.server_discount_percent,
            "traffic": self.traffic_discount_percent,
            "devices": self.device_discount_percent,
        }
        percent = mapping.get(category, 0)

        return max(0, min(100, percent))


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    status = Column(String(20), default=UserStatus.ACTIVE.value)
    language = Column(String(5), default="ru")
    balance_kopeks = Column(Integer, default=0)
    used_promocodes = Column(Integer, default=0) 
    has_had_paid_subscription = Column(Boolean, default=False, nullable=False)
    referred_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    referral_code = Column(String(20), unique=True, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    last_activity = Column(DateTime, default=func.now())
    remnawave_uuid = Column(String(255), nullable=True, unique=True)
    broadcasts = relationship("BroadcastHistory", back_populates="admin")
    referrals = relationship("User", backref="referrer", remote_side=[id], foreign_keys="User.referred_by_id")
    subscription = relationship("Subscription", back_populates="user", uselist=False)
    transactions = relationship("Transaction", back_populates="user")
    referral_earnings = relationship("ReferralEarning", foreign_keys="ReferralEarning.user_id", back_populates="user")
    discount_offers = relationship("DiscountOffer", back_populates="user")
    promo_offer_logs = relationship("PromoOfferLog", back_populates="user")
    lifetime_used_traffic_bytes = Column(BigInteger, default=0)
    auto_promo_group_assigned = Column(Boolean, nullable=False, default=False)
    auto_promo_group_threshold_kopeks = Column(BigInteger, nullable=False, default=0)
    promo_offer_discount_percent = Column(Integer, nullable=False, default=0)
    promo_offer_discount_source = Column(String(100), nullable=True)
    promo_offer_discount_expires_at = Column(DateTime, nullable=True)
    last_remnawave_sync = Column(DateTime, nullable=True)
    trojan_password = Column(String(255), nullable=True)
    vless_uuid = Column(String(255), nullable=True)
    ss_password = Column(String(255), nullable=True)
    has_made_first_topup: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    promo_group_id = Column(Integer, ForeignKey("promo_groups.id", ondelete="RESTRICT"), nullable=False, index=True)
    promo_group = relationship("PromoGroup", back_populates="users")
    external_admin_api_keys = relationship(
        "ExternalAdminApiKey",
        back_populates="creator",
        cascade="all, delete-orphan",
    )
    
    @property
    def balance_rubles(self) -> float:
        return self.balance_kopeks / 100
    
    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.last_name]
        return " ".join(filter(None, parts)) or self.username or f"ID{self.telegram_id}"

    def get_promo_discount(self, category: str, period_days: Optional[int] = None) -> int:
        if not self.promo_group:
            return 0
        return self.promo_group.get_discount_percent(category, period_days)
    
    def add_balance(self, kopeks: int) -> None:
        self.balance_kopeks += kopeks
    
    def subtract_balance(self, kopeks: int) -> bool:
        if self.balance_kopeks >= kopeks:
            self.balance_kopeks -= kopeks
            return True
        return False


class Subscription(Base):
    __tablename__ = "subscriptions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    
    status = Column(String(20), default=SubscriptionStatus.TRIAL.value)
    is_trial = Column(Boolean, default=True)
    
    start_date = Column(DateTime, default=func.now())
    end_date = Column(DateTime, nullable=False)
    
    traffic_limit_gb = Column(Integer, default=0)
    traffic_used_gb = Column(Float, default=0.0)

    subscription_url = Column(String, nullable=True)
    subscription_crypto_link = Column(String, nullable=True)

    device_limit = Column(Integer, default=1)
    
    connected_squads = Column(JSON, default=list)
    
    autopay_enabled = Column(Boolean, default=False)
    autopay_days_before = Column(Integer, default=3)
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    remnawave_short_uuid = Column(String(255), nullable=True)

    user = relationship("User", back_populates="subscription")
    discount_offers = relationship("DiscountOffer", back_populates="subscription")
    temporary_accesses = relationship("SubscriptionTemporaryAccess", back_populates="subscription")
    
    @property
    def is_active(self) -> bool:
        current_time = datetime.utcnow()
        return (
            self.status == SubscriptionStatus.ACTIVE.value and 
            self.end_date > current_time
        )
    
    @property
    def is_expired(self) -> bool:
        """Проверяет, истёк ли срок подписки"""
        return self.end_date <= datetime.utcnow()

    @property
    def should_be_expired(self) -> bool:
        current_time = datetime.utcnow()
        return (
            self.status == SubscriptionStatus.ACTIVE.value and 
            self.end_date <= current_time
        )

    @property
    def actual_status(self) -> str:
        current_time = datetime.utcnow()
        
        if self.status == SubscriptionStatus.EXPIRED.value:
            return "expired"
        
        if self.status == SubscriptionStatus.DISABLED.value:
            return "disabled"
        
        if self.status == SubscriptionStatus.ACTIVE.value:
            if self.end_date <= current_time:
                return "expired"
            else:
                return "active"
        
        if self.status == SubscriptionStatus.TRIAL.value:
            if self.end_date <= current_time:
                return "expired"
            else:
                return "trial"
        
        return self.status

    @property
    def status_display(self) -> str:
        actual_status = self.actual_status
        current_time = datetime.utcnow()
        
        if actual_status == "expired":
            return "🔴 Истекла"
        elif actual_status == "active":
            if self.is_trial:
                return "🎯 Тестовая"
            else:
                return "🟢 Активна"
        elif actual_status == "disabled":
            return "⚫ Отключена"
        elif actual_status == "trial":
            return "🎯 Тестовая"
        
        return "❓ Неизвестно"

    @property
    def status_emoji(self) -> str:
        actual_status = self.actual_status
        
        if actual_status == "expired":
            return "🔴"
        elif actual_status == "active":
            if self.is_trial:
                return "🎁"
            else:
                return "💎"
        elif actual_status == "disabled":
            return "⚫"
        elif actual_status == "trial":
            return "🎁"
        
        return "❓"

    @property
    def days_left(self) -> int:
        current_time = datetime.utcnow()
        if self.end_date <= current_time:
            return 0
        delta = self.end_date - current_time
        return max(0, delta.days)

    @property
    def time_left_display(self) -> str:
        current_time = datetime.utcnow()
        if self.end_date <= current_time:
            return "истёк"
        
        delta = self.end_date - current_time
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        
        if days > 0:
            return f"{days} дн."
        elif hours > 0:
            return f"{hours} ч."
        else:
            return f"{minutes} мин."
    
    @property
    def traffic_used_percent(self) -> float:
        if self.traffic_limit_gb == 0: 
            return 0.0
        if self.traffic_limit_gb > 0:
            return min((self.traffic_used_gb / self.traffic_limit_gb) * 100, 100.0)
        return 0.0
    
    def extend_subscription(self, days: int):

        if self.end_date > datetime.utcnow():
            self.end_date = self.end_date + timedelta(days=days)
        else:
            self.end_date = datetime.utcnow() + timedelta(days=days)
    
        if self.status == SubscriptionStatus.EXPIRED.value:
            self.status = SubscriptionStatus.ACTIVE.value
    
    def add_traffic(self, gb: int):
        if self.traffic_limit_gb == 0:  
            return
        self.traffic_limit_gb += gb


class Transaction(Base):
    __tablename__ = "transactions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    type = Column(String(50), nullable=False)
    amount_kopeks = Column(Integer, nullable=False)
    description = Column(Text, nullable=True)
    
    payment_method = Column(String(50), nullable=True)
    external_id = Column(String(255), nullable=True)  
    
    is_completed = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=func.now())
    completed_at = Column(DateTime, nullable=True)
    
    user = relationship("User", back_populates="transactions")
    
    @property
    def amount_rubles(self) -> float:
        return self.amount_kopeks / 100

class SubscriptionConversion(Base):
    __tablename__ = "subscription_conversions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    converted_at = Column(DateTime, default=func.now())
    
    trial_duration_days = Column(Integer, nullable=True)
    
    payment_method = Column(String(50), nullable=True)
    
    first_payment_amount_kopeks = Column(Integer, nullable=True)
    
    first_paid_period_days = Column(Integer, nullable=True)
    
    created_at = Column(DateTime, default=func.now())
    
    user = relationship("User", backref="subscription_conversions")
    
    @property
    def first_payment_amount_rubles(self) -> float:
        return (self.first_payment_amount_kopeks or 0) / 100
    
    def __repr__(self):
        return f"<SubscriptionConversion(user_id={self.user_id}, converted_at={self.converted_at})>"


class PromoCode(Base):
    __tablename__ = "promocodes"
    
    id = Column(Integer, primary_key=True, index=True)
    
    code = Column(String(50), unique=True, nullable=False, index=True)
    type = Column(String(50), nullable=False)
    
    balance_bonus_kopeks = Column(Integer, default=0)  
    subscription_days = Column(Integer, default=0) 
    
    max_uses = Column(Integer, default=1)  
    current_uses = Column(Integer, default=0)
    
    valid_from = Column(DateTime, default=func.now())
    valid_until = Column(DateTime, nullable=True)
    
    is_active = Column(Boolean, default=True)
    
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    uses = relationship("PromoCodeUse", back_populates="promocode")
    
    @property
    def is_valid(self) -> bool:
        now = datetime.utcnow()
        return (
            self.is_active and
            self.current_uses < self.max_uses and
            self.valid_from <= now and
            (self.valid_until is None or self.valid_until >= now)
        )
    
    @property
    def uses_left(self) -> int:
        return max(0, self.max_uses - self.current_uses)


class PromoCodeUse(Base):
    __tablename__ = "promocode_uses"
    
    id = Column(Integer, primary_key=True, index=True)
    promocode_id = Column(Integer, ForeignKey("promocodes.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    used_at = Column(DateTime, default=func.now())
    
    promocode = relationship("PromoCode", back_populates="uses")
    user = relationship("User")


class ReferralEarning(Base):
    __tablename__ = "referral_earnings"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)  
    referral_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    amount_kopeks = Column(Integer, nullable=False)
    reason = Column(String(100), nullable=False) 
    
    referral_transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    
    created_at = Column(DateTime, default=func.now())
    
    user = relationship("User", foreign_keys=[user_id], back_populates="referral_earnings")
    referral = relationship("User", foreign_keys=[referral_id])
    referral_transaction = relationship("Transaction")
    
    @property
    def amount_rubles(self) -> float:
        return self.amount_kopeks / 100


class Squad(Base):
    __tablename__ = "squads"
    
    id = Column(Integer, primary_key=True, index=True)
    
    uuid = Column(String(255), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    country_code = Column(String(5), nullable=True)
    
    is_available = Column(Boolean, default=True)
    price_kopeks = Column(Integer, default=0) 
    
    description = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    @property
    def price_rubles(self) -> float:
        return self.price_kopeks / 100


class ServiceRule(Base):
    __tablename__ = "service_rules"

    id = Column(Integer, primary_key=True, index=True)

    order = Column(Integer, default=0)
    title = Column(String(255), nullable=False)
    
    content = Column(Text, nullable=False)
    
    is_active = Column(Boolean, default=True)
    
    language = Column(String(5), default="ru")
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class PrivacyPolicy(Base):
    __tablename__ = "privacy_policies"

    id = Column(Integer, primary_key=True, index=True)
    language = Column(String(10), nullable=False, unique=True)
    content = Column(Text, nullable=False)
    is_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class PublicOffer(Base):
    __tablename__ = "public_offers"

    id = Column(Integer, primary_key=True, index=True)
    language = Column(String(10), nullable=False, unique=True)
    content = Column(Text, nullable=False)
    is_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class FaqSetting(Base):
    __tablename__ = "faq_settings"

    id = Column(Integer, primary_key=True, index=True)
    language = Column(String(10), nullable=False, unique=True)
    is_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class FaqPage(Base):
    __tablename__ = "faq_pages"

    id = Column(Integer, primary_key=True, index=True)
    language = Column(String(10), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    display_order = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(255), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class MonitoringLog(Base):
    __tablename__ = "monitoring_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    
    event_type = Column(String(100), nullable=False)
    
    message = Column(Text, nullable=False)
    data = Column(JSON, nullable=True)
    
    is_success = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=func.now())


class SentNotification(Base):
    __tablename__ = "sent_notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False)
    notification_type = Column(String(50), nullable=False)
    days_before = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now())

    user = relationship("User", backref="sent_notifications")
    subscription = relationship("Subscription", backref="sent_notifications")


class DiscountOffer(Base):
    __tablename__ = "discount_offers"
    __table_args__ = (
        Index("ix_discount_offers_user_type", "user_id", "notification_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True)
    notification_type = Column(String(50), nullable=False)
    discount_percent = Column(Integer, nullable=False, default=0)
    bonus_amount_kopeks = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=False)
    claimed_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    effect_type = Column(String(50), nullable=False, default="percent_discount")
    extra_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="discount_offers")
    subscription = relationship("Subscription", back_populates="discount_offers")
    logs = relationship("PromoOfferLog", back_populates="offer")


class PromoOfferTemplate(Base):
    __tablename__ = "promo_offer_templates"
    __table_args__ = (
        Index("ix_promo_offer_templates_type", "offer_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    offer_type = Column(String(50), nullable=False)
    message_text = Column(Text, nullable=False)
    button_text = Column(String(255), nullable=False)
    valid_hours = Column(Integer, nullable=False, default=24)
    discount_percent = Column(Integer, nullable=False, default=0)
    bonus_amount_kopeks = Column(Integer, nullable=False, default=0)
    active_discount_hours = Column(Integer, nullable=True)
    test_duration_hours = Column(Integer, nullable=True)
    test_squad_uuids = Column(JSON, default=list)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    creator = relationship("User")


class SubscriptionTemporaryAccess(Base):
    __tablename__ = "subscription_temporary_access"

    id = Column(Integer, primary_key=True, index=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False)
    offer_id = Column(Integer, ForeignKey("discount_offers.id", ondelete="CASCADE"), nullable=False)
    squad_uuid = Column(String(255), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=func.now())
    deactivated_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    was_already_connected = Column(Boolean, default=False, nullable=False)

    subscription = relationship("Subscription", back_populates="temporary_accesses")
    offer = relationship("DiscountOffer")


class PromoOfferLog(Base):
    __tablename__ = "promo_offer_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    offer_id = Column(Integer, ForeignKey("discount_offers.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(50), nullable=False)
    source = Column(String(100), nullable=True)
    percent = Column(Integer, nullable=True)
    effect_type = Column(String(50), nullable=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now())

    user = relationship("User", back_populates="promo_offer_logs")
    offer = relationship("DiscountOffer", back_populates="logs")

class BroadcastHistory(Base):
    __tablename__ = "broadcast_history"
    
    id = Column(Integer, primary_key=True, index=True)
    target_type = Column(String(100), nullable=False)  
    message_text = Column(Text, nullable=False)  
    has_media = Column(Boolean, default=False)
    media_type = Column(String(20), nullable=True) 
    media_file_id = Column(String(255), nullable=True)
    media_caption = Column(Text, nullable=True)
    total_count = Column(Integer, default=0) 
    sent_count = Column(Integer, default=0)  
    failed_count = Column(Integer, default=0) 
    status = Column(String(50), default="in_progress")
    admin_id = Column(Integer, ForeignKey("users.id")) 
    admin_name = Column(String(255)) 
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    admin = relationship("User", back_populates="broadcasts")

class ServerSquad(Base):
    __tablename__ = "server_squads"

    id = Column(Integer, primary_key=True, index=True)

    squad_uuid = Column(String(255), unique=True, nullable=False, index=True)
    
    display_name = Column(String(255), nullable=False)
    
    original_name = Column(String(255), nullable=True)
    
    country_code = Column(String(5), nullable=True)
    
    is_available = Column(Boolean, default=True)
    is_trial_eligible = Column(Boolean, default=False, nullable=False)
    
    price_kopeks = Column(Integer, default=0)
    
    description = Column(Text, nullable=True)
    
    sort_order = Column(Integer, default=0)
    
    max_users = Column(Integer, nullable=True) 
    current_users = Column(Integer, default=0)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    allowed_promo_groups = relationship(
        "PromoGroup",
        secondary=server_squad_promo_groups,
        back_populates="server_squads",
        lazy="selectin",
    )
    
    @property
    def price_rubles(self) -> float:
        return self.price_kopeks / 100
    
    @property
    def is_full(self) -> bool:
        if self.max_users is None:
            return False
        return self.current_users >= self.max_users
    
    @property
    def availability_status(self) -> str:
        if not self.is_available:
            return "Недоступен"
        elif self.is_full:
            return "Переполнен"
        else:
            return "Доступен"


class SubscriptionServer(Base):
    __tablename__ = "subscription_servers"
    
    id = Column(Integer, primary_key=True, index=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id"), nullable=False)
    server_squad_id = Column(Integer, ForeignKey("server_squads.id"), nullable=False)
    
    connected_at = Column(DateTime, default=func.now())
    
    paid_price_kopeks = Column(Integer, default=0)
    
    subscription = relationship("Subscription", backref="subscription_servers")
    server_squad = relationship("ServerSquad", backref="subscription_servers")


class SupportAuditLog(Base):
    __tablename__ = "support_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_telegram_id = Column(BigInteger, nullable=False)
    is_moderator = Column(Boolean, default=False)
    action = Column(String(50), nullable=False)  # close_ticket, block_user_timed, block_user_perm, unblock_user
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    target_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now())

    actor = relationship("User", foreign_keys=[actor_user_id])
    ticket = relationship("Ticket", foreign_keys=[ticket_id])

class UserMessage(Base):
    __tablename__ = "user_messages"
    id = Column(Integer, primary_key=True, index=True)
    message_text = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True) 
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    creator = relationship("User", backref="created_messages")
    
    def __repr__(self):
        return f"<UserMessage(id={self.id}, active={self.is_active}, text='{self.message_text[:50]}...')>"

class WelcomeText(Base):
    __tablename__ = "welcome_texts"

    id = Column(Integer, primary_key=True, index=True)
    text_content = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    is_enabled = Column(Boolean, default=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    creator = relationship("User", backref="created_welcome_texts")


class AdvertisingCampaign(Base):
    __tablename__ = "advertising_campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    start_parameter = Column(String(64), nullable=False, unique=True, index=True)
    bonus_type = Column(String(20), nullable=False)

    balance_bonus_kopeks = Column(Integer, default=0)

    subscription_duration_days = Column(Integer, nullable=True)
    subscription_traffic_gb = Column(Integer, nullable=True)
    subscription_device_limit = Column(Integer, nullable=True)
    subscription_squads = Column(JSON, default=list)

    is_active = Column(Boolean, default=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    registrations = relationship("AdvertisingCampaignRegistration", back_populates="campaign")

    @property
    def is_balance_bonus(self) -> bool:
        return self.bonus_type == "balance"

    @property
    def is_subscription_bonus(self) -> bool:
        return self.bonus_type == "subscription"


class AdvertisingCampaignRegistration(Base):
    __tablename__ = "advertising_campaign_registrations"
    __table_args__ = (
        UniqueConstraint("campaign_id", "user_id", name="uq_campaign_user"),
    )

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("advertising_campaigns.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    bonus_type = Column(String(20), nullable=False)
    balance_bonus_kopeks = Column(Integer, default=0)
    subscription_duration_days = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=func.now())

    campaign = relationship("AdvertisingCampaign", back_populates="registrations")
    user = relationship("User")

    @property
    def balance_bonus_rubles(self) -> float:
        return (self.balance_bonus_kopeks or 0) / 100


class TicketStatus(Enum):
    OPEN = "open"
    ANSWERED = "answered"
    CLOSED = "closed"
    PENDING = "pending"


class Ticket(Base):
    __tablename__ = "tickets"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    title = Column(String(255), nullable=False)
    status = Column(String(20), default=TicketStatus.OPEN.value, nullable=False)
    priority = Column(String(20), default="normal", nullable=False)  # low, normal, high, urgent
    # Блокировка ответов пользователя в этом тикете
    user_reply_block_permanent = Column(Boolean, default=False, nullable=False)
    user_reply_block_until = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    closed_at = Column(DateTime, nullable=True)
    # SLA reminders
    last_sla_reminder_at = Column(DateTime, nullable=True)
    
    # Связи
    user = relationship("User", backref="tickets")
    messages = relationship("TicketMessage", back_populates="ticket", cascade="all, delete-orphan")
    
    @property
    def is_open(self) -> bool:
        return self.status == TicketStatus.OPEN.value
    
    @property
    def is_answered(self) -> bool:
        return self.status == TicketStatus.ANSWERED.value
    
    @property
    def is_closed(self) -> bool:
        return self.status == TicketStatus.CLOSED.value
    
    @property
    def is_pending(self) -> bool:
        return self.status == TicketStatus.PENDING.value

    @property
    def is_user_reply_blocked(self) -> bool:
        if self.user_reply_block_permanent:
            return True
        if self.user_reply_block_until:
            try:
                from datetime import datetime
                return self.user_reply_block_until > datetime.utcnow()
            except Exception:
                return True
        return False
    
    @property
    def status_emoji(self) -> str:
        status_emojis = {
            TicketStatus.OPEN.value: "🔴",
            TicketStatus.ANSWERED.value: "🟡", 
            TicketStatus.CLOSED.value: "🟢",
            TicketStatus.PENDING.value: "⏳"
        }
        return status_emojis.get(self.status, "❓")
    
    @property
    def priority_emoji(self) -> str:
        priority_emojis = {
            "low": "🟢",
            "normal": "🟡", 
            "high": "🟠",
            "urgent": "🔴"
        }
        return priority_emojis.get(self.priority, "🟡")
    
    def __repr__(self):
        return f"<Ticket(id={self.id}, user_id={self.user_id}, status={self.status}, title='{self.title[:30]}...')>"


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    message_text = Column(Text, nullable=False)
    is_from_admin = Column(Boolean, default=False, nullable=False)
    
    # Для медиа файлов
    has_media = Column(Boolean, default=False)
    media_type = Column(String(20), nullable=True)  # photo, video, document, voice, etc.
    media_file_id = Column(String(255), nullable=True)
    media_caption = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=func.now())
    
    # Связи
    ticket = relationship("Ticket", back_populates="messages")
    user = relationship("User")
    
    @property
    def is_user_message(self) -> bool:
        return not self.is_from_admin
    
    @property
    def is_admin_message(self) -> bool:
        return self.is_from_admin

    def __repr__(self):
        return f"<TicketMessage(id={self.id}, ticket_id={self.ticket_id}, is_admin={self.is_from_admin}, text='{self.message_text[:30]}...')>"


class WebApiToken(Base):
    __tablename__ = "web_api_tokens"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    token_prefix = Column(String(32), nullable=False, index=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    last_used_ip = Column(String(64), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(String(255), nullable=True)

    def __repr__(self) -> str:
        status = "active" if self.is_active else "revoked"
        return f"<WebApiToken id={self.id} name='{self.name}' status={status}>"


class ExternalAdminApiKey(Base):
    __tablename__ = "external_admin_api_keys"

    id = Column(Integer, primary_key=True, index=True)
    creator_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    target_telegram_id = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    creator = relationship("User", back_populates="external_admin_api_keys")

    __table_args__ = (
        UniqueConstraint(
            "creator_user_id",
            "target_telegram_id",
            name="uq_external_admin_api_keys_creator_target",
        ),
        Index("ix_external_admin_api_keys_target", "target_telegram_id"),
    )

    def __repr__(self) -> str:
        return (
            "<ExternalAdminApiKey id={id} creator={creator} target={target}>".format(
                id=self.id,
                creator=self.creator_user_id,
                target=self.target_telegram_id,
            )
        )


class MainMenuButton(Base):
    __tablename__ = "main_menu_buttons"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(String(64), nullable=False)
    action_type = Column(String(20), nullable=False)
    action_value = Column(Text, nullable=False)
    visibility = Column(String(20), nullable=False, default=MainMenuButtonVisibility.ALL.value)
    is_active = Column(Boolean, nullable=False, default=True)
    display_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_main_menu_buttons_order", "display_order", "id"),
    )

    @property
    def action_type_enum(self) -> MainMenuButtonActionType:
        try:
            return MainMenuButtonActionType(self.action_type)
        except ValueError:
            return MainMenuButtonActionType.URL

    @property
    def visibility_enum(self) -> MainMenuButtonVisibility:
        try:
            return MainMenuButtonVisibility(self.visibility)
        except ValueError:
            return MainMenuButtonVisibility.ALL

    def __repr__(self) -> str:
        return (
            f"<MainMenuButton id={self.id} text='{self.text}' "
            f"action={self.action_type} visibility={self.visibility} active={self.is_active}>"
        )
