from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional

from elfa_grvt_bot.elfa_client import ElfaClient
from elfa_grvt_bot.registry import Registry, Strategy


def _registry() -> Registry:
    path = os.environ.get("REGISTRY_DB_PATH")
    if not path:
        raise SystemExit("REGISTRY_DB_PATH is not set")
    return Registry(path)


def _make_elfa_client() -> ElfaClient:
    api_key = os.environ.get("ELFA_API_KEY")
    if not api_key:
        raise SystemExit("ELFA_API_KEY must be set")
    return ElfaClient(api_key=api_key)


def cmd_add(args: argparse.Namespace) -> int:
    r = _registry()
    s = Strategy(
        query_id=args.query_id,
        title=args.title,
        description=args.description,
        eql_json=args.eql_json,
        symbol=args.symbol,
        side=args.side,
        amount=args.amount,
        order_type=args.order_type,
        price=args.price,
        leverage=args.leverage,
        tp_pct=args.tp_pct,
        sl_pct=args.sl_pct,
        time_in_force=args.time_in_force,
        reduce_only=args.reduce_only,
        max_notional_usd=args.max_notional_usd,
        env="prod",
        status="active",
        created_at=int(time.time()),
        fired_at=None,
    )
    r.insert_strategy(s)
    print(f"added strategy {s.query_id} (status=active)")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    r = _registry()
    rows = r.list_strategies(status=args.status)
    if not rows:
        print("no strategies")
        return 0
    print(f"{'query_id':<20}  {'symbol':<18}  {'side':<5}  {'amount':<10}  {'env':<8}  {'status':<10}  title")
    for s in rows:
        print(
            f"{s.query_id:<20}  {s.symbol:<18}  {s.side:<5}  {s.amount:<10}  "
            f"{s.env:<8}  {s.status:<10}  {s.title}"
        )
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    r = _registry()
    strat = r.get_strategy(args.query_id)
    if strat is None:
        print(f"no strategy with query_id={args.query_id}", file=sys.stderr)
        return 1
    client = _make_elfa_client()
    client.cancel_query(args.query_id)
    r.set_strategy_status(args.query_id, "cancelled")
    print(f"cancelled {args.query_id} on Elfa and locally")
    return 0


def cmd_alerts(args: argparse.Namespace) -> int:
    r = _registry()
    rows = r.list_alerts(only_unacked=args.pending)
    if not rows:
        print("no alerts")
        return 0
    for a in rows:
        marker = "  " if a["acknowledged"] else "* "
        print(f"{marker}#{a['id']} [{a['severity']}/{a['category']}] {a['message']}")
        if a["query_id"]:
            print(f"     query_id={a['query_id']}")
        if a["details_json"]:
            try:
                pretty = json.dumps(json.loads(a["details_json"]), indent=2)
                print(f"     details={pretty}")
            except Exception:
                print(f"     details={a['details_json']}")
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    r = _registry()
    if args.target == "all":
        r.ack_all_alerts()
        print("acknowledged all alerts")
    else:
        try:
            aid = int(args.target)
        except ValueError:
            print(f"ack target must be an integer or 'all', got {args.target!r}",
                  file=sys.stderr)
            return 1
        r.ack_alert(aid)
        print(f"acknowledged alert #{aid}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="registry_cli")
    sub = p.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add")
    add.add_argument("--query-id", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--description", default=None)
    add.add_argument("--eql-json", required=True)
    add.add_argument("--symbol", required=True)
    add.add_argument("--side", choices=["buy", "sell"], required=True)
    add.add_argument("--amount", type=float, required=True)
    add.add_argument("--order-type", choices=["limit", "market"], required=True)
    add.add_argument("--price", type=float, default=None)
    add.add_argument("--leverage", type=int, default=None)
    add.add_argument("--tp-pct", type=float, default=None,
                     help="take-profit percentage (e.g. 1.5 → 1.5%% from fill)")
    add.add_argument("--sl-pct", type=float, default=None,
                     help="stop-loss percentage (e.g. 1.0 → 1.0%% from fill)")
    add.add_argument("--time-in-force", default=None)
    add.add_argument("--reduce-only", action="store_true")
    add.add_argument("--max-notional-usd", type=float, required=True)
    add.set_defaults(func=cmd_add)

    lst = sub.add_parser("list")
    lst.add_argument("--status", default=None)
    lst.set_defaults(func=cmd_list)

    can = sub.add_parser("cancel")
    can.add_argument("query_id")
    can.set_defaults(func=cmd_cancel)

    al = sub.add_parser("alerts")
    al.add_argument("--pending", action="store_true")
    al.set_defaults(func=cmd_alerts)

    ack = sub.add_parser("ack")
    ack.add_argument("target", help="alert id or 'all'")
    ack.set_defaults(func=cmd_ack)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
