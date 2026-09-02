# -*- coding: utf-8 -*-
"""SQLite storage layer for the Taiwan stock screener.

The GUI never screens against a remote API.  Official TWSE/TPEx/MOPS data is
first normalised into this database and all conditions are then evaluated from
local DataFrames.
"""
from __future__ import annotations

import os
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


APP_DATA_DIR = Path(
    os.environ.get("TW_SCREENER_DATA_DIR", Path.home() / ".tw_stock_screener")
)
DEFAULT_DB_PATH = APP_DATA_DIR / "tw_stock_data.sqlite3"
CONFIG_PATH = APP_DATA_DIR / "config.json"


def preferred_db_path() -> Path:
    """Resolve the database path, with environment variables taking priority."""
    explicit = os.environ.get("TW_SCREENER_DB_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    try:
        if CONFIG_PATH.exists():
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            configured = str(payload.get("db_path", "")).strip()
            if configured:
                return Path(configured).expanduser()
    except (OSError, ValueError, TypeError):
        pass
    return DEFAULT_DB_PATH


def save_preferred_db_path(db_path: os.PathLike):
    """Persist the GUI-selected SQLite file for the next application start."""
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"db_path": str(Path(db_path).expanduser().resolve())}
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class DataStore:
    """Thread-safe-by-connection SQLite repository."""

    def __init__(self, db_path: Optional[os.PathLike] = None):
        self.db_path = Path(db_path or preferred_db_path()).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialise(self):
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS stock_info (
                    stock_id TEXT PRIMARY KEY,
                    stock_name TEXT NOT NULL DEFAULT '',
                    market TEXT NOT NULL DEFAULT '',
                    industry_category TEXT NOT NULL DEFAULT '',
                    paid_in_capital REAL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_price (
                    stock_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    market TEXT NOT NULL,
                    open REAL,
                    max REAL,
                    min REAL,
                    close REAL,
                    Trading_Volume INTEGER,
                    Trading_money INTEGER,
                    spread REAL,
                    Trading_turnover INTEGER,
                    PRIMARY KEY (stock_id, date)
                );
                CREATE INDEX IF NOT EXISTS idx_daily_price_date
                    ON daily_price(date);

                CREATE TABLE IF NOT EXISTS monthly_revenue (
                    stock_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    market TEXT NOT NULL,
                    revenue_year INTEGER NOT NULL,
                    revenue_month INTEGER NOT NULL,
                    revenue REAL,
                    prev_month_revenue REAL,
                    last_year_revenue REAL,
                    mom_pct REAL,
                    yoy_pct REAL,
                    PRIMARY KEY (stock_id, revenue_year, revenue_month)
                );
                CREATE INDEX IF NOT EXISTS idx_monthly_revenue_date
                    ON monthly_revenue(date);

                CREATE TABLE IF NOT EXISTS financial_statement (
                    stock_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    market TEXT NOT NULL,
                    type TEXT NOT NULL,
                    value REAL,
                    origin_name TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (stock_id, date, type)
                );
                CREATE INDEX IF NOT EXISTS idx_financial_statement_date
                    ON financial_statement(date);

                CREATE TABLE IF NOT EXISTS institutional_buysell (
                    stock_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    market TEXT NOT NULL,
                    foreign_buy INTEGER,
                    foreign_sell INTEGER,
                    foreign_net INTEGER,
                    trust_buy INTEGER,
                    trust_sell INTEGER,
                    trust_net INTEGER,
                    dealer_total_buy INTEGER,
                    dealer_total_sell INTEGER,
                    dealer_total_net INTEGER,
                    dealer_self_buy INTEGER,
                    dealer_self_sell INTEGER,
                    dealer_self_net INTEGER,
                    dealer_hedge_buy INTEGER,
                    dealer_hedge_sell INTEGER,
                    dealer_hedge_net INTEGER,
                    total_net INTEGER,
                    PRIMARY KEY (stock_id, date)
                );
                CREATE INDEX IF NOT EXISTS idx_institutional_buysell_date
                    ON institutional_buysell(date);

                CREATE TABLE IF NOT EXISTS shareholding_distribution (
                    stock_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    holders INTEGER,
                    shares INTEGER,
                    ratio_pct REAL,
                    PRIMARY KEY (stock_id, date, level)
                );
                CREATE INDEX IF NOT EXISTS idx_shareholding_distribution_date
                    ON shareholding_distribution(date);

                CREATE TABLE IF NOT EXISTS sync_state (
                    sync_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                """
            )
            # 舊版資料庫原本沒有股本欄位；啟動新版時就地升級，保留所有既有資料。
            stock_info_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(stock_info)").fetchall()
            }
            if "paid_in_capital" not in stock_info_columns:
                conn.execute("ALTER TABLE stock_info ADD COLUMN paid_in_capital REAL")

    def backup_to(self, destination: os.PathLike) -> Path:
        """Safely copy the live SQLite database, including committed WAL data."""
        target = Path(destination).expanduser().resolve()
        source = self.db_path.expanduser().resolve()
        if target == source:
            return target
        if target.exists():
            raise FileExistsError(f"目標資料庫已存在：{target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source, timeout=30) as source_conn:
            with sqlite3.connect(target, timeout=30) as target_conn:
                source_conn.backup(target_conn)
        return target

    @staticmethod
    def _records(df: pd.DataFrame, columns: Iterable[str]):
        clean = df[list(columns)].copy()
        clean = clean.where(pd.notna(clean), None)
        return [tuple(row) for row in clean.itertuples(index=False, name=None)]

    def upsert_stock_info(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        required = [
            "stock_id", "stock_name", "market", "industry_category",
            "paid_in_capital",
        ]
        work = df.copy()
        for col in required:
            if col not in work:
                work[col] = None if col == "paid_in_capital" else ""
        work = work.dropna(subset=["stock_id"])
        work["stock_id"] = work["stock_id"].astype(str).str.strip()
        work = work[work["stock_id"] != ""].drop_duplicates("stock_id", keep="last")
        work["updated_at"] = datetime.now().isoformat(timespec="seconds")
        sql = """
            INSERT INTO stock_info
                (stock_id, stock_name, market, industry_category,
                 paid_in_capital, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_id) DO UPDATE SET
                stock_name = CASE
                    WHEN excluded.stock_name <> '' THEN excluded.stock_name
                    ELSE stock_info.stock_name END,
                market = CASE
                    WHEN excluded.market <> '' THEN excluded.market
                    ELSE stock_info.market END,
                industry_category = CASE
                    WHEN excluded.industry_category <> '' THEN excluded.industry_category
                    ELSE stock_info.industry_category END,
                paid_in_capital = COALESCE(
                    excluded.paid_in_capital, stock_info.paid_in_capital
                ),
                updated_at = excluded.updated_at
        """
        with self.connect() as conn:
            conn.executemany(sql, self._records(work, required + ["updated_at"]))
        return len(work)

    def upsert_daily_prices(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        columns = [
            "stock_id", "date", "market", "open", "max", "min", "close",
            "Trading_Volume", "Trading_money", "spread", "Trading_turnover",
        ]
        work = df.copy()
        for col in columns:
            if col not in work:
                work[col] = None
        work = work.dropna(subset=["stock_id", "date"])
        work["stock_id"] = work["stock_id"].astype(str).str.strip()
        work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        work = work.dropna(subset=["date"]).drop_duplicates(["stock_id", "date"], keep="last")
        sql = """
            INSERT INTO daily_price
                (stock_id, date, market, open, max, min, close,
                 Trading_Volume, Trading_money, spread, Trading_turnover)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_id, date) DO UPDATE SET
                market=excluded.market, open=excluded.open, max=excluded.max,
                min=excluded.min, close=excluded.close,
                Trading_Volume=excluded.Trading_Volume,
                Trading_money=excluded.Trading_money,
                spread=excluded.spread,
                Trading_turnover=excluded.Trading_turnover
        """
        with self.connect() as conn:
            conn.executemany(sql, self._records(work, columns))
        return len(work)

    def upsert_monthly_revenue(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        columns = [
            "stock_id", "date", "market", "revenue_year", "revenue_month",
            "revenue", "prev_month_revenue", "last_year_revenue", "mom_pct", "yoy_pct",
        ]
        work = df.copy()
        for col in columns:
            if col not in work:
                work[col] = None
        work = work.dropna(subset=["stock_id", "revenue_year", "revenue_month"])
        work["stock_id"] = work["stock_id"].astype(str).str.strip()
        work = work.drop_duplicates(["stock_id", "revenue_year", "revenue_month"], keep="last")
        sql = """
            INSERT INTO monthly_revenue
                (stock_id, date, market, revenue_year, revenue_month, revenue,
                 prev_month_revenue, last_year_revenue, mom_pct, yoy_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_id, revenue_year, revenue_month) DO UPDATE SET
                date=excluded.date, market=excluded.market,
                revenue=excluded.revenue,
                prev_month_revenue=COALESCE(excluded.prev_month_revenue, monthly_revenue.prev_month_revenue),
                last_year_revenue=COALESCE(excluded.last_year_revenue, monthly_revenue.last_year_revenue),
                mom_pct=COALESCE(excluded.mom_pct, monthly_revenue.mom_pct),
                yoy_pct=COALESCE(excluded.yoy_pct, monthly_revenue.yoy_pct)
        """
        with self.connect() as conn:
            conn.executemany(sql, self._records(work, columns))
        return len(work)

    def upsert_financial_statements(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        columns = ["stock_id", "date", "market", "type", "value", "origin_name"]
        work = df.copy()
        for col in columns:
            if col not in work:
                work[col] = "" if col == "origin_name" else None
        work = work.dropna(subset=["stock_id", "date", "type"])
        work["stock_id"] = work["stock_id"].astype(str).str.strip()
        work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        work = work.dropna(subset=["date"]).drop_duplicates(["stock_id", "date", "type"], keep="last")
        sql = """
            INSERT INTO financial_statement
                (stock_id, date, market, type, value, origin_name)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_id, date, type) DO UPDATE SET
                market=excluded.market, value=excluded.value,
                origin_name=excluded.origin_name
        """
        with self.connect() as conn:
            conn.executemany(sql, self._records(work, columns))
        return len(work)

    def upsert_institutional_buysell(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        columns = [
            "stock_id", "date", "market",
            "foreign_buy", "foreign_sell", "foreign_net",
            "trust_buy", "trust_sell", "trust_net",
            "dealer_total_buy", "dealer_total_sell", "dealer_total_net",
            "dealer_self_buy", "dealer_self_sell", "dealer_self_net",
            "dealer_hedge_buy", "dealer_hedge_sell", "dealer_hedge_net",
            "total_net",
        ]
        work = df.copy()
        for col in columns:
            if col not in work:
                work[col] = None
        work = work.dropna(subset=["stock_id", "date"])
        work["stock_id"] = work["stock_id"].astype(str).str.strip()
        work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        work = work.dropna(subset=["date"]).drop_duplicates(["stock_id", "date"], keep="last")
        update_columns = columns[2:]
        sql = f"""
            INSERT INTO institutional_buysell ({', '.join(columns)})
            VALUES ({', '.join('?' for _ in columns)})
            ON CONFLICT(stock_id, date) DO UPDATE SET
                {', '.join(f'{col}=excluded.{col}' for col in update_columns)}
        """
        with self.connect() as conn:
            conn.executemany(sql, self._records(work, columns))
        return len(work)

    def upsert_shareholding_distribution(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        columns = ["stock_id", "date", "level", "holders", "shares", "ratio_pct"]
        work = df.copy()
        for col in columns:
            if col not in work:
                work[col] = None
        work = work.dropna(subset=["stock_id", "date", "level"])
        work["stock_id"] = work["stock_id"].astype(str).str.strip()
        work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        work["level"] = pd.to_numeric(work["level"], errors="coerce")
        work = work.dropna(subset=["date", "level"])
        work["level"] = work["level"].astype(int)
        work = work.drop_duplicates(["stock_id", "date", "level"], keep="last")
        sql = """
            INSERT INTO shareholding_distribution
                (stock_id, date, level, holders, shares, ratio_pct)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_id, date, level) DO UPDATE SET
                holders=excluded.holders, shares=excluded.shares,
                ratio_pct=excluded.ratio_pct
        """
        with self.connect() as conn:
            conn.executemany(sql, self._records(work, columns))
        return len(work)

    def set_sync_state(self, sync_key: str, status: str, row_count: int = 0, message: str = ""):
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state(sync_key, status, row_count, message, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(sync_key) DO UPDATE SET
                    status=excluded.status, row_count=excluded.row_count,
                    message=excluded.message, updated_at=excluded.updated_at
                """,
                (sync_key, status, int(row_count), message, datetime.now().isoformat(timespec="seconds")),
            )

    def sync_succeeded(self, sync_key: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status FROM sync_state WHERE sync_key = ?", (sync_key,)
            ).fetchone()
        return bool(row and row["status"] == "ok")

    def recent_sync_errors(self, limit: int = 5) -> list[dict]:
        """Return recent report failures for a useful GUI message."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT sync_key, message, updated_at
                FROM sync_state
                WHERE status = 'error'
                  AND (sync_key LIKE 'revenue:%'
                       OR sync_key LIKE 'financial:%'
                       OR sync_key LIKE 'institutional:%'
                       OR sync_key LIKE 'shareholding:%')
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_stock_info(self) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query(
                """
                SELECT stock_id, stock_name, market, industry_category,
                       paid_in_capital
                FROM stock_info ORDER BY stock_id
                """,
                conn,
            )

    def get_candidates(self, industry: str = "全部", industries=None,
                       min_volume: float = 0, max_stocks: int = 0,
                       min_bars: int = 180) -> pd.DataFrame:
        where = [
            "COALESCE(p.Trading_Volume, 0) >= ?",
            "c.bar_count >= ?",
            # 排除已下市或長期停牌、只有舊歷史資料的代碼。
            "p.date >= date((SELECT MAX(date) FROM daily_price), '-7 days')",
        ]
        params = [float(min_volume), int(min_bars)]
        selected_industries = industries
        if selected_industries is None:
            selected_industries = (
                list(industry) if isinstance(industry, (list, tuple, set)) else [industry]
            )
        selected_industries = list(dict.fromkeys(
            str(value).strip() for value in selected_industries
            if value is not None and str(value).strip()
            and str(value).strip() != "全部"
        ))
        if selected_industries:
            placeholders = ", ".join("?" for _ in selected_industries)
            where.append(f"s.industry_category IN ({placeholders})")
            params.extend(selected_industries)
        limit_sql = ""
        if max_stocks and max_stocks > 0:
            limit_sql = " LIMIT ?"
            params.append(int(max_stocks))
        query = f"""
            WITH price_counts AS (
                SELECT stock_id, COUNT(*) AS bar_count, MAX(date) AS latest_date
                FROM daily_price GROUP BY stock_id
            )
            SELECT s.stock_id, s.stock_name, s.market, s.industry_category,
                   s.paid_in_capital,
                   p.date, p.open, p.max, p.min, p.close,
                   p.Trading_Volume, p.Trading_money, c.bar_count
            FROM stock_info s
            JOIN price_counts c ON c.stock_id = s.stock_id
            JOIN daily_price p ON p.stock_id = c.stock_id AND p.date = c.latest_date
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(p.Trading_Volume, 0) DESC, s.stock_id
            {limit_sql}
        """
        with self.connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def get_daily_price(self, stock_id: str, limit: Optional[int] = None) -> pd.DataFrame:
        sql = """
            SELECT date, stock_id, Trading_Volume, Trading_money,
                   open, max, min, close, spread, Trading_turnover
            FROM daily_price WHERE stock_id = ? ORDER BY date DESC
        """
        params = [str(stock_id)]
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        return df.sort_values("date").reset_index(drop=True) if not df.empty else df

    def get_monthly_revenue(self, stock_id: str, limit: int = 36) -> pd.DataFrame:
        with self.connect() as conn:
            df = pd.read_sql_query(
                """
                SELECT date, stock_id, revenue, revenue_year, revenue_month,
                       mom_pct, yoy_pct
                FROM monthly_revenue WHERE stock_id = ?
                ORDER BY revenue_year DESC, revenue_month DESC LIMIT ?
                """,
                conn,
                params=(str(stock_id), int(limit)),
            )
        return df.sort_values("date").reset_index(drop=True) if not df.empty else df

    def get_financial_statement(self, stock_id: str, limit_quarters: int = 16) -> pd.DataFrame:
        with self.connect() as conn:
            df = pd.read_sql_query(
                """
                SELECT date, stock_id, type, value, origin_name
                FROM financial_statement
                WHERE stock_id = ?
                  AND date IN (
                      SELECT DISTINCT date
                      FROM financial_statement
                      WHERE stock_id = ?
                      ORDER BY date DESC
                      LIMIT ?
                  )
                ORDER BY date DESC, type
                """,
                conn, params=(str(stock_id), str(stock_id), int(limit_quarters)),
            )
        return df.sort_values("date").reset_index(drop=True) if not df.empty else df

    def get_institutional_buysell(self, stock_id: str, limit_days: int = 260) -> pd.DataFrame:
        """Return official institutional data in the long format used by conditions.py."""
        with self.connect() as conn:
            wide = pd.read_sql_query(
                """
                SELECT * FROM institutional_buysell
                WHERE stock_id = ?
                ORDER BY date DESC LIMIT ?
                """,
                conn,
                params=(str(stock_id), max(int(limit_days), 1)),
            )
        if wide.empty:
            return pd.DataFrame(columns=["date", "stock_id", "buy", "sell", "net", "name"])
        wide = wide.sort_values("date").reset_index(drop=True)
        mappings = (
            ("Foreign_Investor", "foreign"),
            ("Investment_Trust", "trust"),
            ("Dealer_Total", "dealer_total"),
            ("Dealer_self", "dealer_self"),
            ("Dealer_Hedging", "dealer_hedge"),
        )
        frames = []
        for name, prefix in mappings:
            frame = wide[["date", "stock_id", f"{prefix}_buy", f"{prefix}_sell", f"{prefix}_net"]].copy()
            frame.columns = ["date", "stock_id", "buy", "sell", "net"]
            frame["name"] = name
            frames.append(frame)
        return pd.concat(frames, ignore_index=True).sort_values(["date", "name"]).reset_index(drop=True)

    def get_shareholding_distribution(self, stock_id: str,
                                      limit_weeks: int = 16) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query(
                """
                SELECT date, stock_id, level, holders, shares, ratio_pct
                FROM shareholding_distribution
                WHERE stock_id = ?
                  AND date IN (
                      SELECT DISTINCT date
                      FROM shareholding_distribution
                      WHERE stock_id = ?
                      ORDER BY date DESC
                      LIMIT ?
                  )
                ORDER BY date, level
                """,
                conn,
                params=(str(stock_id), str(stock_id), max(int(limit_weeks), 1)),
            )

    def coverage_summary(self) -> dict:
        with self.connect() as conn:
            stock_count = conn.execute("SELECT COUNT(*) FROM stock_info").fetchone()[0]
            price_rows, price_stocks, min_date, max_date = conn.execute(
                """SELECT COUNT(*), COUNT(DISTINCT stock_id), MIN(date), MAX(date)
                   FROM daily_price"""
            ).fetchone()
            revenue_rows, revenue_stocks, revenue_min_date, revenue_max_date = conn.execute(
                """SELECT COUNT(*), COUNT(DISTINCT stock_id), MIN(date), MAX(date)
                   FROM monthly_revenue"""
            ).fetchone()
            revenue_market_rows = dict(conn.execute(
                """SELECT market, COUNT(*) FROM monthly_revenue
                   GROUP BY market"""
            ).fetchall())
            fin_rows = conn.execute("SELECT COUNT(*) FROM financial_statement").fetchone()[0]
            institutional_rows, institutional_stocks, institutional_min_date, institutional_max_date = conn.execute(
                """SELECT COUNT(*), COUNT(DISTINCT stock_id), MIN(date), MAX(date)
                   FROM institutional_buysell"""
            ).fetchone()
            institutional_market_rows = dict(conn.execute(
                """SELECT market, COUNT(*) FROM institutional_buysell
                   GROUP BY market"""
            ).fetchall())
            shareholding_rows, shareholding_stocks, shareholding_min_date, shareholding_max_date = conn.execute(
                """SELECT COUNT(*), COUNT(DISTINCT stock_id), MIN(date), MAX(date)
                   FROM shareholding_distribution"""
            ).fetchone()
        return {
            "stock_count": stock_count,
            "price_rows": price_rows,
            "price_stocks": price_stocks,
            "price_min_date": min_date,
            "price_max_date": max_date,
            "revenue_rows": revenue_rows,
            "revenue_stocks": revenue_stocks,
            "revenue_min_date": revenue_min_date,
            "revenue_max_date": revenue_max_date,
            "revenue_twse_rows": int(revenue_market_rows.get("TWSE", 0)),
            "revenue_tpex_rows": int(revenue_market_rows.get("TPEx", 0)),
            "financial_rows": fin_rows,
            "institutional_rows": institutional_rows,
            "institutional_stocks": institutional_stocks,
            "institutional_min_date": institutional_min_date,
            "institutional_max_date": institutional_max_date,
            "institutional_twse_rows": int(institutional_market_rows.get("TWSE", 0)),
            "institutional_tpex_rows": int(institutional_market_rows.get("TPEx", 0)),
            "shareholding_rows": shareholding_rows,
            "shareholding_stocks": shareholding_stocks,
            "shareholding_min_date": shareholding_min_date,
            "shareholding_max_date": shareholding_max_date,
            "db_path": str(self.db_path),
        }
