"""
Basic synchronous agent example using ai-blackbox-recorder.
"""

import time
from ai_blackbox_recorder import SpanKind, trace, tracer


@trace(name="calculator", kind=SpanKind.TOOL)
def calculate_vat(amount: float, rate: float = 0.20) -> float:
    time.sleep(0.02)
    return round(amount * rate, 2)


@trace(name="crm_lookup", kind=SpanKind.TOOL)
def lookup_customer(customer_id: str) -> dict:
    time.sleep(0.03)
    return {"customer_id": customer_id, "name": "Alice", "plan": "enterprise"}


@trace(name="billing_agent", kind=SpanKind.AGENT)
def handle_billing_request(customer_id: str, amount: float) -> dict:
    customer = lookup_customer(customer_id)
    vat = calculate_vat(amount)
    total = amount + vat
    return {
        "customer": customer["name"],
        "subtotal": amount,
        "vat": vat,
        "total": total,
    }


if __name__ == "__main__":
    print("Running synchronous agent with flight recorder...")
    tracer.set_session_id("tg_user_111")
    
    result = handle_billing_request("cust_99", 500.0)
    print(f"Agent Result: {result}")
    
    # Wait for queue to flush to SQLite
    tracer.flush()
    print("\nTrace recorded successfully in blackbox_traces.db")
    print("Run `python -m ai_blackbox_recorder list` or `python -m ai_blackbox_recorder stats` to inspect.")
