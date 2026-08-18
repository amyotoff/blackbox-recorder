"""
Realistic LLM agent example showing how BlackBox records prompts,
completions, chain-of-thought reasoning, tool calls, and token usage.
"""

import asyncio
from blackbox_recorder import SpanKind, tracer


async def main():
    tracer.set_session_id("alice_111")

    # ---- Root agent span ----
    with tracer.span("support_agent", kind=SpanKind.AGENT, inputs={"user_query": "Какая погода в Париже?"}) as agent_span:
        agent_span.set_metadata("agent_version", "2.1")

        # ---- Step 1: LLM decides which tool to call ----
        with tracer.span("plan_step", kind=SpanKind.LLM) as llm1:
            llm1.set_llm_io(
                system_prompt="TODAY IS 2026-08-18. You are a helpful assistant with access to tools.",
                prompt="Какая погода в Париже?",
                thinking="Пользователь спрашивает о погоде. У меня есть инструмент get_weather. "
                         "Нужно вызвать его с city='Paris'. Других инструментов не нужно.",
                completion="Вызываю инструмент get_weather для города Paris.",
                model="gemini-2.5-flash",
                temperature=0.1,
                prompt_tokens=45,
                completion_tokens=18,
                tool_calls=[{"name": "get_weather", "args": {"city": "Paris"}}],
                stop_reason="tool_use",
            )

        # ---- Step 2: Tool execution ----
        with tracer.span("get_weather", kind=SpanKind.TOOL) as tool_span:
            tool_span.set_tool_call(
                tool_name="get_weather",
                tool_args={"city": "Paris", "units": "metric"},
                tool_result={"temp_c": 24, "condition": "Sunny", "humidity": 45},
            )

        # ---- Step 3: LLM synthesizes final answer ----
        with tracer.span("answer_step", kind=SpanKind.LLM) as llm2:
            llm2.set_llm_io(
                messages=[
                    {"role": "system", "content": "TODAY IS 2026-08-18. You are a helpful assistant."},
                    {"role": "user", "content": "Какая погода в Париже?"},
                    {"role": "assistant", "content": "[tool_call: get_weather(city=Paris)]"},
                    {"role": "tool", "content": '{"temp_c": 24, "condition": "Sunny", "humidity": 45}'},
                ],
                thinking="Получил результат от инструмента. Температура 24°C, солнечно, "
                         "влажность 45%. Сформирую ответ на русском языке.",
                completion="В Париже сейчас 24°C, солнечно, влажность воздуха 45%. "
                           "Отличная погода для прогулки! 🌞",
                model="gemini-2.5-flash",
                prompt_tokens=120,
                completion_tokens=35,
                stop_reason="end_turn",
            )

        agent_span.finish(output="В Париже сейчас 24°C, солнечно, влажность 45%. Отличная погода!")

    tracer.flush()
    print("✅ Trace recorded. Now inspect it:\n")
    print("  blackbox-recorder list")
    print("  blackbox-recorder show <TRACE_ID>")
    print("  blackbox-recorder show <TRACE_ID> -v   # ← prompts, thinking, tokens!")


if __name__ == "__main__":
    asyncio.run(main())
