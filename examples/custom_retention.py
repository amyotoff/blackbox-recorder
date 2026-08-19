"""
Configuring custom retention policy (TTL) and maximum database size.
"""

from ai_blackbox_recorder import BlackBoxConfig, SpanKind, Tracer

# Custom configuration with 60 days retention and 200MB max disk size
custom_config = BlackBoxConfig(
    db_path="./logs/custom_agent_traces.db",
    retention="60d",        # Options: "7d", "30d", "60d", "2months", or integer days
    max_db_size_mb=200,     # Evicts oldest traces if file exceeds 200MB
    batch_size=50,
)

custom_tracer = Tracer(config=custom_config)


@custom_tracer.trace(name="custom_job", kind=SpanKind.CHAIN)
def run_periodic_job(job_id: int):
    with custom_tracer.span("substep", kind=SpanKind.TOOL) as s:
        s.set_metric("processed_items", 42)
        return f"Job {job_id} done"


if __name__ == "__main__":
    print(f"Configured retention: {custom_config.retention_days} days")
    print(f"Configured max DB size: {custom_config.max_db_size_mb} MB")
    
    run_periodic_job(101)
    custom_tracer.flush()
    custom_tracer.close()
    
    print("Job completed and flushed.")
