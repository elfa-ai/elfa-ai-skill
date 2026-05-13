import pytest

from elfa_grvt_bot.grvt_executor import (
    GrvtExecutor, OrderResult, GrvtError, ErrorClass,
)


class FakeGrvt:
    def __init__(self):
        self.calls: list = []
        self.tickers = {"BTC_USDT_Perp": {"mid_price": 60000.0, "last_price": 60000.0, "mark_price": 60000.0}}
        self.create_order_responses: list = []  # popped per call
        self.set_leverage_responses: list = []

    def fetch_mini_ticker(self, symbol):
        self.calls.append(("fetch_mini_ticker", symbol))
        return self.tickers[symbol]

    def set_leverage(self, leverage, symbol):
        self.calls.append(("set_leverage", leverage, symbol))
        if not self.set_leverage_responses:
            return {"ok": True}
        r = self.set_leverage_responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def create_order(self, **kwargs):
        self.calls.append(("create_order", kwargs))
        if not self.create_order_responses:
            return {"id": "ord_default"}
        r = self.create_order_responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _fast_sleep_factory():
    sleeps = []
    def fn(s):
        sleeps.append(s)
    return fn, sleeps


def test_fetch_mid_price_returns_last():
    fake = FakeGrvt()
    sleep_fn, _ = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn)
    assert ex.fetch_mid_price("BTC_USDT_Perp") == 60000.0


def test_place_order_success():
    fake = FakeGrvt()
    fake.create_order_responses.append({"id": "ord_xyz"})
    sleep_fn, _ = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn)
    res = ex.place_order(
        symbol="BTC_USDT_Perp", side="buy", amount=0.05,
        order_type="market", price=None, time_in_force=None,
        reduce_only=False,
    )
    assert isinstance(res, OrderResult)
    assert res.order_id == "ord_xyz"


def test_set_leverage_calls_underlying():
    fake = FakeGrvt()
    sleep_fn, _ = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn)
    ex.set_leverage(symbol="BTC_USDT_Perp", leverage=5)
    assert ("set_leverage", 5, "BTC_USDT_Perp") in fake.calls


def test_set_leverage_failure_raises_leverage_error():
    fake = FakeGrvt()
    fake.set_leverage_responses.append(RuntimeError("invalid leverage"))
    sleep_fn, _ = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn)
    with pytest.raises(GrvtError) as exc_info:
        ex.set_leverage(symbol="BTC_USDT_Perp", leverage=99)
    assert exc_info.value.error_class == ErrorClass.LEVERAGE


def test_terminal_error_classified_correctly():
    fake = FakeGrvt()
    fake.create_order_responses.append(RuntimeError("insufficient margin"))
    sleep_fn, _ = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn)
    with pytest.raises(GrvtError) as exc_info:
        ex.place_order(symbol="BTC_USDT_Perp", side="buy", amount=0.05,
                       order_type="market", price=None, time_in_force=None,
                       reduce_only=False)
    assert exc_info.value.error_class == ErrorClass.TERMINAL
    assert "margin" in str(exc_info.value).lower()


def test_transient_error_retries_then_surfaces():
    fake = FakeGrvt()
    fake.create_order_responses.extend([
        RuntimeError("connection reset"),
        RuntimeError("connection reset"),
        RuntimeError("connection reset"),
    ])
    sleep_fn, sleeps = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn)
    with pytest.raises(GrvtError) as exc_info:
        ex.place_order(symbol="BTC_USDT_Perp", side="buy", amount=0.05,
                       order_type="market", price=None, time_in_force=None,
                       reduce_only=False)
    assert exc_info.value.error_class == ErrorClass.TRANSIENT
    # Two backoff sleeps between three attempts: 0.5, 1.0
    assert sleeps == [0.5, 1.0]


def test_transient_then_success_recovers():
    fake = FakeGrvt()
    fake.create_order_responses.extend([
        RuntimeError("connection reset"),
        {"id": "ord_recovered"},
    ])
    sleep_fn, _ = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn)
    res = ex.place_order(symbol="BTC_USDT_Perp", side="buy", amount=0.05,
                         order_type="market", price=None, time_in_force=None,
                         reduce_only=False)
    assert res.order_id == "ord_recovered"


# ---------------------------------------------------------------------------
# wait_for_fill + place_tpsl_pair
# ---------------------------------------------------------------------------


class FakeGrvtWithFetch(FakeGrvt):
    def __init__(self):
        super().__init__()
        self.fetch_order_responses: list = []

    def fetch_order(self, id=None, params=None):
        self.calls.append(("fetch_order", id))
        if not self.fetch_order_responses:
            return {}
        r = self.fetch_order_responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeTriggerClient:
    def __init__(self):
        self.calls: list = []
        self.responses: list = []

    def place_trigger_close(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            return {"result": {"order_id": f"trig_{len(self.calls)}"}}
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_wait_for_fill_returns_avg_fill_price_when_filled():
    fake = FakeGrvtWithFetch()
    fake.fetch_order_responses.append({
        "result": {
            "state": {
                "status": "FILLED",
                "avg_fill_price": ["59950.0"],
            }
        }
    })
    sleeps = []
    ex = GrvtExecutor(client=fake, sleep=lambda s: sleeps.append(s))
    px = ex.wait_for_fill(symbol="BTC_USDT_Perp", order_id="ord_1", timeout_secs=2)
    assert px == 59950.0


def test_wait_for_fill_returns_none_on_timeout():
    fake = FakeGrvtWithFetch()
    # Always returns OPEN , never fills
    for _ in range(20):
        fake.fetch_order_responses.append({
            "result": {"state": {"status": "OPEN", "avg_fill_price": []}}
        })
    sleeps = []

    class FakeTime:
        def __init__(self):
            self.t = 0.0
        def __call__(self):
            self.t += 0.5  # each call advances 0.5s
            return self.t

    ex = GrvtExecutor(
        client=fake,
        sleep=lambda s: sleeps.append(s),
        clock=FakeTime(),
    )
    px = ex.wait_for_fill(symbol="BTC_USDT_Perp", order_id="ord_1", timeout_secs=1)
    assert px is None


def test_place_tpsl_pair_short_entry_places_both():
    fake = FakeGrvtWithFetch()
    trig = FakeTriggerClient()
    sleep_fn, _ = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn, trigger_client=trig)

    # Short entry @ 100. tp_pct=1.5 -> tp at 98.5 (buy below). sl_pct=1.0 -> sl at 101 (buy above).
    res = ex.place_tpsl_pair(
        symbol="SOL_USDT_Perp",
        entry_side="sell",
        amount=0.5,
        fill_price=100.0,
        tp_pct=1.5,
        sl_pct=1.0,
    )

    assert res["errors"] == []
    assert res["tp_order_id"] is not None
    assert res["sl_order_id"] is not None
    assert res["tp_price"] == pytest.approx(98.5)
    assert res["sl_price"] == pytest.approx(101.0)

    # TP went via the CCXT path (limit reduce-only buy)
    create_order_calls = [c for c in fake.calls if c[0] == "create_order"]
    assert len(create_order_calls) == 1
    kwargs = create_order_calls[0][1]
    assert kwargs["symbol"] == "SOL_USDT_Perp"
    assert kwargs["side"] == "buy"
    assert kwargs["order_type"] == "limit"
    assert kwargs["price"] == pytest.approx(98.5)
    assert kwargs["params"]["reduce_only"] is True

    # SL went via the trigger client
    assert len(trig.calls) == 1
    sl = trig.calls[0]
    assert sl["symbol"] == "SOL_USDT_Perp"
    assert sl["side"] == "buy"
    assert sl["amount"] == 0.5
    assert sl["trigger_price"] == pytest.approx(101.0)
    assert sl["trigger_type"] == "STOP_LOSS"


def test_place_tpsl_pair_long_entry_places_both_mirrored():
    fake = FakeGrvtWithFetch()
    trig = FakeTriggerClient()
    sleep_fn, _ = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn, trigger_client=trig)

    # Long entry @ 100. tp_pct=1.5 -> tp at 101.5 (sell above). sl_pct=1.0 -> sl at 99 (sell below).
    res = ex.place_tpsl_pair(
        symbol="SOL_USDT_Perp",
        entry_side="buy",
        amount=0.5,
        fill_price=100.0,
        tp_pct=1.5,
        sl_pct=1.0,
    )

    assert res["errors"] == []
    assert res["tp_price"] == pytest.approx(101.5)
    assert res["sl_price"] == pytest.approx(99.0)

    create_order_calls = [c for c in fake.calls if c[0] == "create_order"]
    assert create_order_calls[0][1]["side"] == "sell"
    assert trig.calls[0]["side"] == "sell"


def test_place_tpsl_pair_only_tp():
    fake = FakeGrvtWithFetch()
    trig = FakeTriggerClient()
    sleep_fn, _ = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn, trigger_client=trig)
    res = ex.place_tpsl_pair(
        symbol="SOL_USDT_Perp", entry_side="sell", amount=0.5,
        fill_price=100.0, tp_pct=1.0, sl_pct=None,
    )
    assert res["tp_order_id"] is not None
    assert res["sl_order_id"] is None
    assert res["sl_price"] is None
    assert trig.calls == []


def test_place_tpsl_pair_only_sl():
    fake = FakeGrvtWithFetch()
    trig = FakeTriggerClient()
    sleep_fn, _ = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn, trigger_client=trig)
    res = ex.place_tpsl_pair(
        symbol="SOL_USDT_Perp", entry_side="sell", amount=0.5,
        fill_price=100.0, tp_pct=None, sl_pct=1.5,
    )
    assert res["tp_order_id"] is None
    assert res["sl_order_id"] is not None
    create_order_calls = [c for c in fake.calls if c[0] == "create_order"]
    assert create_order_calls == []


def test_place_tpsl_pair_tp_failure_still_attempts_sl():
    fake = FakeGrvtWithFetch()
    fake.create_order_responses.append(RuntimeError("tick size"))
    trig = FakeTriggerClient()
    sleep_fn, _ = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn, trigger_client=trig)
    res = ex.place_tpsl_pair(
        symbol="SOL_USDT_Perp", entry_side="sell", amount=0.5,
        fill_price=100.0, tp_pct=1.0, sl_pct=1.0,
    )
    assert res["tp_order_id"] is None
    assert res["sl_order_id"] is not None
    assert any("tp" in e.lower() for e in res["errors"])
    assert len(trig.calls) == 1


def test_place_tpsl_pair_sl_failure_reported():
    fake = FakeGrvtWithFetch()
    trig = FakeTriggerClient()
    trig.responses.append(RuntimeError("trigger rejected"))
    sleep_fn, _ = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn, trigger_client=trig)
    res = ex.place_tpsl_pair(
        symbol="SOL_USDT_Perp", entry_side="sell", amount=0.5,
        fill_price=100.0, tp_pct=1.0, sl_pct=1.0,
    )
    assert res["tp_order_id"] is not None  # tp still went through
    assert res["sl_order_id"] is None
    assert any("sl" in e.lower() for e in res["errors"])


def test_place_tpsl_pair_without_trigger_client_skips_sl_with_error():
    fake = FakeGrvtWithFetch()
    sleep_fn, _ = _fast_sleep_factory()
    ex = GrvtExecutor(client=fake, sleep=sleep_fn)  # no trigger client
    res = ex.place_tpsl_pair(
        symbol="SOL_USDT_Perp", entry_side="sell", amount=0.5,
        fill_price=100.0, tp_pct=1.0, sl_pct=1.0,
    )
    assert res["tp_order_id"] is not None
    assert res["sl_order_id"] is None
    assert any("trigger" in e.lower() for e in res["errors"])


# ---------------------------------------------------------------------------
# place_entry_with_tpsl (atomic OTOCO via bulk_orders v2)
# ---------------------------------------------------------------------------


class FakeBulkTriggerClient:
    """Stand-in for GrvtTriggerClient.place_entry_with_tpsl-capable client."""

    def __init__(self):
        self.calls: list = []
        self.responses: list = []  # popped per call

    def place_trigger_close(self, **kwargs):  # legacy back-compat
        self.calls.append(("place_trigger_close", kwargs))
        return {"result": {"order_id": "trig_legacy"}}

    def place_entry_with_tpsl(self, **kwargs):
        self.calls.append(("place_entry_with_tpsl", kwargs))
        if not self.responses:
            return {
                "parent_order_id": "p_default", "tp_order_id": "t_default",
                "sl_order_id": "s_default", "tp_price": None, "sl_price": None,
                "errors": [],
            }
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_place_entry_with_tpsl_delegates_to_trigger_client():
    fake = FakeGrvt()
    trig = FakeBulkTriggerClient()
    trig.responses.append({
        "parent_order_id": "ord_p", "tp_order_id": "ord_t", "sl_order_id": "ord_s",
        "tp_price": 101.5, "sl_price": 99.0, "errors": [],
    })
    ex = GrvtExecutor(client=fake, sleep=lambda s: None, trigger_client=trig)

    res = ex.place_entry_with_tpsl(
        symbol="SOL_USDT_Perp", entry_side="buy", amount=0.5,
        order_type="market", limit_price=None,
        reference_price=100.0, tp_pct=1.5, sl_pct=1.0,
    )

    assert res["parent_order_id"] == "ord_p"
    assert res["tp_order_id"] == "ord_t"
    assert res["sl_order_id"] == "ord_s"
    assert res["errors"] == []
    # Verify forwarded args.
    assert trig.calls == [(
        "place_entry_with_tpsl", {
            "symbol": "SOL_USDT_Perp", "entry_side": "buy", "amount": 0.5,
            "reference_price": 100.0, "tp_pct": 1.5, "sl_pct": 1.0,
            "order_type": "market", "limit_price": None,
        },
    )]


def test_place_entry_with_tpsl_without_trigger_client_returns_error_not_raises():
    fake = FakeGrvt()
    ex = GrvtExecutor(client=fake, sleep=lambda s: None)  # no trigger client
    res = ex.place_entry_with_tpsl(
        symbol="SOL_USDT_Perp", entry_side="buy", amount=0.5,
        order_type="market", limit_price=None,
        reference_price=100.0, tp_pct=1.0, sl_pct=1.0,
    )
    assert res["parent_order_id"] is None
    assert any("trigger_client" in e for e in res["errors"])


def test_place_entry_with_tpsl_swallows_trigger_client_exceptions():
    fake = FakeGrvt()
    trig = FakeBulkTriggerClient()
    trig.responses.append(RuntimeError("network blip"))
    ex = GrvtExecutor(client=fake, sleep=lambda s: None, trigger_client=trig)
    res = ex.place_entry_with_tpsl(
        symbol="SOL_USDT_Perp", entry_side="buy", amount=0.5,
        order_type="market", limit_price=None,
        reference_price=100.0, tp_pct=1.0, sl_pct=1.0,
    )
    assert res["parent_order_id"] is None
    assert any("network blip" in e for e in res["errors"])


def test_place_entry_with_tpsl_propagates_partial_errors():
    fake = FakeGrvt()
    trig = FakeBulkTriggerClient()
    trig.responses.append({
        "parent_order_id": "p", "tp_order_id": "t", "sl_order_id": None,
        "tp_price": 101.5, "sl_price": 99.0,
        "errors": ["sl: code=2020: trigger rejected"],
    })
    ex = GrvtExecutor(client=fake, sleep=lambda s: None, trigger_client=trig)
    res = ex.place_entry_with_tpsl(
        symbol="SOL_USDT_Perp", entry_side="buy", amount=0.5,
        order_type="market", limit_price=None,
        reference_price=100.0, tp_pct=1.5, sl_pct=1.0,
    )
    assert res["parent_order_id"] == "p"
    assert res["tp_order_id"] == "t"
    assert res["sl_order_id"] is None
    assert res["errors"] == ["sl: code=2020: trigger rejected"]
