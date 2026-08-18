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
  <a href="#-incident-investigation-cli">CLI</a> •
  <a href="#-llm-tracing">LLM Tracing</a> •
  <a href="#-configuration">Configuration</a> •
  <a href="#%EF%B8%8F-api-reference">API</a> •
  <a href="#-v10-roadmap">Roadmap</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.5.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/python-3.10+-brightgreen" alt="Python">
  <img src="https://img.shields.io/badge/dependencies-0_(stdlib_only)-orange" alt="Zero deps">
  <img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="License">
  <img src="https://img.shields.io/badge/storage-SQLite_WAL-blueviolet" alt="SQLite">
</p>

---

## The Problem

Your AI agent made a wrong decision at 3 AM. A customer got a nonsensical answer. The pipeline silently swallowed an error. **What happened? Why?**

Most tracing solutions (LangSmith, Logfire, Phoenix) require cloud subscriptions, heavy infrastructure (PostgreSQL, ClickHouse), or dozens of transitive dependencies (`grpc`, `protobuf`, `opentelemetry-*`).

## The Solution

`blackbox-recorder` is a **flight recorder** — like the black box in an aircraft. It writes everything, silently and efficiently, so when you need to investigate, the full history is right there in a single local SQLite file.

- **Zero external dependencies** — pure Python 3.10+ stdlib
- **Never blocks your agent** — background daemon thread with `queue.Queue`
- **Automatic call hierarchy** — `contextvars` builds the execution tree for you (sync + async)
- **Configurable retention** — 7 days, 30 days, 60 days, or custom
- **Disk protection** — hard cap at 300 MB (configurable), oldest traces evicted automatically
- **First-class LLM support** — prompts, completions, chain-of-thought, token counts, tool calls
- **Incident CLI** — beautiful ASCII tree with everything you need to debug

---

## 🚀 Quick Start

### Installation

```bash
pip install blackbox-recorder
```

### Two Lines to Start Recording

```python
from blackbox_recorder import trace, SpanKind

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
blackbox-recorder list

# Show the execution tree
blackbox-recorder show <TRACE_ID>

# Full details: prompts, thinking, tokens
blackbox-recorder show <TRACE_ID> -v
```

---

## 🔍 Incident Investigation (CLI)

The CLI is your primary tool for post-incident analysis. Install the package and it's available globally.

### `list` — Recent Traces

```bash
$ blackbox-recorder list --limit 5

📋 Last 5 Traces:
─────────────────────────────────────────────────────────────────────────────────────
Start Time           Status  Spans   Duration   Root Operation       Trace ID
─────────────────────────────────────────────────────────────────────────────────────
2026-08-18 18:52:08  ✅ OK    4       132.7ms    support_agent        0f472b36b2a9...
2026-08-18 18:46:17  ❌ ERR   3       59.6ms     billing_agent        61252647020244...
```

### `show` — Execution Tree

```bash
$ blackbox-recorder show 0f472b36b2a941469f7a9ff66b28abd0

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
$ blackbox-recorder show 0f472b36... -v

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
| `blackbox-recorder stats` | Database stats: size, span/trace counts, retention policy |
| `blackbox-recorder list [--limit N] [--session ID] [--errors-only]` | List traces with filtering |
| `blackbox-recorder errors [--limit N]` | Show only traces with errors |
| `blackbox-recorder show <TRACE_ID> [-v] [--json]` | Hierarchical tree view; `-v` for prompts/thinking/tokens |
| `blackbox-recorder export -o file.jsonl [--trace ID]` | Export to OpenInference-compatible JSONL |
| `blackbox-recorder cleanup [--retention 7d]` | Manual TTL cleanup and disk reclaim |

> **Tip:** You can also run CLI as a module: `python -m blackbox_recorder stats`

---

## 🧠 LLM Tracing

BlackBox has first-class support for recording everything that happens inside an LLM call.

### Recording Prompts, Thinking, and Completions

```python
from blackbox_recorder import tracer, SpanKind

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
from blackbox_recorder import trace, SpanKind

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
from blackbox_recorder import tracer, SpanKind

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
from blackbox_recorder import tracer

# Set once — all subsequent spans inherit this session ID
tracer.set_session_id("tg_user_12345")

# Later, filter by session in CLI
# blackbox-recorder list --session tg_user_12345
```

### Python Query API

Access traces programmatically without the CLI:

```python
from blackbox_recorder import tracer

# List recent traces
traces = tracer.storage.list_traces(limit=10, has_error=True)

# Get full span tree for a trace
spans = tracer.storage.get_trace("abc123def456")

# Database statistics
stats = tracer.storage.get_stats()
print(f"DB size: {stats['db_size_mb']} MB, Traces: {stats['total_traces']}")

# Export to JSONL
from blackbox_recorder import export_trace_to_jsonl
export_trace_to_jsonl(tracer.storage, "abc123def456", "incident_report.jsonl")
```

### Graceful Shutdown

Always flush before your process exits to ensure all buffered spans are written:

```python
tracer.flush()   # Wait for queue to drain
tracer.close()   # Flush + stop worker thread
```

---

## ⚙️ Configuration

### Default Configuration

```python
from blackbox_recorder import BlackBoxConfig, Tracer

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
)

tracer = Tracer(config=config)
```

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
3. Manually via `blackbox-recorder cleanup`

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
- **SQLite WAL mode** — Write-Ahead Logging allows concurrent reads (CLI queries) while the worker thread writes. No locking contention.
- **Zero dependencies** — the entire library uses only Python standard library modules: `contextvars`, `sqlite3`, `dataclasses`, `queue`, `threading`, `json`, `uuid`, `time`, `inspect`, `functools`, `argparse`.

---

## 📂 Project Structure

```
blackbox_recorder/
├── __init__.py          # Public API exports
├── __main__.py          # python -m blackbox_recorder entry point
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

---

## 🐳 Docker

```bash
docker build -t blackbox-recorder .
docker run blackbox-recorder          # runs test suite
```

---

## 🆚 Comparison with Alternatives

| Feature | blackbox-recorder | LangSmith | Langfuse | Arize Phoenix | ai-trace |
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
blackbox-recorder ui                    # Open browser at localhost:8080
blackbox-recorder ui --port 9090        # Custom port
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
blackbox-recorder report <TRACE_ID> -o incident_2026-08-18.html
```

### Auto-Instrumentation Hooks

Optional helpers to automatically instrument popular LLM SDKs (Google GenAI, OpenAI, Anthropic) without manual `set_llm_io()` calls:

```python
# Planned v1.0 API
from blackbox_recorder.integrations import patch_google_genai
patch_google_genai(tracer)  # All google.genai calls auto-recorded
```

---

<p align="center">
  <sub>Built with ❤️ for developers who debug at 3 AM.</sub>
</p>
