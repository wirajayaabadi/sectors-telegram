from functools import cached_property

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = ""
    admin_chat_ids: str = ""

    db_path: str = "data/bot.db"
    log_level: str = "INFO"

    @cached_property
    def admin_ids(self) -> set[int]:
        return {
            int(x)
            for x in self.admin_chat_ids.replace(" ", "").split(",")
            if x.isdigit()
        }


settings = Settings()
