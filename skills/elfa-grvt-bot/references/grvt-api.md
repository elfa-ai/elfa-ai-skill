# GRVT API quirks (empirical)

Things we learned the hard way during integration. The pysdk ships only v1 endpoints; some features only work via undocumented v2 paths.

## OTOCO (entry + TP + SL atomic) requires `full/v2/bulk_orders`

GRVT supports linked OCO TPSL pairs at the protocol level, but the pysdk does NOT expose this. The `pysdk.grvt_ccxt.GrvtCcxt.create_order` accepts only `'limit'` and `'market'`. The `pysdk.grvt_raw_sync.GrvtRawSync.create_order_v1` is a single-order endpoint.

The right endpoint is `https://trades.grvt.io/full/v2/bulk_orders`. It accepts:
- BulkCreate: 1 order, OR 2 OCO orders, OR 2 OTO (parent + 1 trigger), OR 3 OTOCO (parent + TP + SL)
- BulkCancel: cancel multiple by `order_id` or `client_order_id`
- BulkCancelReplace

For OTOCO: parent order plus TP plus SL. Same instrument, same size on all three. Parent side opposite of TP and SL sides. Each order signed independently with EIP-712, then batched in a single POST.

The bot's `GrvtTriggerClient.place_entry_with_tpsl` implements this. Internally it uses `GrvtRawSyncBase._post(True, url, payload)` to reuse the cookie-refresh logic.

Payload shape:

```json
{
  "sub_account_id": "<sub_account_id>",
  "orders": [
    { "<parent order, fully signed>" },
    { "<TP order, fully signed, with metadata.trigger.trigger_type=TAKE_PROFIT>" },
    { "<SL order, fully signed, with metadata.trigger.trigger_type=STOP_LOSS>" }
  ]
}
```

Each order has: `sub_account_id`, `is_market`, `time_in_force`, `post_only`, `reduce_only`, `legs`, `signature`, `metadata`. The trigger orders also have `metadata.trigger.tpsl` with `trigger_by` (MARK / LAST / INDEX), `trigger_price`, `close_position` (False for OTOCO legs since size is explicit).

Code reference: `src/elfa_grvt_bot/grvt_trigger_client.py`, methods `_build_signed_order`, `_post_bulk_orders`, `place_entry_with_tpsl`.

## Tick alignment is required before signing

If you sign an order with `limit_price="88.21872499999999"` but GRVT rounds to the instrument's tick size before validation, the recomputed EIP-712 hash differs from yours and you get error `2002: Signature does not match payload`.

The fix: round TP/SL prices to the instrument's `tick_size` (e.g. `0.01` for SOL_USDT_Perp, `0.001` for HYPE_USDT_Perp) BEFORE constructing the order and signing. The bot does this in `place_entry_with_tpsl` via `Decimal.quantize(...)`.

Tick sizes are in the `Instrument` dataclass returned by `get_all_instruments_v1`. Cache the dict on first use.

## `set_initial_leverage` is deprecated (code 2106)

The pysdk's `GrvtRawSync.set_initial_leverage_v1(...)` returns:
```
{'code': 2106, 'message': 'This API has been deprecated and can no longer be used to set leverage'}
```

On modern GRVT cross-margin accounts, leverage is determined at the account / sub-account level, not per-order. The bot's executor catches the deprecated error specifically and proceeds (a warning is logged). Other set_leverage failures (auth, network) still raise.

If you need to change account leverage, do it via the GRVT UI.

## `order_id` returned from `create_order_v1` and `bulk_orders` is `0x00`

GRVT assigns the real on-chain order ID asynchronously after settlement. Initial responses from `create_order` and `bulk_orders` return `order_id="0x00"` as a placeholder. The bot strips this from user-visible alerts (it would just be noise).

To find the real order_id post-fill, use `client_order_id` (which we generate) and look up via `fetch_open_orders` or `fetch_order_history`.

## Position-linked TPSL via API is blocked (code 2117)

If you submit a single trigger order with `metadata.trigger.tpsl.close_position=True` and `reduce_only=True`, GRVT returns:
```
{'code': 2117, 'message': 'Position linked TPSL orders must be created from web or mobile clients'}
```

The web/mobile UI uses a different (undocumented) endpoint for linked TPSL. API users get OCO via `bulk_orders` instead, which is functionally equivalent.

The bot uses OCO via bulk_orders, not position-linked. Set `close_position=False` on TP and SL legs and let the leg's explicit size handle the close.

## Reduce-only size cap (code 2402)

Total `reduce_only=True` orders cannot exceed your position size. If you have a 0.58 SOL long and try to add a TP and SL each sized 0.58, the second one is rejected with code 2402.

Solutions:
- **Use OCO via bulk_orders**: GRVT treats the linked pair specially; total reduce_only is the position size, not 2x.
- **Half-size each**: TP at 50%, SL at 50%. Loses some protection.
- **Cancel one before adding the other**.

The bot uses OCO, so this is automatic for entries through `place_entry_with_tpsl`.

## Market orders must have `limit_price="0"` on the leg (code 2020)

```
{'code': 2020, 'message': 'Market Order must always be supplied without a limit price'}
```

For market orders set `is_market=True` AND `legs[0].limit_price="0"`. For limit orders `is_market=False` AND `limit_price=<actual price>`. Trigger orders that fire as market on activation: `is_market=True` AND `limit_price="0"`; the trigger price is in `metadata.trigger.tpsl.trigger_price`.

## Min notional ($5 on most perps) and tick / step size

Each instrument has `min_notional_usd`, `min_size`, `tick_size`. Available via `fetch_market(symbol)` from the CCXT wrapper or `Instrument.tick_size` / `min_size` / `min_notional` from the raw API.

Receiver guardrails: notional cap is enforced via `max_notional_usd` per strategy. Min notional is enforced server-side (returns code 2066 if violated).

## `fetch_mini_ticker` field names

Actual fields in the response:
- `mid_price`, `mark_price`, `index_price`, `last_price`
- NOT `last` or `mid`

The bot's `fetch_mid_price` reads `mid_price` first, falls back to `last_price` then `mark_price`.

## EIP-712 signing details

`pysdk.grvt_raw_signing.sign_order(order, config, account, instruments)` does the full EIP-712 message construction and signs with the EVM private key. It mutates `order.signature.r/s/v/signer` in place and returns the order.

Signed message structure (from `build_EIP712_order_message_data`):
```
subAccountID, isMarket, timeInForce, postOnly, reduceOnly,
legs=[{assetID, contractSize=size*decimals, limitPrice=price*1e9, isBuyingContract}],
nonce, expiration
```

Note: `metadata.trigger` is NOT part of the signed payload. Trigger configuration is "advisory" (server-side enforced but not crypto-protected). The signature only protects the order's economic terms.

Domain separator includes `chainId` (325 for prod, 326 for staging) and the GRVT contract addresses (encoded by `get_EIP712_domain_data`).

## Nonce must fit in uint32

`signature.nonce` is encoded in the EIP-712 message as `uint32`. Values above `2**32 - 1` produce:
```
ValueOutOfBounds: Cannot be encoded in 32 bits
```

The bot's `_gen_nonce` returns `random.randint(0, 2**32 - 1)`. The `client_order_id` (separate field, in `metadata`) is uint64 and uses the `[2**63, 2**64-1]` range to avoid colliding with GRVT UI-generated IDs.

## ASCII-only content (project convention)

Em-dashes (U+2014) and other non-ASCII characters are banned project-wide in chat, code, commits, alerts, and API request bodies. Use hyphens, parens, colons, or commas instead. Project-wide convention for ASCII-only output.
