from __future__ import annotations

from agents import (
    Agent,
    GuardrailFunctionOutput,
    ModelSettings,
    RunContextWrapper,
    Runner,
    input_guardrail,
)

from app.provider import SMALL
from app.schemas import TopicCheck

_topic_agent = Agent(
    name="Topic Guard",
    model=SMALL,
    instructions=(
        "Classify the user message. on_topic=True if it is about markets, stocks, companies, "
        "indices, or this bot. asks_personal_advice=True ONLY if it asks what the USER should "
        "personally buy/sell/hold or asks for a price target. Give a one-line reason."
    ),
    output_type=TopicCheck,
    model_settings=ModelSettings(max_tokens=256, temperature=0),
)


def _newest_question(user_input) -> str:
    """Pull just the question being asked now.

    With a session attached the SDK hands the guardrail the whole conversation, so a
    plain str() gives the Topic Guard a transcript dominated by neutral market data with
    the real question buried at the end - and it starts waving through requests for
    personal advice.
    """
    if isinstance(user_input, str):
        return user_input
    for item in reversed(user_input):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            ).strip()
    return str(user_input)


@input_guardrail
async def topic_guardrail(
    ctx: RunContextWrapper, agent: Agent, user_input
) -> GuardrailFunctionOutput:
    result = await Runner.run(
        _topic_agent, _newest_question(user_input), context=ctx.context
    )
    check: TopicCheck = result.final_output
    # Trip the wire if off-topic OR asking for personal advice.
    tripped = (not check.on_topic) or check.asks_personal_advice
    return GuardrailFunctionOutput(output_info=check, tripwire_triggered=tripped)
