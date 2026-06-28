from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

# ArtinVps2026

class Settings(BaseSettings):
    controld_api_token: str = Field(default="", alias="CONTROLD_API_TOKEN")
    controld_profile_id: str = Field(default="", alias="CONTROLD_PROFILE_ID")
    paystar_gateway_id: str | None = Field(default=None, alias="PAYSTAR_GATEWAY_ID")
    paystar_sign_key: str | None = Field(default=None, alias="PAYSTAR_SIGN_KEY")
    public_web_base_url: str = Field(default="https://bot.pingsep.ir", alias="PUBLIC_WEB_BASE_URL")

    bot_token: str = Field(default="", alias="BOT_TOKEN")
    database_url: str = Field(
        default="postgresql+asyncpg://dns_admin:Artin2026@localhost:5432/dns_bot",
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
    allow_placeholder_configs: bool = Field(default=False, alias="ALLOW_PLACEHOLDER_CONFIGS")
    config_low_stock_threshold: int = Field(default=3, alias="CONFIG_LOW_STOCK_THRESHOLD")
    wallet_min_withdraw_amount: int = Field(default=100000, alias="WALLET_MIN_WITHDRAW_AMOUNT")
    wallet_max_withdraw_amount: int = Field(default=0, alias="WALLET_MAX_WITHDRAW_AMOUNT")

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

    @classmethod
    def validate_bot_token(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("BOT_TOKEN environment variable must be set and non-empty")
        # Validate Telegram bot token format (must contain ':' separator)
        if ':' not in value:
            raise ValueError("BOT_TOKEN format invalid: must be in format NUMERIC:ALPHANUMERIC")
        return value.strip()

    @classmethod
    def validate_bot_token_format(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("BOT_TOKEN environment variable must be set and non-empty")
        if ':' not in value:
            raise ValueError("BOT_TOKEN format invalid: must be NUMERIC:ALPHANUMERIC (e.g., 123456789:ABCdefGHIjkl)")
        return value.strip()

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

    @field_validator("public_web_base_url", mode="after")
    @classmethod
    def normalize_public_web_base_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

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

    @field_validator("allow_placeholder_configs", mode="before")
    @classmethod
    def normalize_allow_placeholder_configs(cls, value: object) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if not text:
            return False
        return text in {"1", "true", "yes", "on", "y"}

    @field_validator("config_low_stock_threshold", mode="before")
    @classmethod
    def normalize_config_low_stock_threshold(cls, value: object) -> int:
        if value is None:
            return 3
        try:
            parsed = int(str(value).strip())
        except ValueError:
            return 3
        return max(parsed, 0)

    @field_validator("wallet_min_withdraw_amount", "wallet_max_withdraw_amount", mode="before")
    @classmethod
    def normalize_wallet_withdraw_amount(cls, value: object) -> int:
        if value is None:
            return 0
        try:
            parsed = int(str(value).strip().replace(",", ""))
        except ValueError:
            return 0
        return max(parsed, 0)

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
    settings = Settings()
    _validate_settings(settings)
    return settings


def _validate_settings(settings: Settings) -> None:
    """Startup validation to catch configuration errors early with clear messages."""
    errors: list[str] = []
    
    # 1. Validate DATABASE_URL
    if not settings.database_url:
        errors.append("❌ DATABASE_URL is not set or empty")
    elif not settings.database_url.startswith(("postgresql://", "postgresql+asyncpg://")):
        errors.append("❌ DATABASE_URL must be PostgreSQL (postgresql:// or postgresql+asyncpg://)")
    
    # 2. Validate FSM_STORAGE consistency
    if settings.fsm_storage == "redis" and not settings.redis_url:
        errors.append("❌ FSM_STORAGE=redis configured but REDIS_URL is empty")
    
    # 3. Warn if using memory storage (state will be lost on restart)
    if settings.fsm_storage == "memory":
        import warnings
        warnings.warn(
            "⚠️  FSM_STORAGE=memory: User conversation state will be lost on bot restart. "
            "For production, set REDIS_URL and FSM_STORAGE=redis",
            RuntimeWarning,
            stacklevel=3
        )
    
    # Raise error if critical validations failed
    if errors:
        error_msg = "Configuration validation failed:\n\n" + "\n".join(errors)
        error_msg += "\n\nPlease set missing environment variables in .env or system environment."
        raise ValueError(error_msg)
