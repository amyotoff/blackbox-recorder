"""
Command-line interface (CLI) for BlackBox Recorder.
Inspect traces, investigate incidents, view error graphs, and run maintenance.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from typing import Optional

from ai_blackbox_recorder.config import BlackBoxConfig
from ai_blackbox_recorder.export import export_all_to_jsonl, export_trace_to_jsonl, render_trace_tree
from ai_blackbox_recorder.storage import TraceStorage


def _format_time(ts: Optional[float]) -> str:
    if not ts:
        return "N/A"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def cmd_stats(args: argparse.Namespace) -> None:
    config = BlackBoxConfig(db_path=args.db)
    storage = TraceStorage(config)
    stats = storage.get_stats()

    print("\n📊 BlackBox Recorder — Statistics")
    print("═" * 45)
    print(f"📁 Database Path:    {stats['db_path']}")
    print(f"💾 File Size:        {stats['db_size_mb']} MB (Max limit: {stats['max_db_size_mb']} MB)")
    print(f"📦 Total Traces:     {stats['total_traces']}")
    print(f"⏱️ Total Spans:      {stats['total_spans']}")
    print(f"❌ Total Errors:     {stats['total_errors']}")
    print(f"⏳ Unfinished Spans: {stats['total_incomplete']}")
    print(f"⏳ Retention (TTL):  {stats['retention_days']} days")
    print(f"🗓️ Oldest Trace:     {_format_time(stats['oldest_timestamp'])}")
    print(f"🗓️ Newest Trace:     {_format_time(stats['newest_timestamp'])}")
    print("═" * 45 + "\n")


def cmd_list(args: argparse.Namespace) -> None:
    config = BlackBoxConfig(db_path=args.db)
    storage = TraceStorage(config)

    has_error = True if args.errors_only else None
    traces = storage.list_traces(limit=args.limit, session_id=args.session, has_error=has_error)

    if not traces:
        print("\n🔍 No traces found.")
        return

    print(f"\n📋 Last {len(traces)} Traces:")
    print("─" * 85)
    print(f"{'Start Time':<20} {'Status':<7} {'Spans':<7} {'Duration':<10} {'Root Operation':<20} {'Trace ID'}")
    print("─" * 85)

    for t in traces:
        if t.get("error_count"):
            status = "❌ ERR"
        elif t.get("incomplete_count"):
            status = "⏳ OPEN"
        else:
            status = "✅ OK"
        time_str = _format_time(t["start_time"])
        duration_str = f"{t.get('duration_ms', 0)}ms"
        root_name = (t.get("root_name") or "unnamed")[:18]
        print(f"{time_str:<20} {status:<7} {t['span_count']:<7} {duration_str:<10} {root_name:<20} {t['trace_id']}")
    print("─" * 85 + "\n")


def cmd_show(args: argparse.Namespace) -> None:
    config = BlackBoxConfig(db_path=args.db)
    storage = TraceStorage(config)
    spans = storage.get_trace(args.trace_id)

    if not spans:
        print(f"\n❌ Trace '{args.trace_id}' not found.")
        return

    if args.json:
        print(json.dumps(spans, indent=2, ensure_ascii=False, default=str))
    else:
        # Verbose tree includes prompts, completions, thinking, tokens inline
        print("\n" + render_trace_tree(spans, verbose=args.verbose))
        if args.verbose:
            print("\n📝 Raw Span Payloads:")
            print("─" * 60)
            for s in spans:
                print(f"\n🔹 [{s['kind']}] {s['name']} (ID: {s['span_id'][:12]}...)")
                if s.get("inputs"):
                    print(f"  Inputs:  {json.dumps(s['inputs'], ensure_ascii=False, default=str)}")
                if s.get("outputs"):
                    print(f"  Outputs: {json.dumps(s['outputs'], ensure_ascii=False, default=str)}")
                if s.get("error"):
                    print(f"  Error:   {s['error']}")
                if s.get("metadata"):
                    print(f"  Meta:    {json.dumps(s['metadata'], ensure_ascii=False, default=str)}")
                if s.get("metrics"):
                    print(f"  Metrics: {json.dumps(s['metrics'], ensure_ascii=False, default=str)}")
        print()


def cmd_cleanup(args: argparse.Namespace) -> None:
    config = BlackBoxConfig(db_path=args.db)
    if args.retention:
        config.retention = args.retention
    storage = TraceStorage(config)
    res = storage.cleanup_all()
    print(
        f"🧹 Cleanup complete. Deleted {res['ttl_deleted']} expired spans (TTL) "
        f"and {res['size_deleted']} spans (size limit)."
    )


def cmd_export(args: argparse.Namespace) -> None:
    config = BlackBoxConfig(db_path=args.db)
    storage = TraceStorage(config)

    if args.trace:
        count = export_trace_to_jsonl(storage, args.trace, args.output)
        print(f"💾 Exported {count} spans for trace {args.trace} to {args.output}")
    else:
        count = export_all_to_jsonl(storage, args.output)
        print(f"💾 Exported total {count} spans to {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ai-blackbox-recorder",
        description="🛫 BlackBox Recorder — AI Agent Incident Flight Recorder",
    )
    parser.add_argument("--db", default="blackbox_traces.db", help="Path to SQLite trace database")

    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    # stats
    sub_stats = subparsers.add_parser("stats", help="Show database & recording statistics")
    sub_stats.set_defaults(func=cmd_stats)

    # list
    sub_list = subparsers.add_parser("list", help="List recorded traces")
    sub_list.add_argument("--limit", type=int, default=20, help="Number of traces to show")
    sub_list.add_argument("--session", type=str, default=None, help="Filter by session ID")
    sub_list.add_argument("--errors-only", action="store_true", help="Show only traces with errors")
    sub_list.set_defaults(func=cmd_list)

    # errors
    sub_errors = subparsers.add_parser("errors", help="Show only failing traces")
    sub_errors.add_argument("--limit", type=int, default=20, help="Number of traces to show")
    sub_errors.set_defaults(func=lambda a: setattr(a, "errors_only", True) or cmd_list(a))

    # show
    sub_show = subparsers.add_parser("show", help="Show hierarchical trace tree for incident analysis")
    sub_show.add_argument("trace_id", help="Trace ID to inspect")
    sub_show.add_argument("-v", "--verbose", action="store_true", help="Print inputs, outputs and metadata")
    sub_show.add_argument("--json", action="store_true", help="Print raw JSON format")
    sub_show.set_defaults(func=cmd_show)

    # cleanup
    sub_cleanup = subparsers.add_parser("cleanup", help="Run TTL retention and size cleanup")
    sub_cleanup.add_argument("--retention", type=str, default=None, help="Override retention TTL (e.g. 7d, 30d)")
    sub_cleanup.set_defaults(func=cmd_cleanup)

    # export
    sub_export = subparsers.add_parser("export", help="Export traces to JSONL")
    sub_export.add_argument("--trace", type=str, default=None, help="Specific trace ID to export")
    sub_export.add_argument("-o", "--output", required=True, help="Output .jsonl file path")
    sub_export.set_defaults(func=cmd_export)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
