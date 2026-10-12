# -*- coding: utf-8 -*-
"""Exploratory, point-in-time score validation for the mobile snapshot.

This is a cross-sectional diagnostic, not a trading strategy simulator.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import math

import pandas as pd

from chip_analysis import build_chip_analysis
from financial_analysis import build_financial_analysis
from mobile_export import _chip_records

HORIZONS = (5, 10, 20)
MIN_HISTORY = 60
FINANCIAL_LAG_DAYS = 90
TDCC_LAG_DAYS = 10


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _calendar_week(value):
    d = date.fromisoformat(str(value)[:10])
    return d.isocalendar()[:2]


def _summarize(samples, dates, signal, horizon):
    groups = samples[signal, horizon]
    paired = []
    for day in sorted(dates):
        high = groups[day]["high"]
        low = groups[day]["low"]
        if len(high) >= 10 and len(low) >= 10:
            paired.append((day, high, low))
    count_high = sum(len(x[1]) for x in paired)
    count_low = sum(len(x[2]) for x in paired)
    if not paired:
        return {"horizon": horizon, "status": "insufficient", "cohorts": 0,
                "high_samples": 0, "low_samples": 0}
    high = sum(sum(a) / len(a) for _, a, _ in paired) / len(paired)
    low = sum(sum(b) / len(b) for _, _, b in paired) / len(paired)
    spread = high - low
    return {
        "horizon": horizon,
        "status": "exploratory" if len(paired) >= 6 else "insufficient",
        "first_date": paired[0][0], "last_date": paired[-1][0],
        "cohorts": len(paired), "high_samples": count_high,
        "low_samples": count_low, "high_return_pct": round(high, 2),
        "low_return_pct": round(low, 2), "spread_pct": round(spread, 2),
        "high_outperformed_cohorts": sum(
            sum(a) / len(a) > sum(b) / len(b) for _, a, b in paired
        ),
    }


def run_backtest(store, stock_ids, *, horizons=HORIZONS):
    """Recalculate scores at weekly closes using only conservatively dated inputs.

    Quarter ends are not publication dates. The 90-day lag is an approximation;
    historic restatements and today's surviving stock universe still bias results.
    """
    horizons = tuple(sorted(set(int(h) for h in horizons if int(h) > 0)))
    if not horizons:
        raise ValueError("At least one positive holding period is required")
    ids = sorted(set(str(s) for s in stock_ids if str(s)))
    samples = defaultdict(lambda: defaultdict(lambda: {"high": [], "low": []}))
    selected_dates = set()
    all_dates = set()
    for stock_id in ids:
        prices = store.get_daily_price(stock_id, limit=None)
        if not prices.empty:
            all_dates.update(str(d)[:10] for d in prices.date)
    # One observation per calendar week, never the same stock every day.
    by_week = {}
    for d in sorted(all_dates):
        by_week[_calendar_week(d)] = d
    weekly_dates = set(by_week.values())
    if not weekly_dates:
        return {"status": "insufficient", "results": {}}

    for stock_id in ids:
        prices = store.get_daily_price(stock_id, limit=None)
        if len(prices) < MIN_HISTORY + min(horizons) + 1:
            continue
        price_rows = prices.to_dict("records")
        chips = _chip_records(store.get_institutional_buysell(stock_id, limit_days=300), 300)
        finance = store.get_financial_statement(stock_id, limit_quarters=20)
        tdcc = store.get_shareholding_distribution(stock_id, limit_weeks=70)
        finance_by_date = {}
        if not finance.empty:
            finance = finance.copy()
            finance["available"] = (pd.to_datetime(finance.date) + pd.Timedelta(days=FINANCIAL_LAG_DAYS)).dt.strftime("%Y-%m-%d")
        if not tdcc.empty:
            tdcc = tdcc.copy()
            tdcc["available"] = (pd.to_datetime(tdcc.date) + pd.Timedelta(days=TDCC_LAG_DAYS)).dt.strftime("%Y-%m-%d")
        index_by_date = {str(row["date"])[:10]: i for i, row in enumerate(price_rows)}
        for day in sorted(weekly_dates.intersection(index_by_date)):
            i = index_by_date[day]
            if i < MIN_HISTORY or i + min(horizons) >= len(price_rows):
                continue
            entry = _number(price_rows[i + 1].get("open"))
            if entry is None or entry <= 0:
                continue
            selected_dates.add(day)
            future = {}
            for h in horizons:
                if i + h >= len(price_rows):
                    continue
                end = price_rows[i + h]
                exit_price = _number(end.get("close"))
                # A long suspension is not an ordinary N-session outcome.
                if (exit_price is None or exit_price <= 0 or
                    (date.fromisoformat(str(end["date"])[:10]) - date.fromisoformat(day)).days > h * 2 + 10):
                    continue
                future[h] = (exit_price / entry - 1) * 100
            if not future:
                continue
            signals = {}
            past_chips = [row for row in chips if row["date"] <= day]
            available_tdcc = tdcc[tdcc.available <= day].drop(columns="available") if not tdcc.empty else tdcc
            # Require current-day institutional figures and two published TDCC weeks.
            tdcc_recent = (not available_tdcc.empty and
                           date.fromisoformat(str(available_tdcc.date.max())[:10]) >=
                           date.fromisoformat(day) - timedelta(days=31))
            if len(past_chips) >= 10 and past_chips[-1]["date"] == day:
                try:
                    recent_prices = [{"date": str(p["date"]), "volume": p.get("Trading_Volume")}
                                     for p in price_rows[max(0, i - 119):i + 1]]
                    for name, distribution in (("institutional", None),
                                               ("chip", available_tdcc if tdcc_recent and available_tdcc.date.nunique() >= 2 else None)):
                        if name == "chip" and distribution is None:
                            continue
                        score = build_chip_analysis(
                            past_chips[-120:], distribution=distribution,
                            price_records=recent_prices,
                        )["score"]
                        if score >= 25:
                            signals[name] = "high"
                        elif score <= -25:
                            signals[name] = "low"
                except (KeyError, TypeError, ValueError):
                    pass
            if not finance.empty:
                known = finance[finance.available <= day]
                if not known.empty:
                    quarter = known.date.max()
                    if quarter not in finance_by_date:
                        try:
                            result = build_financial_analysis(known.drop(columns="available"))
                            finance_by_date[quarter] = result
                        except (KeyError, TypeError, ValueError):
                            finance_by_date[quarter] = {}
                    result = finance_by_date[quarter]
                    coverage = result.get("coverage", {})
                    # Missing balance/cash flow must not silently become low risk.
                    if (all(coverage.get(key) for key in ("income", "balance", "cashflow"))
                        and coverage.get("income_quarters", 0) >= 8
                        and coverage.get("balance_quarters", 0) >= 4
                        and coverage.get("cashflow_quarters", 0) >= 4):
                        risk = result.get("risk_score")
                        if risk == 0:
                            signals["financial"] = "high"
                        elif risk is not None and risk >= 30:
                            signals["financial"] = "low"
            for signal, group in signals.items():
                for h, value in future.items():
                    samples[signal, h][day][group].append(value)

    results = {signal: [_summarize(samples, selected_dates, signal, h) for h in horizons]
               for signal in ("financial", "institutional", "chip")}
    return {
        "status": "exploratory" if any(row["status"] == "exploratory" for group in results.values() for row in group) else "insufficient",
        "method": "每週末收盤後計分，次一交易日開盤進場，第 N 個交易日收盤出場；同日先算每組等權平均，再對各週等權平均。未計交易成本、稅、股利。",
        "limits": "僅目前仍在資料庫的股票，存在存活者偏差；季報以季末加90天、集保以資料日加10天估計可得日，實際公告日和歷史更正未保存；未還原權息，原始價差可能失真。未控制市場、產業與規模；樣本日期重疊且非獨立，不代表可交易報酬。",
        "definitions": {"financial": "高分=財務風險分數0且損益、資產負債、現金流齊全；低分=風險分數至少30。此為風險低／高分組，並非獲利品質分數。", "institutional": "只用法人訊號計分，高分至少25、低分最多-25；不含TDCC。", "chip": "完整籌碼分數高分至少25、低分最多-25，且有當日法人及至少兩週可得集保資料。"},
        "candidate_weeks": len(weekly_dates), "stock_universe": len(ids),
        "results": results,
    }
