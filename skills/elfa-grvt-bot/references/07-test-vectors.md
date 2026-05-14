# 07 - Test vectors

`(input, expected output)` pairs the implementation must pass. These are the contract between the spec and the runtime: an implementation that passes every vector below is correct on the dimensions the spec cares about. Add more vectors when you find edge cases that aren't covered.

Organize tests by the function under test. Each vector below maps to one `pytest` function.

---

## SSE parser

### Vector P1: production cron.once frame parses to a valid event

```
INPUT
  raw bytes: contents of captured-frames/notification_cron_once_2026-05-13.txt
  expected_query_id: "25bd0932-be09-4e80-913f-efcfa1567d22"

EXPECT
  parse_sse_frames yields exactly 1 event:
    event_id == "94631fa0-05db-482a-9040-cfbaf13ece71"
    data["queryId"] == "25bd0932-be09-4e80-913f-efcfa1567d22"
    data["status"] == "triggered"
    data["conditionsMet"] == 1
```

### Vector P2: production price.current frame parses to a valid event

```
INPUT
  raw bytes: contents of captured-frames/notification_price_current_2026-05-13.txt
  expected_query_id: "359cd110-9cb6-4be6-ac16-99401d13d998"

EXPECT
  parse_sse_frames yields exactly 1 event:
    event_id == "354df2b8-913d-4c26-afce-9a80bb0955d0"
    data["queryId"] == "359cd110-9cb6-4be6-ac16-99401d13d998"
```

### Vector P3a: current documented schema (event: notification:new)

```
INPUT
  id: 12345
  event: notification:new
  data: {"id":12345,"type":"athena_query_notify_only","category":"alerts","title":"Query triggered: BTC > 100000","body":"BTC price crossed above 100000","data":{"queryId":"q1"},"priority":"high","createdAt":"2026-04-01T12:00:00.000Z"}
  <blank>

EXPECT
  parse_sse_frames("q1") yields 1 event:
    event_id == "12345"           # numeric id from payload, cast to string
    data["id"] == 12345
    data["data"]["queryId"] == "q1"
```

### Vector P3b: docs schema with queryId mismatch drops

```
INPUT
  id: 12346
  event: notification:new
  data: {"id":12346,"type":"...","category":"alerts","title":"...","body":"...","data":{"queryId":"q_OTHER"},"priority":"high","createdAt":"..."}
  <blank>

EXPECT
  parse_sse_frames("q_REQUESTED") yields []
  log captures WARN containing "data.queryId"
```

### Vector P3c: docs schema with missing top-level id drops

```
INPUT
  id: 12347
  event: notification:new
  data: {"type":"...","category":"alerts","title":"...","body":"...","data":{"queryId":"q1"},"priority":"high","createdAt":"..."}
  <blank>

EXPECT
  parse_sse_frames("q1") yields []
  log captures WARN containing "missing top-level 'id'"
```

### Vector P3d: legacy event:query.triggered with eventId still parses

```
INPUT
  event: query.triggered
  id: sse_id
  data: {"eventId":"e1","queryId":"q1","eventType":"query.triggered","channel":"sse"}
  <blank>

EXPECT
  parse_sse_frames("q1") yields 1 event with event_id == "e1"
```

### Vector P4: missing required field drops with WARN

```
INPUT
  event: notification
  id: sse_id
  data: {"status":"triggered","queryId":"q1"}    # missing executionId, triggerTime
  <blank>

EXPECT
  parse_sse_frames("q1") yields []
  log captures a WARN line containing "missing fields"
```

### Vector P5: status != 'triggered' drops

```
INPUT
  event: notification
  id: sse_id
  data: {"status":"pending","queryId":"q1","executionId":"e1","triggerTime":"..."}
  <blank>

EXPECT
  parse_sse_frames("q1") yields []
  log captures WARN containing "status"
```

### Vector P6: queryId mismatch drops

```
INPUT
  event: notification
  id: sse_id
  data: {"status":"triggered","queryId":"q_OTHER","executionId":"e1","triggerTime":"..."}
  <blank>

EXPECT
  parse_sse_frames("q_REQUESTED") yields []
  log captures WARN containing "queryId"
```

### Vector P7: multi-line data: concatenated with \n

```
INPUT
  event: notification
  id: sse_id
  data: {"status":"triggered",
  data: "queryId":"q1",
  data: "executionId":"e1",
  data: "triggerTime":"2026-05-13T00:00:00Z"}
  <blank>

EXPECT
  parse_sse_frames("q1") yields 1 event with event_id == "e1"
```

### Vector P8: keep-alive comments skipped

```
INPUT
  : keep-alive
  : keep-alive
  event: notification
  id: sse_id
  data: {"status":"triggered","queryId":"q1","executionId":"e1","triggerTime":"..."}
  <blank>
  : keep-alive

EXPECT
  parse_sse_frames("q1") yields 1 event
```

### Vector P9: invalid JSON drops

```
INPUT
  event: notification
  id: sse_id
  data: not valid json at all
  <blank>

EXPECT
  parse_sse_frames("q1") yields []
  log captures WARN containing "JSON"
```

### Vector P9b: stream-close event:end is silently skipped (no WARN, no drift counter)

```
INPUT
  event: end
  data: {"code":"QUERY_STREAM_CLOSED","status":"triggered","queryId":"q1"}
  <blank>

EXPECT
  parse_sse_frames("q1") yields []
  log does NOT capture a WARN (this is a known non-trigger event)
  per-query drift counter is NOT incremented
```

### Vector P10: unknown event type silently skipped

```
INPUT
  event: heartbeat
  data: {}
  <blank>
  event: end
  data: {}
  <blank>

EXPECT
  parse_sse_frames("q1") yields []
  log does NOT capture any WARN (these are silent skips, not drops)
```

---

## TP/SL price computation

Use mid_price = 1000.0 throughout for round numbers.

### Vector T1: long-side TP

```
INPUT
  compute_target_price(reference=1000.0, pct=1.5, entry_side='buy', kind='tp')

EXPECT
  return 1015.0
```

### Vector T2: long-side SL

```
INPUT
  compute_target_price(reference=1000.0, pct=1.0, entry_side='buy', kind='sl')

EXPECT
  return 990.0
```

### Vector T3: short-side TP

```
INPUT
  compute_target_price(reference=1000.0, pct=1.5, entry_side='sell', kind='tp')

EXPECT
  return 985.0
```

### Vector T4: short-side SL

```
INPUT
  compute_target_price(reference=1000.0, pct=1.0, entry_side='sell', kind='sl')

EXPECT
  return 1010.0
```

### Vector T5: invalid combo raises

```
INPUT
  compute_target_price(reference=1000, pct=1, entry_side='hold', kind='tp')

EXPECT
  raises ValueError
```

---

## Tick alignment

### Vector A1: round down to tick

```
INPUT
  align_tick(price=1015.7, tick_size=0.5, direction='down')

EXPECT
  return 1015.5
```

### Vector A2: round up to tick

```
INPUT
  align_tick(price=1015.3, tick_size=0.5, direction='up')

EXPECT
  return 1015.5
```

### Vector A3: nearest (Half-Even)

```
INPUT
  align_tick(price=1015.25, tick_size=0.5, direction='nearest')

EXPECT
  return 1015.0  # banker's rounding: .5 -> nearest even
```

### Vector A4: small tick, no float drift

```
INPUT
  align_tick(price=0.12347, tick_size=0.0001, direction='down')

EXPECT
  return 0.1234
  (NOT 0.12339999... from float arithmetic)
```

### Vector A5: already-aligned price returns unchanged

```
INPUT
  align_tick(price=1015.0, tick_size=0.5, direction='down')

EXPECT
  return 1015.0
```

---

## Guardrails

### Vector G1: notional cap respected

```
INPUT
  strategy = {amount: 1.0, max_notional_usd: 100.0, env: 'prod', side: 'buy'}
  reference_price = 50.0  # notional = 50, under cap

EXPECT
  check_guardrails returns Allow()
```

### Vector G2: notional cap exceeded

```
INPUT
  strategy = {amount: 1.0, max_notional_usd: 100.0, env: 'prod', side: 'buy'}
  reference_price = 500.0  # notional = 500, over cap

EXPECT
  check_guardrails returns Reject(reason containing 'exceeds max_notional_usd')
```

### Vector G3: non-prod env rejected

```
INPUT
  strategy = {amount: 1.0, max_notional_usd: 100.0, env: 'testnet'}

EXPECT
  check_guardrails returns Reject(reason containing 'env')
```

### Vector G4: zero amount rejected

```
INPUT
  strategy = {amount: 0.0, max_notional_usd: 100.0, env: 'prod'}

EXPECT
  check_guardrails returns Reject(reason containing 'amount')
```

---

## Dedupe (fires table)

### Vector D1: first insert succeeds

```
SETUP
  empty registry

ACT
  registry.insert_fire_marker(event_id='e1', query_id='q1', outcome='unknown', ...)

EXPECT
  no exception raised
  SELECT COUNT(*) FROM fires WHERE event_id='e1' == 1
```

### Vector D2: second insert with same event_id is a no-op

```
SETUP
  registry has fires row with event_id='e1'

ACT
  registry.insert_fire_marker(event_id='e1', query_id='q1', ...) again

EXPECT
  IntegrityError raised (caller catches it)
  fires table still has exactly 1 row with event_id='e1'
```

### Vector D3: fire_exists returns true for inserted, false for absent

```
SETUP
  registry has fires row with event_id='e1'

ASSERT
  registry.fire_exists('e1') == True
  registry.fire_exists('e_other') == False
```

---

## Status transitions

### Vector S1: active -> fired on placed

```
SETUP
  strategies row q1 status='active'

ACT
  process_fire(event_id='e1', query_id='q1', ...) succeeds at order placement

EXPECT
  strategies.status for q1 == 'fired'
  fires has 1 row with outcome='placed' for q1
  alerts has 2 rows for q1: trigger_received, order_placed
```

### Vector S2: active -> fired on guardrail reject

```
SETUP
  strategies row q1 amount=1.0, max_notional_usd=10.0

ACT
  process_fire with reference_price=100 (notional=100, over cap)

EXPECT
  strategies.status == 'fired'
  fires outcome == 'rejected_guardrail'
  alerts has guardrail_rejected
```

### Vector S3: active -> failed on remote recurring

```
SETUP
  strategies row q1 status='active'
  poll-query returns status='recurring'

ACT
  sync_terminal_status_locally

EXPECT
  strategies.status == 'failed'
  alerts has strategy_terminated_remotely with severity='error'
```

### Vector S4: terminal status from poll-query is suppressed if SSE already handled fire

```
SETUP
  strategies q1 status='active'
  fires has 1 row event_id='exec_1' outcome='placed' for q1
  poll-query returns status='triggered', executions=[{id:'exec_1', ...}]

ACT
  sync_terminal_status_locally

EXPECT
  strategies.status == 'fired'  (synced)
  no NEW alert emitted (the order_placed alert from SSE is the only one)
```

### Vector S5: manual_intervention raised on offline fire

```
SETUP
  strategies q1 status='active'
  fires is empty for q1
  poll-query returns status='triggered', executions=[{id:'exec_offline', ...}]

ACT
  sync_terminal_status_locally

EXPECT
  strategies.status == 'fired'
  fires has 1 row with event_id='exec_offline', outcome='unknown'
  alerts has manual_intervention_required
```

---

## Preflight

### Vector PF1: all probes pass

```
SETUP
  mock Elfa /v2/auto/queries?limit=1 -> 200 {"queries":[]}
  mock GRVT /auth/api_key/login -> 200 with Set-Cookie: gravity=abc; expires=...
  mock Telegram /getMe -> 200 {"ok":true,"result":{"username":"bot"}}

ACT
  preflight.main()

EXPECT
  exit code 0
  stdout contains "[ok] elfa", "[ok] grvt", "[ok] telegram"
```

### Vector PF2: GRVT geo-block detected

```
SETUP
  mock GRVT /auth/api_key/login -> 200, no Set-Cookie,
    body {"error":"Access from this location is not allowed.","status":"failure"}

ACT
  preflight.main()

EXPECT
  exit code 1
  stdout contains "[!!] grvt" and "GEO-BLOCK"
  stdout contains "Access from this location is not allowed"
```

### Vector PF3: bad Elfa key

```
SETUP
  mock Elfa /v2/auto/queries?limit=1 -> 401

ACT
  preflight.main()

EXPECT
  exit code 1
  stdout contains "[!!] elfa" and "401" or "rejected"
```

### Vector PF4: Telegram missing chat_id

```
SETUP
  TELEGRAM_BOT_TOKEN set, TELEGRAM_CHAT_ID empty

ACT
  preflight.main()

EXPECT
  exit code 1
  stdout contains "[!!] telegram" and "set both or leave both blank"
```

### Vector PF5: Telegram fully unconfigured -> skipped

```
SETUP
  TELEGRAM_BOT_TOKEN empty, TELEGRAM_CHAT_ID empty
  Elfa + GRVT probes pass

ACT
  preflight.main()

EXPECT
  exit code 0
  stdout contains "telegram: skipped (not configured"
```

---

## How to run

The implementing agent should produce a `tests/` directory with one `test_*.py` per logical group above (`test_parser.py`, `test_tpsl.py`, `test_align.py`, `test_guardrails.py`, `test_dedupe.py`, `test_status_transitions.py`, `test_preflight.py`). Each vector becomes one test function with a descriptive name.

`pytest` should report at least one passing test per vector listed above. If any vector's expected output cannot be produced by your implementation, the implementation is wrong (not the vector). Fix the code before adjusting the vector.

For SSE vectors that reference captured frames, load the file at test time:

```python
from pathlib import Path
FIXTURES = Path(__file__).parent.parent / "skills" / "elfa-grvt-bot" / "references" / "captured-frames"
def load_frame(name):
    return FIXTURES / name
```

(Adjust the path to wherever the captured frames live in your deployment.)
