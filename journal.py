"""
journal.py — SQLite + CSV journaling and performance statistics.
"""
import csv
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from config import DB_PATH, CSV_PATH

log = logging.getLogger(__name__)


class Journal:
    def __init__(self):
        self.db_path  = DB_PATH
        self.csv_path = CSV_PATH
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                signal_id    TEXT PRIMARY KEY,
                created_at   TEXT,
                direction    TEXT,
                strategy     TEXT,
                session      TEXT,
                regime       TEXT,
                entry        REAL,
                sl           REAL,
                tp1          REAL,
                tp2          REAL,
                tp3          REAL,
                lot          REAL,
                risk_usd     REAL,
                score        REAL,
                grade        TEXT,
                pattern      TEXT,
                result       TEXT,
                pnl_pts      REAL,
                pnl_usd      REAL,
                r_multiple   REAL,
                duration_mins REAL,
                notes        TEXT
            )""")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                stat_date    TEXT PRIMARY KEY,
                signals      INTEGER,
                wins         INTEGER,
                losses       INTEGER,
                breakeven    INTEGER,
                expired      INTEGER,
                pnl_usd      REAL,
                win_rate     REAL
            )""")
            conn.commit()

    def log_signal(self, sig):
        """Log a new signal when fired."""
        with self._conn() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO signals
            (signal_id,created_at,direction,strategy,session,regime,
             entry,sl,tp1,tp2,tp3,lot,risk_usd,score,grade,pattern,
             result,pnl_pts,pnl_usd,r_multiple,duration_mins,notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                sig.signal_id,
                sig.created_at.isoformat(),
                sig.direction,
                sig.strategy,
                sig.session,
                sig.regime,
                sig.entry,
                sig.sl,
                sig.tp1,
                sig.tp2,
                sig.tp3,
                sig.lot,
                sig.risk_usd,
                sig.score,
                sig.grade,
                sig.pattern,
                sig.status,
                sig.pnl_pts,
                sig.pnl_usd,
                sig.r_multiple,
                0.0,
                "",
            ))
            conn.commit()
        self._append_csv(sig)

    def update_result(self, signal_id: str, result: str, pnl_pts: float,
                      pnl_usd: float, r_mult: float, duration_mins: float,
                      notes: str = ""):
        with self._conn() as conn:
            conn.execute("""
            UPDATE signals SET result=?, pnl_pts=?, pnl_usd=?, r_multiple=?,
                               duration_mins=?, notes=?
            WHERE signal_id=?
            """, (result, pnl_pts, pnl_usd, r_mult, duration_mins, notes, signal_id))
            conn.commit()

    def _append_csv(self, sig):
        file_exists = os.path.exists(self.csv_path)
        with open(self.csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "signal_id","datetime","direction","strategy","session","regime",
                    "entry","sl","tp1","tp2","tp3","lot","risk_usd","score","grade",
                    "pattern","result","pnl_pts","pnl_usd","r_multiple","duration_mins"
                ])
            writer.writerow([
                sig.signal_id, sig.created_at.isoformat(), sig.direction,
                sig.strategy, sig.session, sig.regime,
                sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3,
                sig.lot, sig.risk_usd, sig.score, sig.grade, sig.pattern,
                sig.status, sig.pnl_pts, sig.pnl_usd, sig.r_multiple, 0
            ])

    def get_stats(self, days: int = 7) -> dict:
        with self._conn() as conn:
            rows = conn.execute("""
            SELECT result, pnl_usd, r_multiple, strategy, session, direction, score
            FROM signals
            WHERE created_at >= datetime('now', ?)
            AND result NOT IN ('PENDING','LIVE','TP1','TP2')
            """, (f"-{days} days",)).fetchall()

        if not rows:
            return {
                "total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "net_pnl": 0, "avg_r": 0, "best_strategy": "N/A",
                "profit_factor": 0,
            }

        total  = len(rows)
        wins   = sum(1 for r in rows if r[0] in ("win","partial"))
        losses = sum(1 for r in rows if r[0] == "loss")
        be     = sum(1 for r in rows if r[0] == "breakeven")
        net    = sum(r[1] for r in rows)
        avg_r  = sum(r[2] for r in rows) / total if total > 0 else 0
        gross_win  = sum(r[1] for r in rows if r[1] > 0)
        gross_loss = abs(sum(r[1] for r in rows if r[1] < 0))
        pf = gross_win / gross_loss if gross_loss > 0 else gross_win

        # Best strategy
        from collections import defaultdict
        strat_wins = defaultdict(lambda: [0,0])
        for r in rows:
            strat = r[3]
            strat_wins[strat][1] += 1
            if r[0] in ("win","partial"):
                strat_wins[strat][0] += 1
        best = max(strat_wins.items(), key=lambda x: x[1][0]/max(x[1][1],1)) if strat_wins else ("N/A",)

        return {
            "total": total, "wins": wins, "losses": losses, "breakeven": be,
            "win_rate": round(wins/total*100, 1) if total > 0 else 0,
            "net_pnl": round(net, 2),
            "avg_r": round(avg_r, 2),
            "best_strategy": best[0] if best else "N/A",
            "profit_factor": round(pf, 2),
        }

    def get_last_trades(self, n: int = 10) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
            SELECT signal_id, direction, strategy, entry, result,
                   pnl_usd, r_multiple, duration_mins, created_at
            FROM signals
            ORDER BY created_at DESC LIMIT ?
            """, (n,)).fetchall()
        return [
            {
                "id": r[0], "dir": r[1], "strategy": r[2], "entry": r[3],
                "result": r[4], "pnl_usd": r[5], "r_mult": r[6],
                "duration": r[7], "time": r[8]
            }
            for r in rows
        ]

    def weekly_report_text(self) -> str:
        stats = self.get_stats(days=7)
        from datetime import date
        today = date.today()
        return (
            f"📊 APEX WEEKLY REPORT\n"
            f"Week ending: {today.strftime('%d/%m/%Y')}\n"
            f"─────────────────────────────────\n"
            f"Total signals:    {stats['total']}\n"
            f"Wins:             {stats['wins']} ({stats['win_rate']}%)\n"
            f"Losses:           {stats['losses']}\n"
            f"Breakeven:        {stats.get('breakeven',0)}\n"
            f"Net P&L:          ${stats['net_pnl']:+.2f}\n"
            f"Profit factor:    {stats['profit_factor']}\n"
            f"Avg R achieved:   {stats['avg_r']:.2f}R\n"
            f"Best strategy:    {stats['best_strategy']}\n"
            f"─────────────────────────────────\n"
            f"Stay disciplined. Next week begins now. 💪"
        )
