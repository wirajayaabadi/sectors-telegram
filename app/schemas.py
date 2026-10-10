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
        description="List of valid IDX ticker symbols, 4 letters each, e.g., 'BBCA', 'BBRI'.",
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


class MetricValue(BaseModel):
    symbol: str = Field(description="Ticker this value belongs to")
    value: str = Field(
        description="The figure as given by the sources, with its unit, e.g. '768.9 trillion IDR'"
    )


class ComparisonRow(BaseModel):
    metric: str = Field(description="What is being compared, e.g. 'Market cap'")
    values: list[MetricValue] = Field(description="One entry per company being compared")
    note: str = Field(
        description="One factual line on which company leads this metric and by how much"
    )


class Comparison(BaseModel):
    symbols: list[str] = Field(description="The tickers compared, in the order requested")
    headline: str = Field(description="One sentence naming the main factual difference")
    rows: list[ComparisonRow] = Field(
        description="3-6 metrics compared head to head, each with every company's value"
    )
    takeaways: list[str] = Field(
        description="2-4 short factual bullets. Observations only, never recommendations",
    )
    as_of: str = Field(
        description="A real date from the sources as YYYY-MM-DD (no time, no timezone). Never invented.",
    )


class TopicCheck(BaseModel):
    on_topic: bool = Field(description="About markets, stocks, companies or this bot")
    asks_personal_advice: bool = Field(
        description="Asks what the user personally should buy/sell/hold, or for price targets"
    )
    reason: str
