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

    # --- LLM (OpenAI Agents SDK via Groq) ---
    groq_api_key: str = ""
    llm_base_url: str = "https://api.groq.com/openai/v1"
    model_small: str = "qwen/qwen3.8-27b"
    model_large: str = "qwen/qwen3.8-27b"
    is_paid: bool = (
        True  # True with a Groq Dev-tier key: full parallel report; False: lean partial
    )

    timezone: str = "Asia/Jakarta"
    report_time: str = "07:30"  # HH:MM in `timezone`; when the daily report is sent

    db_path: str = "data/bot.db"
    log_level: str = "INFO"

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @cached_property
    def report_time_obj(self):
        from datetime import time

        hh, mm = self.report_time.split(":")
        return time(int(hh), int(mm), tzinfo=self.tz)

    @cached_property
    def admin_ids(self) -> set[int]:
        return {
            int(x)
            for x in self.admin_chat_ids.replace(" ", "").split(",")
            if x.isdigit()
        }


settings = Settings()
