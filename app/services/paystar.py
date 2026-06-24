# app/services/paystar.py
import hmac
import hashlib
import httpx
import logging
import asyncio  # Imported for sleep/delay [cite: 1]
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

    async def create_payment(
        self,
        amount_toman: int,
        order_id: str,
        callback_url: str,
        callback_method: int = 2,
    ) -> str | None:
        """
        Creates a payment transaction on Paystar.
        Implements an automatic 3-attempt retry loop to bypass network latency spikes [cite: 1].
        """
        if not self.gateway_id or not self.sign_key:
            logger.error("Paystar gateway credentials are not configured")
            return None

        amount_rial = amount_toman * 10  # Paystar expects Rials [cite: 3.3.1]
        
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
            "callback_method": callback_method,
            "sign": signature
        }

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.post(
                        f"{PAYSTAR_BASE_URL}/create", 
                        json=payload, 
                        headers=headers, 
                        timeout=6.0  # Safe timeout per attempt
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("status") == 1:
                            return data.get("data", {}).get("token")
                        
                        # If the error is credentials-related, do not retry [cite: 3.3.1]
                        logger.error(f"Paystar API returned error status: {data}")
                        break
                    
                    logger.warning(f"Paystar connection attempt {attempt} returned status {response.status_code}. Retrying...")
                except Exception as e:
                    logger.warning(f"Paystar connection attempt {attempt} failed: {str(e)}. Retrying...")
            
            # Wait briefly before attempting the next retry in the background [cite: 1]
            if attempt < max_retries:
                await asyncio.sleep(0.5)

        return None

    async def verify_payment(self, amount_toman: int, ref_num: str, card_number: str, tracking_code: str) -> bool:
        """
        Verifies a successful transaction directly with the Paystar API [cite: 5.4.1].
        """
        if not self.gateway_id or not self.sign_key:
            logger.error("Paystar gateway credentials are not configured")
            return False

        amount_rial = amount_toman * 10
        
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

        # Apply a 2-attempt retry loop on verification as well for extreme database stability [cite: 1]
        max_retries = 2
        for attempt in range(1, max_retries + 1):
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.post(f"{PAYSTAR_BASE_URL}/verify", json=payload, headers=headers, timeout=8.0)
                    if response.status_code == 200:
                        data = response.json()
                        return data.get("status") == 1
                    logger.warning(f"Paystar verification attempt {attempt} returned status {response.status_code}. Retrying...")
                except Exception as e:
                    logger.warning(f"Paystar verification attempt {attempt} failed: {str(e)}. Retrying...")
            
            if attempt < max_retries:
                await asyncio.sleep(0.5)

        return False
