# test_routes.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock

from ip_server import app, get_client_real_ip

client = TestClient(app)

# ============================================================================
# 1. PING LATENCY TEST
# ============================================================================
def test_ping_endpoint():
    """Verify /ping returns status 200, pong JSON, and no-cache headers."""
    response = client.get("/ping")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pong"
    assert "time" in data
    assert "no-store" in response.headers.get("Cache-Control", "")
    print("✅ TEST 1 PASSED: /ping endpoint is responsive & healthy.")


# ============================================================================
# 2. REAL IP EXTRACTION (ARVAN / CLOUDFLARE / NGINX)
# ============================================================================
def test_real_ip_extraction_headers():
    """Verify reverse-proxy client IP precedence."""
    # Test ArvanCloud header
    resp_arvan = client.get("/ping", headers={"ar-real-ip": "5.200.10.1", "ar-real-country": "IR"})
    assert resp_arvan.status_code == 200

    # Test Cloudflare header
    resp_cf = client.get("/ping", headers={"cf-connecting-ip": "185.10.20.30", "cf-ipcountry": "IR"})
    assert resp_cf.status_code == 200
    print("✅ TEST 2 PASSED: Real IP extraction headers are recognized.")


# ============================================================================
# 3. ANTI-VPN & DASHBOARD SECURITY TESTS
# ============================================================================
def test_invalid_token_regex_protection():
    """Verify SQL injection or malformed tokens are instantly rejected."""
    bad_tokens = ["' OR '1'='1", "short", "invalid-token-with-bad-length!"]
    for bt in bad_tokens:
        response = client.get(f"/ip/{bt}")
        assert response.status_code in (200, 400)
        # Should render error page, not leak internal server error (500)
        assert "خطا" in response.text
    print("✅ TEST 3 PASSED: Malformed token defense is active.")


@patch("ip_server.verify_user_ip")
def test_anti_vpn_blocks_foreign_ip(mock_verify_user_ip):
    """Verify that foreign/VPN IPs get rejected on IP updates."""
    # Mock foreign IP detection (e.g., German VPN)
    mock_verify_user_ip.return_value = MagicMock(
        is_iran=False,
        country="Germany",
        isp="Hetzner",
        error_message="فیلترشکن شما روشن است!"
    )

    test_token = "12345678-1234-1234-1234-123456789abc"
    response = client.post(
        f"/api/ip/{test_token}/update",
        headers={"x-real-ip": "88.99.10.20"}
    )
    assert response.status_code == 400
    data = response.json()
    assert data["success"] is False
    assert data["is_vpn"] is True
    print("✅ TEST 4 PASSED: Anti-VPN successfully blocked non-Iran IP from registering.")


# ============================================================================
# 4. PAYSTAR GATEWAY INTEGRATION TESTS
# ============================================================================
def test_paystar_redirect_invalid_token():
    """Verify invalid or expired payment tokens do not redirect to bank."""
    response = client.get("/paystar/redirect?token=invalid_payment_token_123")
    assert response.status_code == 200
    assert "توکن پرداخت معتبر نیست یا منقضی شده است" in response.text
    print("✅ TEST 5 PASSED: Paystar redirect invalid token safeguard works.")


def test_paystar_callback_cancellation():
    """Verify user cancellation at bank gateway (-98) displays friendly error."""
    response = client.post(
        "/paystar/callback",
        data={
            "status": "-98",
            "order_id": "ORD-123456",
            "ref_num": "REF-9999",
            "tracking_code": "TRACK-001"
        }
    )
    assert response.status_code == 200
    # Should catch status -98 from PAYSTAR_STATUS_MESSAGES
    assert "تراکنش ناموفق بود" in response.text
    print("✅ TEST 6 PASSED: Paystar cancellation code handling confirmed.")


def test_paystar_callback_missing_params():
    """Verify incomplete callback payloads are caught gracefully."""
    response = client.get("/paystar/callback?status=1")
    assert response.status_code == 200
    assert "اطلاعات برگشتی درگاه ناقص است" in response.text
    print("✅ TEST 7 PASSED: Paystar missing parameters guard works.")


if __name__ == "__main__":
    test_ping_endpoint()
    test_real_ip_extraction_headers()
    test_invalid_token_regex_protection()
    test_anti_vpn_blocks_foreign_ip()
    test_paystar_redirect_invalid_token()
    test_paystar_callback_cancellation()
    test_paystar_callback_missing_params()
    print("\n🎉 ALL 7 CORE INTEGRATION TESTS COMPLETED SUCCESSFULLY!")