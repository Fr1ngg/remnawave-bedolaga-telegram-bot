from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MiniAppBranding(BaseModel):
    service_name: Dict[str, Optional[str]] = Field(default_factory=dict)
    service_description: Dict[str, Optional[str]] = Field(default_factory=dict)


class MiniAppSubscriptionRequest(BaseModel):
    init_data: str = Field(..., alias="initData")


class MiniAppSubscriptionUser(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: str
    language: Optional[str] = None
    status: str
    subscription_status: str
    subscription_actual_status: str
    status_label: str
    expires_at: Optional[datetime] = None
    device_limit: Optional[int] = None
    traffic_used_gb: float = 0.0
    traffic_used_label: str
    traffic_limit_gb: Optional[int] = None
    traffic_limit_label: str
    lifetime_used_traffic_gb: float = 0.0
    has_active_subscription: bool = False
    promo_offer_discount_percent: int = 0
    promo_offer_discount_expires_at: Optional[datetime] = None
    promo_offer_discount_source: Optional[str] = None


class MiniAppPromoGroup(BaseModel):
    id: int
    name: str
    server_discount_percent: int = 0
    traffic_discount_percent: int = 0
    device_discount_percent: int = 0
    period_discounts: Dict[int, int] = Field(default_factory=dict)
    apply_discounts_to_addons: bool = True


class MiniAppAutoPromoGroupLevel(BaseModel):
    id: int
    name: str
    threshold_kopeks: int
    threshold_rubles: float
    threshold_label: str
    is_reached: bool = False
    is_current: bool = False
    server_discount_percent: int = 0
    traffic_discount_percent: int = 0
    device_discount_percent: int = 0
    period_discounts: Dict[int, int] = Field(default_factory=dict)
    apply_discounts_to_addons: bool = True


class MiniAppConnectedServer(BaseModel):
    uuid: str
    name: str


class MiniAppDevice(BaseModel):
    hwid: Optional[str] = None
    platform: Optional[str] = None
    device_model: Optional[str] = None
    app_version: Optional[str] = None
    last_seen: Optional[str] = None
    last_ip: Optional[str] = None


class MiniAppDeviceRemovalRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    hwid: str


class MiniAppDeviceRemovalResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None


class MiniAppTransaction(BaseModel):
    id: int
    type: str
    amount_kopeks: int
    amount_rubles: float
    description: Optional[str] = None
    payment_method: Optional[str] = None
    external_id: Optional[str] = None
    is_completed: bool
    created_at: datetime
    completed_at: Optional[datetime] = None


class MiniAppPromoOffer(BaseModel):
    id: int
    status: str
    notification_type: Optional[str] = None
    offer_type: Optional[str] = None
    effect_type: Optional[str] = None
    discount_percent: int = 0
    bonus_amount_kopeks: int = 0
    bonus_amount_label: Optional[str] = None
    expires_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    is_active: bool = False
    template_id: Optional[int] = None
    template_name: Optional[str] = None
    button_text: Optional[str] = None
    title: Optional[str] = None
    message_text: Optional[str] = None
    icon: Optional[str] = None
    test_squads: List[MiniAppConnectedServer] = Field(default_factory=list)
    active_discount_expires_at: Optional[datetime] = None
    active_discount_started_at: Optional[datetime] = None
    active_discount_duration_seconds: Optional[int] = None


class MiniAppPromoOfferClaimRequest(BaseModel):
    init_data: str = Field(..., alias="initData")


class MiniAppPromoOfferClaimResponse(BaseModel):
    success: bool = True
    code: Optional[str] = None


class MiniAppSubscriptionAutopay(BaseModel):
    enabled: bool = False
    autopay_enabled: Optional[bool] = None
    autopay_enabled_at: Optional[datetime] = None
    days_before: Optional[int] = None
    autopay_days_before: Optional[int] = None
    default_days_before: Optional[int] = None
    autopay_days_options: List[int] = Field(default_factory=list)
    days_options: List[int] = Field(default_factory=list)
    options: List[int] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class MiniAppSubscriptionRenewalPeriod(BaseModel):
    id: str
    days: Optional[int] = None
    months: Optional[int] = None
    price_kopeks: Optional[int] = Field(default=None, alias="priceKopeks")
    price_label: Optional[str] = Field(default=None, alias="priceLabel")
    original_price_kopeks: Optional[int] = Field(default=None, alias="originalPriceKopeks")
    original_price_label: Optional[str] = Field(default=None, alias="originalPriceLabel")
    discount_percent: int = Field(default=0, alias="discountPercent")
    price_per_month_kopeks: Optional[int] = Field(default=None, alias="pricePerMonthKopeks")
    price_per_month_label: Optional[str] = Field(default=None, alias="pricePerMonthLabel")
    is_recommended: bool = Field(default=False, alias="isRecommended")
    description: Optional[str] = None
    badge: Optional[str] = None
    title: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class MiniAppSubscriptionRenewalOptionsRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    subscription_id: Optional[int] = Field(default=None, alias="subscriptionId")

    model_config = ConfigDict(populate_by_name=True)


class MiniAppSubscriptionRenewalOptionsResponse(BaseModel):
    success: bool = True
    subscription_id: Optional[int] = Field(default=None, alias="subscriptionId")
    currency: str
    balance_kopeks: Optional[int] = Field(default=None, alias="balanceKopeks")
    balance_label: Optional[str] = Field(default=None, alias="balanceLabel")
    promo_group: Optional[MiniAppPromoGroup] = Field(default=None, alias="promoGroup")
    promo_offer: Optional[Dict[str, Any]] = Field(default=None, alias="promoOffer")
    periods: List[MiniAppSubscriptionRenewalPeriod] = Field(default_factory=list)
    default_period_id: Optional[str] = Field(default=None, alias="defaultPeriodId")
    missing_amount_kopeks: Optional[int] = Field(default=None, alias="missingAmountKopeks")
    status_message: Optional[str] = Field(default=None, alias="statusMessage")
    autopay_enabled: bool = False
    autopay_days_before: Optional[int] = None
    autopay_days_options: List[int] = Field(default_factory=list)
    autopay: Optional[MiniAppSubscriptionAutopay] = None
    autopay_settings: Optional[MiniAppSubscriptionAutopay] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class MiniAppSubscriptionRenewalRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    subscription_id: Optional[int] = Field(default=None, alias="subscriptionId")
    period_id: Optional[str] = Field(default=None, alias="periodId")
    period_days: Optional[int] = Field(default=None, alias="periodDays")
    method: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class MiniAppSubscriptionRenewalResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None
    balance_kopeks: Optional[int] = Field(default=None, alias="balanceKopeks")
    balance_label: Optional[str] = Field(default=None, alias="balanceLabel")
    subscription_id: Optional[int] = Field(default=None, alias="subscriptionId")
    renewed_until: Optional[datetime] = Field(default=None, alias="renewedUntil")
    requires_payment: bool = Field(default=False, alias="requiresPayment")
    payment_method: Optional[str] = Field(default=None, alias="paymentMethod")
    payment_url: Optional[str] = Field(default=None, alias="paymentUrl")
    payment_amount_kopeks: Optional[int] = Field(default=None, alias="paymentAmountKopeks")
    payment_id: Optional[int] = Field(default=None, alias="paymentId")
    invoice_id: Optional[str] = Field(default=None, alias="invoiceId")
    payment_payload: Optional[str] = Field(default=None, alias="paymentPayload")
    payment_extra: Optional[Dict[str, Any]] = Field(default=None, alias="paymentExtra")

    model_config = ConfigDict(populate_by_name=True)


class MiniAppSubscriptionAutopayRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    subscription_id: Optional[int] = Field(default=None, alias="subscriptionId")
    enabled: Optional[bool] = None
    days_before: Optional[int] = Field(default=None, alias="daysBefore")

    model_config = ConfigDict(populate_by_name=True)


class MiniAppSubscriptionAutopayResponse(BaseModel):
    success: bool = True
    subscription_id: Optional[int] = Field(default=None, alias="subscriptionId")
    autopay_enabled: bool = False
    autopay_days_before: Optional[int] = None
    autopay_days_options: List[int] = Field(default_factory=list)
    autopay: Optional[MiniAppSubscriptionAutopay] = None
    autopay_settings: Optional[MiniAppSubscriptionAutopay] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class MiniAppPromoCode(BaseModel):
    code: str
    type: Optional[str] = None
    balance_bonus_kopeks: int = 0
    subscription_days: int = 0
    max_uses: Optional[int] = None
    current_uses: Optional[int] = None
    valid_until: Optional[datetime] = None


class MiniAppPromoCodeActivationRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    code: str


class MiniAppPromoCodeActivationResponse(BaseModel):
    success: bool = True
    description: Optional[str] = None
    promocode: Optional[MiniAppPromoCode] = None


class MiniAppFaqItem(BaseModel):
    id: int
    title: Optional[str] = None
    content: Optional[str] = None
    display_order: Optional[int] = None


class MiniAppFaq(BaseModel):
    requested_language: str
    language: str
    is_enabled: bool = True
    total: int = 0
    items: List[MiniAppFaqItem] = Field(default_factory=list)


class MiniAppRichTextDocument(BaseModel):
    requested_language: str
    language: str
    title: Optional[str] = None
    is_enabled: bool = True
    content: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MiniAppLegalDocuments(BaseModel):
    public_offer: Optional[MiniAppRichTextDocument] = None
    service_rules: Optional[MiniAppRichTextDocument] = None
    privacy_policy: Optional[MiniAppRichTextDocument] = None


class MiniAppReferralTerms(BaseModel):
    minimum_topup_kopeks: int = 0
    minimum_topup_label: Optional[str] = None
    first_topup_bonus_kopeks: int = 0
    first_topup_bonus_label: Optional[str] = None
    inviter_bonus_kopeks: int = 0
    inviter_bonus_label: Optional[str] = None
    commission_percent: float = 0.0


class MiniAppReferralStats(BaseModel):
    invited_count: int = 0
    paid_referrals_count: int = 0
    active_referrals_count: int = 0
    total_earned_kopeks: int = 0
    total_earned_label: Optional[str] = None
    month_earned_kopeks: int = 0
    month_earned_label: Optional[str] = None
    conversion_rate: float = 0.0


class MiniAppReferralRecentEarning(BaseModel):
    amount_kopeks: int = 0
    amount_label: Optional[str] = None
    reason: Optional[str] = None
    referral_name: Optional[str] = None
    created_at: Optional[datetime] = None


class MiniAppReferralItem(BaseModel):
    id: int
    telegram_id: Optional[int] = None
    full_name: Optional[str] = None
    username: Optional[str] = None
    created_at: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    has_made_first_topup: bool = False
    balance_kopeks: int = 0
    balance_label: Optional[str] = None
    total_earned_kopeks: int = 0
    total_earned_label: Optional[str] = None
    topups_count: int = 0
    days_since_registration: Optional[int] = None
    days_since_activity: Optional[int] = None
    status: Optional[str] = None


class MiniAppReferralList(BaseModel):
    total_count: int = 0
    has_next: bool = False
    has_prev: bool = False
    current_page: int = 1
    total_pages: int = 1
    items: List[MiniAppReferralItem] = Field(default_factory=list)


class MiniAppReferralInfo(BaseModel):
    referral_code: Optional[str] = None
    referral_link: Optional[str] = None
    terms: Optional[MiniAppReferralTerms] = None
    stats: Optional[MiniAppReferralStats] = None
    recent_earnings: List[MiniAppReferralRecentEarning] = Field(default_factory=list)
    referrals: Optional[MiniAppReferralList] = None


class MiniAppPaymentMethodsRequest(BaseModel):
    init_data: str = Field(..., alias="initData")


class MiniAppPaymentIntegrationType(str, Enum):
    IFRAME = "iframe"
    REDIRECT = "redirect"


class MiniAppPaymentOption(BaseModel):
    id: str
    icon: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    title_key: Optional[str] = Field(default=None, alias="titleKey")
    description_key: Optional[str] = Field(default=None, alias="descriptionKey")

    model_config = ConfigDict(populate_by_name=True)


class MiniAppPaymentIframeConfig(BaseModel):
    expected_origin: str

    @model_validator(mode="after")
    def _normalize_expected_origin(
        cls, values: "MiniAppPaymentIframeConfig"
    ) -> "MiniAppPaymentIframeConfig":
        origin = (values.expected_origin or "").strip()
        if not origin:
            raise ValueError("expected_origin must not be empty")

        parsed = urlparse(origin)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("expected_origin must include scheme and host")

        values.expected_origin = f"{parsed.scheme}://{parsed.netloc}"
        return values


class MiniAppPaymentMethod(BaseModel):
    id: str
    name: Optional[str] = None
    icon: Optional[str] = None
    requires_amount: bool = False
    currency: str = "RUB"
    min_amount_kopeks: Optional[int] = None
    max_amount_kopeks: Optional[int] = None
    amount_step_kopeks: Optional[int] = None
    integration_type: MiniAppPaymentIntegrationType
    options: List[MiniAppPaymentOption] = Field(default_factory=list)
    iframe_config: Optional[MiniAppPaymentIframeConfig] = None

    @model_validator(mode="after")
    def _ensure_iframe_config(cls, values: "MiniAppPaymentMethod") -> "MiniAppPaymentMethod":
        if (
            values.integration_type == MiniAppPaymentIntegrationType.IFRAME
            and values.iframe_config is None
        ):
            raise ValueError("iframe_config is required when integration_type is 'iframe'")
        return values


class MiniAppPaymentMethodsResponse(BaseModel):
    methods: List[MiniAppPaymentMethod] = Field(default_factory=list)


class MiniAppPaymentCreateRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    method: str
    amount_rubles: Optional[float] = Field(default=None, alias="amountRubles")
    amount_kopeks: Optional[int] = Field(default=None, alias="amountKopeks")
    payment_option: Optional[str] = Field(default=None, alias="option")


class MiniAppPaymentCreateResponse(BaseModel):
    success: bool = True
    method: str
    payment_url: Optional[str] = None
    amount_kopeks: Optional[int] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class MiniAppPaymentStatusQuery(BaseModel):
    method: str
    local_payment_id: Optional[int] = Field(default=None, alias="localPaymentId")
    payment_link_id: Optional[str] = Field(default=None, alias="paymentLinkId")
    invoice_id: Optional[str] = Field(default=None, alias="invoiceId")
    payment_id: Optional[str] = Field(default=None, alias="paymentId")
    payload: Optional[str] = None
    amount_kopeks: Optional[int] = Field(default=None, alias="amountKopeks")
    started_at: Optional[str] = Field(default=None, alias="startedAt")


class MiniAppPaymentStatusRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    payments: List[MiniAppPaymentStatusQuery] = Field(default_factory=list)


class MiniAppPaymentStatusResult(BaseModel):
    method: str
    status: str
    is_paid: bool = False
    amount_kopeks: Optional[int] = None
    currency: Optional[str] = None
    completed_at: Optional[datetime] = None
    transaction_id: Optional[int] = None
    external_id: Optional[str] = None
    message: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class MiniAppPaymentStatusResponse(BaseModel):
    results: List[MiniAppPaymentStatusResult] = Field(default_factory=list)


class MiniAppSubscriptionResponse(BaseModel):
    success: bool = True
    subscription_id: Optional[int] = None
    remnawave_short_uuid: Optional[str] = None
    user: MiniAppSubscriptionUser
    subscription_url: Optional[str] = None
    subscription_crypto_link: Optional[str] = None
    subscription_purchase_url: Optional[str] = None
    links: List[str] = Field(default_factory=list)
    ss_conf_links: Dict[str, str] = Field(default_factory=dict)
    connected_squads: List[str] = Field(default_factory=list)
    connected_servers: List[MiniAppConnectedServer] = Field(default_factory=list)
    connected_devices_count: int = 0
    connected_devices: List[MiniAppDevice] = Field(default_factory=list)
    happ: Optional[Dict[str, Any]] = None
    happ_link: Optional[str] = None
    happ_crypto_link: Optional[str] = None
    happ_cryptolink_redirect_link: Optional[str] = None
    happ_cryptolink_redirect_template: Optional[str] = None
    balance_kopeks: int = 0
    balance_rubles: float = 0.0
    balance_currency: Optional[str] = None
    transactions: List[MiniAppTransaction] = Field(default_factory=list)
    promo_offers: List[MiniAppPromoOffer] = Field(default_factory=list)
    promo_group: Optional[MiniAppPromoGroup] = None
    auto_assign_promo_groups: List[MiniAppAutoPromoGroupLevel] = Field(default_factory=list)
    total_spent_kopeks: int = 0
    total_spent_rubles: float = 0.0
    total_spent_label: Optional[str] = None
    subscription_type: str
    autopay_enabled: bool = False
    autopay_days_before: Optional[int] = None
    autopay_days_options: List[int] = Field(default_factory=list)
    autopay: Optional[MiniAppSubscriptionAutopay] = None
    autopay_settings: Optional[MiniAppSubscriptionAutopay] = None
    branding: Optional[MiniAppBranding] = None
    faq: Optional[MiniAppFaq] = None
    legal_documents: Optional[MiniAppLegalDocuments] = None
    referral: Optional[MiniAppReferralInfo] = None
    subscription_missing: bool = False
    subscription_missing_reason: Optional[str] = None
    trial_available: bool = False
    trial_duration_days: Optional[int] = None
    trial_status: Optional[str] = None
    trial_payment_required: bool = Field(default=False, alias="trialPaymentRequired")
    trial_price_kopeks: Optional[int] = Field(default=None, alias="trialPriceKopeks")
    trial_price_label: Optional[str] = Field(default=None, alias="trialPriceLabel")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class MiniAppSubscriptionServerOption(BaseModel):
    uuid: str
    name: Optional[str] = None
    price_kopeks: Optional[int] = None
    price_label: Optional[str] = None
    discount_percent: Optional[int] = None
    is_connected: bool = False
    is_available: bool = True
    disabled_reason: Optional[str] = None


class MiniAppSubscriptionTrafficOption(BaseModel):
    value: Optional[int] = None
    label: Optional[str] = None
    price_kopeks: Optional[int] = None
    price_label: Optional[str] = None
    is_current: bool = False
    is_available: bool = True
    description: Optional[str] = None


class MiniAppSubscriptionDeviceOption(BaseModel):
    value: int
    label: Optional[str] = None
    price_kopeks: Optional[int] = None
    price_label: Optional[str] = None


class MiniAppSubscriptionCurrentSettings(BaseModel):
    servers: List[MiniAppConnectedServer] = Field(default_factory=list)
    traffic_limit_gb: Optional[int] = None
    traffic_limit_label: Optional[str] = None
    device_limit: int = 0


class MiniAppSubscriptionServersSettings(BaseModel):
    available: List[MiniAppSubscriptionServerOption] = Field(default_factory=list)
    min: int = 0
    max: int = 0
    can_update: bool = True
    hint: Optional[str] = None


class MiniAppSubscriptionTrafficSettings(BaseModel):
    options: List[MiniAppSubscriptionTrafficOption] = Field(default_factory=list)
    can_update: bool = True
    current_value: Optional[int] = None


class MiniAppSubscriptionDevicesSettings(BaseModel):
    options: List[MiniAppSubscriptionDeviceOption] = Field(default_factory=list)
    can_update: bool = True
    min: int = 0
    max: int = 0
    step: int = 1
    current: int = 0
    price_kopeks: Optional[int] = None
    price_label: Optional[str] = None


class MiniAppSubscriptionBillingContext(BaseModel):
    months_remaining: int = 1
    period_hint_days: Optional[int] = None
    renews_at: Optional[datetime] = None


class MiniAppSubscriptionSettings(BaseModel):
    subscription_id: int
    currency: str = "RUB"
    current: MiniAppSubscriptionCurrentSettings
    servers: MiniAppSubscriptionServersSettings
    traffic: MiniAppSubscriptionTrafficSettings
    devices: MiniAppSubscriptionDevicesSettings
    billing: Optional[MiniAppSubscriptionBillingContext] = None


class MiniAppSubscriptionSettingsResponse(BaseModel):
    success: bool = True
    settings: MiniAppSubscriptionSettings


class MiniAppSubscriptionSettingsRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    subscription_id: Optional[int] = None

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _populate_aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            if "subscriptionId" in values and "subscription_id" not in values:
                values["subscription_id"] = values["subscriptionId"]
        return values


class MiniAppSubscriptionServersUpdateRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    subscription_id: Optional[int] = None
    servers: Optional[List[str]] = None
    squads: Optional[List[str]] = None
    server_uuids: Optional[List[str]] = None
    squad_uuids: Optional[List[str]] = None

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _populate_aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            alias_map = {
                "subscriptionId": "subscription_id",
                "serverUuids": "server_uuids",
                "squadUuids": "squad_uuids",
            }
            for alias, target in alias_map.items():
                if alias in values and target not in values:
                    values[target] = values[alias]
        return values


class MiniAppSubscriptionTrafficUpdateRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    subscription_id: Optional[int] = None
    traffic: Optional[int] = None
    traffic_gb: Optional[int] = None

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _populate_aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            alias_map = {
                "subscriptionId": "subscription_id",
                "trafficGb": "traffic_gb",
            }
            for alias, target in alias_map.items():
                if alias in values and target not in values:
                    values[target] = values[alias]
        return values


class MiniAppSubscriptionDevicesUpdateRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    subscription_id: Optional[int] = None
    devices: Optional[int] = None
    device_limit: Optional[int] = None

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _populate_aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            alias_map = {
                "subscriptionId": "subscription_id",
                "deviceLimit": "device_limit",
            }
            for alias, target in alias_map.items():
                if alias in values and target not in values:
                    values[target] = values[alias]
        return values


class MiniAppSubscriptionUpdateResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None


class MiniAppSubscriptionPurchaseOptionsRequest(BaseModel):
    init_data: str = Field(..., alias="initData")

    model_config = ConfigDict(populate_by_name=True)


class MiniAppSubscriptionPurchaseOptionsResponse(BaseModel):
    success: bool = True
    currency: str
    balance_kopeks: Optional[int] = Field(default=None, alias="balanceKopeks")
    balance_label: Optional[str] = Field(default=None, alias="balanceLabel")
    subscription_id: Optional[int] = Field(default=None, alias="subscriptionId")
    data: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


class MiniAppSubscriptionPurchasePreviewRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    subscription_id: Optional[int] = Field(default=None, alias="subscriptionId")
    selection: Optional[Dict[str, Any]] = None
    period_id: Optional[str] = Field(default=None, alias="periodId")
    period_days: Optional[int] = Field(default=None, alias="periodDays")
    period: Optional[str] = None
    traffic_value: Optional[int] = Field(default=None, alias="trafficValue")
    traffic: Optional[int] = None
    traffic_gb: Optional[int] = Field(default=None, alias="trafficGb")
    servers: Optional[List[str]] = None
    countries: Optional[List[str]] = None
    server_uuids: Optional[List[str]] = Field(default=None, alias="serverUuids")
    devices: Optional[int] = None
    device_limit: Optional[int] = Field(default=None, alias="deviceLimit")

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _merge_selection(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        selection = values.get("selection")
        if isinstance(selection, dict):
            merged = {**selection, **values}
        else:
            merged = dict(values)
        aliases = {
            "period_id": ("periodId", "period", "code"),
            "period_days": ("periodDays",),
            "traffic_value": ("trafficValue", "traffic", "trafficGb"),
            "servers": ("countries", "server_uuids", "serverUuids"),
            "devices": ("deviceLimit",),
        }
        for target, sources in aliases.items():
            if merged.get(target) is not None:
                continue
            for source in sources:
                if source in merged and merged[source] is not None:
                    merged[target] = merged[source]
                    break
        return merged


class MiniAppSubscriptionPurchasePreviewResponse(BaseModel):
    success: bool = True
    preview: Dict[str, Any] = Field(default_factory=dict)
    balance_kopeks: Optional[int] = Field(default=None, alias="balanceKopeks")
    balance_label: Optional[str] = Field(default=None, alias="balanceLabel")

    model_config = ConfigDict(populate_by_name=True)


class MiniAppSubscriptionPurchaseRequest(MiniAppSubscriptionPurchasePreviewRequest):
    pass


class MiniAppSubscriptionPurchaseResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None
    balance_kopeks: Optional[int] = Field(default=None, alias="balanceKopeks")
    balance_label: Optional[str] = Field(default=None, alias="balanceLabel")
    subscription_id: Optional[int] = Field(default=None, alias="subscriptionId")

    model_config = ConfigDict(populate_by_name=True)


class MiniAppSubscriptionTrialRequest(BaseModel):
    init_data: str = Field(..., alias="initData")

    model_config = ConfigDict(populate_by_name=True)


class MiniAppSubscriptionTrialResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None
    subscription_id: Optional[int] = Field(default=None, alias="subscriptionId")
    trial_status: Optional[str] = Field(default=None, alias="trialStatus")
    trial_duration_days: Optional[int] = Field(default=None, alias="trialDurationDays")
    charged_amount_kopeks: Optional[int] = Field(default=None, alias="chargedAmountKopeks")
    charged_amount_label: Optional[str] = Field(default=None, alias="chargedAmountLabel")
    balance_kopeks: Optional[int] = Field(default=None, alias="balanceKopeks")
    balance_label: Optional[str] = Field(default=None, alias="balanceLabel")

    model_config = ConfigDict(populate_by_name=True)

