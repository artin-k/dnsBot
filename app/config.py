from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

# ArtinVps2026

class Settings(BaseSettings):
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/telegram_vpn_shop",
        alias="DATABASE_URL",
    )
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    fsm_storage: str = Field(default="memory", alias="FSM_STORAGE")
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")
    root_admin_telegram_id: int | None = Field(default=None, alias="ROOT_ADMIN_TELEGRAM_ID")
    owner_commission_percent: float = Field(default=10, alias="OWNER_COMMISSION_PERCENT")
    referral_commission_percent: float = Field(default=0, alias="REFERRAL_COMMISSION_PERCENT")
    commission_base: str = Field(default="final_amount", alias="COMMISSION_BASE")
    affiliate_default_to_root: bool = Field(default=True, alias="AFFILIATE_DEFAULT_TO_ROOT")
    dice_win_discount_percent: int = Field(default=10, alias="DICE_WIN_DISCOUNT_PERCENT")
    dice_cooldown_hours: int = Field(default=24, alias="DICE_COOLDOWN_HOURS")
    dice_discount_expire_hours: int = Field(default=72, alias="DICE_DISCOUNT_EXPIRE_HOURS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    @field_validator("redis_url", mode="after")
    @classmethod
    def normalize_redis_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip()

    @field_validator("fsm_storage", mode="after")
    @classmethod
    def normalize_fsm_storage(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"memory", "redis"}:
            return "memory"
        return normalized

    @field_validator("root_admin_telegram_id", mode="before")
    @classmethod
    def normalize_root_admin_telegram_id(cls, value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            return None
        return int(text)

    @field_validator("owner_commission_percent", "referral_commission_percent", mode="before")
    @classmethod
    def normalize_percent(cls, value: object) -> float:
        if value is None:
            return 0
        if isinstance(value, int | float):
            return float(value)
        text = str(value).strip().replace("%", "")
        if not text:
            return 0
        return float(text)

    @field_validator("affiliate_default_to_root", mode="before")
    @classmethod
    def normalize_affiliate_default_to_root(cls, value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if not text:
            return True
        return text in {"1", "true", "yes", "on", "y"}

    @field_validator("commission_base", mode="after")
    @classmethod
    def normalize_commission_base(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"final_amount", "amount"}:
            return "final_amount"
        return normalized

    @property
    def admin_ids(self) -> list[int]:
        parsed, _invalid = self._parse_admin_ids()
        return parsed

    @property
    def invalid_admin_ids(self) -> list[str]:
        _parsed, invalid = self._parse_admin_ids()
        return invalid

    def _parse_admin_ids(self) -> tuple[list[int], list[str]]:
        parsed: list[int] = []
        invalid: list[str] = []
        for item in self.admin_ids_raw.split(","):
            value = item.strip()
            if not value:
                continue
            try:
                parsed.append(int(value))
            except ValueError:
                invalid.append(value)
        return parsed, invalid


@lru_cache
def get_settings() -> Settings:
    return Settings()
