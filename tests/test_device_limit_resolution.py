import pytest

from app.utils import subscription_utils
from app.utils.subscription_utils import (
    resolve_hwid_device_limit,
    resolve_simple_subscription_device_limit,
)


class DummySubscription:
    def __init__(self, device_limit=None):
        self.device_limit = device_limit


class StubSettings:
    def __init__(
        self,
        enabled: bool,
        disabled_amount,
        *,
        simple_limit: int = 3,
        disabled_selection_amount: int = 0,
    ):
        self._enabled = enabled
        self._disabled_amount = disabled_amount
        self._disabled_selection_amount = disabled_selection_amount
        self.SIMPLE_SUBSCRIPTION_DEVICE_LIMIT = simple_limit

    def is_devices_selection_enabled(self) -> bool:
        return self._enabled

    def get_disabled_mode_device_limit(self):
        return self._disabled_amount

    def get_devices_selection_disabled_amount(self) -> int:
        return self._disabled_selection_amount


@pytest.mark.parametrize(
    "forced_amount, expected",
    [
        (0, None),
        (5, 5),
    ],
)
def test_resolve_hwid_device_limit_disabled_mode(monkeypatch, forced_amount, expected):
    subscription = DummySubscription(device_limit=42)

    monkeypatch.setattr(
        subscription_utils,
        "settings",
        StubSettings(
            enabled=False,
            disabled_amount=None if forced_amount == 0 else forced_amount,
            disabled_selection_amount=forced_amount,
        ),
    )

    assert resolve_hwid_device_limit(subscription) == expected


def test_resolve_hwid_device_limit_enabled_mode(monkeypatch):
    subscription = DummySubscription(device_limit=4)

    monkeypatch.setattr(
        subscription_utils,
        "settings",
        StubSettings(enabled=True, disabled_amount=None),
    )

    assert resolve_hwid_device_limit(subscription) == 4


def test_resolve_hwid_device_limit_enabled_ignores_non_positive(monkeypatch):
    subscription = DummySubscription(device_limit=0)

    monkeypatch.setattr(
        subscription_utils,
        "settings",
        StubSettings(enabled=True, disabled_amount=None),
    )

    assert resolve_hwid_device_limit(subscription) is None


@pytest.mark.parametrize(
    "enabled, simple_limit, disabled_amount, disabled_selection_amount, expected",
    [
        (True, 4, None, 0, 4),
        (False, 4, None, 0, 0),
        (False, 4, 7, 7, 7),
    ],
)
def test_resolve_simple_subscription_device_limit(
    monkeypatch,
    enabled,
    simple_limit,
    disabled_amount,
    disabled_selection_amount,
    expected,
):
    monkeypatch.setattr(
        subscription_utils,
        "settings",
        StubSettings(
            enabled=enabled,
            disabled_amount=disabled_amount,
            simple_limit=simple_limit,
            disabled_selection_amount=disabled_selection_amount,
        ),
    )

    assert resolve_simple_subscription_device_limit() == expected
