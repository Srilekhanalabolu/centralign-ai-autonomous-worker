"""Sandbox 'internal finance system' backed by SQLite.

It behaves like a strict enterprise system: it validates input and returns structured
errors with hints. Amounts must be plain numbers, so a currency-formatted value such as
"₹48,500" is rejected with HTTP 400 — this is the real validation the agent has to recover
from (it is not a scripted failure: any formatted amount is rejected, every time).
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

AMOUNT_RE = re.compile(r"^\d+(\.\d{1,2})?$")
REQUIRED_FIELDS = ("invoice_number", "company", "amount", "due_date")

# Set FINANCE_STRICT_VALIDATION=false to disable amount-format validation (not recommended for the demo).
STRICT_VALIDATION = os.getenv("FINANCE_STRICT_VALIDATION", "true").lower() not in {"0", "false", "no"}


class FinanceStore:
    def __init__(self, db_path: Path, start_id: int = 1842) -> None:
        self.db_path = Path(db_path)
        self.start_id = start_id
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    # ------------------------------------------------------------------ setup
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS finance_invoices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_number TEXT NOT NULL,
                    company TEXT NOT NULL,
                    amount REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'INR',
                    due_date TEXT NOT NULL,
                    source_invoice_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(invoice_number, company)
                )"""
            )
            seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='finance_invoices'").fetchone()
            if seq is None:
                # Start record ids at a realistic number (e.g. #1842) instead of 1.
                conn.execute(
                    "INSERT INTO sqlite_sequence(name, seq) VALUES('finance_invoices', ?)", (self.start_id - 1,)
                )

    def reset(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM finance_invoices")
            conn.execute("UPDATE sqlite_sequence SET seq=? WHERE name='finance_invoices'", (self.start_id - 1,))

    # ------------------------------------------------------------- validation
    @staticmethod
    def validate(payload: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
        missing = [f for f in REQUIRED_FIELDS if payload.get(f) in (None, "")]
        if missing:
            return 422, {
                "success": False,
                "error": f"Missing required fields: {', '.join(missing)}",
                "hint": f"Provide all of: {', '.join(REQUIRED_FIELDS)}",
            }

        amount = payload["amount"]
        numeric = (isinstance(amount, (int, float)) and not isinstance(amount, bool)) or (
            isinstance(amount, str) and AMOUNT_RE.match(amount.strip()) is not None
        )
        if STRICT_VALIDATION and not numeric:
            return 400, {
                "success": False,
                "error": "Amount must be numeric",
                "hint": "Remove currency symbols and separators",
                "field": "amount",
                "received": amount,
            }
        try:
            value = float(re.sub(r"[^\d.]", "", str(amount)))
        except ValueError:
            value = -1
        if value <= 0:
            return 400, {"success": False, "error": "Amount must be greater than zero", "field": "amount"}

        try:
            date.fromisoformat(str(payload["due_date"]))
        except ValueError:
            return 400, {
                "success": False,
                "error": "due_date must be an ISO-8601 date",
                "hint": "Use the format YYYY-MM-DD",
                "field": "due_date",
            }
        return None

    # ------------------------------------------------------------ operations
    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        if float(d["amount"]).is_integer():
            d["amount"] = int(d["amount"])
        return d

    def create(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        error = self.validate(payload)
        if error:
            return error
        amount = float(re.sub(r"[^\d.]", "", str(payload["amount"])))
        with self._lock, self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM finance_invoices WHERE invoice_number=? AND company=?",
                (str(payload["invoice_number"]).strip(), str(payload["company"]).strip()),
            ).fetchone()
            if existing:
                return 409, {
                    "success": False,
                    "error": "Duplicate invoice: a record with this invoice number already exists",
                    "hint": "Retrieve the existing record instead of creating a new one",
                    "existing_record_id": existing["id"],
                }
            cur = conn.execute(
                """INSERT INTO finance_invoices
                   (invoice_number, company, amount, currency, due_date, source_invoice_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(payload["invoice_number"]).strip(),
                    str(payload["company"]).strip(),
                    amount,
                    str(payload.get("currency") or "INR"),
                    str(payload["due_date"]),
                    payload.get("source_invoice_id"),
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            record = conn.execute("SELECT * FROM finance_invoices WHERE id=?", (cur.lastrowid,)).fetchone()
        return 201, {"success": True, "record_id": record["id"], "record": self._row(record)}

    def get(self, record_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM finance_invoices WHERE id=?", (record_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, invoice_number: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if invoice_number:
                rows = conn.execute(
                    "SELECT * FROM finance_invoices WHERE invoice_number=? ORDER BY id DESC", (invoice_number,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM finance_invoices ORDER BY id DESC").fetchall()
        return [self._row(r) for r in rows]
