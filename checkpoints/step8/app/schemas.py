from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class KeyNumber(BaseModel):
    label: str = Field(description="Label for the key number, e.g., 'Market Cap'")
    value: float


# title, bullets, key_numbers, tickers, sentiment, as_of
class Section(BaseModel):
    title: str = Field(description="Title of the section")
    bullets: list[str] = Field(description="2-5 short factual bullets for the section")
    key_numbers: list[KeyNumber] = Field(description="Every number used in the bullets")
    tickers: list[str] = Field(description="Every ticker mentioned, 4 uppercase letters")
    sentiment: Literal["bullish", "bearish", "neutral"] = Field(
        description="Sentiment of the section"
    )
    as_of: str = Field(
        description="A real date from tool output as YYYY-MM-DD (no time, no timezone). Never invented.",
    )


class WatchlistItem(BaseModel):
    symbol: str = Field(description="Ticker symbol, e.g., 'BBCA'")
    reason: str = Field(description="Reason for adding the ticker to the watchlist")


class ValidTickers(BaseModel):
    tickers: list[str] = Field(
        description="List of valid ticker symbols. IDX tickers, 4 letters, e.g., 'BBCA', 'BBRI'. SGX tickers, 3 to 4 letters, e.g., 'U11', 'C6L'.",
    )


class Report(BaseModel):
    headline: str = Field(description="Headline of the report")
    sections: list[Section] = Field(description="List of sections in the report")
    watchlist: list[WatchlistItem] = Field(
        description="Watchlist items, 0-5 tickers (may be empty)",
    )
    as_of: str = Field(
        description="A real date from tool output as YYYY-MM-DD (no time, no timezone). Never invented.",
    )


class EvaluationFeedback(BaseModel):
    feedbacks: list[str] = Field(
        description="Specific, actionable feedback comments; empty list if the report passes",
    )
    score: Literal["pass", "needs_improvement", "fail"] = Field(
        description="pass = send as-is; needs_improvement/fail = revise",
    )


class TopicCheck(BaseModel):
    on_topic: bool = Field(description="About markets, stocks, companies or this bot")
    asks_personal_advice: bool = Field(
        description="Asks what the user personally should buy/sell/hold, or for price targets"
    )
    reason: str
