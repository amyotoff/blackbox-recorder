<p align="center">
  <h1 align="center">🛫 BlackBox Recorder</h1>
  <p align="center">
    <strong>Universal, zero-dependency flight recorder for AI agents.</strong><br>
    Records every thought, every tool call, every LLM prompt.<br>
    When something goes wrong — you'll know exactly what happened and why.
  </p>
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-wiring-it-into-your-agent">Wiring It In</a> •
  <a href="#-working-with-your-traces">Working With Traces</a> •
  <a href="#-incident-investigation-cli">CLI</a> •
  <a href="#-llm-tracing">LLM Tracing</a> •
  <a href="#-configuration">Configuration</a> •
  <a href="#%EF%B8%8F-api-reference">API</a> •
  <a href="#-v10-roadmap">Roadmap</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.7.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/python-3.10+-brightgreen" alt="Python">
  <img src="https://img.shields.io/badge/dependencies-0_(stdlib_only)-orange" alt="Zero deps">
  <img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="License">
  <img src="https://img.shields.io/badge/storage-SQLite_WAL-blueviolet" alt="SQLite">
</p>

---

## 🤦‍♂️ The Problem

It's 3 AM. Your autonomous AI agent went rogue and offered a user a 99% discount on a Tesla. The pipeline silently swallowed the error like a golden retriever inhaling a dropped meatball. 

**What happened? Did the LLM hallucinate? Did a tool return garbage? Why?**

If you try to add standard tracing (LangSmith, Logfire, Phoenix), you suddenly need a cloud subscription, a PhD in DevOps to deploy PostgreSQL/ClickHouse, or you have to pollute your pristine project with 42 `opentelemetry-*` dependencies that conflict with everything.

## 🦸 The Solution

`ai-blackbox-recorder` is a **dashcam for your AI**. 

Like a flight recorder in an aircraft, it sits quietly in the background, sipping tea, and silently writing every single thought, prompt, and tool call into a local SQLite file. When things go boom, you don't guess — you just pull the tape.

- **Zero external dependencies** — pure Python 3.10+ stdlib (doesn't bloat your `requirements.txt`)
- **Never blocks your agent** — background daemon thread with `queue.Queue` handles the I/O
- **Survives the crash it is recording** — spans hit disk the moment they *start*, and a shutdown hook drains the buffer, so a `SIGKILL`ed or OOM-killed agent still tells you where it died
- **Silent until used** — importing the library creates no database and starts no thread; the recorder wakes up on your first traced call
- **Automatic call hierarchy** — `contextvars` magically builds the execution tree for you (sync + async)
- **Configurable retention** — auto-deletes traces older than 7 days, 30 days, etc.
- **Disk protection** — hard disk cap at 300 MB, older traces get evicted so your server doesn't crash
- **First-class LLM support** — prompts, completions, chain-of-thought, token counts, tool calls
- **Incident CLI** — beautiful ASCII tree with everything you need to debug right in the terminal

---

## 🚀 Quick Start

### 🤖 Note for AI Agents (Antigravity, Claude, Cursor)
> If you are an AI assistant helping a human integrate tracing, you'll love this: **It has zero dependencies**. 
> 1. Install it from a release (see below) — zero dependencies means no pip resolver conflicts.
> 2. Import it as `ai_blackbox_recorder`, add the `@trace` decorator to the human's functions.
> 3. You can immediately check your own work using `python -m ai_blackbox_recorder list` in the terminal!

### Installation

> **Not on PyPI yet.** Note that `pip install blackbox-recorder` fetches an [unrelated project](https://github.com/Harshit-code-tech/BLACKBOX) that happens to share that name — not this library.

```bash
# latest release
pip install https://github.com/amyotoff/blackbox-recorder/releases/download/v0.7.0/ai_blackbox_recorder-0.7.0-py3-none-any.whl

# or from source
pip install git+https://github.com/amyotoff/blackbox-recorder@v0.7.0
```

Python 3.10+, no dependencies. The distribution is `ai-blackbox-recorder`, the import is `ai_blackbox_recorder`, and the CLI is `ai-blackbox-recorder`.

### Two Lines to Start Recording

```python
from ai_blackbox_recorder import trace, SpanKind

@trace(kind=SpanKind.TOOL)
def search_web(query: str) -> list:
    return google_search(query)

@trace(kind=SpanKind.AGENT)
async def my_agent(user_message: str) -> str:
    results = search_web(user_message)
    return synthesize(results)
```

That's it. Every call to `search_web` and `my_agent` is now recorded with full inputs, outputs, timing, and automatic parent→child hierarchy.

### Investigate an Incident

```bash
# What happened recently?
ai-blackbox-recorder list

# Show the execution tree
ai-blackbox-recorder show <TRACE_ID>

# Full details: prompts, thinking, tokens
ai-blackbox-recorder show <TRACE_ID> -v
```

---

## 🔌 Wiring It Into Your Agent

Installing the package records nothing on its own. A useful flight recording comes from *where* you put the spans and *what* you bind to them. This is the integration playbook — follow it once per service and you never think about it again.

### 1. Bootstrap once, at the process entry point

```python
# main.py — the first thing your process runs
import os
from ai_blackbox_recorder import BlackBoxConfig, configure

configure(BlackBoxConfig(
    db_path=os.getenv("BLACKBOX_DB", "/var/lib/myagent/traces.db"),
    retention="30d",
    max_db_size_mb=300,
    enabled=os.getenv("ENV") != "test",
))
```

Three rules that cover every deployment:

- **Call `configure()` before the first traced function *runs*.** Import order doesn't matter — decorating a function is free and touches nothing. Skip `configure()` entirely and you get `blackbox_traces.db` in the current working directory.
- **Point `db_path` at a writable volume.** In Docker, the container's working directory is usually ephemeral: mount a volume, or your black box burns up with the container.
- **One database per service.** Multiple processes of the *same* service can share one file safely (SQLite WAL), but two unrelated services sharing a file makes every trace list a mess.

### 2. Decide what becomes a span

The rule of thumb: **one span per thing that can independently fail, be slow, or be wrong.** Tracing every pure helper drowns the tree in noise and tells you nothing.

| Layer in your agent | SpanKind | What to record | Question it answers at 3 AM |
| :--- | :--- | :--- | :--- |
| HTTP handler / bot update / queue job | `AGENT` | user message, final answer | What did the user actually ask, and what did we answer? |
| Planner / router model call | `LLM` | system + prompt, thinking, `tool_calls`, tokens | Did the model pick the wrong tool, or hallucinate the args? |
| Every tool, API call, DB write | `TOOL` | args, raw result | Did the tool return garbage that the model then trusted? |
| Vector search / RAG fetch | `RETRIEVER` | query, doc IDs, scores | Did we answer confidently from the wrong context? |
| Deterministic post-processing | `CHAIN` | in / out | Did our own code mangle a good model answer? |

### 3. Put every model call behind one traced function

This is the highest-value hour you'll spend. Route all LLM traffic through a single helper and prompts, completions, thinking and token counts get recorded everywhere at once — no per-call-site instrumentation, no forgotten branches.

```python
# llm.py — the only place in the codebase that talks to the model
from ai_blackbox_recorder import tracer, SpanKind

async def call_llm(prompt: str, *, system: str = "", model: str = "gemini-2.5-flash", tools=None):
    with tracer.span(f"llm:{model}", kind=SpanKind.LLM) as span:
        response = await client.generate(model=model, system=system, prompt=prompt, tools=tools)

        span.set_llm_io(
            system_prompt=system,
            prompt=prompt,
            completion=response.text,
            thinking=response.thinking,          # if your provider exposes it
            tool_calls=response.tool_calls,
            model=model,
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            stop_reason=response.finish_reason,
        )
        return response
```

Do the same for tools — one decorator on the function, and the arguments and return value are captured automatically:

```python
@trace(kind=SpanKind.TOOL)
def charge_customer(order_id: str, amount_cents: int) -> dict:
    return billing_api.charge(order_id, amount_cents)
```

### 4. Bind every trace to a user and hand back the trace ID

This is what turns *"a user is complaining about something that happened yesterday"* into *"here is the exact recording"*.

```python
from ai_blackbox_recorder import set_session_id, tracer, SpanKind

@app.post("/chat")
async def chat(req: ChatRequest):
    set_session_id(req.user_id)          # every child span inherits it

    with tracer.span("chat_request", kind=SpanKind.AGENT, inputs={"message": req.text}) as root:
        answer = await agent(req.text)
        log.info("handled chat", extra={"trace_id": root.trace_id})   # bridge to your normal logs
        return {"answer": answer, "trace_id": root.trace_id}
```

Now `ai-blackbox-recorder list --session <user_id>` gives you that user's history, and any trace ID appearing in your logs, error tracker or support ticket opens the full recording with `show -v`.

### 5. Async, threads and workers — the one real gotcha

Hierarchy is carried by `contextvars`, so it behaves differently per concurrency model:

```python
# ✅ asyncio — context propagates into tasks automatically
async def agent(q):
    a, b = await asyncio.gather(search(q), classify(q))   # both become children

# ❌ threads — a new thread starts with an EMPTY context.
#    The child span silently becomes its own orphan root trace.
with ThreadPoolExecutor() as pool:
    pool.submit(search, q)

# ✅ threads — carry the context across explicitly
import contextvars
ctx = contextvars.copy_context()
with ThreadPoolExecutor() as pool:
    pool.submit(ctx.run, search, q)
```

For `multiprocessing` and separate worker processes there is no shared context at all: each process calls `configure()` itself, and either writes to the same file (WAL handles concurrent writers) or to its own. To stitch a trace across a process or queue boundary, pass the ID and re-bind it on the other side:

```python
job = {"payload": ..., "trace_id": tracer.get_trace_id()}   # producer
...
set_trace_id(job["trace_id"])                               # consumer, before its first span
```

### 6. Deployment checklist

- [ ] `db_path` on a mounted volume, not the container's working directory
- [ ] `retention` and `max_db_size_mb` sized to that volume — the recorder evicts oldest traces rather than filling the disk
- [ ] `enabled=False` in unit tests and CI so test runs don't pollute the recording
- [ ] `*.db`, `*.db-wal`, `*.db-shm` in `.gitignore` (already there in this repo)
- [ ] the CLI is available where the DB lives — `docker exec -it myagent ai-blackbox-recorder list`
- [ ] nothing else to run: no collector, no sidecar, no port

---

## 🧭 Working With Your Traces

A recording nobody reads is just disk usage. Four workflows where the tape pays for itself.

### Designing a feature — start from what actually happens

Before writing the prompt for a new capability, look at what users are really doing, not at what the spec assumes:

```bash
ai-blackbox-recorder export -o corpus.jsonl        # everything, one span per line
```

```sql
-- the top requests hitting your agent, straight from SQLite
SELECT json_extract(inputs, '$.user_query') AS request, COUNT(*) AS n
FROM spans WHERE kind = 'AGENT'
GROUP BY request ORDER BY n DESC LIMIT 20;
```

That distribution tells you which branch is worth building, what the real input lengths are, and which tools already get called for the job. Instrument the *current* behaviour first, ship the feature second — otherwise you have no baseline to compare against.

### Evolving the project — measure the change, don't guess

Tag traces with the build or prompt version, then compare like for like:

```python
@trace(name="support_agent", kind=SpanKind.AGENT, metadata={"prompt_version": "v3"})
async def support_agent(query: str) -> str:
    ...
```

```sql
-- did prompt v3 actually get cheaper and faster, or just feel that way?
SELECT json_extract(metadata, '$.prompt_version') AS version,
       COUNT(*)                                   AS runs,
       ROUND(AVG(duration_ms))                    AS avg_ms,
       ROUND(100.0 * SUM(has_error) / COUNT(*), 1) AS error_pct
FROM spans WHERE name = 'support_agent'
GROUP BY version;
```

The same query shape catches silent regressions after a refactor: latency drifting up, a tool quietly failing more often, token spend doubling because a prompt grew.

### Evals — build the dataset out of production

The best eval set is the traffic you already recorded. Pull real prompts and the completions you shipped, then use them as regression cases:

```python
from ai_blackbox_recorder import BlackBoxConfig, TraceStorage

storage = TraceStorage(BlackBoxConfig(db_path="traces.db"))

cases = []
for t in storage.list_traces(limit=500, has_error=False):
    for span in storage.get_trace(t["trace_id"]):
        inputs, outputs = span.get("inputs") or {}, span.get("outputs") or {}
        if span["kind"] == "LLM" and isinstance(outputs, dict) and inputs.get("prompt"):
            cases.append({"prompt": inputs["prompt"], "shipped": outputs.get("completion")})
```

Run the eval itself under its own session ID so it never mixes with real users:

```python
set_session_id("eval:prompt-v3")
```

```bash
ai-blackbox-recorder list --session eval:prompt-v3 --errors-only    # what broke in this run
```

Because every eval run is itself a set of traces, a failing case opens with `show -v` and shows the whole chain — prompt, thinking, tool calls — instead of a bare pass/fail.

### Incident investigation — the 3 AM runbook

```bash
ai-blackbox-recorder errors --limit 20              # 1. what failed recently
ai-blackbox-recorder show <TRACE_ID> -v             # 2. the full chain of that failure
ai-blackbox-recorder export --trace <TRACE_ID> -o incident.jsonl   # 3. attach to the ticket
```

Read the tree top-down and the failure usually names itself: an `LLM` span whose `🔧 Tool Call` has wrong arguments is a prompt problem; a `TOOL` span with a clean call but a junk `📤 Result` is someone else's outage; a clean tool result followed by a wrong final answer is your post-processing.

**When the process died instead of erroring.** Spans are written to disk the moment they *start*, so a `SIGKILL`, an OOM kill or a hard container stop still leaves a record. Unfinished spans are marked rather than hidden:

```
📦 Trace ID: 2707d6c61ed247788fdb60edb0a6d9d3
⏱️  Total Spans: 3
⏳ Unfinished: 2 (still running, or the process died before they returned)
──────────────────────────────────────────────────────────────────────
└── ⏳ [AGENT] support_agent (unfinished)
    ├── ✅ [LLM] classify [gemini-2.5-flash] (0.01ms) 🔤 12→3
    └── ⏳ [TOOL] charge_api (unfinished)
```

The deepest `⏳` span is where the process was when it went down — here, inside `charge_api`, with the arguments it was called with. The same marker appears as `⏳ OPEN` in `ai-blackbox-recorder list`. A span that is still `⏳` for a long-finished run is a hang, a kill, or a tool that never returns.

### SQL cookbook

The database is a plain SQLite file with one `spans` table — every question is one query away, no API, no export step.

```sql
-- token spend by model
SELECT json_extract(metadata, '$.model') AS model,
       COUNT(*) AS calls,
       SUM(json_extract(metrics, '$.total_tokens')) AS tokens
FROM spans WHERE kind = 'LLM' GROUP BY model ORDER BY tokens DESC;

-- which tool is the least reliable
SELECT name, COUNT(*) AS runs, SUM(has_error) AS failures,
       ROUND(100.0 * SUM(has_error) / COUNT(*), 1) AS failure_pct
FROM spans WHERE kind = 'TOOL' GROUP BY name ORDER BY failure_pct DESC;

-- p95 latency of the entry point
SELECT ROUND(duration_ms, 1) AS p95_ms FROM spans
WHERE name = 'support_agent' AND duration_ms IS NOT NULL
ORDER BY duration_ms
LIMIT 1 OFFSET (SELECT CAST(COUNT(*) * 0.95 AS INT) FROM spans
                WHERE name = 'support_agent' AND duration_ms IS NOT NULL);

-- runs that never finished: hangs and kills
SELECT trace_id, name, datetime(created_at, 'unixepoch') AS started
FROM spans WHERE end_time IS NULL ORDER BY created_at DESC;
```

---

## 🔍 Incident Investigation (CLI)

The CLI is your primary tool for post-incident analysis. Install the package and it's available globally.

### `list` — Recent Traces

```bash
$ ai-blackbox-recorder list --limit 5

📋 Last 5 Traces:
─────────────────────────────────────────────────────────────────────────────────────
Start Time           Status  Spans   Duration   Root Operation       Trace ID
─────────────────────────────────────────────────────────────────────────────────────
2026-08-18 18:52:08  ✅ OK    4       132.7ms    support_agent        0f472b36b2a9...
2026-08-18 18:46:17  ❌ ERR   3       59.6ms     billing_agent        61252647020244...
2026-08-18 18:44:02  ⏳ OPEN  3       0.1ms      support_agent        2707d6c61ed247...
```

`✅ OK` finished cleanly, `❌ ERR` raised, `⏳ OPEN` never finished — still running, or the process was killed mid-flight.

### `show` — Execution Tree

```bash
$ ai-blackbox-recorder show 0f472b36b2a941469f7a9ff66b28abd0

📦 Trace ID: 0f472b36b2a941469f7a9ff66b28abd0
⏱️  Total Spans: 4
🔤 Tokens: 165 in → 53 out (218 total)
──────────────────────────────────────────────────────────────────────
└── ✅ [AGENT] support_agent (132.7ms)
    ├── ✅ [LLM] plan_step [gemini-2.5-flash] (45.2ms) 🔤 45→18
    ├── ✅ [TOOL] get_weather (12.1ms)
    └── ✅ [LLM] answer_step [gemini-2.5-flash] (74.3ms) 🔤 120→35
```

### `show -v` — Full Verbose Output with Prompts and Thinking

```bash
$ ai-blackbox-recorder show 0f472b36... -v

└── ✅ [AGENT] support_agent (132.7ms)
    📥 Input: {"user_query": "Какая погода в Париже?"}
    ├── ✅ [LLM] plan_step [gemini-2.5-flash] (45.2ms) 🔤 45→18
    │   📋 System: You are a helpful assistant with access to tools.
    │   💬 Prompt: Какая погода в Париже?
    │   🧠 Thinking: Пользователь спрашивает о погоде. Нужно вызвать get_weather...
    │   🤖 Response: Вызываю инструмент get_weather для города Paris.
    │   🔧 Tool Call: get_weather({"city": "Paris"})
    │   ⏹️  Stop: tool_use
    ├── ✅ [TOOL] get_weather (12.1ms)
    │   📥 Args: {"city": "Paris", "units": "metric"}
    │   📤 Result: {"temp_c": 24, "condition": "Sunny", "humidity": 45}
    └── ✅ [LLM] answer_step [gemini-2.5-flash] (74.3ms) 🔤 120→35
        🧠 Thinking: Получил результат. Температура 24°C, солнечно...
        🤖 Response: В Париже сейчас 24°C, солнечно! 🌞
        ⏹️  Stop: end_turn
```

### All CLI Commands

| Command | Description |
| :--- | :--- |
| `ai-blackbox-recorder stats` | Database stats: size, span/trace counts, retention policy |
| `ai-blackbox-recorder list [--limit N] [--session ID] [--errors-only]` | List traces with filtering |
| `ai-blackbox-recorder errors [--limit N]` | Show only traces with errors |
| `ai-blackbox-recorder show <TRACE_ID> [-v] [--json]` | Hierarchical tree view; `-v` for prompts/thinking/tokens |
| `ai-blackbox-recorder export -o file.jsonl [--trace ID]` | Export to OpenInference-compatible JSONL |
| `ai-blackbox-recorder cleanup [--retention 7d]` | Manual TTL cleanup and disk reclaim |

> **Tip:** You can also run CLI as a module: `python -m ai_blackbox_recorder stats`

---

## 🧠 LLM Tracing

BlackBox has first-class support for recording everything that happens inside an LLM call.

### Recording Prompts, Thinking, and Completions

```python
from ai_blackbox_recorder import tracer, SpanKind

with tracer.span("reasoning_step", kind=SpanKind.LLM) as span:
    # Call your LLM here...
    response = call_gemini(prompt, system_prompt)

    # Record everything in one call
    span.set_llm_io(
        system_prompt="You are a helpful assistant.",
        prompt="What is the capital of France?",
        thinking="User asks about geography. This is a factual question...",
        completion="The capital of France is Paris.",
        model="gemini-2.5-flash",
        temperature=0.1,
        prompt_tokens=28,
        completion_tokens=9,
        stop_reason="end_turn",
    )
```

### Recording Tool Calls from LLM Responses

```python
with tracer.span("plan_step", kind=SpanKind.LLM) as span:
    span.set_llm_io(
        prompt="What's the weather in Berlin?",
        completion="I'll check the weather for you.",
        model="gpt-4.1",
        prompt_tokens=15,
        completion_tokens=12,
        tool_calls=[
            {"name": "get_weather", "args": {"city": "Berlin"}},
        ],
        stop_reason="tool_use",
    )
```

### Recording Chat-Style Messages

```python
with tracer.span("chat_turn", kind=SpanKind.LLM) as span:
    span.set_llm_io(
        messages=[
            {"role": "system", "content": "You are a travel advisor."},
            {"role": "user", "content": "Suggest a weekend trip."},
            {"role": "assistant", "content": "How about Barcelona?"},
            {"role": "user", "content": "Too far. Something closer."},
        ],
        completion="How about a day trip to nearby vineyards?",
        model="claude-sonnet-5",
        prompt_tokens=85,
        completion_tokens=14,
    )
```

### What Gets Recorded for LLM Spans

| Field | Method | CLI Display |
| :--- | :--- | :--- |
| System prompt | `set_llm_io(system_prompt=...)` | `📋 System: ...` |
| User prompt | `set_llm_io(prompt=...)` | `💬 Prompt: ...` |
| Chat messages | `set_llm_io(messages=[...])` | `[user]: ... [assistant]: ...` |
| Chain-of-thought | `set_llm_io(thinking=...)` | `🧠 Thinking: ...` |
| Model response | `set_llm_io(completion=...)` | `🤖 Response: ...` |
| Tool calls | `set_llm_io(tool_calls=[...])` | `🔧 Tool Call: name(args)` |
| Input tokens | `set_llm_io(prompt_tokens=N)` | `🔤 N→M` (inline) |
| Output tokens | `set_llm_io(completion_tokens=M)` | Aggregated in trace header |
| Model name | `set_llm_io(model=...)` | `[model-name]` (inline) |
| Stop reason | `set_llm_io(stop_reason=...)` | `⏹️ Stop: ...` |

---

## 🛠️ API Reference

### Three Ways to Record

#### 1. `@trace` Decorator — Automatic Recording

The simplest approach. Works with both sync and async functions. Automatically captures all arguments and return values.

```python
from ai_blackbox_recorder import trace, SpanKind

@trace(kind=SpanKind.TOOL)
def calculate_vat(amount: float, rate: float = 0.20) -> float:
    return round(amount * rate, 2)

@trace(name="research_agent", kind=SpanKind.AGENT)
async def run_agent(query: str) -> str:
    result = calculate_vat(500.0)
    return f"VAT is {result}"
```

> Hierarchy is automatic — if `run_agent` calls `calculate_vat`, the VAT span becomes a child of the agent span. No manual wiring needed.

#### 2. `with tracer.span()` Context Manager — Manual Control

For fine-grained control over what's recorded, or when you need to set LLM-specific fields.

```python
from ai_blackbox_recorder import tracer, SpanKind

with tracer.span("vector_search", kind=SpanKind.RETRIEVER) as span:
    span.set_metadata("index", "knowledge_base_v2")
    span.set_metric("top_k", 10)

    results = search_vector_db(query, top_k=10)

    span.finish(output={"found": len(results), "ids": [r.id for r in results]})
```

#### 3. Mix Both — Decorator + Context Manager

```python
@trace(name="support_bot", kind=SpanKind.AGENT)
async def handle_ticket(ticket_id: str):
    with tracer.span("classify", kind=SpanKind.LLM) as llm_span:
        category = await classify_ticket(ticket_id)
        llm_span.set_llm_io(
            prompt=f"Classify ticket {ticket_id}",
            completion=category,
            model="gemini-2.5-flash",
            prompt_tokens=30,
            completion_tokens=5,
        )

    with tracer.span("resolve", kind=SpanKind.TOOL) as tool_span:
        tool_span.set_tool_call(
            tool_name="jira_api",
            tool_args={"ticket_id": ticket_id, "action": "resolve"},
            tool_result={"status": "resolved"},
        )
```

### Span Kinds

Compatible with the [OpenInference](https://github.com/Arize-ai/openinference) standard:

| SpanKind | When to Use |
| :--- | :--- |
| `SpanKind.AGENT` | Top-level reasoning loop / orchestrator |
| `SpanKind.LLM` | Direct LLM API call (prompt → completion) |
| `SpanKind.TOOL` | Function call, API request, calculator, code execution |
| `SpanKind.RETRIEVER` | Vector search, RAG document fetch, knowledge base query |
| `SpanKind.CHAIN` | Deterministic multi-step pipeline or workflow |

### Session Tracking

Bind traces to a user, session, or conversation:

```python
from ai_blackbox_recorder import tracer

# Set once — all subsequent spans inherit this session ID
tracer.set_session_id("tg_user_12345")

# Later, filter by session in CLI
# ai-blackbox-recorder list --session tg_user_12345
```

### Python Query API

Access traces programmatically without the CLI:

```python
from ai_blackbox_recorder import tracer

# List recent traces
traces = tracer.storage.list_traces(limit=10, has_error=True)

# Get full span tree for a trace
spans = tracer.storage.get_trace("abc123def456")

# Database statistics
stats = tracer.storage.get_stats()
print(f"DB size: {stats['db_size_mb']} MB, Traces: {stats['total_traces']}")

# Export to JSONL
from ai_blackbox_recorder import export_trace_to_jsonl
export_trace_to_jsonl(tracer.storage, "abc123def456", "incident_report.jsonl")
```

### Graceful Shutdown

Buffered spans are flushed automatically when the interpreter exits, so a normal shutdown — or an unhandled exception — loses nothing. You only need these when you want to control the timing yourself:

```python
tracer.flush()             # Wait for the queue to drain; returns False on timeout
tracer.flush(timeout=2.0)  # Bounded wait
tracer.close()             # Flush + stop the worker thread (idempotent)
```

Neither survives `SIGKILL` or an OOM kill — nothing running inside the process does. That case is covered by writing spans when they start; see [Crash Durability](#crash-durability).

---

## ⚙️ Configuration

### Default Configuration

```python
from ai_blackbox_recorder import BlackBoxConfig, Tracer

config = BlackBoxConfig(
    db_path="blackbox_traces.db",       # SQLite file path
    retention="30d",                     # TTL: "7d", "30d", "60d", or int
    max_db_size_mb=300,                  # Hard disk cap in MB
    enabled=True,                        # Master kill-switch
    batch_size=100,                      # Spans per write batch
    flush_interval_seconds=0.5,          # Max queue wait time
    cleanup_interval_hours=6,            # Periodic maintenance interval
    capture_inputs=True,                 # Record function arguments
    capture_outputs=True,                # Record return values
    max_field_chars=100_000,             # Truncate oversized payloads
    record_open_spans=True,              # Write spans on start, so a hard kill still leaves a record
    flush_timeout_seconds=5.0,           # Max wait for the buffer to drain on close() / exit
)

tracer = Tracer(config=config)
```

To configure the *default* tracer — the one behind the bare `@trace` decorator and `tracer` object — call `configure()` at your entry point instead of building an instance:

```python
from ai_blackbox_recorder import BlackBoxConfig, configure

configure(BlackBoxConfig(db_path="/var/lib/myagent/traces.db", retention="7d"))
```

Importing the library creates no file and starts no thread; the default tracer comes up on the first traced call, so `configure()` just has to run before that.

### Crash Durability

`record_open_spans=True` (the default) writes every span the moment it starts, with `end_time` still empty; the finished row replaces it by `span_id` when the function returns. That costs one extra write per span and buys the thing the product is named after: if the process is killed, the recording still shows what was in flight and with which arguments.

On a normal exit — including an unhandled exception — a shutdown hook drains the buffer automatically, so spans are not lost between the last call and process teardown. Set `record_open_spans=False` if you are write-bound and only care about completed spans.

### Retention Presets

| Value | Days |
| :--- | ---: |
| `"7d"` or `"week"` | 7 |
| `"30d"` or `"month"` | 30 |
| `"60d"` or `"2months"` | 60 |
| `"90d"` or `"quarter"` | 90 |
| `14` (any integer) | 14 |

### Disk Protection

When the database file exceeds `max_db_size_mb`, the oldest complete traces are evicted automatically. After eviction, SQLite reclaims disk space via `PRAGMA incremental_vacuum`.

This runs:
1. On tracer startup
2. Every `cleanup_interval_hours` (default: 6 hours)
3. Manually via `ai-blackbox-recorder cleanup`

### Disabling in Tests

```python
config = BlackBoxConfig(enabled=False)
tracer = Tracer(config=config)
# All @trace decorators and context managers become no-ops
```

### Multiple Tracers

You can create isolated tracer instances for different subsystems:

```python
billing_tracer = Tracer(BlackBoxConfig(db_path="billing_traces.db", retention="60d"))
support_tracer = Tracer(BlackBoxConfig(db_path="support_traces.db", retention="7d"))
```

---

## 🏗️ Architecture

```
   @trace("agent")          with tracer.span("llm_call")
        │                              │
        ▼                              ▼
  ┌───────────┐  contextvars   ┌──────────────┐
  │ Root Span  │◄─────────────│  Child Span   │
  │ trace_id=X │  parent_span │  trace_id=X   │
  │ span_id=A  │              │  parent_id=A  │
  └─────┬──────┘              └───────┬───────┘
        │                             │
        ▼                             ▼
  ┌─────────────────────────────────────────┐
  │      queue.Queue  (thread-safe)         │
  │   Non-blocking put() — never stalls     │
  └──────────────────┬──────────────────────┘
                     │
                     ▼  daemon thread
  ┌─────────────────────────────────────────┐
  │     Background Worker Thread             │
  │  • Drains queue in batches (≤100)       │
  │  • INSERT batch into SQLite (WAL mode)  │
  │  • Periodic TTL + size cleanup          │
  └─────────────────────────────────────────┘
                     │
                     ▼
  ┌─────────────────────────────────────────┐
  │     SQLite Database (WAL mode)          │
  │                                         │
  │  PRAGMA journal_mode = WAL              │
  │  PRAGMA synchronous = NORMAL            │
  │  PRAGMA auto_vacuum = INCREMENTAL       │
  │                                         │
  │  Indexes: trace_id, session_id,         │
  │           created_at, kind, has_error   │
  └─────────────────────────────────────────┘
```

**Key design decisions:**

- **`contextvars`** — Python's built-in mechanism for implicit context propagation across sync and async code. Each decorated function automatically knows its parent span without passing IDs manually.
- **`queue.Queue` + daemon thread** — recording never blocks your agent's main loop. Spans are enqueued in nanoseconds and flushed to disk in the background.
- **Write on start, replace on finish** — a span reaches disk before the work it describes completes, so a process that dies mid-call still leaves the tape at the right frame. `INSERT OR REPLACE` on `span_id` means the finished row overwrites the open one instead of duplicating it.
- **Lazy default tracer** — importing the library has no side effects: the database file and worker thread are created on the first traced call, which keeps `configure()` meaningful and keeps `import` cheap in tests.
- **SQLite WAL mode** — Write-Ahead Logging allows concurrent reads (CLI queries) while the worker thread writes. No locking contention.
- **Zero dependencies** — the entire library uses only Python standard library modules: `contextvars`, `sqlite3`, `dataclasses`, `queue`, `threading`, `json`, `uuid`, `time`, `inspect`, `functools`, `argparse`.

---

## 📂 Project Structure

```
ai_blackbox_recorder/
├── __init__.py          # Public API exports
├── __main__.py          # python -m ai_blackbox_recorder entry point
├── tracer.py            # Core: Tracer class, @trace decorator, context manager, worker thread
├── span.py              # Span dataclass, SpanKind enum, set_llm_io(), set_tool_call()
├── storage.py           # SQLite WAL engine, TTL cleanup, max-size eviction
├── config.py            # BlackBoxConfig dataclass with retention parser
├── export.py            # JSONL export and ASCII tree renderer
└── _cli.py              # CLI commands: list, show, errors, stats, cleanup, export
```

---

## 🧪 Testing

The project includes a zero-dependency test suite that runs on bare Python without pytest:

```bash
# Run all tests (no pip install needed)
python3 run_tests.py

# Or with pytest (if available)
pip install pytest pytest-asyncio
pytest tests/ -v
```

Tests cover: sync/async tracing, automatic hierarchy, error capture, TTL retention, 300 MB size eviction, CLI commands, JSONL export, and tree rendering.

Durability is tested the only way that proves anything — by killing a real process: `tests/test_crash_recovery.py` starts an agent in a subprocess, `SIGKILL`s it mid-span, and asserts the unfinished span is still on disk with its inputs. It also asserts that importing the package creates no database, and that a process exiting without `close()` loses nothing.

---

## 🐳 Docker

```bash
docker build -t ai-blackbox-recorder .
docker run ai-blackbox-recorder          # runs test suite
```

---

## 🆚 Comparison with Alternatives

| Feature | ai-blackbox-recorder | LangSmith | Langfuse | Arize Phoenix | ai-trace |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Dependencies** | 0 (stdlib) | 50+ | 30+ | 40+ | 0 |
| **Requires infra** | No | Cloud | PostgreSQL | Docker | No |
| **Local-first** | ✅ | ❌ | ⚠️ self-host | ⚠️ self-host | ✅ |
| **SQL query traces** | ✅ SQLite | API only | SQL | SQL | ❌ JSONL |
| **Async support** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Auto hierarchy** | ✅ contextvars | ✅ | ✅ | ✅ OTel | ❌ |
| **LLM prompt/completion** | ✅ set_llm_io | ✅ | ✅ | ✅ | ⚠️ manual |
| **Chain-of-thought** | ✅ thinking field | ❌ | ❌ | ❌ | ❌ |
| **Token tracking** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **TTL retention** | ✅ built-in | ✅ | ✅ | ✅ | ❌ |
| **Disk size cap** | ✅ 300 MB | N/A cloud | N/A | N/A | ❌ |
| **CLI incident viewer** | ✅ ASCII tree | Web UI | Web UI | Web UI | ❌ |
| **Web UI** | 🔜 v1.0 | ✅ | ✅ | ✅ | ❌ |
| **PII masking** | 🔜 v1.0 | ✅ | ⚠️ | ⚠️ | ❌ |
| **Cost** | Free | Paid | Free tier | Free | Free |

**Best for:** Solo developers, small teams, edge/local agents, privacy-sensitive environments, and anyone who wants a "plug in 2 lines and forget" flight recorder without infrastructure overhead.

---

## 📜 License

[MIT](LICENSE) — use it anywhere, modify freely, no strings attached.

---

## 🗺️ v1.0 Roadmap

The following features are planned for the next major release:

### PII Masking & Data Redaction

Automatic detection and masking of sensitive data before it's written to the database:

- API keys and tokens (`sk-...`, `ghp_...`, `Bearer ...`)
- Credit card numbers, phone numbers, email addresses
- Custom regex patterns via configuration
- Field-level opt-out (`capture_inputs=False` already works per-config, v1.0 adds per-span granularity)

```python
# Planned v1.0 API
config = BlackBoxConfig(
    redact_patterns=[
        r"sk-[a-zA-Z0-9]{20,}",          # OpenAI keys
        r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",  # Credit cards
    ],
    redact_fields=["password", "secret", "api_key"],
)
```

### Built-in Web Viewer

A lightweight, single-page HTML dashboard served directly from the CLI — no Node.js, no npm, no separate process:

```bash
ai-blackbox-recorder ui                    # Open browser at localhost:8080
ai-blackbox-recorder ui --port 9090        # Custom port
```

Features planned:
- Interactive trace tree with expand/collapse
- Timeline visualization of span durations
- Filter by session, time range, errors
- Search across prompts and completions
- Token usage charts

### Standalone HTML Incident Report

Export a single self-contained HTML file with the full trace tree, prompts, thinking, and metadata — shareable via email or Slack with zero tooling required on the recipient's side:

```bash
ai-blackbox-recorder report <TRACE_ID> -o incident_2026-08-18.html
```

### Auto-Instrumentation Hooks

Optional helpers to automatically instrument popular LLM SDKs (Google GenAI, OpenAI, Anthropic) without manual `set_llm_io()` calls:

```python
# Planned v1.0 API
from ai_blackbox_recorder.integrations import patch_google_genai
patch_google_genai(tracer)  # All google.genai calls auto-recorded
```

---

<p align="center">
  <sub>Built with ❤️ for developers who debug at 3 AM.</sub>
</p>
