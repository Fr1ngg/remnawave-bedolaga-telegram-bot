from datetime import datetime, timedelta
from typing import Optional, List
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
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.sql import func


Base = declarative_base()


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
    MANUAL = "manual"  

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


class PromoGroup(Base):
    __tablename__ = "promo_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    server_discount_percent = Column(Integer, nullable=False, default=0)
    traffic_discount_percent = Column(Integer, nullable=False, default=0)
    device_discount_percent = Column(Integer, nullable=False, default=0)
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    users = relationship("User", back_populates="promo_group")

    def get_discount_percent(self, category: str, period_days: Optional[int] = None) -> int:
        mapping = {
            "servers": self.server_discount_percent,
            "traffic": self.traffic_discount_percent,
            "devices": self.device_discount_percent,
        }
        percent = mapping.get(category, 0)

        if self.is_default and period_days is not None:
            try:
                from app.config import settings

                if settings.is_base_promo_group_period_discount_enabled():
                    discounts = settings.get_base_promo_group_period_discounts()
                    if period_days in discounts:
                        period_discount = discounts[period_days]
                        percent = period_discount
            except Exception:
                pass

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
    lifetime_used_traffic_bytes = Column(BigInteger, default=0)
    last_remnawave_sync = Column(DateTime, nullable=True)
    trojan_password = Column(String(255), nullable=True)
    vless_uuid = Column(String(255), nullable=True)
    ss_password = Column(String(255), nullable=True)
    has_made_first_topup: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    promo_group_id = Column(Integer, ForeignKey("promo_groups.id", ondelete="RESTRICT"), nullable=False, index=True)
    promo_group = relationship("PromoGroup", back_populates="users")
    
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
    
    device_limit = Column(Integer, default=1)
    
    connected_squads = Column(JSON, default=list)
    
    autopay_enabled = Column(Boolean, default=False)
    autopay_days_before = Column(Integer, default=3)
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    remnawave_short_uuid = Column(String(255), nullable=True)
    
    user = relationship("User", back_populates="subscription")
    
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
        from datetime import timedelta, datetime
    
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
    
    price_kopeks = Column(Integer, default=0)
    
    description = Column(Text, nullable=True)
    
    sort_order = Column(Integer, default=0)
    
    max_users = Column(Integer, nullable=True) 
    current_users = Column(Integer, default=0) 
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
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
