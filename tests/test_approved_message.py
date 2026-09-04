# tests/test_approved_message.py
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest
from app.services.payment_service import ApprovedPaymentResult
from bot.routers.admin import _approved_message


@pytest.fixture
def mock_settings():
    with patch("bot.routers.admin.get_settings") as mock_get:
        settings_mock = MagicMock()
        settings_mock.adguard_primary_dns = "192.168.1.2"
        settings_mock.adguard_secondary_dns = "192.168.1.3"
        settings_mock.adguard_doh_url = "https://dns.example.com/dns-query"
        mock_get.return_value = settings_mock
        yield settings_mock


@pytest.fixture
def mock_payment_result():
    return ApprovedPaymentResult(
        user_telegram_id=123456789,
        order_kind="purchase",
        service_username="test_user",
        plan_title="VIP 1 Month",
        volume_gb=0,
        duration_days=30,
        config_link="https://example.com/sub",
        subscription_link=None,
        new_expire_at=datetime(2027, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


def test_approved_message_waiting_inventory():
    result = ApprovedPaymentResult(
        user_telegram_id=123456789,
        order_kind="purchase",
        service_username="test_user",
        plan_title="VIP",
        volume_gb=0,
        duration_days=30,
        config_link=None,
        subscription_link=None,
        waiting_inventory=True,
    )
    msg = _approved_message(result)
    assert "پشتیبانی به‌زودی اطلاعات اشتراک شما را ارسال می‌کند" in msg


def test_approved_message_html_well_formedness(mock_payment_result, mock_settings):
    """Ensures Telegram will not throw 'can't parse entities' due to broken HTML."""
    msg = _approved_message(
        result=mock_payment_result,
        expire_at=datetime(2027, 2, 1, 10, 0, 0, tzinfo=timezone.utc),
        ipv4_primary="76.76.2.162",
        ipv4_secondary="76.76.10.162",
        custom_username="artin_device|default|de",
    )

    # Wrap in root to test XML validity of HTML fragments
    wrapped_xml = f"<root>{msg}</root>"
    try:
        ET.fromstring(wrapped_xml)
    except ET.ParseError as e:
        pytest.fail(f"Telegram HTML entity parse failure: {e}")


def test_approved_message_contains_both_providers(mock_payment_result, mock_settings):
    msg = _approved_message(
        result=mock_payment_result,
        expire_at=datetime(2027, 2, 1, 10, 0, 0, tzinfo=timezone.utc),
        ipv4_primary="76.76.2.116",
        ipv4_secondary="76.76.10.116",
        custom_username="tg_user_123|default|dxb",
    )

    # Verify Control D
    assert "Control D" in msg
    assert "76.76.2.116" in msg
    assert "76.76.10.116" in msg
    assert "76.76.2.22" in msg

    # Verify AdGuard Home
    assert "AdGuard Home" in msg
    assert "192.168.1.2" in msg
    assert "192.168.1.3" in msg
    assert "https://dns.example.com/dns-query" in msg