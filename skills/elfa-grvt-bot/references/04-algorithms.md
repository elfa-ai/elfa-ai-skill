# 04 - Algorithms

Pseudocode for every non-trivial control flow. Implement each one in this order; later algorithms call earlier ones. Reference column names match `03-state.md` exactly.

## Supervisor (main process loop)

The receiver's top-level loop. One supervisor per receiver process.

```
async def supervisor(registry, elfa, executor, alerts, config):
    tasks = {}  # query_id -> asyncio.Task
    POLL_INTERVAL_S = 5.0

    log("supervisor started")
    while not shutdown_signal:
        active = registry.list_strategies(status='active')
        active_ids = {s.query_id for s in active}

        # Spawn missing tasks
        for s in active:
            if s.query_id not in tasks or tasks[s.query_id].done():
                tasks[s.query_id] = spawn(strategy_loop(
                    s.query_id, registry, elfa, executor, alerts, config,
                ))
                log(f"spawning SSE task for {s.query_id}")

        # Reap tasks for strategies no longer active
        for qid in list(tasks.keys()):
            if qid not in active_ids:
                tasks[qid].cancel()
                del tasks[qid]
                log(f"cancelled SSE task for {qid}")

        # Reap exited tasks (terminal status reached)
        for qid, t in list(tasks.items()):
            if t.done():
                del tasks[qid]

        await sleep(POLL_INTERVAL_S)

    # Shutdown: cancel all tasks
    for t in tasks.values(): t.cancel()
    await gather(*tasks.values(), return_exceptions=True)
```

Notes:

- `shutdown_signal` is set on SIGINT/SIGTERM. The bot catches these and shuts down cleanly.
- The 5-second poll interval is what determines how fast newly-authored strategies are picked up. Don't go below 1s (database thrashing) or above 30s (user-visible delay).
- A single supervisor can hold hundreds of SSE streams open since each is just an idle async task waiting on bytes.

## Strategy loop (one per active strategy)

```
async def strategy_loop(query_id, registry, elfa, executor, alerts, config):
    BACKOFF_INITIAL_S = 2.0
    BACKOFF_MAX_S = 60.0
    backoff = BACKOFF_INITIAL_S

    while True:
        # 1. Status reconciliation via poll-query
        try:
            state = elfa.get_query(query_id)
        except NotFound:  # 404
            registry.set_strategy_status(query_id, 'failed')
            alerts.emit('error', 'strategy_terminated_remotely',
                        f'strategy not found on Elfa', query_id=query_id)
            return

        remote_status = state['status']
        executions = state.get('executions', []) or []

        if remote_status in TERMINAL_STATUSES:
            sync_terminal_status_locally(
                query_id, remote_status, executions, registry, alerts)
            return

        if remote_status == 'recurring':
            registry.set_strategy_status(query_id, 'failed')
            alerts.emit('error', 'strategy_terminated_remotely',
                        'recurring queries are not supported by this bot',
                        query_id=query_id)
            return

        # remote_status == 'active'; check for executions we missed
        for ex in executions:
            if not registry.fire_exists(ex['id']):
                # A fire happened on Elfa but we have no local record.
                # Insert a marker row so we don't try to "process" it later,
                # and surface to the user.
                registry.insert_fire_marker(
                    event_id=ex['id'], query_id=query_id,
                    outcome='unknown',
                    raw_payload=json.dumps(ex),
                )
                alerts.emit('error', 'manual_intervention_required',
                    f'strategy triggered on Elfa while receiver was disconnected. '
                    f'Order was NOT placed by the bot. Review GRVT.',
                    query_id=query_id,
                    fire_event_id=ex['id'],
                    details={'execution': ex})
                # The strategy will likely transition to 'triggered' remotely
                # on next iteration; we'll exit via sync_terminal_status then.

        # 2. Open SSE stream and consume events
        try:
            async for event in elfa.stream_notifications(query_id):
                process_fire(event['event_id'], query_id, event['data'],
                             registry, executor, alerts, config)
                # Strategy is now 'fired'; supervisor will cancel the task
                # on next reconcile. We can also exit immediately:
                return

            # Stream closed without yielding events (410, 204, or clean EOF).
            # Loop back to poll-query.
            backoff = BACKOFF_INITIAL_S  # reset on clean close

        except ElfaStreamError as e:
            if e.status_code in (401, 404):
                alerts.emit('error', 'strategy_terminated_remotely',
                            f'SSE failed: HTTP {e.status_code}', query_id=query_id)
                registry.set_strategy_status(query_id, 'failed')
                return
            # 5xx or network: exponential backoff
            log(f"strategy loop {query_id} transient error: {e}; backing off {backoff}s")
            await sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)

        except CancelledError:
            log(f"strategy loop {query_id} cancelled")
            raise

    log(f"strategy loop {query_id} finished")
```

Notes:

- `TERMINAL_STATUSES = {'triggered', 'expired', 'cancelled', 'failed'}`.
- `process_fire` is at-most-once via the dedupe insert in `03-state.md`. If the SSE redelivers the same event (same dedupe key regardless of which schema variant the frame used), the second call is a no-op.
- After a successful fire, we return immediately rather than looping; the supervisor's next reconcile will see local status `fired` and reap the task.
- The poll-query check on iteration 1 of the loop catches the "fired while offline" case before opening any stream.

## SSE frame parser

This is the most-tested part of the system. Production has shipped multiple wire-format schemas under the same endpoint; the parser must accept all of them (see `02-protocols.md` "Wire-format drift").

```
TRIGGER_EVENT_TYPES = ('notification', 'notification:new', 'query.triggered')
SILENT_SKIP_EVENT_TYPES = ('end', 'heartbeat', 'ping')  # known non-triggers; no warn, no drift counter


async def parse_sse_frames(lines, expected_query_id):
    """Yields well-formed trigger events. Drops malformed frames with WARN."""
    current_event = None
    current_id = None
    data_lines = []
    in_frame = False

    async for line in lines:
        if line.startswith(':'):
            continue  # keep-alive comment
        if line == '':
            if in_frame:
                event = build_event(current_event, current_id,
                                    '\n'.join(data_lines) if data_lines else None,
                                    expected_query_id)
                if event is not None:
                    yield event
            current_event, current_id, data_lines, in_frame = None, None, [], False
            continue
        in_frame = True
        if ':' not in line:
            continue
        field, _, value = line.partition(':')
        value = value[1:] if value.startswith(' ') else value
        field = field.strip()
        if   field == 'event': current_event = value.strip()
        elif field == 'id':    current_id = value.strip()
        elif field == 'data':  data_lines.append(value)


def build_event(event_type, sse_id, data, expected_query_id):
    """Returns {event_id, data} or None. None = drop.

    Dispatches on event_type. Each branch yields the same shape
    ({event_id: <string>, data: <full payload>}) even though the
    underlying schemas differ.
    """
    if event_type in SILENT_SKIP_EVENT_TYPES:
        return None  # known non-trigger (close frame, heartbeat); no warn
    if event_type not in TRIGGER_EVENT_TYPES:
        return None  # unknown event type; no warn (could be a new control event)
    if not data:
        warn(f"dropping SSE {event_type!r} frame: missing data")
        return None
    try:
        payload = json.loads(data)
    except JSONDecodeError:
        warn(f"dropping SSE {event_type!r} frame: data is not JSON")
        return None
    if not isinstance(payload, dict):
        warn(f"dropping SSE {event_type!r} frame: data is not a JSON object")
        return None

    # Branch 1: production schema ('notification' + status=triggered).
    # Verified emitted by api.elfa.ai as of 2026-05-14. Lead with this
    # branch because it is what's actually being delivered today.
    if event_type == 'notification' and payload.get('status') == 'triggered':
        for required in ('queryId', 'executionId', 'triggerTime'):
            if not isinstance(payload.get(required), str) or not payload.get(required):
                warn(f"dropping SSE {event_type!r}: missing/invalid {required}")
                return None
        if payload['queryId'] != expected_query_id:
            warn(f"dropping SSE {event_type!r}: queryId mismatch")
            return None
        return {'event_id': payload['executionId'], 'data': payload}

    # Branch 2: documented schema ('notification:new') - accepted for the
    # day Elfa rolls it out; not observed in production as of 2026-05-14.
    if event_type == 'notification:new':
        nid = payload.get('id')
        nested = payload.get('data') or {}
        qid = nested.get('queryId')
        if nid is None:
            warn(f"dropping SSE {event_type!r}: missing top-level 'id'")
            return None
        if qid != expected_query_id:
            warn(f"dropping SSE {event_type!r}: data.queryId {qid!r} != stream {expected_query_id!r}")
            return None
        return {'event_id': str(nid), 'data': payload}

    # Branch 3: older canonical envelope ('query.triggered' with eventId)
    if event_type == 'query.triggered':
        ev_id = payload.get('eventId')
        qid = payload.get('queryId')
        if not isinstance(ev_id, str) or not ev_id:
            warn(f"dropping SSE {event_type!r}: missing/invalid eventId")
            return None
        if qid != expected_query_id:
            warn(f"dropping SSE {event_type!r}: queryId mismatch")
            return None
        return {'event_id': ev_id, 'data': payload}

    # Event type accepted but payload doesn't match any known schema
    warn(f"dropping SSE {event_type!r}: payload matches no known schema (parser drift?)")
    return None
```

Notes:

- The SSE `id:` line value (`sse_id`) is parsed but discarded. The dedupe key always comes from a field inside the JSON payload, never the SSE-level id, because the SSE id is not always meaningful (e.g., production has used both UUIDs and numbers).
- All "drop" paths are WARN-level logs. They are NOT alerts. A burst of drops should be loud in logs but quiet in the user-facing alerts table.
- **Exception**: if the parser keeps dropping frames for the same active strategy (3+ drops in a 10-minute window), emit a `parser_drift` ERROR alert. Keep a per-query drop counter in memory; reset on first successful parse or task cancellation.
- The dedupe key produced is a string regardless of source schema (`str(payload["id"])` for the docs schema; UUIDs for the others). The `fires.event_id` TEXT column accepts all of them.
- `event: end` close frames from Elfa (with payload `{"code": "QUERY_STREAM_CLOSED", ...}`) are explicitly in `SILENT_SKIP_EVENT_TYPES`. The strategy-loop catches the subsequent connection close and falls back to poll-query for reconciliation.

## Fire processing

```
def process_fire(event_id, query_id, raw_payload, registry, executor, alerts, config):
    """At-most-once per event_id. Called from strategy_loop."""

    # 1. Dedupe + reserve via PK insert
    try:
        registry.insert_fire_marker(
            event_id=event_id, query_id=query_id,
            outcome='unknown',
            raw_payload=json.dumps(raw_payload),
            received_at=now_iso())
    except IntegrityError:
        log(f"duplicate event {event_id}, skipped")
        return

    # 2. Look up the strategy
    strategy = registry.get_strategy(query_id)
    if strategy is None:
        registry.update_fire_outcome(event_id, outcome='unknown_strategy',
                                     error='no local strategy for this query_id')
        alerts.emit('warning', 'unknown_strategy',
            f'SSE fire for unknown strategy {query_id}',
            query_id=query_id, fire_event_id=event_id)
        return

    # 3. Surface "trigger received" alert (info-level, no payload data)
    alerts.emit('info', 'trigger_received',
        f'Elfa trigger fired: {strategy.title}. '
        f'Placing {strategy.side.upper()} {strategy.amount} {strategy.symbol} '
        f'({strategy.order_type}) on GRVT',
        query_id=query_id, fire_event_id=event_id)

    # 4. Fetch reference price
    try:
        mid = executor.fetch_mid_price(strategy.symbol)
    except GrvtError as e:
        finalize_failure(event_id, query_id, alerts, registry,
                         outcome='grvt_error',
                         category='grvt_other',
                         message=f'fetch_mid_price failed: {e}')
        return

    # 5. Guardrails
    guard = check_guardrails(strategy, mid)
    if isinstance(guard, Reject):
        registry.update_fire_outcome(event_id, outcome='rejected_guardrail',
                                     error=guard.reason)
        registry.set_strategy_status(query_id, 'fired')
        alerts.emit('warning', 'guardrail_rejected',
            f'fire rejected locally: {guard.reason}',
            query_id=query_id, fire_event_id=event_id)
        return

    # 6. Optionally set leverage (idempotent on GRVT side)
    if strategy.leverage is not None:
        try:
            executor.set_leverage(symbol=strategy.symbol, leverage=strategy.leverage)
        except GrvtError as e:
            # Surface but continue; leverage may already be at desired value
            alerts.emit('warning', 'grvt_set_leverage',
                f'set_leverage failed (continuing): {e}',
                query_id=query_id, fire_event_id=event_id)

    # 7. Compute TP/SL prices (if configured)
    tp_price = sl_price = None
    if strategy.tp_pct is not None:
        tp_price = align_tick(
            compute_target_price(mid, strategy.tp_pct, strategy.side, kind='tp'),
            tick_size=executor.tick_size(strategy.symbol),
            direction='down' if strategy.side == 'buy' else 'up',
        )
    if strategy.sl_pct is not None:
        sl_price = align_tick(
            compute_target_price(mid, strategy.sl_pct, strategy.side, kind='sl'),
            tick_size=executor.tick_size(strategy.symbol),
            direction='up' if strategy.side == 'buy' else 'down',
        )

    # 8. Place OTOCO order
    try:
        result = executor.place_entry_with_tpsl(
            symbol=strategy.symbol,
            entry_side=strategy.side,
            amount=strategy.amount,
            order_type=strategy.order_type,
            limit_price=strategy.price,
            time_in_force=strategy.time_in_force,
            reference_price=mid,
            tp_price=tp_price,
            sl_price=sl_price,
        )
    except InsufficientMargin as e:
        finalize_failure(event_id, query_id, alerts, registry,
                         outcome='grvt_error',
                         category='insufficient_margin',
                         message=f'GRVT rejected for margin: {e}')
        return
    except GrvtError as e:
        # Distinguish auth failures from other errors
        category = 'grvt_auth_failed' if 'authenticate' in str(e).lower() else 'grvt_other'
        finalize_failure(event_id, query_id, alerts, registry,
                         outcome='grvt_error', category=category,
                         message=f'order placement failed: {e}')
        return

    # 9. Record success
    registry.update_fire_outcome(
        event_id,
        outcome='placed',
        parent_order_id=result['parent_order_id'],
        tp_order_id=result.get('tp_order_id'),
        sl_order_id=result.get('sl_order_id'),
        reference_price=mid,
        tp_price=tp_price,
        sl_price=sl_price,
        placed_at=now_iso(),
    )
    registry.set_strategy_status(query_id, 'fired')
    alerts.emit('info', 'order_placed',
        f'{strategy.side.upper()} {strategy.amount} {strategy.symbol} '
        f'({strategy.order_type}) placed on GRVT. order={result["parent_order_id"][:12]}',
        query_id=query_id, fire_event_id=event_id)


def finalize_failure(event_id, query_id, alerts, registry, *,
                     outcome, category, message):
    registry.update_fire_outcome(event_id, outcome=outcome, error=message)
    registry.set_strategy_status(query_id, 'fired')
    alerts.emit('error', category, message,
                query_id=query_id, fire_event_id=event_id)
```

Notes:

- The strategy transitions to `fired` regardless of order outcome (placed, rejected, errored). This is intentional: this bot is single-fire, and any "the trigger happened but the order didn't" case is a manual-review situation surfaced via the alert. Re-arming the strategy would be unsafe (prices have moved; the trigger is stale).
- All alerts include `query_id` and `fire_event_id` so the user can correlate them with the registry.

## Guardrails

```
@dataclass
class Reject: reason: str
@dataclass
class Allow: pass

def check_guardrails(strategy, reference_price):
    # 1. Notional cap
    notional = strategy.amount * reference_price
    if notional > strategy.max_notional_usd:
        return Reject(
            f'notional ${notional:.2f} exceeds max_notional_usd '
            f'${strategy.max_notional_usd:.2f}')

    # 2. Env sanity (defense-in-depth; receiver should refuse to start if != prod)
    if strategy.env != 'prod':
        return Reject(f'strategy env is {strategy.env!r}, expected prod')

    # 3. Amount sanity
    if strategy.amount <= 0:
        return Reject(f'amount {strategy.amount} must be positive')

    # 4. GRVT instrument sanity. Use Decimal. Metadata fields are strings.
    # `min_size` is also the size step unless GRVT exposes a more specific
    # quantity increment. Reject too-granular sizes locally; GRVT returns
    # "Order size too granular" otherwise.
    meta = strategy.instrument_meta
    if meta:
        amount = Decimal(str(strategy.amount))
        min_size = Decimal(str(meta['min_size']))
        min_notional = Decimal(str(meta['min_notional']))
        if amount < min_size:
            return Reject(f'amount {amount} below min_size {min_size}')
        if amount % min_size != 0:
            return Reject(f'amount {amount} is not a multiple of min_size {min_size}')
        notional_dec = amount * Decimal(str(reference_price))
        if notional_dec < min_notional:
            return Reject(f'notional ${notional_dec:.2f} below min_notional ${min_notional:.2f}')

    return Allow()
```

Notes:

- Symbol-level guardrails that are stable in instrument metadata (`min_size`, size step inferred from `min_size`, `min_notional`, `tick_size`) are checked locally to catch obvious rejects before signing. Dynamic checks (margin, max position, risk limits, reduce-only consistency) remain GRVT's job and are surfaced as `grvt_other` or `insufficient_margin` alerts.
- The notional cap is the user-controlled safety primitive; it must hold even when GRVT's own protections might allow a larger order.

## TP/SL price computation

```
def compute_target_price(reference, pct, entry_side, *, kind):
    """Compute TP or SL price from a reference and a percentage.

    kind: 'tp' or 'sl'. Direction depends on entry_side:
      - long entry: TP is above (pct positive), SL is below (pct positive)
      - short entry: TP is below (pct positive), SL is above (pct positive)
    """
    pct_frac = pct / 100.0
    if entry_side == 'buy':
        if kind == 'tp': return reference * (1 + pct_frac)
        if kind == 'sl': return reference * (1 - pct_frac)
    if entry_side == 'sell':
        if kind == 'tp': return reference * (1 - pct_frac)
        if kind == 'sl': return reference * (1 + pct_frac)
    raise ValueError(f"bad inputs: entry_side={entry_side}, kind={kind}")
```

## Tick alignment

```
def align_tick(price, tick_size, direction):
    """Round `price` to a multiple of `tick_size`. `direction` is 'up',
    'down', or 'nearest'. Use Decimal to avoid float drift on small ticks
    like 0.0001."""
    from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_EVEN
    p = Decimal(str(price))
    t = Decimal(str(tick_size))
    mode = {'down': ROUND_DOWN, 'up': ROUND_UP, 'nearest': ROUND_HALF_EVEN}[direction]
    aligned = (p / t).quantize(Decimal('1'), rounding=mode) * t
    return float(aligned)
```

Notes:

- Use Decimal, not floats. Tick sizes like `0.0001` produce nasty drift if you do `round(price / tick) * tick` in float.
- Direction matters: for a long's TP (which is ABOVE the entry), rounding down to the tick is safer (closer to entry, harder to fill) than rounding up. For a long's SL (BELOW the entry), rounding up is safer. The pseudocode in `process_fire` follows this convention.

## Order placement (GRVT executor)

The executor wraps the patterns published in `grvt-skills/skills/perpetual-trading` plus one spec-specific addition: atomic OTOCO submission via `POST /full/v2/bulk_orders`.

**For all of these methods, follow the perpetual-trading skill's implementations verbatim:**

- `__init__`: construct `GrvtCcxt` per the skill's "SDK Setup" section. Use env vars `GRVT_TRADING_API_KEY`, `GRVT_TRADING_PRIVATE_KEY`, and the auto-discovered `GRVT_TRADING_ACCOUNT_ID` (populated by preflight from the login response).
- `fetch_mid_price(symbol)`: thin wrapper over the skill's market-data ticker fetch. Falls back through `mid_price -> mark_price -> (best_bid + best_ask) / 2 -> last_price`. Raises `GrvtError` if all are zero/missing.
- `tick_size(symbol)`: read from the cached instruments dict (loaded by `GrvtCcxt.fetch_all_markets` per the skill).
- `set_leverage(symbol, leverage)`: **call the skill's `set_position_config` helper directly.** Do not wrap or rename. The helper handles EIP-712 signing of the `SetSubAccountPositionMarginConfig` typed-data structure and hits `POST /full/v1/set_position_config`. The legacy `set_initial_leverage_v1` endpoint is deprecated and must not be used.

The OTOCO submission is the only piece NOT in the perpetual-trading skill (that skill places trigger orders independently). The bot needs atomicity because a fire that fills entry but fails to place TP/SL leaves a naked position; the v2 `bulk_orders` endpoint is the only path GRVT provides for atomic 3-leg submission.

```
TIF_FOR_SDK = {
    'GTC': 'GOOD_TILL_TIME',
    'IOC': 'IMMEDIATE_OR_CANCEL',
    'FOK': 'FILL_OR_KILL',
}


def build_and_sign_order(self, *, symbol, side, amount, order_type,
                         limit_price, time_in_force, reduce_only,
                         order_duration_secs=24 * 60 * 60):
    """Return the signed GRVT Order object that belongs inside bulk_orders.
    Do not return the wrapper dict if get_order_payload returns {order: ...}.
    """
    sdk_tif = TIF_FOR_SDK.get(time_in_force, time_in_force)
    client_order_id = random_int_between_2_63_and_2_64_minus_1()
    params = {
        'time_in_force': sdk_tif,
        'reduce_only': reduce_only,
        'client_order_id': client_order_id,
    }
    order = get_grvt_order(
        self._trading_account_id,
        symbol,
        order_type,
        side,
        amount,
        limit_price,
        order_duration_secs=order_duration_secs,
        params=params,  # keyword only; positional arg here is order_duration_secs
    )
    payload = get_order_payload(
        order,
        private_key=self._private_key,
        env=GrvtEnv.PROD,
        instruments=self._instruments,
    )
    signed_order = payload['order'] if 'order' in payload else payload
    assert 'metadata' in signed_order
    assert 'client_order_id' in signed_order['metadata']
    return signed_order
```

```
def place_entry_with_tpsl(self, *, symbol, entry_side, amount, order_type,
                          limit_price, time_in_force,
                          reference_price, tp_price, sl_price):
    """Submit entry + TP + SL as one atomic OTOCO bulk request.

    Returns {parent_order_id, tp_order_id, sl_order_id, client_order_ids}.
    `client_order_ids` are how the bot will track fills (parent_order_id
    comes back as 0x00 placeholder per the perpetual-trading skill's note).
    """
    exit_side = 'sell' if entry_side == 'buy' else 'buy'

    # Build and sign each Order independently. build_and_sign_order returns
    # the signed Order object, not the SDK wrapper dict.
    parent = build_and_sign_order(
        symbol=symbol, side=entry_side, amount=amount,
        order_type=order_type, limit_price=limit_price,
        time_in_force=time_in_force, reduce_only=False,
    )
    tp = build_and_sign_order(
        symbol=symbol, side=exit_side, amount=amount,
        order_type='limit', limit_price=tp_price,
        time_in_force='GTC', reduce_only=True,
    )
    sl = build_and_sign_order(
        symbol=symbol, side=exit_side, amount=amount,
        order_type='limit', limit_price=sl_price,
        time_in_force='GTC', reduce_only=True,
    )

    # Inject trigger metadata AFTER signing (per the perpetual-trading
    # skill's create_trigger_order pattern: metadata.trigger is unsigned).
    tp['metadata']['trigger'] = {
        'trigger_type': 'TAKE_PROFIT',
        'tpsl': {
            'trigger_by': 'MARK',          # safer than LAST/MID for perps
            'trigger_price': str(tp_price),
            'close_position': False,        # OTOCO carries explicit size
            'is_split_position': False,     # not used by this bot
        },
    }
    sl['metadata']['trigger'] = {
        'trigger_type': 'STOP_LOSS',
        'tpsl': {
            'trigger_by': 'MARK',
            'trigger_price': str(sl_price),
            'close_position': False,
            'is_split_position': False,
        },
    }

    # Submit as one bulk request. pysdk does not wrap this endpoint;
    # POST manually with the auth cookie + X-Grvt-Account-Id header.
    body = {
        'sub_account_id': self._trading_account_id,
        'orders': [parent, tp, sl],
        'order_i_ds': [],
        'client_order_i_ds': [],
        'time_to_live_ms': '500',
    }
    url = 'https://trades.grvt.io/full/v2/bulk_orders'
    resp = self._http.post(
        url, json=body,
        headers={
            'Cookie': f'gravity={self._cookie}',
            'X-Grvt-Account-Id': self._account_id,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    client_order_ids = [
        parent['metadata']['client_order_id'],
        tp['metadata']['client_order_id'],
        sl['metadata']['client_order_id'],
    ]

    # Response shape: see references/02-protocols.md and the api-spec
    # repo's api_bulk_orders_response.md. A syntactic accept is not enough.
    # Immediately verify parent by client_order_id through open orders,
    # order history, or positions. Return only after parent is accepted or
    # filled. A live test saw parent Rejected with TP/SL Cancelled even though
    # the POST returned an accepted-looking response.
    verified = self.verify_parent_accepted_or_filled(client_order_ids[0])
    if not verified.ok:
        raise GrvtError(verified.reason)
    return {
        'parent_order_id': data['results'][0].get('order_id'),
        'tp_order_id': data['results'][1].get('order_id'),
        'sl_order_id': data['results'][2].get('order_id'),
        'client_order_ids': client_order_ids,
    }
```

Notes:

- `build_and_sign_order` is the perpetual-trading skill's `get_grvt_order` + `get_order_payload`. It must return the signed Order object nested under `payload["order"]` when the SDK returns a wrapper, because `bulk_orders.orders[]` expects Order objects, not wrappers.
- Pass `order_duration_secs` explicitly. The SDK default is 300 seconds; TP/SL legs should not expire 5 minutes after entry unless the user explicitly requested that.
- All three orders share the same `sub_account_id` (from `GRVT_TRADING_ACCOUNT_ID`).
- All three orders share the same `client_order_id` namespace; generate three unique values (in the range `[2^63, 2^64-1]` per the perpetual-trading skill's guidance).
- Tracking fills: use `client_order_id`, not `order_id`. The bulk_orders response may return `order_id: "0x00"` placeholders per the same note in the perpetual-trading skill. The actual order_id is observable via `fetch_open_orders` or `fetch_order_history`.
- Error handling: `bulk_orders` is atomic at the SUBMISSION level. If GRVT rejects any of the three orders (bad signature, BELOW_MARGIN, etc.), the entire request fails and none of the three are accepted. Parse `data['results']` for per-order rejection reasons. If accepted, the OTOCO group is wired server-side; cancelling any one of the three cancels the whole group.
- Verification after submit is mandatory. Only emit `order_placed` after confirming parent accepted/filled. If the parent is `Rejected` and TP/SL are `Cancelled`, update the fire as `grvt_error` and surface the exact rejection, for example `Order size too granular`.
- Add an order-builder dry-run test that constructs signed parent/TP/SL payloads without POSTing. It must run during bootstrap before any live smoke test.

## Capturing SSE bytes (for spec maintenance)

To regenerate `captured-frames/notification_<source>_<YYYY-MM-DD>.txt` if Elfa changes the schema:

```
async def capture_one_frame(api_key, eql_source):
    # 1. Create a fresh notify-only query (the bot's authoring patterns)
    # 2. Open the SSE stream with httpx.AsyncClient(timeout=None)
    # 3. Iterate raw lines from response.aiter_lines()
    # 4. Accumulate lines until a non-keep-alive blank line ends a frame
    # 5. Write the captured frame to a file, including the SSE-level id:
    #    line, the event: line, and the data: line verbatim
    # 6. Cancel the query so it doesn't sit active
```

Use a condition that fires quickly (e.g., `BTC price > 1`, always true; or `cron.once period=1m`) so you don't wait. See the reference implementation's `scripts/capture_frame.py` if one exists in the runtime repo.

## Sync terminal status

```
def sync_terminal_status_locally(query_id, remote_status, executions, registry, alerts):
    """Map remote terminal to local, emit alerts, but suppress alerts if
    a live SSE fire was already processed locally for the same execution."""
    local = registry.get_strategy(query_id)
    if local is None or local.status != 'active':
        return

    LOCAL_STATUS = {
        'triggered': 'fired', 'expired': 'expired',
        'cancelled': 'cancelled', 'failed': 'failed',
    }
    new_status = LOCAL_STATUS.get(remote_status, 'failed')
    registry.set_strategy_status(query_id, new_status)

    if remote_status == 'triggered':
        # Was the execution already handled live?
        for ex in executions:
            if not registry.fire_exists(ex['id']):
                # Yes: live SSE missed this fire. Manual intervention.
                registry.insert_fire_marker(
                    event_id=ex['id'], query_id=query_id,
                    outcome='unknown',
                    raw_payload=json.dumps(ex),
                    received_at=now_iso(),
                )
                alerts.emit('error', 'manual_intervention_required',
                    'strategy triggered on Elfa while receiver was disconnected. '
                    'Order was NOT placed by the bot. Review GRVT.',
                    query_id=query_id, fire_event_id=ex['id'],
                    details={'execution': ex})
        return  # don't double-alert terminal status

    SEVERITY = {'expired': 'info', 'cancelled': 'warning', 'failed': 'error'}
    alerts.emit(SEVERITY[remote_status], 'strategy_terminated_remotely',
                f'strategy ended with status {remote_status!r}',
                query_id=query_id, details={'executions': executions[:10]})
```

The dedupe-via-executionId logic is what makes this safe: if SSE already processed the fire, `registry.fire_exists(ex['id'])` returns True and no manual-intervention alert is raised.
