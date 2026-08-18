"""
Asynchronous agent pipeline with concurrent tool calls and LLM reasoning steps.
"""

import asyncio
from blackbox_recorder import SpanKind, trace, tracer


@trace(name="web_search", kind=SpanKind.TOOL)
async def search_docs(query: str) -> list[str]:
    await asyncio.sleep(0.05)
    return [f"Doc 1 for {query}", f"Doc 2 for {query}"]


@trace(name="llm_generation", kind=SpanKind.LLM)
async def generate_answer(prompt: str) -> str:
    await asyncio.sleep(0.08)
    return f"Synthesized answer based on: {prompt}"


@trace(name="research_agent", kind=SpanKind.AGENT)
async def run_agent(topic: str) -> str:
    # Context prompt with current date
    query = f"TODAY IS 2026-08-18. Latest research on {topic}"
    
    docs = await search_docs(query)
    combined_prompt = f"Summarize: {' | '.join(docs)}"
    summary = await generate_answer(combined_prompt)
    return summary


async def main():
    print("Running async research agent...")
    tracer.set_session_id("session_alice_111")
    
    res = await run_agent("Quantum Computing breakthroughs")
    print(f"Result: {res}")
    
    tracer.flush()
    print("\nRecorded trace! Run `python -m blackbox_recorder show <trace_id>` to view.")


if __name__ == "__main__":
    asyncio.run(main())
