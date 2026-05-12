from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from elfa_grvt_bot.grvt_trigger_client import GrvtTriggerClient


def _fake_instrument(symbol: str = "SOL_USDT_Perp"):
    """Build a minimal stand-in Instrument; signing requires base_decimals + instrument_hash; tick_size used for TP/SL alignment."""
    inst = MagicMock()
    inst.instrument = symbol
    inst.instrument_hash = "0x" + "00" * 32
    inst.base_decimals = 1
    inst.tick_size = "0.01"
    return inst


def _fake_instruments_response(symbol: str = "SOL_USDT_Perp"):
    resp = MagicMock()
    resp.result = [_fake_instrument(symbol)]
    return resp


def test_place_trigger_close_short_stop_loss_wires_metadata_correctly():
    """For a short closed by SL: side=buy, trigger_price above entry, trigger_type STOP_LOSS."""
    captured: dict = {}

    with patch("elfa_grvt_bot.grvt_trigger_client.GrvtRawSync") as raw_cls, \
         patch("elfa_grvt_bot.grvt_trigger_client.Account") as account_cls, \
         patch("elfa_grvt_bot.grvt_trigger_client.sign_order") as sign_order_mock:
        raw_instance = MagicMock()
        raw_instance.get_all_instruments_v1.return_value = _fake_instruments_response()
        raw_instance.create_order_v1.return_value = {"result": {"order_id": "ord_sl_1"}}
        raw_cls.return_value = raw_instance
        account_cls.from_key.return_value = MagicMock()
        # Pass-through signing , return the order untouched so we can inspect it.
        sign_order_mock.side_effect = lambda order, *_args, **_kw: order

        client = GrvtTriggerClient(
            env="prod",
            trading_account_id="ta_42",
            private_key="0xdeadbeef",
            api_key="apikey",
        )

        resp = client.place_trigger_close(
            symbol="SOL_USDT_Perp",
            side="buy",
            amount=0.5,
            trigger_price=170.0,
            trigger_type="STOP_LOSS",
        )

        # The submitted ApiCreateOrderRequest should contain a fully-formed Order.
        assert raw_instance.create_order_v1.call_count == 1
        req = raw_instance.create_order_v1.call_args[0][0]
        order = req.order
        assert order.sub_account_id == "ta_42"
        assert order.reduce_only is True
        assert len(order.legs) == 1
        leg = order.legs[0]
        assert leg.instrument == "SOL_USDT_Perp"
        assert leg.is_buying_asset is True  # side=buy
        # trigger metadata
        meta = order.metadata
        assert meta.trigger is not None
        assert meta.trigger.trigger_type.value == "STOP_LOSS"
        tpsl = meta.trigger.tpsl
        assert tpsl.trigger_by.value == "MARK"
        assert tpsl.close_position is True
        # trigger price is sent as a string at decimal precision
        assert float(tpsl.trigger_price) == pytest.approx(170.0)

        # Response is the underlying SDK response.
        assert resp == {"result": {"order_id": "ord_sl_1"}}


def test_place_trigger_close_long_take_profit_wires_metadata_correctly():
    """For a long closed by TP: side=sell, trigger_type TAKE_PROFIT."""
    with patch("elfa_grvt_bot.grvt_trigger_client.GrvtRawSync") as raw_cls, \
         patch("elfa_grvt_bot.grvt_trigger_client.Account") as account_cls, \
         patch("elfa_grvt_bot.grvt_trigger_client.sign_order") as sign_order_mock:
        raw_instance = MagicMock()
        raw_instance.get_all_instruments_v1.return_value = _fake_instruments_response("BTC_USDT_Perp")
        raw_instance.create_order_v1.return_value = {"result": {"order_id": "ord_tp_1"}}
        raw_cls.return_value = raw_instance
        account_cls.from_key.return_value = MagicMock()
        sign_order_mock.side_effect = lambda order, *_args, **_kw: order

        client = GrvtTriggerClient(
            env="prod",
            trading_account_id="ta",
            private_key="0xkey",
            api_key="api",
        )
        client.place_trigger_close(
            symbol="BTC_USDT_Perp",
            side="sell",
            amount=0.01,
            trigger_price=70000.0,
            trigger_type="TAKE_PROFIT",
        )

        req = raw_instance.create_order_v1.call_args[0][0]
        order = req.order
        leg = order.legs[0]
        assert leg.is_buying_asset is False  # side=sell
        assert order.metadata.trigger.trigger_type.value == "TAKE_PROFIT"
        assert order.metadata.trigger.tpsl.trigger_by.value == "MARK"


def test_invalid_side_raises():
    with patch("elfa_grvt_bot.grvt_trigger_client.GrvtRawSync"), \
         patch("elfa_grvt_bot.grvt_trigger_client.Account"):
        client = GrvtTriggerClient(
            env="prod", trading_account_id="ta",
            private_key="0xk", api_key="a",
        )
        with pytest.raises(ValueError, match="side"):
            client.place_trigger_close(
                symbol="SOL_USDT_Perp", side="hold", amount=1.0,
                trigger_price=100.0, trigger_type="STOP_LOSS",
            )


def test_invalid_trigger_type_raises():
    with patch("elfa_grvt_bot.grvt_trigger_client.GrvtRawSync"), \
         patch("elfa_grvt_bot.grvt_trigger_client.Account"):
        client = GrvtTriggerClient(
            env="prod", trading_account_id="ta",
            private_key="0xk", api_key="a",
        )
        with pytest.raises(ValueError, match="trigger_type"):
            client.place_trigger_close(
                symbol="SOL_USDT_Perp", side="buy", amount=1.0,
                trigger_price=100.0, trigger_type="MOON_OR_BUST",
            )


def test_grvt_error_response_is_raised():
    """When the SDK returns a GrvtError dataclass instance, surface it."""
    from elfa_grvt_bot.grvt_trigger_client import GrvtTriggerClient
    from pysdk.grvt_raw_base import GrvtError as RawGrvtError

    with patch("elfa_grvt_bot.grvt_trigger_client.GrvtRawSync") as raw_cls, \
         patch("elfa_grvt_bot.grvt_trigger_client.Account") as account_cls, \
         patch("elfa_grvt_bot.grvt_trigger_client.sign_order") as sign_order_mock:
        raw_instance = MagicMock()
        raw_instance.get_all_instruments_v1.return_value = _fake_instruments_response()
        raw_instance.create_order_v1.return_value = RawGrvtError(
            code=400, message="bad", status=400
        )
        raw_cls.return_value = raw_instance
        account_cls.from_key.return_value = MagicMock()
        sign_order_mock.side_effect = lambda order, *_args, **_kw: order

        client = GrvtTriggerClient(
            env="prod", trading_account_id="ta",
            private_key="0xk", api_key="a",
        )
        with pytest.raises(RuntimeError, match="bad"):
            client.place_trigger_close(
                symbol="SOL_USDT_Perp", side="buy", amount=0.5,
                trigger_price=170.0, trigger_type="STOP_LOSS",
            )


# ---------------------------------------------------------------------------
# Bulk orders v2: place_entry_with_tpsl / place_close_with_oco
# ---------------------------------------------------------------------------


def _make_bulk_client(symbol: str = "SOL_USDT_Perp"):
    """
    Build a GrvtTriggerClient whose underlying GrvtRawSync is mocked. Returns
    (client, raw_instance) so tests can inspect calls on the SDK shim.
    """
    raw_cls = patch("elfa_grvt_bot.grvt_trigger_client.GrvtRawSync").start()
    account_cls = patch("elfa_grvt_bot.grvt_trigger_client.Account").start()
    sign_order_mock = patch("elfa_grvt_bot.grvt_trigger_client.sign_order").start()

    raw_instance = MagicMock()
    raw_instance.get_all_instruments_v1.return_value = _fake_instruments_response(symbol)
    raw_instance.td_rpc = "https://td.example.test"
    raw_cls.return_value = raw_instance
    account_cls.from_key.return_value = MagicMock()
    sign_order_mock.side_effect = lambda order, *_args, **_kw: order

    client = GrvtTriggerClient(
        env="prod", trading_account_id="ta",
        private_key="0xk", api_key="a",
    )
    return client, raw_instance


def test_place_entry_with_tpsl_long_otoco_builds_three_orders():
    client, raw = _make_bulk_client()
    raw._post.return_value = {
        "result": [
            {"order_id": "ord_parent"},
            {"order_id": "ord_tp"},
            {"order_id": "ord_sl"},
        ]
    }
    try:
        res = client.place_entry_with_tpsl(
            symbol="SOL_USDT_Perp",
            entry_side="buy",
            amount=0.5,
            reference_price=100.0,
            tp_pct=1.5,
            sl_pct=1.0,
        )
    finally:
        patch.stopall()

    assert res["errors"] == []
    assert res["parent_order_id"] == "ord_parent"
    assert res["tp_order_id"] == "ord_tp"
    assert res["sl_order_id"] == "ord_sl"
    assert res["tp_price"] == pytest.approx(101.5)
    assert res["sl_price"] == pytest.approx(99.0)

    # Verify the POST happened against the v2 endpoint.
    assert raw._post.call_count == 1
    call_args = raw._post.call_args
    is_auth, url, payload = call_args[0]
    assert is_auth is True
    assert url == "https://td.example.test/full/v2/bulk_orders"
    assert payload["sub_account_id"] == "ta"
    assert len(payload["orders"]) == 3
    parent, tp, sl = payload["orders"]

    # Parent: long market entry, opposite of TP/SL sides.
    assert parent.legs[0].is_buying_asset is True
    assert parent.is_market is True
    assert parent.reduce_only is False
    assert parent.legs[0].limit_price == "0"  # market => 0
    assert parent.metadata.trigger is None

    # TP: limit reduce-only sell with TAKE_PROFIT trigger metadata.
    assert tp.legs[0].is_buying_asset is False
    assert tp.is_market is False
    assert tp.reduce_only is True
    assert float(tp.legs[0].limit_price) == pytest.approx(101.5)
    assert tp.metadata.trigger.trigger_type.value == "TAKE_PROFIT"
    assert tp.metadata.trigger.tpsl.close_position is False
    assert tp.metadata.trigger.tpsl.trigger_by.value == "MARK"

    # SL: market reduce-only sell with STOP_LOSS trigger metadata.
    assert sl.legs[0].is_buying_asset is False
    assert sl.is_market is True
    assert sl.reduce_only is True
    assert sl.legs[0].limit_price == "0"
    assert sl.metadata.trigger.trigger_type.value == "STOP_LOSS"
    assert float(sl.metadata.trigger.tpsl.trigger_price) == pytest.approx(99.0)

    # All three orders must have unique client_order_ids.
    coids = {o.metadata.client_order_id for o in payload["orders"]}
    assert len(coids) == 3


def test_place_entry_with_tpsl_short_mirrors_sides_and_prices():
    client, raw = _make_bulk_client()
    raw._post.return_value = {"result": [{"order_id": "p"}, {"order_id": "t"}, {"order_id": "s"}]}
    try:
        res = client.place_entry_with_tpsl(
            symbol="SOL_USDT_Perp",
            entry_side="sell",
            amount=0.5,
            reference_price=100.0,
            tp_pct=1.5,
            sl_pct=1.0,
        )
    finally:
        patch.stopall()

    # Short entry: TP buy below entry, SL buy above entry.
    assert res["tp_price"] == pytest.approx(98.5)
    assert res["sl_price"] == pytest.approx(101.0)

    payload = raw._post.call_args[0][2]
    parent, tp, sl = payload["orders"]
    assert parent.legs[0].is_buying_asset is False
    assert tp.legs[0].is_buying_asset is True
    assert sl.legs[0].is_buying_asset is True


def test_place_entry_with_tpsl_only_tp_builds_oto():
    client, raw = _make_bulk_client()
    raw._post.return_value = {"result": [{"order_id": "p"}, {"order_id": "t"}]}
    try:
        res = client.place_entry_with_tpsl(
            symbol="SOL_USDT_Perp",
            entry_side="buy",
            amount=1.0,
            reference_price=100.0,
            tp_pct=2.0,
            sl_pct=None,
        )
    finally:
        patch.stopall()

    payload = raw._post.call_args[0][2]
    assert len(payload["orders"]) == 2
    assert res["parent_order_id"] == "p"
    assert res["tp_order_id"] == "t"
    assert res["sl_order_id"] is None
    assert res["sl_price"] is None
    assert res["tp_price"] == pytest.approx(102.0)


def test_place_entry_with_tpsl_only_sl_builds_oto():
    client, raw = _make_bulk_client()
    raw._post.return_value = {"result": [{"order_id": "p"}, {"order_id": "s"}]}
    try:
        res = client.place_entry_with_tpsl(
            symbol="SOL_USDT_Perp",
            entry_side="buy",
            amount=1.0,
            reference_price=100.0,
            tp_pct=None,
            sl_pct=1.0,
        )
    finally:
        patch.stopall()

    payload = raw._post.call_args[0][2]
    assert len(payload["orders"]) == 2
    assert res["parent_order_id"] == "p"
    assert res["sl_order_id"] == "s"
    assert res["tp_order_id"] is None


def test_place_entry_with_tpsl_neither_pct_single_entry():
    client, raw = _make_bulk_client()
    raw._post.return_value = {"result": [{"order_id": "p_only"}]}
    try:
        res = client.place_entry_with_tpsl(
            symbol="SOL_USDT_Perp",
            entry_side="buy",
            amount=1.0,
            reference_price=100.0,
        )
    finally:
        patch.stopall()

    payload = raw._post.call_args[0][2]
    assert len(payload["orders"]) == 1
    assert res["parent_order_id"] == "p_only"
    assert res["tp_order_id"] is None
    assert res["sl_order_id"] is None
    assert res["errors"] == []


def test_place_entry_with_tpsl_limit_parent_uses_limit_price():
    client, raw = _make_bulk_client()
    raw._post.return_value = {"result": [{"order_id": "p"}]}
    try:
        client.place_entry_with_tpsl(
            symbol="SOL_USDT_Perp",
            entry_side="buy",
            amount=1.0,
            reference_price=100.0,
            order_type="limit",
            limit_price=99.5,
        )
    finally:
        patch.stopall()

    payload = raw._post.call_args[0][2]
    parent = payload["orders"][0]
    assert parent.is_market is False
    assert float(parent.legs[0].limit_price) == pytest.approx(99.5)


def test_place_entry_with_tpsl_limit_without_price_raises():
    client, _raw = _make_bulk_client()
    try:
        with pytest.raises(ValueError, match="limit_price"):
            client.place_entry_with_tpsl(
                symbol="SOL_USDT_Perp",
                entry_side="buy",
                amount=1.0,
                reference_price=100.0,
                order_type="limit",
                limit_price=None,
            )
    finally:
        patch.stopall()


def test_place_entry_with_tpsl_invalid_side_raises():
    client, _raw = _make_bulk_client()
    try:
        with pytest.raises(ValueError, match="entry_side"):
            client.place_entry_with_tpsl(
                symbol="SOL_USDT_Perp",
                entry_side="hold",
                amount=1.0,
                reference_price=100.0,
            )
    finally:
        patch.stopall()


def test_place_entry_with_tpsl_partial_failure_reports_per_leg():
    client, raw = _make_bulk_client()
    raw._post.return_value = {
        "result": [
            {"order_id": "p"},
            {"order_id": "t"},
            {"code": 2020, "message": "trigger rejected"},
        ]
    }
    try:
        res = client.place_entry_with_tpsl(
            symbol="SOL_USDT_Perp", entry_side="buy", amount=0.5,
            reference_price=100.0, tp_pct=1.0, sl_pct=1.0,
        )
    finally:
        patch.stopall()

    assert res["parent_order_id"] == "p"
    assert res["tp_order_id"] == "t"
    assert res["sl_order_id"] is None
    assert any("sl" in e.lower() for e in res["errors"])
    assert any("trigger rejected" in e for e in res["errors"])


def test_place_entry_with_tpsl_top_level_error_marks_all_failed():
    client, raw = _make_bulk_client()
    raw._post.return_value = {"code": 401, "message": "auth", "status": 401}
    try:
        res = client.place_entry_with_tpsl(
            symbol="SOL_USDT_Perp", entry_side="buy", amount=0.5,
            reference_price=100.0, tp_pct=1.0, sl_pct=1.0,
        )
    finally:
        patch.stopall()

    assert res["errors"]
    assert any("401" in e or "auth" in e.lower() for e in res["errors"])
    assert res["parent_order_id"] is None
    assert res["tp_order_id"] is None
    assert res["sl_order_id"] is None


def test_place_entry_with_tpsl_falls_back_to_client_order_id():
    """If the response omits order_id, surface the client_order_id we sent."""
    client, raw = _make_bulk_client()
    # No order_id in any entry; entries appear successful otherwise.
    raw._post.return_value = {"result": [{}, {}, {}]}
    try:
        res = client.place_entry_with_tpsl(
            symbol="SOL_USDT_Perp", entry_side="buy", amount=0.5,
            reference_price=100.0, tp_pct=1.0, sl_pct=1.0,
        )
    finally:
        patch.stopall()

    payload = raw._post.call_args[0][2]
    coids = [o.metadata.client_order_id for o in payload["orders"]]
    assert res["parent_order_id"] == coids[0]
    assert res["tp_order_id"] == coids[1]
    assert res["sl_order_id"] == coids[2]
    assert res["errors"] == []


def test_place_close_with_oco_builds_two_reduce_only_legs():
    client, raw = _make_bulk_client()
    raw._post.return_value = {"result": [{"order_id": "tp_id"}, {"order_id": "sl_id"}]}
    try:
        res = client.place_close_with_oco(
            symbol="SOL_USDT_Perp",
            close_side="sell",
            amount=0.5,
            tp_price=110.0,
            sl_price=90.0,
        )
    finally:
        patch.stopall()

    assert res["parent_order_id"] is None
    assert res["tp_order_id"] == "tp_id"
    assert res["sl_order_id"] == "sl_id"

    payload = raw._post.call_args[0][2]
    assert len(payload["orders"]) == 2
    tp, sl = payload["orders"]
    # Both legs are sell + reduce_only.
    assert tp.legs[0].is_buying_asset is False
    assert sl.legs[0].is_buying_asset is False
    assert tp.reduce_only is True
    assert sl.reduce_only is True
    assert tp.metadata.trigger.trigger_type.value == "TAKE_PROFIT"
    assert sl.metadata.trigger.trigger_type.value == "STOP_LOSS"
