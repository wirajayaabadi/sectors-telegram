from functools import cached_property
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = ""
    admin_chat_ids: str = ""

    sectors_api_key: str = ""
    sectors_api_url: str = "https://api.sectors.app"  # no /v2: ENDPOINTS paths carry it

    timezone: str = "Asia/Jakarta"

    db_path: str = "data/bot.db"
    log_level: str = "INFO"

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @cached_property
    def admin_ids(self) -> set[int]:
        return {
            int(x)
            for x in self.admin_chat_ids.replace(" ", "").split(",")
            if x.isdigit()
        }


settings = Settings()
