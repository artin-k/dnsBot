# app/services/paystar.py
import hmac
import hashlib
import httpx
import logging
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

PAYSTAR_BASE_URL = "https://core.paystar.ir/api/pardakht"

class PaystarService:
    def __init__(self) -> None:
        self.gateway_id = settings.paystar_gateway_id
        self.sign_key = settings.paystar_sign_key

    def _generate_signature(self, data: str) -> str:
        """Generates the required HMAC-SHA512 cryptographic signature."""
        return hmac.new(
            self.sign_key.encode('utf-8'),
            data.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()

    async def create_payment(self, amount_toman: int, order_id: str, callback_url: str) -> str | None:
        """
        Creates a payment transaction on Paystar.
        Converts Toman to Rials as required by the API [cite: 3.3.1].
        """
        amount_rial = amount_toman * 10  # Paystar expects Rials [cite: 3.3.1]
        
        # Prepare data for signature: amount#order_id#callback [cite: 3.1.2]
        sign_data = f"{amount_rial}#{order_id}#{callback_url}"
        signature = self._generate_signature(sign_data)

        headers = {
            "Authorization": f"Bearer {self.gateway_id}",
            "Content-Type": "application/json"
        }
        payload = {
            "amount": amount_rial,
            "order_id": order_id,
            "callback": callback_url,
            "sign": signature
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(f"{PAYSTAR_BASE_URL}/create", json=payload, headers=headers, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == 1:
                        # Returns the one-time transaction token
                        return data.get("data", {}).get("token")
                logger.error(f"Paystar payment creation failed: {response.text}")
                return None
            except Exception as e:
                logger.error(f"Error connecting to Paystar: {str(e)}")
                return None

    async def verify_payment(self, amount_toman: int, ref_num: str, card_number: str, tracking_code: str) -> bool:
        """
        Verifies a successful transaction directly with the Paystar API [cite: 5.4.1].
        """
        amount_rial = amount_toman * 10
        
        # Prepare data for verification signature: amount#ref_num#card_number#tracking_code [cite: 5.4.1]
        sign_data = f"{amount_rial}#{ref_num}#{card_number}#{tracking_code}"
        signature = self._generate_signature(sign_data)

        headers = {
            "Authorization": f"Bearer {self.gateway_id}",
            "Content-Type": "application/json"
        }
        payload = {
            "ref_num": ref_num,
            "amount": amount_rial,
            "sign": signature
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(f"{PAYSTAR_BASE_URL}/verify", json=payload, headers=headers, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    return data.get("status") == 1
                logger.error(f"Paystar verification failed: {response.text}")
                return False
            except Exception as e:
                logger.error(f"Error during Paystar verification: {str(e)}")
                return False