from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, VPNService
from bot.routers import services


def _callback(telegram_id: int, data: str) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=telegram_id),
        message=SimpleNamespace(
            answer=AsyncMock(),
            edit_text=AsyncMock(),
            chat=SimpleNamespace(id=telegram_id),
        ),
        answer=AsyncMock(),
        bot=AsyncMock(),
    )


async def _create_user(session: AsyncSession, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, referral_code=f"ref-{telegram_id}")
    session.add(user)
    await session.flush()
    return user


async def _create_service(session: AsyncSession, user: User, device_id: str) -> VPNService:
    service = VPNService(
        user_id=user.id,
        username="PlayStation|default|1",
        controld_device_id=device_id,
        authorized_ip="8.8.8.8",
        expire_at=datetime.now(timezone.utc) + timedelta(days=30),
        status="active",
    )
    session.add(service)
    await session.commit()
    return service


@pytest.fixture
def slot_configs() -> dict[int, dict[str, str]]:
    return {
        1: {"name": "Germany", "device_id": "device-germany", "dns_primary": "1.1.1.1", "dns_secondary": "1.0.0.1"},
        4: {"name": "UAE", "device_id": "device-uae", "dns_primary": "4.4.4.4", "dns_secondary": "4.0.0.4"},
        5: {"name": "Turkey", "device_id": "device-turkey", "dns_primary": "5.5.5.5", "dns_secondary": "5.0.0.5"},
    }


@pytest.mark.asyncio
async def test_delivery_keyboard_includes_service_specific_location_button(test_session: AsyncSession) -> None:
    with (
        patch.object(services.AppSettingsService, "get_teaching_video_link", new=AsyncMock(return_value=None)),
        patch.object(services.AppSettingsService, "get_support_username", new=AsyncMock(return_value=None)),
    ):
        keyboard = await services.create_secure_ip_update_keyboard(test_session, 42)

    callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "change_default_loc_select:42" in callback_data


@pytest.mark.asyncio
async def test_location_switch_updates_owned_service_after_provider_sync(
    test_session: AsyncSession,
    mock_settings,
    slot_configs: dict[int, dict[str, str]],
) -> None:
    user = await _create_user(test_session, 1001)
    service = await _create_service(test_session, user, slot_configs[1]["device_id"])
    callback = _callback(user.telegram_id, f"apply_def_loc:{service.id}:4")
    controld = MagicMock(
        authorize_ip=AsyncMock(return_value=True),
        deauthorize_ip=AsyncMock(return_value=True),
    )

    with (
        patch.object(services, "SLOT_CONFIGS", slot_configs),
        patch.object(services, "ControlDService", return_value=controld),
        patch.object(services, "create_secure_ip_update_keyboard", new=AsyncMock(return_value=InlineKeyboardMarkup(inline_keyboard=[]))),
        patch.object(services, "schedule_message_deletion", new=AsyncMock()),
    ):
        await services.handle_apply_def_loc(callback, test_session, mock_settings)

    await test_session.refresh(service)
    assert service.controld_device_id == slot_configs[4]["device_id"]
    assert service.username == "PlayStation|default|4"
    controld.authorize_ip.assert_awaited_once_with(slot_configs[4]["device_id"], "8.8.8.8")
    controld.deauthorize_ip.assert_awaited_once_with(slot_configs[1]["device_id"], "8.8.8.8")


@pytest.mark.asyncio
async def test_location_switch_rejects_a_service_owned_by_another_user(
    test_session: AsyncSession,
    mock_settings,
    slot_configs: dict[int, dict[str, str]],
) -> None:
    owner = await _create_user(test_session, 1001)
    attacker = await _create_user(test_session, 1002)
    service = await _create_service(test_session, owner, slot_configs[1]["device_id"])
    callback = _callback(attacker.telegram_id, f"apply_def_loc:{service.id}:4")

    with (
        patch.object(services, "SLOT_CONFIGS", slot_configs),
        patch.object(services, "ControlDService") as controld_service,
    ):
        await services.handle_apply_def_loc(callback, test_session, mock_settings)

    await test_session.refresh(service)
    assert service.controld_device_id == slot_configs[1]["device_id"]
    controld_service.assert_not_called()
    callback.message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_location_switch_does_not_persist_after_authorization_failure(
    test_session: AsyncSession,
    mock_settings,
    slot_configs: dict[int, dict[str, str]],
) -> None:
    user = await _create_user(test_session, 1001)
    service = await _create_service(test_session, user, slot_configs[1]["device_id"])
    callback = _callback(user.telegram_id, f"apply_def_loc:{service.id}:4")
    controld = MagicMock(authorize_ip=AsyncMock(return_value=False))

    with (
        patch.object(services, "SLOT_CONFIGS", slot_configs),
        patch.object(services, "ControlDService", return_value=controld),
    ):
        await services.handle_apply_def_loc(callback, test_session, mock_settings)

    await test_session.refresh(service)
    assert service.controld_device_id == slot_configs[1]["device_id"]
    assert service.username == "PlayStation|default|1"
    controld.deauthorize_ip.assert_not_called()


@pytest.mark.asyncio
async def test_location_switch_rejects_invalid_slot_before_provider_call(
    test_session: AsyncSession,
    mock_settings,
    slot_configs: dict[int, dict[str, str]],
) -> None:
    user = await _create_user(test_session, 1001)
    service = await _create_service(test_session, user, slot_configs[1]["device_id"])
    callback = _callback(user.telegram_id, f"apply_def_loc:{service.id}:999")

    with (
        patch.object(services, "SLOT_CONFIGS", slot_configs),
        patch.object(services, "ControlDService") as controld_service,
    ):
        await services.handle_apply_def_loc(callback, test_session, mock_settings)

    controld_service.assert_not_called()
    callback.message.answer.assert_awaited_once()
