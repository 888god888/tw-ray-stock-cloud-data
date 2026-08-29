# -*- coding: utf-8 -*-
"""Official TWSE / TPEx / MOPS data adapters.

This module deliberately has no FinMind or OpenAI dependency.  Public exchange
responses are converted to the column names already used by ``conditions.py``.
Historical downloads are throttled and resumable through ``DataStore``.
"""
from __future__ import annotations

import html
import http.cookiejar
import http.client
import io
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Callable, Iterable, Optional

import pandas as pd

from data_store import DataStore


TWSE_OPENAPI = "https://openapi.twse.com.tw/v1"
TPEX_OPENAPI = "https://www.tpex.org.tw/openapi/v1"
TWSE_REPORT = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_REPORT = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
TWSE_INSTITUTIONAL_REPORT = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_INSTITUTIONAL_REPORT = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
MOPS_BASE = "https://mopsov.twse.com.tw"
MOPS_LEGACY_BASE = "https://mops.twse.com.tw"

ProgressCallback = Optional[Callable[[str, int, int], None]]


# 證交所／櫃買中心共用的證券產業別代碼。
# 07、13 為歷史匯總類別，舊資料庫仍可能出現，因此保留對照。
INDUSTRY_CODE_NAMES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "07": "化學生技醫療", "08": "玻璃陶瓷",
    "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業",
    "13": "電子工業", "14": "建材營造", "15": "航運業", "16": "觀光餐旅",
    "17": "金融保險", "18": "貿易百貨", "19": "綜合", "20": "其他",
    "21": "化學工業", "22": "生技醫療業", "23": "油電燃氣業", "24": "半導體業",
    "25": "電腦及週邊設備業", "26": "光電業", "27": "通信網路業", "28": "電子零組件業",
    "29": "電子通路業", "30": "資訊服務業", "31": "其他電子業", "32": "文化創意業",
    "33": "農業科技業", "34": "電子商務", "35": "綠能環保", "36": "數位雲端",
    "37": "運動休閒", "38": "居家生活", "80": "管理股票", "91": "存託憑證",
}


def industry_name(value) -> str:
    """將官方產業代碼轉為中文；已是中文時原樣保留。"""
    text = _strip_html(value)
    if not text:
        return "未分類"
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        code = str(int(float(text))).zfill(2)
        return INDUSTRY_CODE_NAMES.get(code, f"未分類（代碼 {code}）")
    return text


class OfficialDataError(RuntimeError):
    pass


class OfficialResponse:
    """Small response wrapper used by the standard-library HTTP client."""

    def __init__(self, content: bytes, headers=None, url: str = ""):
        self.content = content
        self.headers = headers or {}
        self.url = url
        content_type = self.headers.get("Content-Type", "")
        match = re.search(r"charset=([\w-]+)", content_type, flags=re.IGNORECASE)
        self.encoding = match.group(1) if match else None
        self.apparent_encoding = None

    def json(self):
        # 官方 OpenAPI 維護或 WAF 阻擋時會回 HTTP 200 + HTML。
        # 先辨識這類回應，避免最後誤報成 Big5/JSON 編碼錯誤。
        probe = self.content.lstrip()[:2000]
        if probe.startswith(b"<"):
            text = probe.decode("utf-8", errors="replace")
            if "FOR SECURITY REASONS" in text or "安全性考量" in text:
                raise OfficialDataError(f"官方網站安全機制暫時阻擋請求（{self.url}）")
            if "Maintainance" in text or "Maintenance" in text or "系統維護" in text:
                raise OfficialDataError(f"官方網站維護中（{self.url}）")
            raise OfficialDataError(f"官方 API 回傳 HTML 而非 JSON（{self.url}）")
        last_error = None
        for encoding in (self.encoding, "utf-8-sig", "utf-8", "big5"):
            if not encoding:
                continue
            try:
                return json.loads(self.content.decode(encoding))
            except (UnicodeError, json.JSONDecodeError) as exc:
                last_error = exc
        raise OfficialDataError(f"官方 JSON 無法解析（{self.url}）：{last_error}")


def _strip_html(value) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("\u3000", " ").replace("\xa0", " ").strip()


def _normalise_label(value) -> str:
    return re.sub(r"[\s\-－_（）()/%％]", "", _strip_html(value)).lower()


def _number(value, integer: bool = False):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return int(value) if integer else float(value)
    text = _strip_html(value).replace(",", "").replace("+", "")
    if text in {"", "-", "--", "---", "nan", "N/A", "NA", "不適用"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    result = float(match.group())
    if negative:
        result = -abs(result)
    return int(result) if integer else result


def _stock_code(value) -> str:
    text = _strip_html(value).replace('="', "").replace('"', "").strip()
    match = re.search(r"\d{4,6}[A-Za-z]?", text)
    return match.group(0) if match else ""


def _is_common_stock(code: str) -> bool:
    # Current screener targets listed/OTC operating companies, not ETF, ETN,
    # warrants, preferred shares or convertible bonds.
    return bool(re.fullmatch(r"\d{4}", code)) and not code.startswith("00")


def roc_date_to_iso(value, fallback: Optional[date] = None) -> str:
    text = _strip_html(value)
    digits = re.sub(r"\D", "", text)
    try:
        if len(digits) == 7:  # 1150813
            year = int(digits[:3]) + 1911
            return f"{year:04d}-{digits[3:5]}-{digits[5:7]}"
        if len(digits) == 8:  # 20260813
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        parts = re.findall(r"\d+", text)
        if len(parts) >= 3:
            year = int(parts[0])
            if year < 1911:
                year += 1911
            return f"{year:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    except (ValueError, IndexError):
        pass
    return (fallback or date.today()).isoformat()


def _row_value(row: dict, aliases: Iterable[str], default=""):
    normalised = {_normalise_label(k): v for k, v in row.items()}
    for alias in aliases:
        key = _normalise_label(alias)
        if key in normalised:
            return normalised[key]
    for alias in aliases:
        key = _normalise_label(alias)
        for actual, value in normalised.items():
            if key and key in actual:
                return value
    return default


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    labels = []
    for col in work.columns:
        parts = col if isinstance(col, tuple) else (col,)
        clean = []
        for part in parts:
            text = _strip_html(part)
            if text and not text.lower().startswith("unnamed") and text not in clean:
                clean.append(text)
        labels.append("-".join(clean))
    work.columns = labels
    return work


def _find_column(columns: Iterable[str], includes: Iterable[str], excludes: Iterable[str] = ()):
    for col in columns:
        label = _normalise_label(col)
        if all(_normalise_label(x) in label for x in includes) and not any(
            _normalise_label(x) in label for x in excludes
        ):
            return col
    return None


class OfficialMarketClient:
    """Low-level HTTP/parsing client for official public data."""

    def __init__(self, request_interval: float = 0.25, timeout: int = 40):
        self.request_interval = max(float(request_interval), 0.0)
        self.timeout = timeout
        self._last_request_at = 0.0
        self._request_lock = threading.Lock()
        self.default_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; TWStockScreener/2.0; personal research)",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.5",
        }
        cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar)
        )

    def _throttle(self):
        with self._request_lock:
            wait = self.request_interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()

    def _request(self, method: str, url: str, params=None, data=None,
                 headers=None) -> OfficialResponse:
        if params:
            query = urllib.parse.urlencode(params)
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{query}"
        payload = None
        request_headers = dict(self.default_headers)
        request_headers.update(headers or {})
        if data is not None:
            payload = urllib.parse.urlencode(data).encode("utf-8")
            request_headers.setdefault(
                "Content-Type", "application/x-www-form-urlencoded; charset=UTF-8"
            )

        retry_statuses = {429, 500, 502, 503, 504}
        last_error = None
        for attempt in range(4):
            self._throttle()
            request = urllib.request.Request(
                url, data=payload, headers=request_headers, method=method
            )
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    return OfficialResponse(response.read(), response.headers, response.geturl())
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in retry_statuses or attempt == 3:
                    break
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == 3:
                    break
            time.sleep(0.8 * (2 ** attempt))
        raise OfficialDataError(f"官方資料連線失敗（{url}）：{last_error}")

    def _get(self, url: str, **kwargs) -> OfficialResponse:
        return self._request("GET", url, **kwargs)

    def _post(self, url: str, **kwargs) -> OfficialResponse:
        return self._request("POST", url, **kwargs)

    @staticmethod
    def parse_company_rows(rows, market: str) -> pd.DataFrame:
        output = []
        for row in rows or []:
            code = _stock_code(_row_value(row, ("公司代號", "SecuritiesCompanyCode", "Code")))
            if not _is_common_stock(code):
                continue
            output.append({
                "stock_id": code,
                "stock_name": _strip_html(_row_value(row, ("公司簡稱", "公司名稱", "CompanyName", "Name"))),
                "market": market,
                "industry_category": industry_name(_row_value(
                    row, ("產業別", "產業類別", "Industry", "IndustryCode",
                          "IndustryCategory", "SecuritiesIndustryCode")
                )),
                # 上市／上櫃公司基本資料的實收資本額，官方單位為元。
                "paid_in_capital": _number(_row_value(
                    row, (
                        "實收資本額", "實收資本額(元)", "實收資本額(新台幣元)",
                        "PaidInCapital", "Paidin.Capital.NTD", "PaidinCapitalNTD",
                    )
                )),
            })
        return pd.DataFrame(output)

    def fetch_stock_info(self) -> pd.DataFrame:
        frames = []
        errors = []
        endpoints = (
            (f"{TWSE_OPENAPI}/opendata/t187ap03_L", "TWSE"),
            (f"{TPEX_OPENAPI}/mopsfin_t187ap03_O", "TPEx"),
        )
        for url, market in endpoints:
            try:
                rows = self._get(url).json()
                frames.append(self.parse_company_rows(rows, market))
            except Exception as exc:
                errors.append(f"{market}: {exc}")
        result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if result.empty:
            detail = "；".join(errors) if errors else "官方回應成功，但沒有可辨識的普通股資料"
            raise OfficialDataError("股票基本資料下載失敗：" + detail)
        return result.drop_duplicates("stock_id", keep="last")

    @staticmethod
    def parse_twse_openapi_prices(rows, fallback_date: Optional[date] = None) -> pd.DataFrame:
        output = []
        for row in rows or []:
            code = _stock_code(_row_value(row, ("Code", "證券代號")))
            if not _is_common_stock(code):
                continue
            close = _number(_row_value(row, ("ClosingPrice", "收盤價")))
            if close is None or close <= 0:
                continue
            output.append({
                "stock_id": code,
                "stock_name": _strip_html(_row_value(row, ("Name", "證券名稱"))),
                "date": roc_date_to_iso(_row_value(row, ("Date", "日期")), fallback_date),
                "market": "TWSE",
                "open": _number(_row_value(row, ("OpeningPrice", "開盤價"))),
                "max": _number(_row_value(row, ("HighestPrice", "最高價"))),
                "min": _number(_row_value(row, ("LowestPrice", "最低價"))),
                "close": close,
                "Trading_Volume": _number(_row_value(row, ("TradeVolume", "成交股數")), True),
                "Trading_money": _number(_row_value(row, ("TradeValue", "成交金額")), True),
                "spread": _number(_row_value(row, ("Change", "漲跌價差"))),
                "Trading_turnover": _number(_row_value(row, ("Transaction", "成交筆數")), True),
            })
        return pd.DataFrame(output)

    @staticmethod
    def parse_tpex_openapi_prices(rows, fallback_date: Optional[date] = None) -> pd.DataFrame:
        output = []
        for row in rows or []:
            code = _stock_code(_row_value(row, ("SecuritiesCompanyCode", "代號", "Code")))
            if not _is_common_stock(code):
                continue
            close = _number(_row_value(row, ("Close", "收盤")))
            if close is None or close <= 0:
                continue
            output.append({
                "stock_id": code,
                "stock_name": _strip_html(_row_value(row, ("CompanyName", "名稱", "Name"))),
                "date": roc_date_to_iso(_row_value(row, ("Date", "日期")), fallback_date),
                "market": "TPEx",
                "open": _number(_row_value(row, ("Open", "開盤"))),
                "max": _number(_row_value(row, ("High", "最高"))),
                "min": _number(_row_value(row, ("Low", "最低"))),
                "close": close,
                "Trading_Volume": _number(_row_value(row, ("TradingShares", "成交股數")), True),
                "Trading_money": _number(_row_value(row, ("TransactionAmount", "成交金額")), True),
                "spread": _number(_row_value(row, ("Change", "漲跌"))),
                "Trading_turnover": _number(_row_value(row, ("TransactionNumber", "成交筆數")), True),
            })
        return pd.DataFrame(output)

    def fetch_latest_prices(self) -> pd.DataFrame:
        twse = self.parse_twse_openapi_prices(
            self._get(f"{TWSE_OPENAPI}/exchangeReport/STOCK_DAY_ALL").json(), date.today()
        )
        tpex = self.parse_tpex_openapi_prices(
            self._get(f"{TPEX_OPENAPI}/tpex_mainboard_daily_close_quotes").json(), date.today()
        )
        return pd.concat([twse, tpex], ignore_index=True)

    @staticmethod
    def _table_candidates(payload: dict):
        for table in payload.get("tables", []) or []:
            fields = table.get("fields") or table.get("columns") or []
            data = table.get("data") or table.get("rows") or []
            if fields and data:
                yield fields, data
        for key, fields in payload.items():
            match = re.fullmatch(r"fields(\d*)", str(key))
            if match and isinstance(fields, list):
                data = payload.get(f"data{match.group(1)}")
                if isinstance(data, list):
                    yield fields, data
        if isinstance(payload.get("aaData"), list):
            fields = payload.get("fields") or payload.get("columns") or []
            if fields:
                yield fields, payload["aaData"]

    @staticmethod
    def _parse_historical_table(payload: dict, market: str, trade_date: date) -> pd.DataFrame:
        chosen = None
        saw_nonempty_table = False
        for fields, data in OfficialMarketClient._table_candidates(payload):
            saw_nonempty_table = saw_nonempty_table or bool(data)
            labels = [_normalise_label(x) for x in fields]
            has_code = any(x in labels for x in ("證券代號", "代號", "公司代號"))
            has_close = any(x in labels for x in ("收盤價", "收盤"))
            if has_code and has_close:
                chosen = (fields, data)
                break
        if not chosen:
            if saw_nonempty_table:
                raise OfficialDataError(
                    f"{market} {trade_date} 官方報表有資料，但找不到股票 OHLC 欄位"
                )
            return pd.DataFrame()

        fields, rows = chosen
        output = []
        for values in rows:
            row = dict(zip(fields, values))
            code = _stock_code(_row_value(row, ("證券代號", "代號", "公司代號")))
            if not _is_common_stock(code):
                continue
            close = _number(_row_value(row, ("收盤價", "收盤")))
            if close is None or close <= 0:
                continue
            spread = _number(_row_value(row, ("漲跌價差", "漲跌")))
            sign = _strip_html(_row_value(row, ("漲跌(+/-)", "漲跌符號")))
            if spread is not None and "-" in sign:
                spread = -abs(spread)
            output.append({
                "stock_id": code,
                "stock_name": _strip_html(_row_value(row, ("證券名稱", "名稱", "公司名稱"))),
                "date": trade_date.isoformat(),
                "market": market,
                "open": _number(_row_value(row, ("開盤價", "開盤"))),
                "max": _number(_row_value(row, ("最高價", "最高"))),
                "min": _number(_row_value(row, ("最低價", "最低"))),
                "close": close,
                "Trading_Volume": _number(_row_value(row, ("成交股數",)), True),
                "Trading_money": _number(_row_value(row, ("成交金額",)), True),
                "spread": spread,
                "Trading_turnover": _number(_row_value(row, ("成交筆數",)), True),
            })
        return pd.DataFrame(output)

    def fetch_prices_by_date(self, trade_date: date, market: str) -> pd.DataFrame:
        if market == "TWSE":
            payload = self._get(
                TWSE_REPORT,
                params={
                    "response": "json",
                    "date": trade_date.strftime("%Y%m%d"),
                    "type": "ALLBUT0999",
                },
            ).json()
        elif market == "TPEx":
            payload = self._get(
                TPEX_REPORT,
                params={
                    "response": "json",
                    "date": trade_date.strftime("%Y/%m/%d"),
                    "id": "",
                },
                headers={"Referer": "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/pricing.html"},
            ).json()
        else:
            raise ValueError(f"未知市場: {market}")
        return self._parse_historical_table(payload, market, trade_date)

    @staticmethod
    def _institutional_record(stock_id: str, stock_name: str, report_date: str,
                              market: str, foreign, trust, dealer_self,
                              dealer_hedge, dealer_total=None, total_net=None) -> dict:
        def group(values):
            buy = _number(values[0], True) or 0
            sell = _number(values[1], True) or 0
            official_net = _number(values[2], True) if len(values) > 2 else None
            return buy, sell, official_net if official_net is not None else buy - sell

        foreign_buy, foreign_sell, foreign_net = group(foreign)
        trust_buy, trust_sell, trust_net = group(trust)
        self_buy, self_sell, self_net = group(dealer_self)
        hedge_buy, hedge_sell, hedge_net = group(dealer_hedge)
        if dealer_total is None:
            dealer_buy = self_buy + hedge_buy
            dealer_sell = self_sell + hedge_sell
            dealer_net = self_net + hedge_net
        else:
            dealer_buy, dealer_sell, dealer_net = group(dealer_total)
        official_total = _number(total_net, True)
        return {
            "stock_id": stock_id, "stock_name": stock_name,
            "date": report_date, "market": market,
            "foreign_buy": foreign_buy, "foreign_sell": foreign_sell,
            "foreign_net": foreign_net,
            "trust_buy": trust_buy, "trust_sell": trust_sell,
            "trust_net": trust_net,
            "dealer_total_buy": dealer_buy, "dealer_total_sell": dealer_sell,
            "dealer_total_net": dealer_net,
            "dealer_self_buy": self_buy, "dealer_self_sell": self_sell,
            "dealer_self_net": self_net,
            "dealer_hedge_buy": hedge_buy, "dealer_hedge_sell": hedge_sell,
            "dealer_hedge_net": hedge_net,
            "total_net": official_total if official_total is not None
                         else foreign_net + trust_net + dealer_net,
        }

    @staticmethod
    def parse_twse_institutional(payload: dict, fallback_date: date) -> pd.DataFrame:
        """Parse TWSE T86 whole-market institutional report (unit: shares)."""
        rows = payload.get("data") or []
        if not rows:
            return pd.DataFrame()
        if len(rows[0]) < 19:
            raise OfficialDataError("TWSE 三大法人報表欄位不足，官方格式可能已變更")
        report_date = roc_date_to_iso(payload.get("date", ""), fallback_date)
        output = []
        for values in rows:
            if len(values) < 19:
                continue
            code = _stock_code(values[0])
            if not _is_common_stock(code):
                continue
            output.append(OfficialMarketClient._institutional_record(
                code, _strip_html(values[1]), report_date, "TWSE",
                foreign=values[2:5], trust=values[8:11],
                dealer_self=values[12:15], dealer_hedge=values[15:18],
                dealer_total=[
                    (_number(values[12], True) or 0) + (_number(values[15], True) or 0),
                    (_number(values[13], True) or 0) + (_number(values[16], True) or 0),
                    values[11],
                ],
                total_net=values[18],
            ))
        return pd.DataFrame(output)

    @staticmethod
    def parse_tpex_institutional(payload: dict, fallback_date: date) -> pd.DataFrame:
        """Parse TPEx whole-market institutional report (unit: shares)."""
        chosen = None
        for table in payload.get("tables", []) or []:
            rows = table.get("data") or []
            if rows and len(rows[0]) >= 24:
                chosen = table
                break
        if chosen is None:
            return pd.DataFrame()
        report_date = roc_date_to_iso(chosen.get("date", ""), fallback_date)
        output = []
        for values in chosen.get("data", []):
            if len(values) < 24:
                continue
            code = _stock_code(values[0])
            if not _is_common_stock(code):
                continue
            output.append(OfficialMarketClient._institutional_record(
                code, _strip_html(values[1]), report_date, "TPEx",
                foreign=values[2:5], trust=values[11:14],
                dealer_self=values[14:17], dealer_hedge=values[17:20],
                dealer_total=values[20:23], total_net=values[23],
            ))
        return pd.DataFrame(output)

    def fetch_institutional_by_date(self, trade_date: date, market: str) -> pd.DataFrame:
        if market == "TWSE":
            payload = self._get(
                TWSE_INSTITUTIONAL_REPORT,
                params={
                    "response": "json",
                    "date": trade_date.strftime("%Y%m%d"),
                    "selectType": "ALLBUT0999",
                },
                headers={"Referer": "https://www.twse.com.tw/zh/trading/foreign/t86.html"},
            ).json()
            return self.parse_twse_institutional(payload, trade_date)
        if market == "TPEx":
            payload = self._get(
                TPEX_INSTITUTIONAL_REPORT,
                params={
                    "response": "json",
                    "date": trade_date.strftime("%Y/%m/%d"),
                    "type": "Daily",
                },
                headers={
                    "Referer": "https://www.tpex.org.tw/zh-tw/mainboard/trading/major-institutional/detail/day.html"
                },
            ).json()
            return self.parse_tpex_institutional(payload, trade_date)
        raise ValueError(f"未知市場: {market}")

    @staticmethod
    def _read_html_tables(response: OfficialResponse):
        raw = response.content
        probe = raw[:4000].decode("utf-8", errors="replace")
        if "FOR SECURITY REASONS" in probe or "安全性考量" in probe:
            raise OfficialDataError("官方網站安全機制暫時阻擋請求")
        if "Maintainance" in probe or "Maintenance" in probe or "系統維護" in probe:
            raise OfficialDataError("官方網站維護中")
        last_error = None
        candidates = []
        encodings = (
            response.encoding, response.apparent_encoding,
            "big5", "cp950", "utf-8-sig", "utf-8",
        )
        seen = set()
        for encoding in encodings:
            if not encoding:
                continue
            encoding = encoding.lower()
            if encoding in seen:
                continue
            seen.add(encoding)
            try:
                # 不使用 errors='replace'：Big5 若先被當 UTF-8 解碼，表格仍能
                # 被 pandas 讀到，但中文欄名會全部壞掉，最後誤判成空資料。
                text = raw.decode(encoding, errors="strict")
                tables = pd.read_html(io.StringIO(text))
                cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
                marker_score = sum(
                    1000 for marker in ("公司代號", "當月營收", "基本每股盈餘")
                    if marker in text
                )
                candidates.append((marker_score + cjk_count, tables))
            except (ValueError, UnicodeError) as exc:
                last_error = exc
        if candidates:
            return max(candidates, key=lambda item: item[0])[1]
        raise OfficialDataError(f"官方報表無法解析：{last_error}")

    @staticmethod
    def parse_revenue_openapi_rows(rows, market: str) -> pd.DataFrame:
        """Parse the latest official OpenAPI revenue snapshot as a fallback."""
        output = []
        for row in rows or []:
            code = _stock_code(_row_value(
                row, ("公司代號", "SecuritiesCompanyCode", "CompanyCode", "Code")
            ))
            if not _is_common_stock(code):
                continue
            period_text = re.sub(r"\D", "", _strip_html(_row_value(
                row, ("資料年月", "DataYearMonth", "YearMonth", "RevenueYearMonth")
            )))
            if len(period_text) == 5:  # 民國 11507
                year, month = int(period_text[:3]) + 1911, int(period_text[3:])
            elif len(period_text) == 6:  # 西元 202607
                year, month = int(period_text[:4]), int(period_text[4:])
            else:
                continue
            revenue = _number(_row_value(row, (
                "營業收入-當月營收", "當月營收", "CurrentMonthRevenue",
                "RevenueCurrentMonth", "CurrentMonthOperatingRevenue",
            )))
            if revenue is None:
                continue
            prev = _number(_row_value(row, (
                "營業收入-上月營收", "上月營收", "PreviousMonthRevenue",
                "RevenuePreviousMonth", "LastMonthOperatingRevenue",
            )))
            last_year = _number(_row_value(row, (
                "營業收入-去年當月營收", "去年當月營收", "PreviousYearMonthRevenue",
                "RevenueSameMonthLastYear", "LastYearMonthOperatingRevenue",
            )))
            output.append({
                "stock_id": code,
                "stock_name": _strip_html(_row_value(
                    row, ("公司名稱", "CompanyName", "Name")
                )),
                "date": f"{year:04d}-{month:02d}-01",
                "market": market,
                "revenue_year": year,
                "revenue_month": month,
                "revenue": revenue * 1000,
                "prev_month_revenue": prev * 1000 if prev is not None else None,
                "last_year_revenue": last_year * 1000 if last_year is not None else None,
                "mom_pct": _number(_row_value(row, (
                    "營業收入-上月比較增減(%)", "上月比較增減(%)",
                    "RevenuePreviousMonthChangePercent", "MoM",
                ))),
                "yoy_pct": _number(_row_value(row, (
                    "營業收入-去年同月增減(%)", "去年同月增減(%)",
                    "RevenueSameMonthLastYearChangePercent", "YoY",
                ))),
            })
        return pd.DataFrame(output)

    @staticmethod
    def parse_revenue_tables(tables, year: int, month: int, market: str) -> pd.DataFrame:
        output = []
        seen = set()
        for raw in tables:
            df = _flatten_columns(raw)
            code_col = _find_column(df.columns, ("公司代號",))
            name_col = _find_column(df.columns, ("公司名稱",))
            revenue_col = _find_column(df.columns, ("當月營收",), ("累計", "去年"))
            if not code_col or not revenue_col:
                continue
            prev_col = _find_column(df.columns, ("上月營收",), ("累計",))
            last_year_col = _find_column(df.columns, ("去年當月營收",), ("累計",))
            mom_col = _find_column(df.columns, ("上月比較增減",))
            yoy_col = _find_column(df.columns, ("去年同月增減",))
            for _, row in df.iterrows():
                code = _stock_code(row.get(code_col))
                if not _is_common_stock(code) or code in seen:
                    continue
                revenue_thousand = _number(row.get(revenue_col))
                if revenue_thousand is None:
                    continue
                seen.add(code)
                output.append({
                    "stock_id": code,
                    "stock_name": _strip_html(row.get(name_col, "")) if name_col else "",
                    "date": f"{year:04d}-{month:02d}-01",
                    "market": market,
                    "revenue_year": year,
                    "revenue_month": month,
                    # MOPS monthly report uses NT$ thousands; store NT dollars
                    # to stay compatible with FinMind's revenue units.
                    "revenue": revenue_thousand * 1000,
                    "prev_month_revenue": (_number(row.get(prev_col)) * 1000
                                             if prev_col and _number(row.get(prev_col)) is not None else None),
                    "last_year_revenue": (_number(row.get(last_year_col)) * 1000
                                           if last_year_col and _number(row.get(last_year_col)) is not None else None),
                    "mom_pct": _number(row.get(mom_col)) if mom_col else None,
                    "yoy_pct": _number(row.get(yoy_col)) if yoy_col else None,
                })
        return pd.DataFrame(output)

    def fetch_latest_monthly_revenue(self, market: str) -> pd.DataFrame:
        """只抓官方 OpenAPI 最新一期全市場月營收快照。"""
        endpoint = (
            f"{TWSE_OPENAPI}/opendata/t187ap05_L"
            if market == "TWSE"
            else f"{TPEX_OPENAPI}/mopsfin_t187ap05_O"
        )
        result = self.parse_revenue_openapi_rows(self._get(endpoint).json(), market)
        if result.empty:
            raise OfficialDataError(f"{market} OpenAPI 有回應，但沒有可解析的月營收")
        return result

    def fetch_monthly_revenue(self, year: int, month: int, market: str,
                              allow_latest_fallback: bool = True) -> pd.DataFrame:
        typek = "sii" if market == "TWSE" else "otc"
        roc_year = year - 1911
        errors = []
        for base in (MOPS_BASE, MOPS_LEGACY_BASE):
            frames = []
            # _0 是本國公司，_1 是 KY 等外國公司；兩份都要合併。
            for company_type in (0, 1):
                static_url = (
                    f"{base}/nas/t21/{typek}/"
                    f"t21sc03_{roc_year}_{month}_{company_type}.html"
                )
                try:
                    response = self._get(static_url)
                    tables = self._read_html_tables(response)
                    parsed = self.parse_revenue_tables(tables, year, month, market)
                    if not parsed.empty:
                        frames.append(parsed)
                except Exception as exc:
                    errors.append(f"{base} 類別{company_type}: {exc}")
            if frames:
                return (
                    pd.concat(frames, ignore_index=True)
                    .drop_duplicates(["stock_id", "revenue_year", "revenue_month"], keep="last")
                    .reset_index(drop=True)
                )
            errors.append(f"{base}: 有回應但找不到可用的營收表格")

        # AJAX 端點目前會主動中斷非瀏覽器 POST。最新一期改用免金鑰的
        # TWSE／TPEx OpenAPI GET 備援；它只會回傳最新一期，不能冒充歷史。
        if allow_latest_fallback:
            try:
                latest = self.fetch_latest_monthly_revenue(market)
                matched = latest[
                    (latest["revenue_year"] == year) & (latest["revenue_month"] == month)
                ] if not latest.empty else latest
                if not matched.empty:
                    return matched.reset_index(drop=True)
                errors.append("OpenAPI 可連線，但最新一期不是指定年月")
            except Exception as exc:
                errors.append(f"OpenAPI: {exc}")
        raise OfficialDataError(
            f"{market} {year}-{month:02d} 月營收下載失敗：" + "；".join(errors)
        )

    @staticmethod
    def parse_income_statement_tables(tables, year: int, quarter: int,
                                      market: str) -> pd.DataFrame:
        mappings = (
            (("基本每股盈餘",), (), "EPS_CUMULATIVE", "基本每股盈餘（累計）", 1.0),
            (("營業收入",), (), "OperatingRevenue", "營業收入", 1000.0),
            (("營業毛利",), (), "GrossProfit", "營業毛利", 1000.0),
            (("營業利益",), (), "OperatingIncome", "營業利益", 1000.0),
            (("本期淨利",), (), "IncomeAfterTaxes", "本期淨利", 1000.0),
        )
        report_date = f"{year:04d}-{quarter * 3:02d}-{(31 if quarter in (1, 4) else 30):02d}"
        output = []
        seen = set()
        for raw in tables:
            df = _flatten_columns(raw)
            code_col = _find_column(df.columns, ("公司代號",))
            if not code_col:
                continue
            for _, row in df.iterrows():
                code = _stock_code(row.get(code_col))
                if not _is_common_stock(code):
                    continue
                for includes, excludes, item_type, origin_name, scale in mappings:
                    col = _find_column(df.columns, includes, excludes)
                    if not col or (code, item_type) in seen:
                        continue
                    value = _number(row.get(col))
                    if value is None:
                        continue
                    seen.add((code, item_type))
                    output.append({
                        "stock_id": code,
                        "date": report_date,
                        "market": market,
                        "type": item_type,
                        "value": value * scale,
                        "origin_name": origin_name,
                    })
        return pd.DataFrame(output)

    @staticmethod
    def parse_income_statement_openapi_rows(rows, market: str) -> pd.DataFrame:
        """Parse the latest general-industry income statement OpenAPI snapshot."""
        output = []
        mappings = (
            (("基本每股盈餘(元)", "基本每股盈餘", "BasicEarningsPerShare", "EPS"),
             "EPS_CUMULATIVE", "基本每股盈餘（累計）", 1.0),
            (("營業收入", "OperatingRevenue"),
             "OperatingRevenue", "營業收入", 1000.0),
            (("營業毛利", "GrossProfitLossFromOperations", "GrossProfit"),
             "GrossProfit", "營業毛利", 1000.0),
            (("營業利益", "OperatingIncomeLoss", "OperatingIncome"),
             "OperatingIncome", "營業利益", 1000.0),
            (("本期淨利", "稅後淨利", "ProfitLoss", "IncomeAfterTaxes"),
             "IncomeAfterTaxes", "本期淨利", 1000.0),
        )
        for row in rows or []:
            code = _stock_code(_row_value(
                row, ("公司代號", "SecuritiesCompanyCode", "CompanyCode", "Code")
            ))
            if not _is_common_stock(code):
                continue
            year_value = _number(_row_value(row, ("年度", "Year")), integer=True)
            quarter = _number(_row_value(row, ("季別", "Quarter", "Season")), integer=True)
            if year_value is None or quarter not in (1, 2, 3, 4):
                continue
            year = year_value + 1911 if year_value < 1911 else year_value
            report_date = f"{year:04d}-{quarter * 3:02d}-{(31 if quarter in (1, 4) else 30):02d}"
            for aliases, item_type, origin_name, scale in mappings:
                value = _number(_row_value(row, aliases, default=None))
                if value is None:
                    continue
                output.append({
                    "stock_id": code, "date": report_date, "market": market,
                    "type": item_type, "value": value * scale,
                    "origin_name": origin_name,
                })
        return pd.DataFrame(output)

    def fetch_latest_eps_summary(self, market: str) -> pd.DataFrame:
        """Fetch the official all-industry EPS summary for the latest quarter.

        Unlike the industry-specific ``t187ap06_*_ci`` feed, this endpoint
        includes general, finance, insurance, securities and holding companies.
        """
        endpoint = (
            f"{TWSE_OPENAPI}/opendata/t187ap14_L"
            if market == "TWSE"
            else f"{TPEX_OPENAPI}/mopsfin_t187ap14_O"
        )
        result = self.parse_income_statement_openapi_rows(
            self._get(endpoint).json(), market
        )
        if result.empty or result[result["type"] == "EPS_CUMULATIVE"].empty:
            raise OfficialDataError(f"{market} EPS OpenAPI 有回應，但沒有可解析資料")
        return result

    def fetch_quarterly_income_statement(self, year: int, quarter: int,
                                         market: str) -> pd.DataFrame:
        typek = "sii" if market == "TWSE" else "otc"
        errors = []
        # 新站的 AJAX 要求瀏覽器產生的參數，會直接中斷舊式 POST；先試仍可
        # 接受傳統表單的官方舊入口，再以 OpenAPI 最新季快照備援。
        try:
            response = self._post(
                f"{MOPS_LEGACY_BASE}/mops/web/ajax_t163sb04",
                data={
                    "encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
                    "TYPEK": typek, "year": str(year - 1911), "season": str(quarter),
                },
                headers={"Referer": f"{MOPS_LEGACY_BASE}/mops/web/t163sb04"},
            )
            result = self.parse_income_statement_tables(
                self._read_html_tables(response), year, quarter, market
            )
            if not result.empty:
                return result
            errors.append("MOPS 舊入口有回應，但沒有可解析資料")
        except Exception as exc:
            errors.append(f"MOPS 舊入口: {exc}")

        # 官方「各產業 EPS 統計」一次涵蓋全市場及所有財報業別，是 GitHub
        # Actions 被 MOPS 歷史頁擋住時較穩定的最新季備援。
        try:
            latest = self.fetch_latest_eps_summary(market)
            report_year = pd.to_datetime(latest["date"]).dt.year if not latest.empty else pd.Series(dtype=int)
            report_quarter = pd.to_datetime(latest["date"]).dt.quarter if not latest.empty else pd.Series(dtype=int)
            matched = latest[(report_year == year) & (report_quarter == quarter)]
            if not matched.empty:
                return matched.reset_index(drop=True)
            errors.append("OpenAPI 可連線，但最新一期不是指定季度")
        except Exception as exc:
            errors.append(f"OpenAPI: {exc}")
        raise OfficialDataError(
            f"{market} {year}Q{quarter} 綜合損益表下載失敗：" + "；".join(errors)
        )


def _month_sequence(end_year: int, end_month: int, count: int):
    year, month = end_year, end_month
    result = []
    for _ in range(count):
        result.append((year, month))
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    return list(reversed(result))


def _quarter_sequence(end_year: int, end_quarter: int, count: int):
    year, quarter = end_year, end_quarter
    result = []
    for _ in range(count):
        result.append((year, quarter))
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
    return list(reversed(result))


class OfficialDataService:
    """Synchronises official data into ``DataStore`` with resume checkpoints."""

    def __init__(self, store: DataStore, client: Optional[OfficialMarketClient] = None):
        self.store = store
        self.client = client or OfficialMarketClient()

    @staticmethod
    def _emit(callback: ProgressCallback, message: str, done: int, total: int):
        if callback:
            callback(message, done, total)

    @staticmethod
    def _stopped(stop_event) -> bool:
        return bool(stop_event and stop_event.is_set())

    def _upsert_names_from_prices(self, prices: pd.DataFrame):
        if prices is None or prices.empty or "stock_name" not in prices:
            return
        info = prices[["stock_id", "stock_name", "market"]].copy()
        info["industry_category"] = ""
        info["paid_in_capital"] = None
        self.store.upsert_stock_info(info)

    def sync_stock_info(self) -> int:
        df = self.client.fetch_stock_info()
        return self.store.upsert_stock_info(df)

    def sync_latest_prices(self) -> int:
        df = self.client.fetch_latest_prices()
        self._upsert_names_from_prices(df)
        return self.store.upsert_daily_prices(df)

    @staticmethod
    def _expand_revenue_snapshot(snapshot: pd.DataFrame) -> pd.DataFrame:
        """
        OpenAPI 最新快照同時含本月、上月、去年同月營收。
        將官方已明確提供的三期金額拆成 SQLite 歷史列；後續抓到
        MOPS 完整報表時，upsert 會自動補上各期 MoM/YoY 欄位。
        """
        if snapshot is None or snapshot.empty:
            return pd.DataFrame()
        output = []
        for row in snapshot.to_dict("records"):
            year, month = int(row["revenue_year"]), int(row["revenue_month"])
            output.append(dict(row))

            prev_year, prev_month = year, month - 1
            if prev_month == 0:
                prev_year, prev_month = year - 1, 12
            comparisons = (
                (prev_year, prev_month, row.get("prev_month_revenue")),
                (year - 1, month, row.get("last_year_revenue")),
            )
            for target_year, target_month, revenue in comparisons:
                if revenue is None or pd.isna(revenue):
                    continue
                output.append({
                    "stock_id": row["stock_id"],
                    "stock_name": row.get("stock_name", ""),
                    "date": f"{target_year:04d}-{target_month:02d}-01",
                    "market": row.get("market", ""),
                    "revenue_year": target_year,
                    "revenue_month": target_month,
                    "revenue": revenue,
                    "prev_month_revenue": None,
                    "last_year_revenue": None,
                    "mom_pct": None,
                    "yoy_pct": None,
                })
        return (
            pd.DataFrame(output)
            .drop_duplicates(["stock_id", "revenue_year", "revenue_month"], keep="first")
            .reset_index(drop=True)
        )

    def sync_price_history(self, calendar_days: int = 365, callback: ProgressCallback = None,
                           stop_event=None) -> dict:
        end = date.today()
        start = end - timedelta(days=max(int(calendar_days), 1))
        dates = []
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                dates.append(cursor)
            cursor += timedelta(days=1)
        jobs = [(d, market) for d in dates for market in ("TWSE", "TPEx")]
        rows_written = errors = 0
        for idx, (trade_date, market) in enumerate(jobs, 1):
            if self._stopped(stop_event):
                break
            key = f"price:{market}:{trade_date.isoformat()}"
            if self.store.sync_succeeded(key):
                self._emit(callback, f"略過已下載 {market} {trade_date}", idx, len(jobs))
                continue
            try:
                df = self.client.fetch_prices_by_date(trade_date, market)
                if not df.empty:
                    self._upsert_names_from_prices(df)
                    rows_written += self.store.upsert_daily_prices(df)
                    self.store.set_sync_state(key, "ok", len(df), "交易日資料已寫入")
                else:
                    # 不把空資料永久標記為成功：它可能是休市日，也可能是當日
                    # 尚未收盤或官方報表暫時未發布；下次更新會安全地再確認。
                    self.store.set_sync_state(key, "empty", 0, "官方目前沒有資料")
            except Exception as exc:
                errors += 1
                self.store.set_sync_state(key, "error", 0, str(exc))
            self._emit(callback, f"價格 {market} {trade_date}", idx, len(jobs))
        return {"rows": rows_written, "errors": errors, "jobs": len(jobs)}

    def sync_institutional_history(self, calendar_days: int = 210,
                                   callback: ProgressCallback = None,
                                   stop_event=None) -> dict:
        """Download one whole-market institutional report per market and weekday."""
        end = date.today()
        start = end - timedelta(days=max(int(calendar_days), 1))
        dates = []
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                dates.append(cursor)
            cursor += timedelta(days=1)
        jobs = [(d, market) for d in dates for market in ("TWSE", "TPEx")]
        rows_written = errors = 0
        for idx, (trade_date, market) in enumerate(jobs, 1):
            if self._stopped(stop_event):
                break
            key = f"institutional:{market}:{trade_date.isoformat()}"
            if self.store.sync_succeeded(key):
                self._emit(callback, f"略過已下載 {market} {trade_date} 法人", idx, len(jobs))
                continue
            try:
                df = self.client.fetch_institutional_by_date(trade_date, market)
                if not df.empty:
                    written = self.store.upsert_institutional_buysell(df)
                    rows_written += written
                    self.store.set_sync_state(key, "ok", len(df), "三大法人資料已寫入")
                else:
                    self.store.set_sync_state(key, "empty", 0, "官方目前沒有資料")
            except Exception as exc:
                errors += 1
                self.store.set_sync_state(key, "error", 0, str(exc))
            self._emit(callback, f"法人 {market} {trade_date}", idx, len(jobs))
        return {"rows": rows_written, "errors": errors, "jobs": len(jobs)}

    def sync_revenue_history(self, months: int = 30, callback: ProgressCallback = None,
                             stop_event=None) -> dict:
        today = date.today()
        # 月營收通常在次月公告，不能把尚未結束的本月當成應有資料。
        end_year, end_month = today.year, today.month - 1
        if end_month == 0:
            end_year -= 1
            end_month = 12
        # 最新月份先處理，不要讓使用者等完一年前的失敗請求才看到資料。
        periods = list(reversed(_month_sequence(end_year, end_month, max(months, 14))))
        jobs = [(y, m, market) for y, m in periods for market in ("TWSE", "TPEx")]
        rows_written = errors = snapshot_rows = 0
        market_rows = {"TWSE": 0, "TPEx": 0}
        history_blocked = {}
        total_jobs = len(jobs) + 2

        # OpenAPI 只有最新快照，每個市場只抓一次，先寫入 SQLite。
        for snapshot_idx, market in enumerate(("TWSE", "TPEx"), 1):
            if self._stopped(stop_event):
                break
            try:
                latest = self.client.fetch_latest_monthly_revenue(market)
                expanded = self._expand_revenue_snapshot(latest)
                written = self.store.upsert_monthly_revenue(expanded)
                rows_written += written
                snapshot_rows += written
                market_rows[market] += written
                latest_year = int(latest.iloc[0]["revenue_year"])
                latest_month = int(latest.iloc[0]["revenue_month"])
                self.store.set_sync_state(
                    f"revenue:{market}:{latest_year}-{latest_month:02d}",
                    "ok", len(latest), "OpenAPI 最新快照已寫入",
                )
            except Exception as exc:
                errors += 1
                self.store.set_sync_state(
                    f"revenue_latest:{market}", "error", 0, str(exc)
                )
            self._emit(callback, f"營收快照 {market}", snapshot_idx, total_jobs)

        for idx, (year, month, market) in enumerate(jobs, 3):
            if self._stopped(stop_event):
                break
            if market in history_blocked:
                self._emit(
                    callback,
                    f"略過維護中 {market} {year}-{month:02d} 營收",
                    idx, total_jobs,
                )
                continue
            key = f"revenue:{market}:{year}-{month:02d}"
            if self.store.sync_succeeded(key):
                self._emit(callback, f"略過已下載 {market} {year}-{month:02d} 營收", idx, len(jobs))
                continue
            try:
                # 最新 OpenAPI 已在上方各叫一次；歷史作業只試 MOPS，
                # 避免對同一個快照端點重複發出數十次請求。
                df = self.client.fetch_monthly_revenue(
                    year, month, market, allow_latest_fallback=False
                )
                written = self.store.upsert_monthly_revenue(df)
                rows_written += written
                market_rows[market] += written
                if "stock_name" in df:
                    info = df[["stock_id", "stock_name", "market"]].copy()
                    info["industry_category"] = ""
                    info["paid_in_capital"] = None
                    self.store.upsert_stock_info(info)
                self.store.set_sync_state(key, "ok", len(df))
            except Exception as exc:
                errors += 1
                message = str(exc)
                self.store.set_sync_state(key, "error", 0, message)
                # 兩個官方域名／公司類別都回維護或安全頁時，
                # 同一輪不再重複數十次。未標記其他月份為成功，下次仍會重試。
                blocked_count = message.count("安全機制") + message.count("維護中")
                if blocked_count >= 2:
                    history_blocked[market] = message
            self._emit(callback, f"營收 {market} {year}-{month:02d}", idx, total_jobs)
        return {
            "rows": rows_written, "snapshot_rows": snapshot_rows,
            "twse_rows": market_rows["TWSE"], "tpex_rows": market_rows["TPEx"],
            "history_blocked_markets": sorted(history_blocked),
            "errors": errors, "jobs": total_jobs,
        }

    def _rebuild_quarterly_eps(self):
        with self.store.connect() as conn:
            cumulative = pd.read_sql_query(
                """SELECT stock_id, date, market, value
                   FROM financial_statement WHERE type='EPS_CUMULATIVE'
                   ORDER BY stock_id, date""",
                conn,
            )
            conn.execute("DELETE FROM financial_statement WHERE type='EPS'")
        if cumulative.empty:
            return 0
        cumulative["date"] = pd.to_datetime(cumulative["date"])
        cumulative["year"] = cumulative["date"].dt.year
        cumulative["quarter"] = cumulative["date"].dt.quarter
        output = []
        for (stock_id, year), group in cumulative.groupby(["stock_id", "year"]):
            values = {int(r.quarter): float(r.value) for r in group.itertuples() if pd.notna(r.value)}
            market = str(group.iloc[-1]["market"])
            for quarter in sorted(values):
                if quarter == 1:
                    value = values[quarter]
                elif quarter - 1 in values:
                    value = values[quarter] - values[quarter - 1]
                else:
                    continue
                report_date = group[group["quarter"] == quarter]["date"].iloc[-1].strftime("%Y-%m-%d")
                output.append({
                    "stock_id": stock_id, "date": report_date, "market": market,
                    "type": "EPS", "value": value,
                    "origin_name": "基本每股盈餘（單季，由累計值換算）",
                })
        return self.store.upsert_financial_statements(pd.DataFrame(output))

    def sync_financial_history(self, quarters: int = 12, callback: ProgressCallback = None,
                               stop_event=None) -> dict:
        today = date.today()
        # The current calendar quarter is usually not yet fully reported.  End
        # at the preceding quarter and let later incremental runs retry it.
        current_q = (today.month - 1) // 3 + 1
        end_year, end_q = today.year, current_q - 1
        if end_q == 0:
            end_year -= 1
            end_q = 4
        periods = _quarter_sequence(end_year, end_q, max(quarters, 8))
        jobs = [(y, q, market) for y, q in periods for market in ("TWSE", "TPEx")]
        rows_written = errors = 0
        for idx, (year, quarter, market) in enumerate(jobs, 1):
            if self._stopped(stop_event):
                break
            # v2 會重新嘗試舊資料庫曾被不完整一般業 OpenAPI 標記成功的季度。
            key = f"financial:v2:{market}:{year}Q{quarter}"
            if self.store.sync_succeeded(key):
                self._emit(callback, f"略過已下載 {market} {year}Q{quarter} 財報", idx, len(jobs))
                continue
            try:
                df = self.client.fetch_quarterly_income_statement(year, quarter, market)
                rows_written += self.store.upsert_financial_statements(df)
                self.store.set_sync_state(key, "ok", len(df))
            except Exception as exc:
                errors += 1
                self.store.set_sync_state(key, "error", 0, str(exc))
            self._emit(callback, f"財報 {market} {year}Q{quarter}", idx, len(jobs))
        eps_rows = self._rebuild_quarterly_eps()
        return {"rows": rows_written, "eps_rows": eps_rows, "errors": errors, "jobs": len(jobs)}

    def initialise(self, callback: ProgressCallback = None, stop_event=None,
                   price_calendar_days: int = 365, revenue_months: int = 30,
                   financial_quarters: int = 12,
                   institutional_calendar_days: int = 210) -> dict:
        result = {"stock_info": 0}
        if not self._stopped(stop_event):
            self._emit(callback, "下載股票基本資料", 0, 1)
            result["stock_info"] = self.sync_stock_info()
        if not self._stopped(stop_event):
            result["prices"] = self.sync_price_history(
                price_calendar_days, callback=callback, stop_event=stop_event
            )
        if not self._stopped(stop_event):
            result["institutional"] = self.sync_institutional_history(
                institutional_calendar_days, callback=callback, stop_event=stop_event
            )
        if not self._stopped(stop_event):
            result["revenue"] = self.sync_revenue_history(
                revenue_months, callback=callback, stop_event=stop_event
            )
        if not self._stopped(stop_event):
            result["financial"] = self.sync_financial_history(
                financial_quarters, callback=callback, stop_event=stop_event
            )
        return result

    def incremental_update(self, callback: ProgressCallback = None, stop_event=None) -> dict:
        result = {"stock_info": self.sync_stock_info()}
        if self._stopped(stop_event):
            return result
        # 以「逐交易日歷史報表」補最近兩週，日期由報表查詢日決定，避免
        # OpenAPI 當日總表在週末／休市日缺少日期時誤寫成今天。
        result["prices"] = self.sync_price_history(
            calendar_days=14, callback=callback, stop_event=stop_event
        )
        if self._stopped(stop_event):
            return result
        # Existing databases automatically backfill about 150 trading days on
        # the first update; subsequent runs only check the recent two weeks.
        institutional_days = (
            210 if not self.store.coverage_summary().get("institutional_rows") else 14
        )
        result["institutional"] = self.sync_institutional_history(
            calendar_days=institutional_days, callback=callback, stop_event=stop_event
        )
        if self._stopped(stop_event):
            return result
        # Retry recent periods because official publication dates differ by
        # company and a previously empty period may later become available.
        result["revenue"] = self.sync_revenue_history(
            months=3, callback=callback, stop_event=stop_event
        )
        result["financial"] = self.sync_financial_history(
            quarters=2, callback=callback, stop_event=stop_event
        )
        return result
