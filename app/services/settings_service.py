from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.settings import SettingsRepository


class SettingValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    label: str
    default: str | int
    value_type: str
    description: str
    min_value: int | None = None

    @property
    def default_text(self) -> str:
        return str(self.default)


SUPPORT_USERNAME = "SUPPORT_USERNAME"
PAYMENT_CARD_NUMBER = "PAYMENT_CARD_NUMBER"
PAYMENT_CARD_HOLDER = "PAYMENT_CARD_HOLDER"
PAYMENT_DESCRIPTION = "PAYMENT_DESCRIPTION"
ORDER_EXPIRE_MINUTES = "ORDER_EXPIRE_MINUTES"
REFERRAL_REWARD_AMOUNT = "REFERRAL_REWARD_AMOUNT"
WALLET_MIN_TOPUP_AMOUNT = "WALLET_MIN_TOPUP_AMOUNT"
WALLET_MAX_TOPUP_AMOUNT = "WALLET_MAX_TOPUP_AMOUNT"


SETTING_DEFINITIONS: tuple[SettingDefinition, ...] = (
    SettingDefinition(
        key=SUPPORT_USERNAME,
        label="نام کاربری پشتیبانی",
        default="",
        value_type="str",
        description="نام کاربری پشتیبانی تلگرام بدون @",
    ),
    SettingDefinition(
        key=PAYMENT_CARD_NUMBER,
        label="شماره کارت پرداخت",
        default="",
        value_type="str",
        description="شماره کارت برای پرداخت دستی",
    ),
    SettingDefinition(
        key=PAYMENT_CARD_HOLDER,
        label="نام صاحب کارت",
        default="",
        value_type="str",
        description="نام صاحب کارت پرداخت",
    ),
    SettingDefinition(
        key=PAYMENT_DESCRIPTION,
        label="توضیحات پرداخت",
        default="پرداخت سفارش اشتراک VPN",
        value_type="str",
        description="توضیح نمایشی پرداخت دستی",
    ),
    SettingDefinition(
        key=ORDER_EXPIRE_MINUTES,
        label="زمان انقضای سفارش به دقیقه",
        default=15,
        value_type="int",
        description="مدت اعتبار سفارش پرداخت‌نشده به دقیقه",
        min_value=1,
    ),
    SettingDefinition(
        key=REFERRAL_REWARD_AMOUNT,
        label="مبلغ پاداش زیرمجموعه‌گیری",
        default=0,
        value_type="int",
        description="پاداش کیف پول برای اولین خرید زیرمجموعه",
        min_value=0,
    ),
    SettingDefinition(
        key=WALLET_MIN_TOPUP_AMOUNT,
        label="حداقل شارژ کیف پول",
        default=50000,
        value_type="int",
        description="کمترین مبلغ مجاز برای شارژ کیف پول",
        min_value=0,
    ),
    SettingDefinition(
        key=WALLET_MAX_TOPUP_AMOUNT,
        label="حداکثر شارژ کیف پول",
        default=0,
        value_type="int",
        description="بیشترین مبلغ مجاز شارژ کیف پول؛ 0 یعنی بدون محدودیت",
        min_value=0,
    ),
)

SETTING_DEFINITION_BY_KEY = {definition.key: definition for definition in SETTING_DEFINITIONS}


class AppSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = SettingsRepository(session)

    async def ensure_defaults(self) -> None:
        rows = await self.repository.list_by_keys([definition.key for definition in SETTING_DEFINITIONS])
        existing_keys = {row.key for row in rows}
        for definition in SETTING_DEFINITIONS:
            if definition.key in existing_keys:
                continue
            await self.repository.upsert(
                key=definition.key,
                value=definition.default_text,
                value_type=definition.value_type,
                description=definition.description,
            )

    async def get_setting(self, key: str, default: Any = None, cast_type: Callable[[Any], Any] = str) -> Any:
        definition = SETTING_DEFINITION_BY_KEY.get(key)
        fallback = default if default is not None else (definition.default if definition else None)
        row = await self.repository.get(key)
        value = row.value if row is not None else fallback
        if value is None:
            return None
        try:
            return cast_type(value)
        except (TypeError, ValueError):
            if fallback is None:
                return None
            try:
                return cast_type(fallback)
            except (TypeError, ValueError):
                return fallback

    async def set_setting(self, key: str, value: Any) -> None:
        definition = self._get_definition(key)
        normalized = await self._normalize_value(definition, value)
        await self.repository.upsert(
            key=definition.key,
            value=normalized,
            value_type=definition.value_type,
            description=definition.description,
        )

    async def get_all_settings(self) -> dict[str, str | int]:
        rows = await self.repository.list_by_keys([definition.key for definition in SETTING_DEFINITIONS])
        values_by_key = {row.key: row.value for row in rows}
        result: dict[str, str | int] = {}
        for definition in SETTING_DEFINITIONS:
            value = values_by_key.get(definition.key, definition.default_text)
            result[definition.key] = self._cast_definition_value(definition, value)
        return result

    async def get_support_username(self) -> str:
        value = await self.get_setting(SUPPORT_USERNAME, cast_type=str)
        return self._normalize_support_username(value)

    async def get_payment_card_number(self) -> str:
        return await self.get_setting(PAYMENT_CARD_NUMBER, cast_type=str)

    async def get_payment_card_holder(self) -> str:
        return await self.get_setting(PAYMENT_CARD_HOLDER, cast_type=str)

    async def get_payment_description(self) -> str:
        return await self.get_setting(PAYMENT_DESCRIPTION, cast_type=str)

    async def get_order_expire_minutes(self) -> int:
        return await self._get_int_setting(ORDER_EXPIRE_MINUTES)

    async def get_referral_reward_amount(self) -> int:
        return await self._get_int_setting(REFERRAL_REWARD_AMOUNT)

    async def get_wallet_min_topup_amount(self) -> int:
        return await self._get_int_setting(WALLET_MIN_TOPUP_AMOUNT)

    async def get_wallet_max_topup_amount(self) -> int:
        return await self._get_int_setting(WALLET_MAX_TOPUP_AMOUNT)

    async def _get_int_setting(self, key: str) -> int:
        definition = self._get_definition(key)
        value = await self.get_setting(key, default=definition.default, cast_type=int)
        return self._validate_int_definition(definition, value, fallback=True)

    async def _normalize_value(self, definition: SettingDefinition, value: Any) -> str:
        if definition.value_type == "int":
            parsed = self._parse_int(value)
            if parsed is None:
                raise SettingValidationError("لطفاً یک عدد صحیح معتبر ارسال کنید.")
            self._validate_int_definition(definition, parsed, fallback=False)
            await self._validate_wallet_bounds(definition.key, parsed)
            return str(parsed)

        text = str(value or "").strip()
        if text == "-":
            text = ""
        if definition.key == SUPPORT_USERNAME:
            text = self._normalize_support_username(text)
        return text

    async def _validate_wallet_bounds(self, key: str, value: int) -> None:
        if key == WALLET_MIN_TOPUP_AMOUNT:
            max_amount = await self.get_wallet_max_topup_amount()
            if max_amount > 0 and value > max_amount:
                raise SettingValidationError("حداقل شارژ نمی‌تواند از حداکثر شارژ بیشتر باشد.")
        elif key == WALLET_MAX_TOPUP_AMOUNT and value > 0:
            min_amount = await self.get_wallet_min_topup_amount()
            if value < min_amount:
                raise SettingValidationError("حداکثر شارژ باید 0 یا بزرگ‌تر/برابر حداقل شارژ باشد.")

    def _cast_definition_value(self, definition: SettingDefinition, value: str) -> str | int:
        if definition.value_type == "int":
            parsed = self._parse_int(value)
            if parsed is None:
                return int(definition.default)
            return self._validate_int_definition(definition, parsed, fallback=True)
        if definition.key == SUPPORT_USERNAME:
            return self._normalize_support_username(value)
        return str(value or "")

    def _validate_int_definition(self, definition: SettingDefinition, value: int, *, fallback: bool) -> int:
        if definition.min_value is not None and value < definition.min_value:
            if fallback:
                return int(definition.default)
            if definition.min_value == 0:
                raise SettingValidationError("این مقدار نمی‌تواند منفی باشد.")
            raise SettingValidationError(f"این مقدار باید حداقل {definition.min_value} باشد.")
        return value

    def _get_definition(self, key: str) -> SettingDefinition:
        definition = SETTING_DEFINITION_BY_KEY.get(key)
        if definition is None:
            raise SettingValidationError("تنظیم انتخاب‌شده معتبر نیست.")
        return definition

    @staticmethod
    def _parse_int(value: Any) -> int | None:
        if isinstance(value, int):
            return value
        text = str(value or "").strip().replace(",", "")
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    @staticmethod
    def _normalize_support_username(value: str) -> str:
        return value.strip().removeprefix("@")
