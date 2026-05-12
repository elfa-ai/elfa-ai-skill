from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, List, Optional

SCHEMA = """
-- Note: foreign keys between fires/alerts and strategies are intentionally
-- soft references (no REFERENCES clause). The receiver's unknown_strategy
-- outcome path inserts a fire row with a query_id that does not exist in
-- strategies , that is correct behavior, not a referential bug. PRAGMA
-- foreign_keys=ON is still set per-connection so future explicit FKs would
-- be enforced.
CREATE TABLE IF NOT EXISTS strategies (
  query_id          TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  description       TEXT,
  eql_json          TEXT NOT NULL,
  symbol            TEXT NOT NULL,
  side              TEXT NOT NULL,
  amount            REAL NOT NULL,
  order_type        TEXT NOT NULL,
  price             REAL,
  leverage          INTEGER,
  tp_pct            REAL,
  sl_pct            REAL,
  time_in_force     TEXT,
  reduce_only       INTEGER NOT NULL DEFAULT 0,
  max_notional_usd  REAL NOT NULL,
  env               TEXT NOT NULL,
  status            TEXT NOT NULL,
  created_at        INTEGER NOT NULL,
  fired_at          INTEGER
);

CREATE TABLE IF NOT EXISTS fires (
  event_id          TEXT PRIMARY KEY,
  query_id          TEXT NOT NULL,
  received_at       INTEGER NOT NULL,
  outcome           TEXT NOT NULL,
  grvt_order_id     TEXT,
  error             TEXT,
  raw_payload       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at      INTEGER NOT NULL,
  severity        TEXT NOT NULL,
  category        TEXT NOT NULL,
  query_id        TEXT,
  fire_event_id   TEXT,
  message         TEXT NOT NULL,
  details_json    TEXT,
  acknowledged    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_strategies_status ON strategies(status);
CREATE INDEX IF NOT EXISTS idx_fires_query_id ON fires(query_id);
CREATE INDEX IF NOT EXISTS idx_alerts_acknowledged ON alerts(acknowledged);
"""


@dataclass(frozen=True)
class Strategy:
    query_id: str
    title: str
    description: Optional[str]
    eql_json: str
    symbol: str
    side: str
    amount: float
    order_type: str
    price: Optional[float]
    leverage: Optional[int]
    tp_pct: Optional[float]
    sl_pct: Optional[float]
    time_in_force: Optional[str]
    reduce_only: bool
    max_notional_usd: float
    env: str
    status: str
    created_at: int
    fired_at: Optional[int]


def _row_to_strategy(row: sqlite3.Row) -> Strategy:
    return Strategy(
        query_id=row["query_id"],
        title=row["title"],
        description=row["description"],
        eql_json=row["eql_json"],
        symbol=row["symbol"],
        side=row["side"],
        amount=row["amount"],
        order_type=row["order_type"],
        price=row["price"],
        leverage=row["leverage"],
        tp_pct=row["tp_pct"],
        sl_pct=row["sl_pct"],
        time_in_force=row["time_in_force"],
        reduce_only=bool(row["reduce_only"]),
        max_notional_usd=row["max_notional_usd"],
        env=row["env"],
        status=row["status"],
        created_at=row["created_at"],
        fired_at=row["fired_at"],
    )


class Registry:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            # Migrations for pre-existing dev DBs that were created before
            # tp_pct / sl_pct existed. ALTER TABLE … ADD COLUMN is idempotent
            # via the duplicate-column OperationalError catch.
            for column in ("tp_pct", "sl_pct"):
                try:
                    con.execute(
                        f"ALTER TABLE strategies ADD COLUMN {column} REAL"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path, isolation_level=None)  # autocommit
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
        finally:
            con.close()

    def insert_strategy(self, s: Strategy) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO strategies (
                  query_id, title, description, eql_json, symbol, side, amount,
                  order_type, price, leverage, tp_pct, sl_pct, time_in_force,
                  reduce_only, max_notional_usd, env, status, created_at, fired_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    s.query_id, s.title, s.description, s.eql_json, s.symbol,
                    s.side, s.amount, s.order_type, s.price, s.leverage,
                    s.tp_pct, s.sl_pct, s.time_in_force, int(s.reduce_only),
                    s.max_notional_usd, s.env, s.status, s.created_at, s.fired_at,
                ),
            )

    def get_strategy(self, query_id: str) -> Optional[Strategy]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM strategies WHERE query_id = ?", (query_id,)
            ).fetchone()
            return _row_to_strategy(row) if row else None

    def set_strategy_status(
        self, query_id: str, status: str, fired_at: Optional[int] = None
    ) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE strategies SET status = ?, fired_at = ? WHERE query_id = ?",
                (status, fired_at, query_id),
            )

    def list_strategies(self, status: Optional[str] = None) -> List[Strategy]:
        sql = "SELECT * FROM strategies"
        params: tuple = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY created_at DESC"
        with self._connect() as con:
            return [_row_to_strategy(r) for r in con.execute(sql, params).fetchall()]

    def insert_fire_if_new(
        self,
        *,
        event_id: str,
        query_id: str,
        received_at: int,
        outcome: str,
        raw_payload: str,
        grvt_order_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> bool:
        """
        Insert a fire row. Returns True if inserted, False if event_id already existed.
        Uses INSERT OR IGNORE for atomic idempotency.
        """
        with self._connect() as con:
            cur = con.execute(
                """
                INSERT OR IGNORE INTO fires (
                  event_id, query_id, received_at, outcome,
                  grvt_order_id, error, raw_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, query_id, received_at, outcome,
                 grvt_order_id, error, raw_payload),
            )
            return cur.rowcount == 1

    def update_fire_outcome(
        self,
        event_id: str,
        *,
        outcome: str,
        grvt_order_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        Update a fire's outcome and optionally its grvt_order_id / error.

        Note: this fully overwrites grvt_order_id and error each call. The
        caller is responsible for passing the full state , this method is
        designed for the receiver's "single update per fire" lifecycle, not
        for incremental edits. Fields not in the UPDATE clause (event_id,
        query_id, received_at, raw_payload) are immutable post-insert.

        Raises KeyError if no fire row matches event_id.
        """
        with self._connect() as con:
            cur = con.execute(
                """
                UPDATE fires
                   SET outcome = ?, grvt_order_id = ?, error = ?
                 WHERE event_id = ?
                """,
                (outcome, grvt_order_id, error, event_id),
            )
            if cur.rowcount != 1:
                raise KeyError(f"no fire row with event_id={event_id!r}")

    def get_fire(self, event_id: str) -> Optional[dict]:
        """
        Return the fire row as a dict, or None if not found.

        Deliberately returns dict (not a Fire dataclass): fires are read mostly
        for triage/debugging, and the receiver only uses insert_fire_if_new's
        bool return value for idempotency. If a future caller needs typed
        access (e.g., a list_fires admin command), introduce a Fire dataclass
        then , not now (YAGNI).
        """
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM fires WHERE event_id = ?", (event_id,)
            ).fetchone()
            return dict(row) if row else None

    def insert_alert(
        self,
        *,
        severity: str,
        category: str,
        message: str,
        created_at: int,
        query_id: Optional[str] = None,
        fire_event_id: Optional[str] = None,
        details_json: Optional[str] = None,
    ) -> int:
        with self._connect() as con:
            cur = con.execute(
                """
                INSERT INTO alerts (
                  created_at, severity, category, query_id,
                  fire_event_id, message, details_json, acknowledged
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (created_at, severity, category, query_id,
                 fire_event_id, message, details_json),
            )
            return int(cur.lastrowid)

    def list_alerts(self, *, only_unacked: bool = False) -> List[dict]:
        """
        List alerts ordered newest-first.

        Returns dicts (not an Alert dataclass): the only consumers are the CLI
        formatter and AlertWriter, neither of which needs typed access. If a
        typed cross-module surface appears later, introduce Alert then , not
        now (YAGNI). Mirrors get_fire's design choice.
        """
        sql = "SELECT * FROM alerts"
        if only_unacked:
            sql += " WHERE acknowledged = 0"
        sql += " ORDER BY created_at DESC"
        with self._connect() as con:
            return [dict(r) for r in con.execute(sql).fetchall()]

    def ack_alert(self, alert_id: int) -> None:
        """
        Mark a single alert as acknowledged.

        Raises KeyError if no alert row matches alert_id.
        """
        with self._connect() as con:
            cur = con.execute(
                "UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,)
            )
            if cur.rowcount != 1:
                raise KeyError(f"no alert row with id={alert_id!r}")

    def ack_all_alerts(self) -> None:
        with self._connect() as con:
            con.execute("UPDATE alerts SET acknowledged = 1 WHERE acknowledged = 0")
