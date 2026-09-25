from __future__ import annotations

from agents import (
    Model,
    OpenAIChatCompletionsModel,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

from app.config import settings

_client = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.groq_api_key)


def model(name: str) -> Model:
    """Return a Model object for the given name, using the default OpenAI client."""
    return OpenAIChatCompletionsModel(model=name, openai_client=_client)


SMALL = model(settings.model_small)
LARGE = model(settings.model_large)


def configure_llm() -> str:
    """Configure the default OpenAI client and disable tracing."""
    set_default_openai_client(_client)
    set_default_openai_api(_client)
    set_tracing_disabled(True)
    return f"Groq configured: {settings.llm_base_url}, model_small={settings.model_small}, model_large={settings.model_large}"
