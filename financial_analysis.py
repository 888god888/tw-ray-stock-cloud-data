# -*- coding: utf-8 -*-
"""Explainable financial highlights and risk signals.

The rule engine intentionally uses only official statement values already
stored in SQLite.  It does not call an LLM and never treats missing data as a
positive or negative signal.
"""
from __future__ import annotations

import math

import pandas as pd


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _ratio(numerator, denominator, scale=100.0):
    a, b = _number(numerator), _number(denominator)
    if a is None or b is None or abs(b) < 1e-12:
        return None
    return a / b * scale


def _growth(current, previous):
    current, previous = _number(current), _number(previous)
    if current is None or previous is None or abs(previous) < 1e-12:
        return None
    # A percentage growth rate is not meaningful when the base is negative.
    if previous < 0:
        return None
    return (current / previous - 1.0) * 100.0


def _rounded(value, digits=2):
    value = _number(value)
    return round(value, digits) if value is not None else None


def _fmt_pct(value):
    value = _number(value)
    return "—" if value is None else f"{value:.1f}%"


def _fmt_money(value):
    value = _number(value)
    if value is None:
        return "—"
    billion = value / 100_000_000.0
    return f"{billion:,.2f} 億"


def _series_values(frame, column):
    if column not in frame:
        return []
    return [_number(value) for value in frame[column].tolist()]


def _last_valid(frame, column):
    values = [value for value in _series_values(frame, column) if value is not None]
    return values[-1] if values else None


def _quarter_ordinal(value):
    stamp = pd.Timestamp(value)
    return stamp.year * 4 + stamp.quarter


def _last_n(frame, column, count):
    if column not in frame:
        return []
    series = pd.to_numeric(frame[column], errors="coerce").dropna().tail(count)
    if len(series) < count:
        return []
    periods = [_quarter_ordinal(index) for index in series.index]
    if any(current - previous != 1 for previous, current in zip(periods, periods[1:])):
        return []
    return [_number(value) for value in series.tolist()]


def _ttm_pair(frame, column):
    values = _last_n(frame, column, 8)
    if len(values) != 8:
        return None, None
    return sum(values[-4:]), sum(values[-8:-4])


def _signal(code, title, detail, value=None):
    return {
        "code": code,
        "title": title,
        "detail": detail,
        "value": _rounded(value),
    }


def _pivot_financial(financial):
    if financial is None or financial.empty:
        return pd.DataFrame()
    work = financial[["date", "type", "value"]].copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["value"] = pd.to_numeric(work["value"], errors="coerce")
    work = work.dropna(subset=["date", "type"]).drop_duplicates(
        ["date", "type"], keep="last"
    )
    if work.empty:
        return pd.DataFrame()
    return work.pivot(index="date", columns="type", values="value").sort_index()


def _pick_column(frame, *names):
    for name in names:
        if name in frame and frame[name].notna().any():
            return name
    return None


def build_financial_analysis(financial, close=None, max_quarters=12):
    """Return compact quarterly metrics plus explainable health signals."""
    frame = _pivot_financial(financial)
    if frame.empty:
        return {
            "latest_date": "",
            "coverage": {"income": False, "balance": False, "cashflow": False},
            "metrics": [], "quarters": [], "highlights": [], "risks": [],
        }

    revenue_col = _pick_column(frame, "OperatingRevenueQuarter", "NetRevenueQuarter")
    net_income_col = _pick_column(
        frame, "ParentNetIncomeQuarter", "IncomeAfterTaxesQuarter"
    )
    equity_col = _pick_column(frame, "ParentEquity", "TotalEquity")

    revenue = frame[revenue_col] if revenue_col else pd.Series(index=frame.index, dtype=float)
    gross_profit = frame.get("GrossProfitQuarter", pd.Series(index=frame.index, dtype=float))
    operating_income = frame.get("OperatingIncomeQuarter", pd.Series(index=frame.index, dtype=float))
    net_income = frame[net_income_col] if net_income_col else pd.Series(index=frame.index, dtype=float)
    equity = frame[equity_col] if equity_col else pd.Series(index=frame.index, dtype=float)

    frame["CalculatedGrossMargin"] = gross_profit / revenue * 100.0
    frame["CalculatedOperatingMargin"] = operating_income / revenue * 100.0
    frame["CalculatedNetMargin"] = net_income / revenue * 100.0
    if {"TotalLiabilities", "TotalAssets"}.issubset(frame.columns):
        frame["CalculatedDebtRatio"] = (
            frame["TotalLiabilities"] / frame["TotalAssets"] * 100.0
        )
    else:
        frame["CalculatedDebtRatio"] = pd.Series(index=frame.index, dtype=float)
    if {"CurrentAssets", "CurrentLiabilities"}.issubset(frame.columns):
        frame["CalculatedCurrentRatio"] = (
            frame["CurrentAssets"] / frame["CurrentLiabilities"]
        )
    else:
        frame["CalculatedCurrentRatio"] = pd.Series(index=frame.index, dtype=float)

    ttm_revenue, previous_revenue = _ttm_pair(frame, revenue_col) if revenue_col else (None, None)
    ttm_eps, previous_eps = _ttm_pair(frame, "EPS")
    ttm_net_income, previous_net_income = _ttm_pair(frame, net_income_col) if net_income_col else (None, None)
    ttm_ocf, previous_ocf = _ttm_pair(frame, "OperatingCashFlowQuarter")

    revenue_growth = _growth(ttm_revenue, previous_revenue)
    eps_growth = _growth(ttm_eps, previous_eps)
    net_income_growth = _growth(ttm_net_income, previous_net_income)
    ocf_growth = _growth(ttm_ocf, previous_ocf)

    latest_gross_margin = _last_valid(frame, "CalculatedGrossMargin")
    latest_operating_margin = _last_valid(frame, "CalculatedOperatingMargin")
    latest_net_margin = _last_valid(frame, "CalculatedNetMargin")
    gross_margins = _last_n(frame, "CalculatedGrossMargin", 5)
    operating_margins = _last_n(frame, "CalculatedOperatingMargin", 5)
    gross_margin_yoy_change = (
        gross_margins[-1] - gross_margins[-5] if len(gross_margins) == 5 else None
    )
    operating_margin_yoy_change = (
        operating_margins[-1] - operating_margins[-5]
        if len(operating_margins) == 5 else None
    )

    latest_assets = _last_valid(frame, "TotalAssets")
    latest_liabilities = _last_valid(frame, "TotalLiabilities")
    latest_equity = _last_valid(frame, equity_col) if equity_col else None
    latest_debt_ratio = _ratio(latest_liabilities, latest_assets)
    latest_current_ratio = _ratio(
        _last_valid(frame, "CurrentAssets"),
        _last_valid(frame, "CurrentLiabilities"),
        scale=1.0,
    )
    latest_bvps = _last_valid(frame, "BookValuePerShare")
    latest_ending_cash = _last_valid(frame, "EndingCash")
    cash_quality = _ratio(ttm_ocf, ttm_net_income, scale=1.0)

    equity_values = [value for value in _series_values(frame, equity_col) if value is not None] if equity_col else []
    average_equity = (
        (equity_values[-1] + equity_values[-5]) / 2.0
        if len(equity_values) >= 5 else latest_equity
    )
    roe = _ratio(ttm_net_income, average_equity) if ttm_net_income is not None else None
    pe = _ratio(close, ttm_eps, scale=1.0) if _number(ttm_eps) and ttm_eps > 0 else None
    pb = _ratio(close, latest_bvps, scale=1.0) if _number(latest_bvps) and latest_bvps > 0 else None

    highlights, risks = [], []

    if revenue_growth is not None:
        if revenue_growth >= 10:
            highlights.append(_signal(
                "revenue_growth", "近四季營收成長",
                f"近四季營收較前四季增加 {_fmt_pct(revenue_growth)}。", revenue_growth,
            ))
        elif revenue_growth <= -10:
            risks.append(_signal(
                "revenue_decline", "近四季營收衰退",
                f"近四季營收較前四季減少 {_fmt_pct(abs(revenue_growth))}。", revenue_growth,
            ))

    if eps_growth is not None:
        if eps_growth >= 15:
            highlights.append(_signal(
                "eps_growth", "近四季 EPS 明顯成長",
                f"近四季 EPS 為 {ttm_eps:.2f} 元，較前四季增加 {_fmt_pct(eps_growth)}。",
                eps_growth,
            ))
        elif eps_growth <= -20:
            risks.append(_signal(
                "eps_decline", "近四季 EPS 明顯衰退",
                f"近四季 EPS 為 {ttm_eps:.2f} 元，較前四季減少 {_fmt_pct(abs(eps_growth))}。",
                eps_growth,
            ))

    recent_eps = _last_n(frame, "EPS", 4)
    if len(recent_eps) == 4 and all(value > 0 for value in recent_eps):
        highlights.append(_signal(
            "eps_positive", "最近四季持續獲利",
            "最近四個單季 EPS 都大於 0，獲利沒有中斷。", sum(recent_eps),
        ))
    if len(recent_eps) >= 2 and recent_eps[-1] < 0 and recent_eps[-2] < 0:
        risks.append(_signal(
            "eps_loss", "連續兩季虧損",
            f"最近兩季 EPS 分別為 {recent_eps[-2]:.2f}、{recent_eps[-1]:.2f} 元。",
            recent_eps[-1],
        ))

    if net_income_growth is not None:
        if net_income_growth >= 15:
            highlights.append(_signal(
                "profit_growth", "近四季淨利成長",
                f"近四季淨利較前四季增加 {_fmt_pct(net_income_growth)}。",
                net_income_growth,
            ))
        elif net_income_growth <= -20:
            risks.append(_signal(
                "profit_decline", "近四季淨利衰退",
                f"近四季淨利較前四季減少 {_fmt_pct(abs(net_income_growth))}。",
                net_income_growth,
            ))

    if gross_margin_yoy_change is not None:
        if gross_margin_yoy_change >= 2:
            highlights.append(_signal(
                "gross_margin_up", "毛利率較去年同期改善",
                f"最新單季毛利率 {_fmt_pct(latest_gross_margin)}，較去年同期增加 {gross_margin_yoy_change:.1f} 個百分點。",
                gross_margin_yoy_change,
            ))
        elif gross_margin_yoy_change <= -3:
            risks.append(_signal(
                "gross_margin_down", "毛利率較去年同期下滑",
                f"最新單季毛利率 {_fmt_pct(latest_gross_margin)}，較去年同期減少 {abs(gross_margin_yoy_change):.1f} 個百分點。",
                gross_margin_yoy_change,
            ))

    if operating_margin_yoy_change is not None:
        if operating_margin_yoy_change >= 2:
            highlights.append(_signal(
                "operating_margin_up", "營業利益率改善",
                f"最新單季營業利益率 {_fmt_pct(latest_operating_margin)}，較去年同期增加 {operating_margin_yoy_change:.1f} 個百分點。",
                operating_margin_yoy_change,
            ))
        elif operating_margin_yoy_change <= -3:
            risks.append(_signal(
                "operating_margin_down", "營業利益率下滑",
                f"最新單季營業利益率 {_fmt_pct(latest_operating_margin)}，較去年同期減少 {abs(operating_margin_yoy_change):.1f} 個百分點。",
                operating_margin_yoy_change,
            ))

    if roe is not None:
        if roe >= 15:
            highlights.append(_signal(
                "roe_high", "股東權益報酬率良好",
                f"近四季 ROE 約 {_fmt_pct(roe)}。", roe,
            ))
        elif roe < 0:
            risks.append(_signal(
                "roe_negative", "股東權益報酬率為負",
                f"近四季 ROE 約 {_fmt_pct(roe)}。", roe,
            ))

    if latest_equity is not None and latest_equity <= 0:
        risks.append(_signal(
            "negative_equity", "股東權益為負",
            f"最新股東權益為 {_fmt_money(latest_equity)}，財務結構風險高。",
            latest_equity,
        ))

    if latest_debt_ratio is not None:
        if latest_debt_ratio < 40:
            highlights.append(_signal(
                "low_debt", "負債比偏低",
                f"最新負債比為 {_fmt_pct(latest_debt_ratio)}。", latest_debt_ratio,
            ))
        elif latest_debt_ratio > 70:
            risks.append(_signal(
                "high_debt", "負債比偏高",
                f"最新負債比為 {_fmt_pct(latest_debt_ratio)}。", latest_debt_ratio,
            ))

    if latest_current_ratio is not None:
        if latest_current_ratio >= 1.5:
            highlights.append(_signal(
                "current_ratio_good", "短期償債能力充足",
                f"最新流動比率為 {latest_current_ratio:.2f} 倍。", latest_current_ratio,
            ))
        elif latest_current_ratio < 1.0:
            risks.append(_signal(
                "current_ratio_low", "短期償債能力需留意",
                f"最新流動比率僅 {latest_current_ratio:.2f} 倍。", latest_current_ratio,
            ))

    if cash_quality is not None and ttm_net_income is not None and ttm_net_income > 0:
        if cash_quality >= 0.8 and ttm_ocf > 0:
            highlights.append(_signal(
                "cash_quality_good", "獲利現金含量良好",
                f"近四季營業現金流為淨利的 {cash_quality:.2f} 倍。", cash_quality,
            ))
        elif cash_quality < 0.5:
            risks.append(_signal(
                "cash_quality_low", "獲利現金含量偏低",
                f"近四季營業現金流僅為淨利的 {cash_quality:.2f} 倍。", cash_quality,
            ))
    elif ttm_ocf is not None and ttm_net_income is not None and ttm_net_income > 0 and ttm_ocf < 0:
        risks.append(_signal(
            "ocf_negative", "有獲利但營業現金流為負",
            f"近四季淨利為 {_fmt_money(ttm_net_income)}，營業現金流為 {_fmt_money(ttm_ocf)}。",
            ttm_ocf,
        ))

    metrics = [
        {"key": "ttm_eps", "label": "近四季 EPS", "value": _rounded(ttm_eps), "unit": "元"},
        {"key": "revenue_growth", "label": "近四季營收年增", "value": _rounded(revenue_growth), "unit": "%"},
        {"key": "gross_margin", "label": "最新單季毛利率", "value": _rounded(latest_gross_margin), "unit": "%"},
        {"key": "operating_margin", "label": "最新單季營益率", "value": _rounded(latest_operating_margin), "unit": "%"},
        {"key": "net_margin", "label": "最新單季淨利率", "value": _rounded(latest_net_margin), "unit": "%"},
        {"key": "roe", "label": "近四季 ROE", "value": _rounded(roe), "unit": "%"},
        {"key": "debt_ratio", "label": "負債比", "value": _rounded(latest_debt_ratio), "unit": "%"},
        {"key": "current_ratio", "label": "流動比率", "value": _rounded(latest_current_ratio), "unit": "倍"},
        {"key": "cash_quality", "label": "營業現金流／淨利", "value": _rounded(cash_quality), "unit": "倍"},
        {"key": "book_value", "label": "每股淨值", "value": _rounded(latest_bvps), "unit": "元"},
        {"key": "pe", "label": "本益比", "value": _rounded(pe), "unit": "倍"},
        {"key": "pb", "label": "股價淨值比", "value": _rounded(pb), "unit": "倍"},
    ]
    metrics = [item for item in metrics if item["value"] is not None]

    quarters = []
    for report_date, row in frame.tail(max(int(max_quarters), 1)).iterrows():
        quarters.append({
            "date": report_date.strftime("%Y-%m-%d"),
            "revenue": _rounded(row.get(revenue_col)) if revenue_col else None,
            "gross_profit": _rounded(row.get("GrossProfitQuarter")),
            "operating_income": _rounded(row.get("OperatingIncomeQuarter")),
            "net_income": _rounded(row.get(net_income_col)) if net_income_col else None,
            "eps": _rounded(row.get("EPS")),
            "gross_margin_pct": _rounded(row.get("CalculatedGrossMargin")),
            "operating_margin_pct": _rounded(row.get("CalculatedOperatingMargin")),
            "net_margin_pct": _rounded(row.get("CalculatedNetMargin")),
            "total_assets": _rounded(row.get("TotalAssets")),
            "total_liabilities": _rounded(row.get("TotalLiabilities")),
            "equity": _rounded(row.get(equity_col)) if equity_col else None,
            "debt_ratio_pct": _rounded(row.get("CalculatedDebtRatio")),
            "current_ratio": _rounded(row.get("CalculatedCurrentRatio")),
            "book_value_per_share": _rounded(row.get("BookValuePerShare")),
            "operating_cash_flow": _rounded(row.get("OperatingCashFlowQuarter")),
            "investing_cash_flow": _rounded(row.get("InvestingCashFlowQuarter")),
            "financing_cash_flow": _rounded(row.get("FinancingCashFlowQuarter")),
            "ending_cash": _rounded(row.get("EndingCash")),
        })

    coverage = {
        "income": bool(revenue_col or "EPS" in frame),
        "balance": bool("TotalAssets" in frame or equity_col),
        "cashflow": bool("OperatingCashFlowQuarter" in frame),
        "income_quarters": int(frame[revenue_col].notna().sum()) if revenue_col else int(frame.get("EPS", pd.Series(dtype=float)).notna().sum()),
        "balance_quarters": int(frame.get("TotalAssets", pd.Series(dtype=float)).notna().sum()),
        "cashflow_quarters": int(frame.get("OperatingCashFlowQuarter", pd.Series(dtype=float)).notna().sum()),
    }

    return {
        "latest_date": frame.index[-1].strftime("%Y-%m-%d"),
        "coverage": coverage,
        "metrics": metrics,
        "quarters": quarters,
        "highlights": highlights,
        "risks": risks,
        "calculation_note": "依官方季報數值計算；累計損益與現金流已換算為單季。缺少資料時不產生判斷。",
        "extra": {
            "ttm_revenue": _rounded(ttm_revenue),
            "ttm_net_income": _rounded(ttm_net_income),
            "ttm_operating_cash_flow": _rounded(ttm_ocf),
            "ending_cash": _rounded(latest_ending_cash),
            "ocf_growth_pct": _rounded(ocf_growth),
        },
    }
