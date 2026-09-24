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


@input_guardrail
async def topic_guardrail(
    ctx: RunContextWrapper, agent: Agent, user_input
) -> GuardrailFunctionOutput:
    text = user_input if isinstance(user_input, str) else str(user_input)
    result = await Runner.run(_topic_agent, text, context=ctx.context)
    check: TopicCheck = result.final_output
    # Trip the wire if off-topic OR asking for personal advice.
    tripped = (not check.on_topic) or check.asks_personal_advice
    return GuardrailFunctionOutput(output_info=check, tripwire_triggered=tripped)
