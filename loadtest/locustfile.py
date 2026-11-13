"""Locust scenario: portfolio managers checking positions/cash/limits
before filling or cancelling an order. Reads outnumber writes, mirroring
how a PM actually works.

One of the write tasks (place_concentrated_order) deliberately targets
the instrument with a seeded ConcentrationLimit, to surface - under
organic concurrent load, not just the isolated repro in
concurrency_probe.py - the known race documented in .claude/context.md:
the concentration-limit check has no locking/reservation, so concurrent
BUY orders can collectively blow past the limit even though each one
individually passed the check.

Keys/IDs come from scripts/provision_load_test_keys.py's output (default
loadtest/.keys.json). Run it first, then e.g.:

  locust -f loadtest/locustfile.py --host http://localhost:8000 \
      --headless -u 25 -r 5 -t 3m \
      --html loadtest/results/run.html --csv loadtest/results/run
"""

import itertools
import json
import os
import random
import uuid
from pathlib import Path

from locust import HttpUser, between, task

_config = json.loads(Path(os.environ.get("LOADTEST_KEYS_FILE", "loadtest/.keys.json")).read_text())
_ACCOUNT_ID = _config["account_id"]
_LIMITED_INSTRUMENT_ID = _config["limited_instrument_id"]
_UNRESTRICTED_INSTRUMENT_ID = _config["unrestricted_instrument_id"]
_KEY_POOL = itertools.cycle(_config["keys"])


class PMUser(HttpUser):
    """One simulated portfolio manager: reads far more than it trades.

    Set LOADTEST_FAST=1 to drop the realistic 1-4s human think-time down to
    near zero - useful for a separate stress pass that actually finds the
    server's ceiling (DB pool / event loop), since at realistic PM pacing
    even 100 concurrent users never generates enough in-flight concurrency
    to stress a 15-connection pool.
    """

    wait_time = between(0.05, 0.2) if os.environ.get("LOADTEST_FAST") == "1" else between(1, 4)

    def on_start(self) -> None:
        key = next(_KEY_POOL)
        self.client.headers["X-API-Key"] = key["api_key"]
        self.open_order_ids: list[str] = []

    def _place_order(self, instrument_id: str, quantity: int, reference_price: str) -> str | None:
        payload = {
            "account_id": _ACCOUNT_ID,
            "instrument_id": instrument_id,
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": str(quantity),
            "reference_price": reference_price,
        }
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        with self.client.post("/orders", json=payload, headers=headers, catch_response=True) as resp:
            if resp.status_code != 201:
                return None
            resp.success()
            order = resp.json()["data"]
            if order["status"] == "REJECTED":
                # Expected once the simulated cash budget / concentration
                # limit genuinely runs out - a compliance rejection, not an
                # open order, so don't track it for the cancel task.
                return None
            self.open_order_ids.append(order["id"])
            return order["id"]

    @task(5)
    def view_positions(self):
        self.client.get(f"/accounts/{_ACCOUNT_ID}/positions")

    @task(4)
    def view_cash(self):
        self.client.get(f"/accounts/{_ACCOUNT_ID}/cash-balances")

    @task(3)
    def view_summary(self):
        self.client.get(f"/accounts/{_ACCOUNT_ID}/summary")

    @task(2)
    def browse_instruments(self):
        self.client.get("/instruments")

    @task(2)
    def place_order(self):
        order_id = self._place_order(_UNRESTRICTED_INSTRUMENT_ID, random.randint(1, 10), "500.00")
        if order_id:
            self.client.get(f"/orders/{order_id}/compliance-checks")

    @task(1)
    def place_concentrated_order(self):
        order_id = self._place_order(_LIMITED_INSTRUMENT_ID, random.randint(5, 20), "150.00")
        if order_id:
            self.client.get(f"/orders/{order_id}/compliance-checks")

    @task(1)
    def cancel_order(self):
        if not self.open_order_ids:
            return
        order_id = self.open_order_ids.pop()
        with self.client.get(f"/orders/{order_id}", catch_response=True) as get_resp:
            if get_resp.status_code != 200:
                get_resp.success()  # may have been cancelled already by a prior task
                return
            etag = get_resp.headers.get("ETag")
            get_resp.success()
        headers = {"Idempotency-Key": str(uuid.uuid4()), "If-Match": etag}
        with self.client.post(f"/orders/{order_id}/cancel", headers=headers, catch_response=True) as resp:
            if resp.status_code in (200, 412):
                resp.success()
