import sys
from pathlib import Path

# Add project root directory to Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
from app.config import get_settings, SLOT_CONFIGS
from app.services.controld import ControlDService
from app.services.adguard import AdGuardHomeService


async def main():
    settings = get_settings()
    cd = ControlDService(settings)
    agh = AdGuardHomeService(settings)

    slot_device = SLOT_CONFIGS[1]["device_id"]
    test_ip = "203.0.113.199"

    print("========================================")
    print("      TESTING DUAL-SYNC PIPELINE        ")
    print("========================================")

    # 1. Authorize Test
    print("\n[1] Authorizing IP:", test_ip)
    cd_auth = await cd.authorize_ip(slot_device, test_ip)
    print(" -> Control D Auth:", "✅ SUCCESS" if cd_auth else "❌ FAILED")

    agh_auth = await agh.allow_client_ip(test_ip)
    print(" -> AdGuard Home Auth:", "✅ SUCCESS" if agh_auth else "❌ FAILED")

    # 2. Deauthorize Test
    print("\n[2] Deauthorizing IP:", test_ip)
    cd_deauth = await cd.deauthorize_ip(slot_device, test_ip)
    print(" -> Control D Deauth:", "✅ SUCCESS" if cd_deauth else "❌ FAILED")

    agh_deauth = await agh.deauthorize_client_ip(test_ip)
    print(" -> AdGuard Home Deauth:", "✅ SUCCESS" if agh_deauth else "❌ FAILED")

    print("\n========================================")
    print("             TEST COMPLETE              ")
    print("========================================")


if __name__ == "__main__":
    asyncio.run(main())