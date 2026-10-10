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
    # cache_ttl: int = 900

    # --- /sector caches (seconds) ---
    # The subsector list is a taxonomy that almost never changes and costs 1 credit: a day.
    subsector_list_ttl: int = 86400
    # A report costs 1 credit PER section (5 here). Its contents are daily market-cap moves
    # plus annual/quarterly aggregates, so 6 hours keeps it within one trading day at most
    # 4 refreshes a day per subsector.
    subsector_report_ttl: int = 21600
    # How long an expired copy may still be shown (labelled with its fetch time) when the
    # API fails, and how long the result buttons keep working: 3 days covers a weekend.
    subsector_stale_max: int = 259200

    # --- /stock caches (seconds) ---
    # Daily prices: 1 credit, end-of-day data that changes at most once a trading day.
    # 15 minutes keeps a fresh close visible soon after the API publishes it.
    stock_daily_ttl: int = 900
    # Company report sections: 1 credit EACH (4 for the card). Fundamentals move
    # quarterly; P/E and P/B move with price but the API itself lags a day. 6 hours.
    stock_report_ttl: int = 21600
    # Older copy shown (labelled) when the API fails, and how long the buttons work.
    stock_stale_max: int = 259200

    # --- /movers and /active (seconds) ---
    # Both rankings are end-of-day data, not real-time. 15 minutes for "the latest" views.
    rankings_ttl: int = 900
    # /active for an explicit past date: a closed trading day does not change.
    rankings_history_ttl: int = 86400
    rankings_stale_max: int = 259200
    # /movers only ranks companies at or above this market cap (billion IDR). 5000 is the
    # API's own default; we send it explicitly so a changed default can't move results.
    movers_min_mcap_billion: int = 5000

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
