# -*- coding: utf-8 -*-
"""Build an offline snapshot for the iPhone PWA and Android app."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from chip_analysis import build_chip_analysis
from financial_analysis import build_financial_analysis
from screen_utils import latest_change_pct


def _float(value, default=None):
    try:
        if value is None or pd.isna(value):
            return default
        text = str(value).replace(",", "").replace("%", "").strip()
        if text in {"", "—", "-"}:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _records(df, columns):
    if df is None or df.empty:
        return []
    work = df.copy()
    for column in columns:
        if column not in work:
            work[column] = None
    return [_json_safe(row) for row in work[list(columns)].to_dict("records")]


def _json_safe(value):
    """Convert pandas/numpy values into strict JSON-compatible values."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "item") and callable(value.item):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _chip_records(chip_df, limit=60):
    if chip_df is None or chip_df.empty:
        return []
    work = chip_df.copy()
    work["net"] = pd.to_numeric(work["net"], errors="coerce").fillna(0) / 1000.0
    pivot = work.pivot_table(
        index="date", columns="name", values="net", aggfunc="sum", fill_value=0
    ).reset_index()
    output = []
    for row in pivot.to_dict("records"):
        output.append({
            "date": str(row.get("date", "")),
            "foreign_net_lots": _float(row.get("Foreign_Investor"), 0.0),
            "trust_net_lots": _float(row.get("Investment_Trust"), 0.0),
            "dealer_net_lots": _float(row.get("Dealer_Total"), 0.0),
            "dealer_self_net_lots": _float(row.get("Dealer_self"), 0.0),
            "dealer_hedge_net_lots": _float(row.get("Dealer_Hedging"), 0.0),
        })
    return output[-max(int(limit), 1):]


def build_mobile_snapshot(store, result_rows, detail_cache=None, conditions=None,
                          progress_callback=None, price_days=180,
                          revenue_months=12, financial_quarters=12,
                          institutional_days=60, shareholding_weeks=12):
    """Export the current screened result set plus its mobile detail data."""
    rows = [dict(row) for row in (result_rows or [])]
    detail_cache = detail_cache or {}
    stock_info = store.get_stock_info()
    capital_by_id = {}
    if stock_info is not None and not stock_info.empty:
        capital_by_id = {
            str(row.stock_id): _float(row.paid_in_capital)
            for row in stock_info.itertuples(index=False)
        }

    stocks = []
    financial_analysis_error_count = 0
    chip_analysis_error_count = 0
    latest_trade_date = ""
    total = len(rows)
    for index, row in enumerate(rows, 1):
        stock_id = str(row.get("stock_id", "")).strip()
        if not stock_id:
            continue
        prices = store.get_daily_price(stock_id, limit=max(int(price_days), 2))
        revenues = store.get_monthly_revenue(
            stock_id, limit=max(int(revenue_months), 1)
        )
        financial = store.get_financial_statement(
            stock_id, limit_quarters=max(int(financial_quarters), 1)
        )
        chip = store.get_institutional_buysell(
            stock_id, limit_days=max(int(institutional_days), 1)
        )
        shareholding = store.get_shareholding_distribution(
            stock_id, limit_weeks=max(int(shareholding_weeks), 1)
        )

        price_records = []
        if prices is not None and not prices.empty:
            prices = prices.sort_values("date").tail(max(int(price_days), 2)).copy()
            for item in prices.to_dict("records"):
                price_records.append({
                    "date": str(item.get("date", "")),
                    "open": _float(item.get("open"), 0.0),
                    "high": _float(item.get("max"), 0.0),
                    "low": _float(item.get("min"), 0.0),
                    "close": _float(item.get("close"), 0.0),
                    "volume": _float(item.get("Trading_Volume"), 0.0),
                })
            latest_trade_date = max(latest_trade_date, price_records[-1]["date"])

        revenue_records = _records(
            revenues.sort_values("date").tail(max(int(revenue_months), 1))
            if revenues is not None else None,
            ("date", "revenue", "mom_pct", "yoy_pct"),
        )
        eps_records = []
        if financial is not None and not financial.empty:
            quarterly = financial[financial["type"] == "EPS"].sort_values("date").tail(12)
            eps_records = [
                {"date": str(item["date"]), "value": _float(item["value"], 0.0),
                 "kind": "quarterly", "label": "單季"}
                for item in quarterly.to_dict("records")
            ]
            cumulative = financial[
                financial["type"] == "EPS_CUMULATIVE"
            ].sort_values("date")
            latest_quarterly_date = str(quarterly.iloc[-1]["date"]) if not quarterly.empty else ""
            if not cumulative.empty and str(cumulative.iloc[-1]["date"]) > latest_quarterly_date:
                latest = cumulative.iloc[-1]
                eps_records.append({
                    "date": str(latest["date"]),
                    "value": _float(latest["value"], 0.0),
                    "kind": "cumulative", "label": "本年度累計",
                })
            eps_records.sort(key=lambda item: item["date"])

        paid_in_capital = capital_by_id.get(stock_id)
        fallback_close = price_records[-1]["close"] if price_records else 0.0
        current_close = _float(row.get("close"), fallback_close)
        chip_records = _chip_records(chip, institutional_days)
        try:
            financial_health = build_financial_analysis(
                financial, close=current_close, max_quarters=12
            )
        except Exception as exc:
            # One company's unusual official statement must not discard the
            # other ~2,000 stocks after a long cloud update.  Preserve an
            # explicit per-stock error for diagnosis and continue exporting.
            financial_analysis_error_count += 1
            financial_health = {
                "latest_date": "",
                "coverage": {
                    "income": False, "balance": False, "cashflow": False,
                },
                "metrics": [], "quarters": [], "highlights": [], "cautions": [],
                "risks": [], "risk_level": "unknown", "risk_label": "資料異常",
                "risk_score": None,
                "calculation_note": "此股票財務健診計算異常，已保留其他股票資料。",
                "analysis_error": f"{type(exc).__name__}: {exc}",
            }
        try:
            chip_health = build_chip_analysis(
                chip_records, distribution=shareholding,
                price_records=price_records, max_weeks=shareholding_weeks,
            )
        except Exception as exc:
            chip_analysis_error_count += 1
            chip_health = {
                "status": "unknown", "status_label": "資料異常", "score": 0,
                "positive_signals": [], "cautions": [], "metrics": [], "weeks": [],
                "coverage": {"institutional": False, "tdcc": False, "tdcc_weeks": 0},
                "calculation_note": "此股票籌碼提示計算異常，已保留其他股票資料。",
                "analysis_error": f"{type(exc).__name__}: {exc}",
            }
        stocks.append({
            "stock_id": stock_id,
            "name": str(row.get("name", "")),
            "industry": str(row.get("industry", "") or "未分類"),
            "capital_billion": (
                paid_in_capital / 100_000_000 if paid_in_capital is not None
                else _float(row.get("capital_billion"))
            ),
            "close": current_close,
            "change_pct": (
                latest_change_pct(prices) if prices is not None and len(prices) >= 2
                else _float(row.get("change_pct"), 0.0)
            ),
            "volume_lots": _float(row.get("volume"), 0.0),
            "match": str(row.get("match", "—")),
            "details": list(detail_cache.get(stock_id, [])),
            "price_history": price_records,
            "monthly_revenue": revenue_records,
            "eps": eps_records,
            "financial_health": financial_health,
            "chip_analysis": chip_health,
            "institutional": chip_records,
        })
        if progress_callback:
            progress_callback(index, total, stock_id)

    return {
        "schema_version": 3,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "latest_trade_date": latest_trade_date,
        "strategy_name": "桌面版目前條件組合",
        "conditions": [str(value) for value in (conditions or [])],
        "stock_count": len(stocks),
        "financial_analysis_error_count": financial_analysis_error_count,
        "chip_analysis_error_count": chip_analysis_error_count,
        "stocks": stocks,
    }


def write_mobile_snapshot(destination, snapshot):
    """Atomically write UTF-8 JSON so a partial file is never transferred."""
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(
                _json_safe(snapshot), ensure_ascii=False,
                separators=(",", ":"), allow_nan=False,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return target
