import base64
import html
import json
import os
import re
import sys
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta
import feedparser
import requests
from google import genai
from google.genai import types

# ================= [1. 기존 키 3개 (환경변수에서 자동 로드)] =================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ================= [2. GitHub 웹 업로드 정보] =================
GITHUB_USER = "no1frieza-cyber"
GITHUB_REPO = "stock-report"
GITHUB_TOKEN = os.environ.get("MY_GITHUB_TOKEN", "")
# =====================================================================

SAVE_DIR = "/tmp/stock" if os.environ.get("GITHUB_ACTIONS") == "true" else r"C:\stock"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

EXCLUDED_KEYWORDS = ("ETF", "ETN", "인버스", "곱버스")
ETF_BRANDS = (
    "KODEX", "TIGER", "RISE", "ACE", "SOL", "HANARO", "PLUS",
    "KIWOOM", "KOSEF", "KBSTAR", "ARIRANG", "TIMEFOLIO", "WON",
    "1Q", "KOACT", "BNK", "UNICORN", "FOCUS", "TRUSTON", "VITA",
    "HK", "마이티", "히어로즈", "에셋플러스", "파워", "마이다스",
    "TREX", "KINDEX", "SMART",
)

US_EXCLUDED_TICKERS = {
    "SPY", "QQQ", "IWM", "DIA", "TQQQ", "SQQQ", "SOXL", "SOXS",
    "TSLL", "TSLS", "NVDL", "NVDS", "MSTU", "MSTX", "CONL", "UVXY",
    "VXX", "TLT", "HYG", "GLD", "SLV", "IBIT", "FBTC", "ARKK",
    "XLF", "XLE", "XLK", "SMH", "BOIL", "KOLD", "LABU", "YANG",
    "YINN", "FXI", "EEM", "VOO", "VTI", "SCHD", "JEPI", "JEPQ",
}
US_EXCLUDED_KEYWORDS = (
    "ETF", "ETN", "PROSHARES", "DIREXION", "GRANITESHARES",
    "YIELDMAX", "DEFIANCE", "ISHARES", "SPDR", "VANGUARD",
    "INVESCO", "2X ", "3X ", "ULTRA ", "INVERSE ", "SHORT ",
    "LEVERAGED", "레버리지", "인버스",
)

SORT_SCRIPT = r"""
<script>
document.addEventListener("DOMContentLoaded", () => {
  const table = document.getElementById("stockTable");
  if (!table) return;
  const tbody = table.querySelector("tbody");
  const headers = table.querySelectorAll("th.sortable");

  function parseKrw(text) {
    const clean = text.replace(/,/g, "").replace(/\s+/g, "");
    if (!clean || clean === "-") return -1;
    let total = 0;
    const jo = clean.match(/(\d+(?:\.\d+)?)조/);
    const eok = clean.match(/(\d+(?:\.\d+)?)억/);
    const man = clean.match(/(\d+(?:\.\d+)?)만/);
    if (jo) total += parseFloat(jo[1]) * 1e12;
    if (eok) total += parseFloat(eok[1]) * 1e8;
    if (man) total += parseFloat(man[1]) * 1e4;
    if (total === 0) {
      const num = parseFloat(clean.replace(/[^0-9.-]/g, ""));
      return isNaN(num) ? -1 : num;
    }
    return total;
  }

  function parseUsd(text) {
    const clean = text.replace(/[,$\s]/g, "").toUpperCase();
    if (!clean || clean === "-") return -1;
    const m = clean.match(/^([0-9.]+)([BMK])?$/);
    if (m) {
      let val = parseFloat(m[1]);
      if (m[2] === "B") val *= 1e9;
      else if (m[2] === "M") val *= 1e6;
      else if (m[2] === "K") val *= 1e3;
      return val;
    }
    const num = parseFloat(clean.replace(/[^0-9.-]/g, ""));
    return isNaN(num) ? -1 : num;
  }

  function parseValue(cellText, type) {
    const text = cellText.trim();
    if (type === "number") {
      const num = parseFloat(text.replace(/[^0-9.-]/g, ""));
      return isNaN(num) ? -Infinity : num;
    }
    if (type === "krw") return parseKrw(text);
    if (type === "usd") return parseUsd(text);
    return text;
  }

  headers.forEach((th) => {
    th.addEventListener("click", () => {
      const colIndex = Array.from(th.parentNode.children).indexOf(th);
      const type = th.dataset.type || "text";
      const currentDir = th.dataset.dir;
      const defaultDir = (type === "number" || type === "krw" || type === "usd") && colIndex !== 0 ? "desc" : "asc";
      const nextDir = currentDir ? (currentDir === "asc" ? "desc" : "asc") : defaultDir;

      headers.forEach((h) => {
        h.removeAttribute("data-dir");
        h.classList.remove("asc", "desc");
      });
      th.dataset.dir = nextDir;
      th.classList.add(nextDir);

      const rows = Array.from(tbody.querySelectorAll("tr"));
      rows.sort((a, b) => {
        const valA = parseValue(a.children[colIndex].textContent, type);
        const valB = parseValue(b.children[colIndex].textContent, type);
        const cmp = type === "text" ? valA.localeCompare(valB, "ko") : valA - valB;
        return nextDir === "asc" ? cmp : -cmp;
      });

      rows.forEach((row) => tbody.appendChild(row));
    });
  });
});
</script>
"""

INDEX_SCRIPT = r"""
<script>
document.addEventListener("DOMContentLoaded", () => {
  const tabBtns = document.querySelectorAll(".market-tab");
  const panels = document.querySelectorAll(".market-panel");

  function activateTab(tabName) {
    tabBtns.forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === tabName));
    panels.forEach((panel) => {
      panel.style.display = panel.id === "panel-" + tabName ? "block" : "none";
    });
  }

  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      activateTab(target);
      history.replaceState(null, "", target === "us" ? "#us" : "#kr");
    });
  });

  if (window.location.hash === "#us") {
    activateTab("us");
  } else {
    activateTab("kr");
  }

  const krPanel = document.getElementById("panel-kr");
  if (krPanel) {
    const buttons = krPanel.querySelectorAll(".filter-btn");
    const groups = krPanel.querySelectorAll(".date-group");

    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const filter = btn.dataset.filter;
        buttons.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");

        groups.forEach((group) => {
          const cards = group.querySelectorAll(".report-card");
          let visibleCount = 0;
          cards.forEach((card) => {
            const show = filter === "ALL" || card.dataset.mode === filter;
            card.style.display = show ? "flex" : "none";
            if (show) visibleCount++;
          });
          group.style.display = visibleCount > 0 ? "block" : "none";
        });
      });
    });
  }
});
</script>
"""


def get_home_url():
    if not GITHUB_USER or "여기에_" in GITHUB_USER:
        return ""
    return f"https://{GITHUB_USER.lower()}.github.io/{GITHUB_REPO}/"


def is_excluded_stock(name, is_etf_etn=False):
    if is_etf_etn:
        return True

    upper_name = str(name).upper().strip()
    if any(kw in upper_name for kw in EXCLUDED_KEYWORDS):
        return True
    if any(upper_name.startswith(f"{brand} ") for brand in ETF_BRANDS):
        return True

    return False


def is_excluded_us_stock(symbol, name, eng_name="", is_etf_etn=False):
    if is_etf_etn:
        return True
    sym = str(symbol).upper().strip()
    if sym in US_EXCLUDED_TICKERS:
        return True
    combined = f"{name} {eng_name}".upper()
    if any(kw in combined for kw in US_EXCLUDED_KEYWORDS):
        return True
    return False


def parse_float(val, default=0.0):
    try:
        return float(str(val).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return default


def parse_int(val, default=0):
    if val is None or str(val).strip() == "":
        return default
    try:
        return int(float(str(val).replace(",", "").strip()))
    except (ValueError, TypeError):
        return default


def parse_us_trading_value(item):
    """네이버 해외주식 거래대금(USD) 파싱"""
    raw = item.get("accumulatedTradingValueRaw")
    if raw is not None and str(raw).strip() != "":
        val = parse_float(raw, 0.0)
        if val > 0:
            return val

    text = str(item.get("accumulatedTradingValue", "")).replace(",", "").replace("$", "").strip()
    if not text:
        return 0.0

    if any(u in text for u in ("조", "억", "백만", "만", "천")):
        total = 0.0
        for unit, mult in (("조", 1e12), ("억", 1e8), ("백만", 1e6), ("만", 1e4), ("천", 1e3)):
            m = re.search(rf"(\d+(?:\.\d+)?)\s*{unit}", text)
            if m:
                total += float(m.group(1)) * mult
        if total > 0:
            return total

    upper = text.upper()
    m = re.match(r"^([0-9.]+)\s*([BMK])$", upper)
    if m:
        mult = {"B": 1e9, "M": 1e6, "K": 1e3}[m.group(2)]
        return float(m.group(1)) * mult

    return parse_float(text, 0.0)


def format_krw(val_krw):
    """원 단위 정수를 네이버 증권 기준 억/조 단위로 반올림 표기"""
    if val_krw <= 0:
        return "-"
    eok = round(val_krw / 100_000_000)
    if eok >= 10_000:
        jo = eok // 10_000
        rem_eok = eok % 10_000
        return f"{jo:,}조 {rem_eok:,}억원" if rem_eok else f"{jo:,}조원"
    if eok >= 1:
        return f"{eok:,}억원"
    man = round(val_krw / 10_000)
    return f"{man:,}만원"


def format_usd(val_usd):
    """달러 단위 금액을 $B / $M / $K 형식으로 표기"""
    if val_usd <= 0:
        return "-"
    if val_usd >= 1_000_000_000:
        return f"${val_usd / 1_000_000_000:,.2f}B"
    if val_usd >= 1_000_000:
        return f"${val_usd / 1_000_000:,.1f}M"
    if val_usd >= 1_000:
        return f"${val_usd / 1_000:,.1f}K"
    return f"${round(val_usd):,}"


def _extract_naver_indicator(data, prefix="", suffix=""):
    """네이버 지수·환율·채권·금 API 공통 파싱"""
    info = data.get("exchangeInfo") if isinstance(data, dict) and "exchangeInfo" in data else data
    if not isinstance(info, dict):
        return None

    raw_price = info.get("closePrice") or info.get("calcPrice") or info.get("price")
    if raw_price is None or str(raw_price).strip() == "":
        return None
    price_str = f"{prefix}{str(raw_price).strip()}{suffix}"

    ratio = parse_float(info.get("fluctuationsRatio"), 0.0)
    comp = info.get("compareToPreviousPrice") or info.get("fluctuationsType") or {}
    code = str(comp.get("code", ""))
    name = str(comp.get("name", "")).upper()
    if code in ("4", "5") or name == "FALLING":
        ratio = -abs(ratio)
    elif code in ("1", "2") or name == "RISING":
        ratio = abs(ratio)

    if ratio > 0:
        direction, rate_str = "up", f"+{ratio:.2f}%"
    elif ratio < 0:
        direction, rate_str = "down", f"{ratio:.2f}%"
    else:
        direction, rate_str = "flat", "0.00%"

    return {"value": price_str, "rate": rate_str, "direction": direction}


def _fetch_yahoo_indicator(symbol, prefix="", suffix="", decimals=2):
    """야후 파이낸스 차트 API로 지표(WTI, 미10년물, VIX 등) 조회"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=5d&interval=1d"
    res = requests.get(url, headers=HEADERS, timeout=10)
    if res.status_code != 200:
        return None
    result = res.json()["chart"]["result"][0]
    meta = result.get("meta", {})
    closes = [
        c for c in result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        if c is not None
    ]
    price = meta.get("regularMarketPrice")
    if price is None and closes:
        price = closes[-1]
    if price is None:
        return None

    prev = meta.get("previousClose")
    if prev is None and len(closes) >= 2:
        prev = closes[-2]

    ratio = ((price - prev) / prev * 100.0) if prev and prev != 0 else 0.0
    if ratio > 0:
        direction, rate_str = "up", f"+{ratio:.2f}%"
    elif ratio < 0:
        direction, rate_str = "down", f"{ratio:.2f}%"
    else:
        direction, rate_str = "flat", "0.00%"

    price_str = f"{prefix}{price:,.{decimals}f}{suffix}"
    return {"value": price_str, "rate": rate_str, "direction": direction}


def fetch_market_indicators():
    """메인 페이지 상단용 11개 주요 시장 지표 수집 (실행 시점 기준)"""
    indicators = []

    naver_specs = [
        ("코스피", "https://m.stock.naver.com/api/index/KOSPI/basic", "", ""),
        ("코스닥", "https://m.stock.naver.com/api/index/KOSDAQ/basic", "", ""),
        ("S&P 500", "https://api.stock.naver.com/index/.INX/basic", "", ""),
        ("나스닥", "https://api.stock.naver.com/index/.IXIC/basic", "", ""),
        ("원/달러", "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW", "", "원"),
        ("미 국채 10년", "https://api.stock.naver.com/marketindex/bond/US10YT=RR", "", "%"),
        ("국내 금", "https://api.stock.naver.com/marketindex/metals/M04020000", "", "원/g"),
    ]

    for label, url, prefix, suffix in naver_specs:
        item = None
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                item = _extract_naver_indicator(res.json(), prefix=prefix, suffix=suffix)
        except Exception as e:
            print(f"지표 조회 오류({label}): {e}")

        if item is None and label == "미 국채 10년":
            try:
                item = _fetch_yahoo_indicator("^TNX", suffix="%")
            except Exception as e:
                print(f"지표 예비 조회 오류({label}): {e}")

        indicators.append(
            {"label": label, **(item or {"value": "-", "rate": "-", "direction": "flat"})}
        )

    # 9) WTI 유가 (야후 파이낸스 CL=F)
    wti_item = None
    try:
        wti_item = _fetch_yahoo_indicator("CL=F", prefix="$")
    except Exception as e:
        print(f"지표 조회 오류(WTI 유가): {e}")
    indicators.append(
        {"label": "WTI 유가", **(wti_item or {"value": "-", "rate": "-", "direction": "flat"})}
    )

    # 10) VIX (네이버 우선, 실패 시 야후 파이낸스)
    vix_item = None
    try:
        res = requests.get("https://api.stock.naver.com/index/.VIX/basic", headers=HEADERS, timeout=10)
        if res.status_code == 200:
            vix_item = _extract_naver_indicator(res.json())
    except Exception as e:
        print(f"지표 조회 오류(VIX): {e}")
    if vix_item is None:
        try:
            vix_item = _fetch_yahoo_indicator("^VIX")
        except Exception as e:
            print(f"지표 예비 조회 오류(VIX): {e}")
    indicators.append(
        {"label": "VIX", **(vix_item or {"value": "-", "rate": "-", "direction": "flat"})}
    )

    # 11) 비트코인 (업비트 KRW-BTC)
    btc_item = None
    try:
        res = requests.get("https://api.upbit.com/v1/ticker?markets=KRW-BTC", headers=HEADERS, timeout=10)
        if res.status_code == 200 and res.json():
            d = res.json()[0]
            price = float(d.get("trade_price", 0))
            ratio = float(d.get("signed_change_rate", 0)) * 100.0
            if ratio > 0:
                direction, rate_str = "up", f"+{ratio:.2f}%"
            elif ratio < 0:
                direction, rate_str = "down", f"{ratio:.2f}%"
            else:
                direction, rate_str = "flat", "0.00%"
            btc_item = {"value": f"{round(price):,}원", "rate": rate_str, "direction": direction}
    except Exception as e:
        print(f"지표 조회 오류(비트코인): {e}")
    indicators.append(
        {"label": "비트코인", **(btc_item or {"value": "-", "rate": "-", "direction": "flat"})}
    )

    return indicators


def get_latest_traded_date():
    """휴장일 판별을 위해 네이버 증권 KRX 기준 최근 거래일(YYYYMMDD) 조회"""
    url = "https://m.stock.naver.com/api/stocks/priceTop/KOSPI?page=1&pageSize=1"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            stocks = res.json().get("stocks", [])
            if stocks:
                traded_at = stocks[0].get("localTradedAt", "")
                return traded_at[:10].replace("-", "") if traded_at else ""
    except Exception:
        pass
    return ""


def get_krx_mobile_fallback_top100():
    """KRX 전용 랭킹 API 실패 시 예비용: ETF/ETN 사전 제외 후 순수 개별 종목 상위 100위 조회"""
    all_stocks = []
    seen = set()
    traded_date = ""

    for market in ["KOSPI", "KOSDAQ"]:
        for page in (1, 2):
            url = f"https://m.stock.naver.com/api/stocks/priceTop/{market}?page={page}&pageSize=100"
            res = requests.get(url, headers=HEADERS, timeout=10)
            if res.status_code != 200:
                print(f"네이버 증권 KRX 모바일({market} p{page}) 조회 실패: {res.status_code}")
                continue
            stocks = res.json().get("stocks", [])
            if stocks and not traded_date:
                traded_at = stocks[0].get("localTradedAt", "")
                traded_date = traded_at[:10].replace("-", "") if traded_at else ""

            for item in stocks:
                name = str(item.get("stockName", "")).strip()
                code = str(item.get("itemCode", "")).strip() or name
                if code in seen:
                    continue

                end_type = str(item.get("stockEndType", "")).lower()
                is_etf_etn = end_type in ("etf", "etn")
                if is_excluded_stock(name, is_etf_etn):
                    continue

                raw_val = parse_int(item.get("accumulatedTradingValueRaw"))
                if raw_val == 0:
                    raw_val = parse_int(item.get("accumulatedTradingValue")) * 1_000_000

                seen.add(code)
                all_stocks.append(
                    {
                        "name": name,
                        "symbol": code,
                        "eng_name": "",
                        "rate": parse_float(item.get("fluctuationsRatio"), 0.0),
                        "val": raw_val,
                    }
                )

    all_stocks.sort(key=lambda x: x["val"], reverse=True)
    return all_stocks[:100], traded_date, True


def get_market_top100(mode, now_hour):
    """네이버 주식 전용 랭킹 API(KRX / NXT)에서 ETF/ETN 사전 제외 후 순수 개별 종목 상위 100위 조회"""
    stocks = []
    seen = set()
    first_row = None

    for start_idx in (0, 100):
        url = (
            f"https://stock.naver.com/api/domestic/market/stock/default"
            f"?tradeType={mode}&marketType=ALL&orderType=priceTop&startIdx={start_idx}&pageSize=100"
        )
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
        except Exception as e:
            print(f"네이버 증권 {mode} 조회 예외(startIdx={start_idx}): {e}")
            break

        if res.status_code != 200:
            print(f"네이버 증권 {mode} 조회 실패(startIdx={start_idx}): {res.status_code}")
            break

        rows = res.json()
        if not isinstance(rows, list) or not rows:
            break

        if first_row is None:
            first_row = rows[0]

        for row in rows:
            name = str(row.get("itemname", "")).strip()
            code = str(row.get("itemcode", "")).strip() or name
            if code in seen:
                continue

            stock_type = str(row.get("type", "")).upper()
            is_etf_etn = (stock_type in ("EF", "EN", "ETF", "ETN")) or bool(row.get("etfType"))
            if is_excluded_stock(name, is_etf_etn):
                continue

            seen.add(code)
            stocks.append(
                {
                    "name": name,
                    "symbol": code,
                    "eng_name": "",
                    "rate": parse_float(row.get("prevChangeRate"), 0.0),
                    "val": parse_int(row.get("tradeAmount")),
                }
            )
            if len(stocks) >= 100:
                break

        if len(stocks) >= 100:
            break

    if not stocks and mode == "KRX":
        print("KRX 전용 API 응답 없음 → 모바일 API(ETF 사전 제외)로 전환합니다.")
        return get_krx_mobile_fallback_top100()

    if not stocks or first_row is None:
        return [], "", False

    is_open = str(first_row.get("marketStatus", "")).upper() == "OPEN"
    if mode == "NXT" and now_hour < 9:
        updated_at = str(first_row.get("tradableStatusUpdatedAt", ""))
        traded_date = updated_at[:10].replace("-", "") if updated_at else ""
    else:
        traded_date = get_latest_traded_date()

    stocks.sort(key=lambda x: x["val"], reverse=True)
    return stocks[:100], traded_date, is_open


def get_us_top100():
    """네이버 해외주식 미국(USA) 거래대금 상위 API에서 ETF 제외 후 순수 개별 종목 100위 조회"""
    stocks = []
    seen = set()
    traded_date = ""
    market_status = ""

    for page in (1, 2, 3):
        url = f"https://api.stock.naver.com/stock/nation/USA/priceTop?page={page}&pageSize=100"
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
        except Exception as e:
            print(f"네이버 미국 증시 조회 예외(p{page}): {e}")
            break

        if res.status_code != 200:
            print(f"네이버 미국 증시 조회 실패(p{page}): {res.status_code}")
            break

        data = res.json()
        if not market_status:
            market_status = str(data.get("marketStatus", "")).upper()

        rows = data.get("stocks", [])
        if not rows:
            if page == 1 and market_status == "PREOPEN":
                print(f"미국 증시 프리마켓 개장 전({data.get('localOpenTimeDesc', 'PREOPEN')})이라 순위 데이터가 비어 있습니다.")
            break

        if not traded_date:
            traded_at = str(rows[0].get("localTradedAt", ""))
            traded_date = traded_at[:10].replace("-", "") if traded_at else ""

        for item in rows:
            symbol = str(item.get("symbolCode") or item.get("reutersCode", "").split(".")[0]).strip()
            kor_name = str(item.get("stockName", "")).strip()
            eng_name = str(item.get("stockNameEng", "")).strip()
            if not symbol and not kor_name:
                continue

            key = symbol or kor_name
            if key in seen:
                continue

            end_type = str(item.get("stockEndType", "")).lower()
            is_etf_etn = (end_type in ("etf", "etn")) or (end_type != "" and end_type != "stock")
            if is_excluded_us_stock(symbol, kor_name, eng_name, is_etf_etn):
                continue

            display_name = f"{kor_name} ({symbol})" if kor_name and symbol and symbol not in kor_name else (kor_name or symbol)
            seen.add(key)
            stocks.append(
                {
                    "name": display_name,
                    "symbol": symbol,
                    "eng_name": eng_name,
                    "rate": parse_float(item.get("fluctuationsRatio"), 0.0),
                    "val": parse_us_trading_value(item),
                }
            )
            if len(stocks) >= 100:
                break

        if len(stocks) >= 100:
            break

    stocks.sort(key=lambda x: x["val"], reverse=True)
    return stocks[:100], traded_date, market_status


def get_google_news(stock_name):
    queries = [f"{stock_name} 특징주 OR 급등", f"{stock_name} 주가"]
    entries = []

    for q in queries:
        encoded_query = urllib.parse.quote(q)
        rss_url = (
            f"https://news.google.com/rss/search"
            f"?q={encoded_query}+when:1d&hl=ko&gl=KR&ceid=KR:ko"
        )
        feed = feedparser.parse(rss_url)
        if feed.entries:
            entries = feed.entries[:3]
            break

    if not entries:
        return "- 관련 최신 기사 없음"

    return "\n".join(
        f"- {getattr(entry, 'title', '')} ({getattr(entry, 'link', '')})"
        for entry in entries
    )


def get_us_google_news(symbol, eng_name, display_name):
    """미국 주식용 영문 구글 뉴스 RSS 조회"""
    queries = []
    if symbol:
        queries.append(f"{symbol} stock surge OR jump OR rally OR earnings")
        queries.append(f"{symbol} stock news")
    if eng_name:
        queries.append(f'"{eng_name}" stock')

    entries = []
    for q in queries:
        encoded_query = urllib.parse.quote(q)
        rss_url = (
            f"https://news.google.com/rss/search"
            f"?q={encoded_query}+when:2d&hl=en-US&gl=US&ceid=US:en"
        )
        feed = feedparser.parse(rss_url)
        if feed.entries:
            entries = feed.entries[:3]
            break

    if not entries:
        return get_google_news(display_name.split("(")[0].strip())

    return "\n".join(
        f"- {getattr(entry, 'title', '')} ({getattr(entry, 'link', '')})"
        for entry in entries
    )


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for i in range(0, len(text), 4000):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text[i : i + 4000],
            "disable_web_page_preview": True,
        }
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            print("텔레그램 전송 오류:", res.text)


def send_telegram_file(file_path, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    with open(file_path, "rb") as f:
        res = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
            files={"document": f},
            timeout=15,
        )
    if res.status_code != 200:
        print("텔레그램 파일 전송 오류:", res.text)


def get_github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def upload_file_to_github(repo_path, content_str, commit_message):
    """GitHub REST API로 파일 업로드(기존 파일이 있으면 덮어쓰기)"""
    if not GITHUB_TOKEN or "여기에_" in GITHUB_TOKEN or not GITHUB_USER or "여기에_" in GITHUB_USER:
        print("GitHub 정보가 입력되지 않아 웹 업로드를 건너뜁니다.")
        return False

    api_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{repo_path}"
    gh_headers = get_github_headers()

    sha = None
    get_res = requests.get(api_url, headers=gh_headers, timeout=15)
    if get_res.status_code == 200:
        sha = get_res.json().get("sha")

    encoded = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
    payload = {"message": commit_message, "content": encoded}
    if sha:
        payload["sha"] = sha

    put_res = requests.put(api_url, headers=gh_headers, json=payload, timeout=20)
    if put_res.status_code in (200, 201):
        return True

    print(f"GitHub 업로드 오류 ({repo_path}): {put_res.status_code} {put_res.text}")
    return False


def fetch_github_report_filenames():
    """GitHub reports 폴더에 저장된 기존 HTML 파일명 목록 조회"""
    api_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/reports"
    try:
        res = requests.get(api_url, headers=get_github_headers(), timeout=15)
        if res.status_code == 200 and isinstance(res.json(), list):
            return [
                item.get("name", "")
                for item in res.json()
                if str(item.get("name", "")).endswith(".html")
            ]
    except Exception as e:
        print(f"GitHub 목록 조회 오류(무시하고 진행): {e}")
    return []


def render_indicators_section(indicators, updated_str):
    if not indicators:
        return ""
    cards = []
    for ind in indicators:
        label = html.escape(str(ind.get("label", "")))
        value = html.escape(str(ind.get("value", "-")))
        rate = html.escape(str(ind.get("rate", "-")))
        direction = ind.get("direction", "flat")
        rate_cls = "ind-up" if direction == "up" else ("ind-down" if direction == "down" else "ind-flat")
        cards.append(
            f"""
            <div class="ind-card">
              <div class="ind-label">{label}</div>
              <div class="ind-value">{value}</div>
              <div class="ind-rate {rate_cls}">{rate}</div>
            </div>
            """
        )
    return f"""
    <section class="ind-section">
      <div class="ind-header">
        <span class="ind-title">🌐 주요 시장 지표</span>
        <span class="ind-time">업데이트: {updated_str}</span>
      </div>
      <div class="ind-grid">
        {''.join(cards)}
      </div>
    </section>
    """


def render_market_section(items, is_us=False):
    if not items:
        msg = "아직 등록된 미국 증시 리포트가 없습니다." if is_us else "아직 등록된 국내 증시 리포트가 없습니다."
        return f'<p class="empty">{msg}</p>'

    latest = items[0]
    d, t, m = latest["date"], latest["time"], latest["mode"]
    latest_label = f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:]} ({m})"
    latest_href = f"reports/{html.escape(latest['filename'])}"
    banner_cls = "latest-banner latest-us" if is_us else "latest-banner"
    icon = "🇺🇸" if is_us else "📈"

    latest_html = f"""
    <a class="{banner_cls}" href="{latest_href}">
      <div>
        <div class="latest-tag">가장 최근 리포트</div>
        <div class="latest-title">{icon} {latest_label} 급등주 바로 보기</div>
      </div>
      <span class="latest-arrow">→</span>
    </a>
    """

    filter_html = ""
    if not is_us:
        filter_html = """
        <div class="filters">
          <button class="filter-btn active" data-filter="ALL">전체</button>
          <button class="filter-btn" data-filter="KRX">KRX 정규장</button>
          <button class="filter-btn" data-filter="NXT">NXT 프리·애프터</button>
        </div>
        """

    grouped = defaultdict(list)
    for item in items:
        grouped[item["date"]].append(item)

    groups_html = []
    for date_key in sorted(grouped.keys(), reverse=True):
        fmt_date = f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:]}"
        cards = []
        for item in grouped[date_key]:
            t = item["time"]
            m = item["mode"]
            fmt_time = f"{t[:2]}:{t[2:]}"
            if m == "US":
                badge_cls = "badge-us"
                session_label = "미국 정규장 마감"
            elif m == "KRX":
                badge_cls = "badge-krx"
                session_label = "정규장"
            else:
                badge_cls = "badge-nxt"
                session_label = "프리마켓" if int(t[:2]) < 9 else "애프터마켓"
            href = f"reports/{html.escape(item['filename'])}"
            cards.append(
                f"""
                <a class="report-card" data-mode="{m}" href="{href}">
                  <div class="card-left">
                    <span class="badge {badge_cls}">{m}</span>
                    <span class="card-time">{fmt_time}</span>
                    <span class="card-session">{session_label}</span>
                  </div>
                  <span class="card-arrow">열기 →</span>
                </a>
                """
            )
        groups_html.append(
            f"""
            <section class="date-group">
              <h2 class="date-title">📅 {fmt_date}</h2>
              <div class="card-grid">
                {''.join(cards)}
              </div>
            </section>
            """
        )

    return latest_html + filter_html + "".join(groups_html)


def build_index_html(report_filenames, indicators=None):
    """11개 주요 시장 지표 + 국내(KRX/NXT)/미국(US) 분리형 메인 대시보드(index.html) 생성"""
    kr_items = []
    us_items = []

    for fname in set(report_filenames):
        if not fname.endswith(".html"):
            continue
        stem = fname[:-5]
        parts = stem.split("_")
        if len(parts) >= 3 and len(parts[0]) == 8 and len(parts[1]) == 4:
            date_str, time_str, mode = parts[0], parts[1], parts[2].upper()
            entry = {"filename": fname, "date": date_str, "time": time_str, "mode": mode}
            if mode == "US":
                us_items.append(entry)
            else:
                kr_items.append(entry)

    kr_items.sort(key=lambda x: (x["date"], x["time"]), reverse=True)
    us_items.sort(key=lambda x: (x["date"], x["time"]), reverse=True)

    updated_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    indicators_html = render_indicators_section(indicators or [], updated_str)
    kr_section = render_market_section(kr_items, is_us=False)
    us_section = render_market_section(us_items, is_us=True)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>증시 급등주 리포트 (국내 / 미국)</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Malgun Gothic', -apple-system, sans-serif; background: #f1f5f9; color: #0f172a; margin: 0; padding: 16px; }}
  .container {{ max-width: 900px; margin: 0 auto; }}
  header {{ background: #1e293b; color: #fff; padding: 22px 24px; border-radius: 14px; margin-bottom: 14px; }}
  header h1 {{ margin: 0 0 6px; font-size: 22px; }}
  header p {{ margin: 0; font-size: 13px; color: #cbd5e1; }}
  .ind-section {{ background: #fff; border-radius: 14px; padding: 16px 18px; margin-bottom: 16px; box-shadow: 0 1px 6px rgba(0,0,0,0.05); }}
  .ind-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; gap: 8px; flex-wrap: wrap; }}
  .ind-title {{ font-size: 15px; font-weight: 700; color: #1e293b; }}
  .ind-time {{ font-size: 12px; color: #64748b; }}
  .ind-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 9px; }}
  .ind-card {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px 12px; }}
  .ind-label {{ font-size: 12px; color: #64748b; font-weight: 600; margin-bottom: 4px; }}
  .ind-value {{ font-size: 15px; font-weight: 700; color: #0f172a; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .ind-rate {{ font-size: 12px; font-weight: 700; margin-top: 3px; }}
  .ind-up {{ color: #dc2626; }}
  .ind-down {{ color: #2563eb; }}
  .ind-flat {{ color: #64748b; }}
  .market-tabs {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 18px; }}
  .market-tab {{ border: 2px solid #cbd5e1; background: #fff; color: #334155; padding: 13px 16px; border-radius: 12px; font-size: 15px; font-weight: 700; cursor: pointer; transition: all 0.15s; }}
  .market-tab:hover {{ border-color: #64748b; }}
  .market-tab.active {{ background: #0f172a; color: #fff; border-color: #0f172a; box-shadow: 0 3px 10px rgba(15,23,42,0.18); }}
  .latest-banner {{ display: flex; justify-content: space-between; align-items: center; background: linear-gradient(135deg, #2563eb, #1d4ed8); color: #fff; text-decoration: none; padding: 18px 20px; border-radius: 14px; margin-bottom: 18px; box-shadow: 0 4px 12px rgba(37,99,235,0.22); }}
  .latest-banner.latest-us {{ background: linear-gradient(135deg, #059669, #047857); box-shadow: 0 4px 12px rgba(5,150,105,0.22); }}
  .latest-banner:hover {{ filter: brightness(1.05); }}
  .latest-tag {{ font-size: 12px; opacity: 0.85; margin-bottom: 4px; }}
  .latest-title {{ font-size: 17px; font-weight: 700; }}
  .latest-arrow {{ font-size: 22px; font-weight: 700; }}
  .filters {{ display: flex; gap: 8px; margin-bottom: 18px; }}
  .filter-btn {{ border: 1px solid #cbd5e1; background: #fff; color: #334155; padding: 8px 16px; border-radius: 999px; font-size: 14px; font-weight: 600; cursor: pointer; }}
  .filter-btn.active {{ background: #0f172a; color: #fff; border-color: #0f172a; }}
  .date-group {{ background: #fff; border-radius: 14px; padding: 18px 20px; margin-bottom: 14px; box-shadow: 0 1px 6px rgba(0,0,0,0.05); }}
  .date-title {{ font-size: 16px; margin: 0 0 12px; color: #1e293b; border-bottom: 1px solid #e2e8f0; padding-bottom: 8px; }}
  .card-grid {{ display: flex; flex-direction: column; gap: 8px; }}
  .report-card {{ display: flex; justify-content: space-between; align-items: center; padding: 12px 14px; border: 1px solid #e2e8f0; border-radius: 10px; text-decoration: none; color: #0f172a; transition: background 0.15s; }}
  .report-card:hover {{ background: #f8fafc; border-color: #94a3b8; }}
  .card-left {{ display: flex; align-items: center; gap: 10px; }}
  .badge {{ font-size: 12px; font-weight: 700; padding: 3px 9px; border-radius: 6px; }}
  .badge-krx {{ background: #fee2e2; color: #b91c1c; }}
  .badge-nxt {{ background: #dbeafe; color: #1d4ed8; }}
  .badge-us {{ background: #dcfce7; color: #15803d; }}
  .card-time {{ font-size: 15px; font-weight: 700; }}
  .card-session {{ font-size: 13px; color: #64748b; }}
  .card-arrow {{ font-size: 13px; color: #2563eb; font-weight: 600; }}
  .empty {{ text-align: center; color: #64748b; padding: 48px 0; background: #fff; border-radius: 14px; }}
</style>
</head>
<body>
  <div class="container">
    <header>
      <h1>📊 한·미 증시 급등주 리포트</h1>
      <p>순수 개별 종목 거래대금 상위 100위 중 10%↑ 상승 종목 · 최근 업데이트: {updated_str}</p>
    </header>
    {indicators_html}
    <div class="market-tabs">
      <button class="market-tab active" data-tab="kr">🇰🇷 국내 증시 ({len(kr_items)})</button>
      <button class="market-tab" data-tab="us">🇺🇸 미국 증시 ({len(us_items)})</button>
    </div>
    <div id="panel-kr" class="market-panel">
      {kr_section}
    </div>
    <div id="panel-us" class="market-panel" style="display:none;">
      {us_section}
    </div>
  </div>
  {INDEX_SCRIPT}
</body>
</html>
"""


def update_index_only(today, time_str):
    """급등주가 없는 경우에도 메인 페이지의 11개 시장 지표만 최신 상태로 갱신"""
    indicators = fetch_market_indicators()
    all_reports = fetch_github_report_filenames()
    index_html = build_index_html(all_reports, indicators)
    if upload_file_to_github("index.html", index_html, f"Update market indicators ({today} {time_str})"):
        print(f"메인 페이지 시장 지표 갱신 완료: {get_home_url()}")


def publish_to_github_pages(today, time_str, mode, html_doc):
    """개별 리포트(reports/...) 업로드 후 시장 지표 + 메인 페이지(index.html) 자동 갱신"""
    report_filename = f"{today}_{time_str}_{mode}.html"
    commit_msg = f"Update {mode} stock report ({today} {time_str})"

    ok_archive = upload_file_to_github(f"reports/{report_filename}", html_doc, commit_msg)
    if not ok_archive:
        return None, None

    print("주요 시장 지표 11개 조회 중...")
    indicators = fetch_market_indicators()
    all_reports = fetch_github_report_filenames()
    if report_filename not in all_reports:
        all_reports.append(report_filename)

    index_html = build_index_html(all_reports, indicators)
    upload_file_to_github("index.html", index_html, f"Update index page ({today} {time_str})")

    home_url = get_home_url()
    report_url = f"{home_url}reports/{report_filename}"
    tab_home_url = f"{home_url}#us" if mode == "US" else home_url
    print(f"GitHub 웹 업로드 완료: {report_url}")
    print(f"메인 페이지 주소: {tab_home_url}")
    return report_url, tab_home_url


def save_html_report(today, time_str, mode, items):
    os.makedirs(SAVE_DIR, exist_ok=True)
    file_path = os.path.join(SAVE_DIR, f"{today}_{time_str}_{mode}_급등주.html")
    formatted_date = f"{today[:4]}-{today[4:6]}-{today[6:]}"
    formatted_time = f"{time_str[:2]}:{time_str[2:]}"
    is_us = mode == "US"
    val_sort_type = "usd" if is_us else "krw"
    icon = "🇺🇸" if is_us else "📈"
    market_title = "미국 증시" if is_us else "한국 증시"

    rows_html = []
    for idx, item in enumerate(items, 1):
        name = html.escape(str(item.get("name", "")))
        rate = html.escape(str(item.get("rate", "")))
        trading_value = html.escape(str(item.get("trading_value", "-")))
        theme = html.escape(str(item.get("theme", "")))
        catalyst = html.escape(str(item.get("catalyst", "")))
        news_title = html.escape(str(item.get("news_title", "관련 최신 기사 없음")))
        news_link = str(item.get("news_link", "")).strip()

        if news_link.startswith("http"):
            safe_link = html.escape(news_link)
            news_cell = f'<a href="{safe_link}" target="_blank" rel="noopener">{news_title}</a>'
        else:
            news_cell = news_title

        rows_html.append(
            f"""
            <tr>
                <td class="num">{idx}</td>
                <td class="name">{name}</td>
                <td class="rate">{rate}</td>
                <td class="value">{trading_value}</td>
                <td>{theme}</td>
                <td>{catalyst}</td>
                <td class="news">{news_cell}</td>
            </tr>
            """
        )

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    tbody_html = "".join(rows_html)
    home_url = get_home_url()
    home_href = f"{home_url}#us" if (home_url and is_us) else home_url
    home_btn_html = (
        f'<a class="home-link" href="{html.escape(home_href)}">← 전체 리포트 목록 (홈)</a>'
        if home_href
        else ""
    )

    html_doc = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{formatted_date} {formatted_time} {market_title} 거래대금 상위 100위 급등주 ({mode}, 10%↑)</title>
<style>
  body {{ font-family: 'Malgun Gothic', -apple-system, sans-serif; background: #f5f7fa; color: #222; margin: 0; padding: 16px; }}
  .container {{ max-width: 1280px; margin: 0 auto; background: #fff; padding: 20px; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }}
  .top-bar {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 8px; }}
  .home-link {{ display: inline-block; font-size: 13px; font-weight: 600; color: #1d4ed8; text-decoration: none; background: #eff6ff; padding: 6px 12px; border-radius: 8px; }}
  .home-link:hover {{ background: #dbeafe; }}
  h1 {{ font-size: 20px; margin: 0; color: #111; }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 16px; }}
  .table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ padding: 11px 12px; border-bottom: 1px solid #e5e8eb; text-align: left; vertical-align: top; line-height: 1.5; }}
  th {{ background: #1e293b; color: #fff; font-weight: 600; white-space: nowrap; }}
  th.sortable {{ cursor: pointer; user-select: none; position: relative; padding-right: 26px; }}
  th.sortable:hover {{ background: #334155; }}
  th.sortable::after {{ content: "↕"; position: absolute; right: 8px; opacity: 0.45; font-size: 12px; }}
  th.sortable.asc::after {{ content: "▲"; opacity: 1; color: #38bdf8; }}
  th.sortable.desc::after {{ content: "▼"; opacity: 1; color: #38bdf8; }}
  tr:hover {{ background: #f8fafc; }}
  .num {{ text-align: center; color: #64748b; width: 36px; }}
  .name {{ font-weight: 700; white-space: nowrap; }}
  .rate {{ color: #d92b2b; font-weight: 700; white-space: nowrap; }}
  .value {{ color: #0f172a; font-weight: 600; white-space: nowrap; }}
  .news a {{ color: #1d4ed8; text-decoration: none; }}
  .news a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
  <div class="container">
    <div class="top-bar">
      <h1>{icon} {formatted_date} {formatted_time} {market_title} 거래대금 상위 100위 내 급등주 ({mode}, 10%↑)</h1>
      {home_btn_html}
    </div>
    <div class="meta">기준 시장: {mode} (개별 종목 100위 기준) · 기본 정렬: 거래대금순 · 총 {len(items)}개 종목 · 생성 시각: {now_str} · 열 제목을 누르면 정렬됩니다.</div>
    <div class="table-wrap">
      <table id="stockTable">
        <thead>
          <tr>
            <th class="sortable" data-type="number">#</th>
            <th class="sortable" data-type="text">종목명</th>
            <th class="sortable" data-type="number">{mode} 상승률</th>
            <th class="sortable desc" data-type="{val_sort_type}" data-dir="desc">{mode} 거래대금</th>
            <th class="sortable" data-type="text">테마</th>
            <th>주요 호재</th>
            <th>연관 최신 기사</th>
          </tr>
        </thead>
        <tbody>
          {tbody_html}
        </tbody>
      </table>
    </div>
  </div>
  {SORT_SCRIPT}
</body>
</html>
"""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_doc)

    return file_path, html_doc


def format_telegram_text(today, time_str, mode, items, report_url=None, home_url=None):
    formatted_time = f"{time_str[:2]}:{time_str[2:]}"
    icon = "🇺🇸" if mode == "US" else "📈"
    market_label = "미국 증시" if mode == "US" else "거래대금 상위 100위 내"
    header = [f"{icon} {today} {formatted_time} {market_label} 급등주 ({mode}, 10%↑ · 거래대금순)"]
    if report_url:
        header.append(f"🌐 이번 리포트 보기: {report_url}")
    if home_url:
        header.append(f"🏠 전체 목록(홈): {home_url}")
    lines = ["\n".join(header)]

    for idx, item in enumerate(items, 1):
        name = item.get("name", "")
        rate = item.get("rate", "")
        trading_value = item.get("trading_value", "-")
        theme = item.get("theme", "")
        catalyst = item.get("catalyst", "")
        news_title = item.get("news_title", "관련 최신 기사 없음")
        news_link = item.get("news_link", "")

        block = [
            f"{idx}. {name} ({rate} | {mode} 거래대금 {trading_value})",
            f"- 테마: {theme}",
            f"- 호재: {catalyst}",
            f"- 기사: {news_title}",
        ]
        if news_link:
            block.append(f"  {news_link}")
        lines.append("\n".join(block))

    return "\n\n".join(lines)


def main():
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M")
    is_manual = len(sys.argv) > 1 and sys.argv[1].lower() in ("krx", "nxt", "us")

    if is_manual:
        mode = sys.argv[1].upper()
    else:
        if now.hour < 8:
            mode = "US"
        elif 9 <= now.hour < 17:
            mode = "KRX"
        else:
            mode = "NXT"

    print(f"[{today} {time_str}] 네이버 증권({mode}) 순수 개별 종목 거래대금 상위 100위 조회 시작...")

    if mode == "US":
        top100, traded_date, market_status = get_us_top100()
        if not top100:
            print("네이버 미국 증시 데이터를 불러오지 못했습니다.")
            return
        expected_us_date = (now - timedelta(days=1)).strftime("%Y%m%d")
        if not is_manual and traded_date and traded_date != expected_us_date:
            print(f"[{today}] 미국 증시 휴장일입니다. (최근 거래일: {traded_date}, 예상 거래일: {expected_us_date})")
            return
    else:
        top100, traded_date, is_open = get_market_top100(mode, now.hour)
        if not top100:
            print(f"네이버 증권({mode}) 데이터를 불러오지 못했습니다.")
            return
        if mode == "NXT" and now.hour < 9 and not is_manual and not is_open:
            print(f"[{today}] NXT 프리마켓 미개장(휴장일)입니다.")
            return
        if traded_date and traded_date != today:
            print(f"[{today}] 휴장일입니다. (최근 거래일: {traded_date})")
            return

    targets = []
    for item in top100:
        name = item["name"]
        rate = item["rate"]
        if rate >= 10.0:
            rate_str = f"+{rate:.2f}%"
            val = item["val"]
            val_str = format_usd(val) if mode == "US" else format_krw(val)
            targets.append((name, val, rate, rate_str, val_str, item.get("symbol", ""), item.get("eng_name", "")))

    targets.sort(key=lambda x: x[1], reverse=True)
    val_map = {name: val for name, val, _, _, _, _, _ in targets}

    if not targets:
        update_index_only(today, time_str)
        send_telegram(f"[{today} {time_str[:2]}:{time_str[2:]}] {mode} 거래대금 상위 100위 중 조건에 해당하는 급등 종목이 없습니다.")
        print("조건 해당 종목 없음 (지표 갱신 및 안내 메시지 전송 완료)")
        return

    stock_info_list = []
    for name, _, _, rate_str, val_str, symbol, eng_name in targets:
        if mode == "US":
            news_text = get_us_google_news(symbol, eng_name, name)
        else:
            news_text = get_google_news(name)
        stock_info_list.append(
            f"종목명: {name} | 상승률: {rate_str} | 거래대금: {val_str}\n관련 뉴스:\n{news_text}"
        )

    raw_data = "\n\n".join(stock_info_list)

    client = genai.Client(api_key=GEMINI_API_KEY)
    if mode == "US":
        prompt = f"""
아래는 {today} 미국 증시(US) 순수 개별 종목 거래대금 상위 100위 중 10% 이상 상승한 종목(ETF/ETN 제외)과 구글 뉴스 최신 영문 기사 목록입니다.
각 종목의 정보를 아래 JSON 배열 형식으로만 반환해 주세요.
- name, rate, trading_value는 입력 데이터의 문자열을 토씨 하나 바꾸지 말고 그대로 유지하고, 입력된 순서를 그대로 유지하세요.
- theme, catalyst, news_title은 한국어로 자연스럽게 요약·번역해 작성하세요. (news_link는 원문 URL 그대로 유지)

[JSON 형식]
[
  {{
    "name": "종목명 (티커)",
    "rate": "+18.50%",
    "trading_value": "$450.2M",
    "theme": "핵심 테마 (한국어로 간결하게)",
    "catalyst": "주요 상승 호재 요약 (한국어 1~2문장)",
    "news_title": "가장 연관성 높은 최신 기사 헤드라인 한국어 번역 (없으면 '관련 최신 기사 없음')",
    "news_link": "해당 기사의 URL (없으면 빈 문자열 '')"
  }}
]

[데이터]
{raw_data}
"""
    else:
        prompt = f"""
아래는 {today} 한국 증시({mode}) 순수 개별 종목 거래대금 상위 100위 중 10% 이상 상승한 종목(ETF/ETN/인버스/곱버스 제외)과 구글 뉴스 최신 기사 목록입니다.
각 종목의 정보를 아래 JSON 배열 형식으로만 반환해 주세요. (name, rate, trading_value는 입력 데이터의 문자열을 토씨 하나 바꾸지 말고 그대로 유지하고, 입력된 순서를 그대로 유지하세요.)

[JSON 형식]
[
  {{
    "name": "종목명",
    "rate": "+29.95%",
    "trading_value": "175억원",
    "theme": "핵심 테마 (간결하게)",
    "catalyst": "주요 상승 호재 요약 (1~2문장)",
    "news_title": "가장 연관성 높은 최신 기사 헤드라인 1개 (없으면 '관련 최신 기사 없음')",
    "news_link": "해당 기사의 URL (없으면 빈 문자열 '')"
  }}
]

[데이터]
{raw_data}
"""

    models_to_try = [
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
        "gemini-3.8-flash",
    ]
    response = None
    last_error = None

    for model_name in models_to_try:
        for attempt in range(1, 3):
            try:
                print(f"Gemini 요청 중... (모델: {model_name}, 시도 {attempt}/2)")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                break
            except Exception as e:
                last_error = e
                err_msg = str(e)
                print(f"[{model_name}] 오류 ({attempt}/2회): {err_msg}")
                if not any(code in err_msg for code in ("503", "429", "404", "UNAVAILABLE", "RESOURCE_EXHAUSTED")):
                    raise
                if attempt < 2:
                    print("10초 후 다시 시도합니다...")
                    time.sleep(10)
        if response is not None:
            break
        print(f"{model_name} 혼잡/실패 → 다음 예비 모델로 전환합니다...")

    if response is None:
        send_telegram(f"[{today}] 모든 Gemini 모델 요청에 실패했습니다.\n오류: {last_error}")
        raise last_error

    items = json.loads(response.text)
    items.sort(key=lambda x: val_map.get(str(x.get("name", "")).strip(), -1), reverse=True)

    # 1) PC에 HTML 표 파일 저장
    html_path, html_doc = save_html_report(today, time_str, mode, items)
    print(f"HTML 표 파일 저장 완료: {html_path}")

    # 2) GitHub Pages 업로드 + 11개 시장 지표 + 메인 페이지(index.html) 자동 갱신
    report_url, home_url = publish_to_github_pages(today, time_str, mode, html_doc)

    # 3) 텔레그램 텍스트(웹 링크 + 홈 주소 포함) + HTML 파일 첨부 전송
    send_telegram(format_telegram_text(today, time_str, mode, items, report_url, home_url))
    caption = f"📊 {today} {time_str[:2]}:{time_str[2:]} {mode} 급등주 표 리포트 (거래대금순)"
    if home_url:
        caption += f"\n🏠 메인 홈: {home_url}"
    send_telegram_file(html_path, caption=caption)
    print("텔레그램 전송 완료!")


if __name__ == "__main__":
    main()
