#!/usr/bin/env python3
"""
check_pelosi_scrape.py — with TEST and ERROR mode

Usage:
- Set environment variables:
  TELEGRAM_BOT_TOKEN (required)
  TELEGRAM_CHAT_ID (required)
  TEST_MODE=1  # optional, sends a test message instead of scraping
  ERROR_MODE=1 # optional, sends errors to Telegram
- Run on schedule (GitHub Actions)
"""

from __future__ import annotations
import os
import sys
import json
import time
import logging
from typing import Optional, Dict, Any, List

import requests
from bs4 import BeautifulSoup

# --- Config ---
POLITICIAN_PAGE = "https://www.quiverquant.com/congresstrading/politician/Nancy%20Pelosi-P000197"
LAST_SEEN_FILE = "last_seen.json"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID")
TEST_MODE = os.getenv("TEST_MODE", "") in ("1", "true", "True")
ERROR_MODE = os.getenv("ERROR_MODE", "") in ("1", "true", "True")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0 Safari/537.36"

# logging
logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)

# --- Helpers ---
def load_last_seen() -> Dict[str, Any]:
    if not os.path.exists(LAST_SEEN_FILE):
        return {}
    try:
        with open(LAST_SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.warning("Failed to load last seen file: %s", e)
        return {}

def save_last_seen(obj: Dict[str, Any]) -> None:
    with open(LAST_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)

def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        logging.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        logging.info("Telegram message sent.")
        return True
    except Exception as e:
        logging.exception("Failed to send Telegram message: %s", e)
        return False

# --- Parsing Helpers ---
TICKER_RE = r"^[A-Z0-9\.\-]{1,6}$"
DATE_RE = r"\d{4}-\d{2}-\d{2}"

def row_to_trade(cols: List[str]) -> Optional[Dict[str, Any]]:
    cols = [c.strip() for c in cols if c and c.strip()]
    if not cols: return None
    ticker = next((c for c in cols[:3] if __import__("re").match(TICKER_RE, c)), None)
    if not ticker: return None
    transaction = next((c for c in cols if any(w in c.lower() for w in ("buy","sell","purchase","sale","option"))), "")
    traded = next((c for c in cols if __import__("re").search(DATE_RE, c)), "")
    if not transaction and len(cols) >= 2: transaction = cols[1]
    if not traded and len(cols) >= 4: traded = cols[3]
    identifier = f"{ticker}||{transaction}||{traded}"
    summary_text = f"{traded} — {transaction} {ticker} — {' | '.join(cols[4:]) if len(cols) > 4 else ''}".strip()
    return {"id": identifier, "raw": cols, "summary_text": summary_text}

def parse_trades_from_html(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    trades = []
    tables = soup.find_all("table")
    for table in tables:
        for tr in table.find_all("tr"):
            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td","th"])]
            trade = row_to_trade(cols)
            if trade: trades.append(trade)
        if trades: break
    return trades

def fetch_html(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logging.warning("Failed to fetch HTML via requests: %s", e)
        return None

# --- Main logic ---
def main() -> int:
    if TEST_MODE:
        send_telegram("✅ Test message: Bot is running in TEST_MODE.")
        return 0

    last = load_last_seen()
    last_id = last.get("last_trade_id")

    try:
        html = fetch_html(POLITICIAN_PAGE)
        if not html:
            raise RuntimeError("Failed to fetch Pelosi page HTML.")
        trades = parse_trades_from_html(html)
        if not trades:
            raise RuntimeError("No trades found on Pelosi page.")

        latest = trades[0]
        if latest["id"] == last_id:
            logging.info("No new trade.")
            return 0

        message = (
            f"🟢 <b>New Pelosi trade detected</b>\n"
            f"{latest.get('summary_text','(no summary)')}\n\n"
            f"Source: {POLITICIAN_PAGE}"
        )
        if send_telegram(message):
            save_last_seen({"last_trade_id": latest["id"], "timestamp": int(time.time()), "summary": latest.get("summary_text")})
        return 0

    except Exception as e:
        logging.exception("Error during scrape or send: %s", e)
        if ERROR_MODE:
            send_telegram(f"❌ <b>Bot Error:</b>\n{e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
