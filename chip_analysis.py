# -*- coding: utf-8 -*-
"""Explainable institutional and TDCC shareholding signals."""
from __future__ import annotations

import math

import pandas as pd


def _num(value, default=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _round(value, digits=2):
    value = _num(value)
    return round(value, digits) if value is not None else None


def _signal(code, title, detail, value=None):
    return {"code": code, "title": title, "detail": detail, "value": _round(value)}


def _consecutive(records, key):
    if not records:
        return 0
    direction = 1 if _num(records[-1].get(key), 0) > 0 else -1 if _num(records[-1].get(key), 0) < 0 else 0
    if not direction:
        return 0
    count = 0
    for row in reversed(records):
        value = _num(row.get(key), 0)
        if (value > 0) != (direction > 0) or value == 0:
            break
        count += 1
    return count * direction


def _aggregate_tdcc(distribution):
    if distribution is None or distribution.empty:
        return []
    work = distribution.copy()
    work["level"] = pd.to_numeric(work["level"], errors="coerce")
    work["ratio_pct"] = pd.to_numeric(work["ratio_pct"], errors="coerce")
    work["holders"] = pd.to_numeric(work["holders"], errors="coerce")
    output = []
    for report_date, group in work.groupby("date", sort=True):
        ratios = group.set_index("level")["ratio_pct"].to_dict()
        holders = group.set_index("level")["holders"].to_dict()
        output.append({
            "date": str(report_date),
            "large_400_pct": _round(sum(_num(ratios.get(level), 0) for level in range(12, 16))),
            "large_1000_pct": _round(_num(ratios.get(15), 0)),
            "retail_10_pct": _round(sum(_num(ratios.get(level), 0) for level in range(1, 4))),
            "large_400_holders": int(sum(_num(holders.get(level), 0) for level in range(12, 16))),
            "total_holders": int(_num(holders.get(17), 0)),
        })
    return output


def build_chip_analysis(institutional_records, distribution=None, price_records=None,
                        max_weeks=12):
    records = sorted(
        [dict(row) for row in (institutional_records or [])],
        key=lambda row: str(row.get("date", "")),
    )
    prices = sorted(
        [dict(row) for row in (price_records or [])],
        key=lambda row: str(row.get("date", "")),
    )
    weeks = _aggregate_tdcc(distribution)[-max(int(max_weeks), 1):]
    positive, cautions, score = [], [], 0

    recent5 = records[-5:]
    recent10 = records[-10:]
    keys = {
        "foreign": "foreign_net_lots",
        "trust": "trust_net_lots",
        "dealer": "dealer_net_lots",
    }
    totals5 = {
        name: sum(_num(row.get(key), 0) for row in recent5)
        for name, key in keys.items()
    }
    totals10 = {
        name: sum(_num(row.get(key), 0) for row in recent10)
        for name, key in keys.items()
    }
    foreign_streak = _consecutive(records, keys["foreign"])
    trust_streak = _consecutive(records, keys["trust"])

    if trust_streak >= 3:
        positive.append(_signal(
            "trust_buy_streak", "投信連續買超",
            f"投信已連續買超 {trust_streak} 個交易日，近 5 日合計 {totals5['trust']:,.0f} 張。",
            totals5["trust"],
        )); score += 20
    elif trust_streak <= -3:
        cautions.append(_signal(
            "trust_sell_streak", "投信連續賣超",
            f"投信已連續賣超 {abs(trust_streak)} 個交易日，近 5 日合計 {totals5['trust']:,.0f} 張。",
            totals5["trust"],
        )); score -= 20

    if foreign_streak >= 3:
        positive.append(_signal(
            "foreign_buy_streak", "外資連續買超",
            f"外資已連續買超 {foreign_streak} 個交易日，近 5 日合計 {totals5['foreign']:,.0f} 張。",
            totals5["foreign"],
        )); score += 15
    elif foreign_streak <= -3:
        cautions.append(_signal(
            "foreign_sell_streak", "外資連續賣超",
            f"外資已連續賣超 {abs(foreign_streak)} 個交易日，近 5 日合計 {totals5['foreign']:,.0f} 張。",
            totals5["foreign"],
        )); score -= 15

    total_net5 = sum(totals5.values())
    recent_dates = {str(row.get("date", "")) for row in recent5}
    volume5 = sum(
        _num(row.get("volume"), 0) / 1000.0
        for row in prices if str(row.get("date", "")) in recent_dates
    )
    net_volume_pct = total_net5 / volume5 * 100.0 if volume5 > 0 else None
    if net_volume_pct is not None and net_volume_pct >= 1.0:
        positive.append(_signal(
            "institutional_accumulation", "法人近 5 日明顯買超",
            f"三大法人近 5 日合計 {total_net5:,.0f} 張，約占同期成交量 {net_volume_pct:.1f}%。",
            net_volume_pct,
        )); score += 15
    elif net_volume_pct is not None and net_volume_pct <= -1.0:
        cautions.append(_signal(
            "institutional_distribution", "法人近 5 日明顯賣超",
            f"三大法人近 5 日合計 {total_net5:,.0f} 張，約占同期成交量 {net_volume_pct:.1f}%。",
            net_volume_pct,
        )); score -= 15

    latest_week = weeks[-1] if weeks else None
    previous_week = weeks[-2] if len(weeks) >= 2 else None
    if latest_week and previous_week:
        large400_change = latest_week["large_400_pct"] - previous_week["large_400_pct"]
        large1000_change = latest_week["large_1000_pct"] - previous_week["large_1000_pct"]
        retail10_change = latest_week["retail_10_pct"] - previous_week["retail_10_pct"]
        latest_week.update({
            "large_400_change_pct": _round(large400_change),
            "large_1000_change_pct": _round(large1000_change),
            "retail_10_change_pct": _round(retail10_change),
        })
        if large400_change >= 0.5:
            positive.append(_signal(
                "large_holder_increase", "集保大戶持股增加",
                f"400 張以上持股比率一週增加 {large400_change:.2f} 個百分點。",
                large400_change,
            )); score += 25
        elif large400_change <= -0.5:
            cautions.append(_signal(
                "large_holder_decrease", "集保大戶持股減少",
                f"400 張以上持股比率一週減少 {abs(large400_change):.2f} 個百分點。",
                large400_change,
            )); score -= 25
        if large1000_change >= 0.3:
            positive.append(_signal(
                "super_holder_increase", "千張大戶持股增加",
                f"1,000 張以上持股比率一週增加 {large1000_change:.2f} 個百分點。",
                large1000_change,
            )); score += 15
        elif large1000_change <= -0.3:
            cautions.append(_signal(
                "super_holder_decrease", "千張大戶持股減少",
                f"1,000 張以上持股比率一週減少 {abs(large1000_change):.2f} 個百分點。",
                large1000_change,
            )); score -= 15
        if large400_change > 0 and retail10_change < 0:
            positive.append(_signal(
                "concentration_up", "籌碼往大戶集中",
                f"大戶增加 {large400_change:.2f}、10 張以下散戶減少 {abs(retail10_change):.2f} 個百分點。",
                large400_change,
            )); score += 10
        elif large400_change < 0 and retail10_change > 0:
            cautions.append(_signal(
                "concentration_down", "籌碼往散戶分散",
                f"大戶減少 {abs(large400_change):.2f}、10 張以下散戶增加 {retail10_change:.2f} 個百分點。",
                large400_change,
            )); score -= 10

    score = max(-100, min(100, score))
    if score >= 25:
        status, label = "positive", "籌碼偏多"
    elif score <= -25:
        status, label = "attention", "籌碼留意"
    elif positive and cautions:
        status, label = "mixed", "多空混合"
    else:
        status, label = "neutral", "籌碼中性"

    metrics = [
        {"key": "foreign_5d", "label": "外資近5日", "value": _round(totals5["foreign"]), "unit": "張"},
        {"key": "trust_5d", "label": "投信近5日", "value": _round(totals5["trust"]), "unit": "張"},
        {"key": "institutional_5d", "label": "法人近5日", "value": _round(total_net5), "unit": "張"},
        {"key": "institutional_volume_pct", "label": "買賣超／成交量", "value": _round(net_volume_pct), "unit": "%"},
    ]
    if latest_week:
        metrics.extend([
            {"key": "large_400_pct", "label": "400張以上大戶", "value": latest_week["large_400_pct"], "unit": "%"},
            {"key": "large_1000_pct", "label": "千張以上大戶", "value": latest_week["large_1000_pct"], "unit": "%"},
            {"key": "retail_10_pct", "label": "10張以下散戶", "value": latest_week["retail_10_pct"], "unit": "%"},
            {"key": "large_400_change", "label": "大戶週增減", "value": latest_week.get("large_400_change_pct"), "unit": "百分點"},
        ])
    metrics = [item for item in metrics if item["value"] is not None]
    return {
        "status": status, "status_label": label, "score": score,
        "positive_signals": positive, "cautions": cautions,
        "metrics": metrics, "weeks": weeks,
        "coverage": {
            "institutional": bool(records), "tdcc": bool(weeks),
            "tdcc_weeks": len(weeks),
        },
        "calculation_note": "法人為每日買賣超；集保集中度以 TDCC 每週 400 張以上持股比率計算，不等同券商分點集中度。",
        "extra": {"institutional_10d": totals10},
    }
