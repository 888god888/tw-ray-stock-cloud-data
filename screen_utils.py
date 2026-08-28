# -*- coding: utf-8 -*-
"""篩選指標計算與GUI設定工具函式。"""
import json
import os
from pathlib import Path

import pandas as pd
import numpy as np


def load_ui_layout(primary_path, legacy_path=None):
    """讀取版面設定，必要時相容舊路徑。"""
    paths = [Path(primary_path)]
    if legacy_path is not None:
        paths.append(Path(legacy_path))
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except (OSError, ValueError, TypeError):
            continue
    return {}


def save_ui_layout(path, payload):
    """以原子替換寫入並重新讀取驗證，失敗時由呼叫端顯示錯誤。"""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, destination)
        verified = json.loads(destination.read_text(encoding="utf-8"))
        if verified != payload:
            raise OSError("寫入後驗證不一致")
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return destination


def result_sort_value(column, value):
    """將 GUI 結果表的顯示值轉成正確排序鍵。

    價格、成交量與符合條件數以數值排序，避免「100」被排在「99」前後錯誤；
    名稱、產業別與代碼則以文字排序。
    """
    text = str(value).strip()
    if column == "match":
        text = text.split("/", 1)[0] if "/" in text else text
    if column in {"close", "volume", "capital_billion", "change_pct", "match"}:
        try:
            return float(text.replace(",", "").replace("%", ""))
        except ValueError:
            return float("-inf")
    return text.casefold()


def sort_result_records(records, sort_specs):
    """依多層排序規則穩定排序；sort_specs 前面的欄位優先級較高。

    sort_specs: [(column, descending), ...]
    """
    result = list(records)
    for column, descending in reversed(list(sort_specs or [])):
        result.sort(
            key=lambda row: result_sort_value(column, row.get(column, "")),
            reverse=bool(descending),
        )
    return result


def format_volume_lots(shares) -> str:
    """將官方成交股數轉為張數，保留一位以顯示零股造成的小數。"""
    try:
        return f"{float(shares) / 1000:,.1f}"
    except (TypeError, ValueError):
        return "0.0"


def format_capital_billion(paid_in_capital) -> str:
    """將官方實收資本額（元）轉為億元；缺資料時不誤顯示為 0。"""
    try:
        if paid_in_capital is None or pd.isna(paid_in_capital):
            return "—"
        return f"{float(paid_in_capital) / 100_000_000:,.2f}"
    except (TypeError, ValueError):
        return "—"


def latest_change_pct(price_df: pd.DataFrame):
    """最新收盤價相對前一交易日收盤價的漲跌幅（市場慣用定義）。"""
    if price_df is None or len(price_df) < 2 or "close" not in price_df:
        return None
    closes = pd.to_numeric(price_df["close"], errors="coerce").dropna()
    if len(closes) < 2:
        return None
    previous_close = float(closes.iloc[-2])
    latest_close = float(closes.iloc[-1])
    if previous_close <= 0:
        return None
    return (latest_close / previous_close - 1.0) * 100.0


def format_change_pct(change_pct) -> str:
    """以 +1.23% / -1.23% 顯示漲跌幅。"""
    try:
        if change_pct is None or pd.isna(change_pct):
            return "—"
        return f"{float(change_pct):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def lots_to_shares(lots) -> float:
    """GUI 以張輸入，查詢 SQLite 前轉回官方的股數單位。"""
    return float(lots) * 1000


def ma_alignment_status(price_df: pd.DataFrame):
    """
    回傳目前均線排列狀態字串。
    price_df 需含 close 欄，且已依日期排序（舊到新）。
    """
    if len(price_df) < 21:
        return "資料不足", False, False
    df = price_df.copy()
    df["MA5"] = df["close"].rolling(5).mean()
    df["MA10"] = df["close"].rolling(10).mean()
    df["MA20"] = df["close"].rolling(20).mean()
    last = df.iloc[-1]
    if pd.isna(last["MA20"]):
        return "資料不足", False, False

    bull_2 = last["MA5"] > last["MA10"]       # 2線多頭排列 (5>10)
    bull_3 = last["MA5"] > last["MA10"] > last["MA20"]  # 3線多頭排列 (5>10>20)
    bear_2 = last["MA5"] < last["MA10"]
    bear_3 = last["MA5"] < last["MA10"] < last["MA20"]

    if bull_3:
        status = "3線多頭排列 (MA5>MA10>MA20)"
    elif bull_2:
        status = "2線多頭排列 (MA5>MA10)"
    elif bear_3:
        status = "3線空頭排列 (MA5<MA10<MA20)"
    elif bear_2:
        status = "2線空頭排列 (MA5<MA10)"
    else:
        status = "均線糾結/無明確排列"
    return status, bull_2, bull_3


def is_n_day_new_low(price_df: pd.DataFrame, n: int) -> bool:
    """判斷今日最低價是否嚴格跌破前 n 個交易日最低價（不含今日）。"""
    n = max(1, int(n))
    if price_df is None or price_df.empty or len(price_df) < n + 1:
        return False
    prior_low = pd.to_numeric(price_df["min"].iloc[-n - 1:-1], errors="coerce").min()
    latest_low = pd.to_numeric(pd.Series([price_df["min"].iloc[-1]]), errors="coerce").iloc[0]
    return bool(pd.notna(prior_low) and pd.notna(latest_low) and latest_low < prior_low)


def revenue_mom_yoy(revenue_df: pd.DataFrame):
    """
    輸入月營收 df (欄位: date, revenue, revenue_month, revenue_year)，
    依日期排序後回傳最新一筆的 (revenue, MoM%, YoY%)。
    """
    if revenue_df is None or revenue_df.empty:
        return None, None, None
    df = revenue_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    if len(df) < 2:
        return (df["revenue"].iloc[-1] if len(df) else None), None, None

    latest = df.iloc[-1]
    prev_month = df.iloc[-2]
    mom = (latest["revenue"] - prev_month["revenue"]) / prev_month["revenue"] * 100 \
        if prev_month["revenue"] else None

    # 找去年同月
    yoy = None
    same_month_last_year = df[
        (df["revenue_year"] == latest["revenue_year"] - 1) &
        (df["revenue_month"] == latest["revenue_month"])
    ]
    if not same_month_last_year.empty and same_month_last_year["revenue"].iloc[0]:
        base = same_month_last_year["revenue"].iloc[0]
        yoy = (latest["revenue"] - base) / base * 100

    return latest["revenue"], mom, yoy


def eps_last_4_quarters(fin_df: pd.DataFrame):
    """
    輸入綜合損益表長表 (欄位: date, type, value)，取出 type == 'EPS' 的
    最近4季，回傳 (list_of_eps_新到舊, all_positive: bool, sum_eps: float)
    """
    if fin_df is None or fin_df.empty:
        return [], False, None
    df = fin_df[fin_df["type"] == "EPS"].copy()
    if df.empty:
        return [], False, None
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    last4 = df.tail(4)
    eps_list = last4["value"].tolist()
    all_positive = all(v > 0 for v in eps_list) if eps_list else False
    total = sum(eps_list) if eps_list else None
    return list(reversed(eps_list)), all_positive, total  # 新到舊


def investment_trust_buy_days(chip_df: pd.DataFrame, lookback: int):
    """
    輸入三大法人買賣超長表 (欄位: date, stock_id, buy, name, sell)，
    篩出 name == 'Investment_Trust'（投信），計算近 lookback 個交易日
    的『買超天數』與『是否連續買超』。
    回傳 (net_buy_days_count, max_consecutive_buy_days, total_net_shares)
    """
    if chip_df is None or chip_df.empty:
        return 0, 0, 0
    df = chip_df[chip_df["name"] == "Investment_Trust"].copy()
    if df.empty:
        return 0, 0, 0
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").tail(lookback)
    df["net"] = df["buy"].astype(float) - df["sell"].astype(float)

    net_buy_days = int((df["net"] > 0).sum())
    total_net = float(df["net"].sum())

    # 計算「最近連續買超天數」(由最新一天往回算)
    consec = 0
    for v in df["net"].values[::-1]:
        if v > 0:
            consec += 1
        else:
            break

    return net_buy_days, consec, total_net
