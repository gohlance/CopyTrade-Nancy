#!/usr/bin/env python3
"""
check_congress_trades.py — track Pelosi and all trades from Quiver

- Pelosi page: https://www.quiverquant.com/congresstrading/politician/Nancy%20Pelosi-P000197
- Main page: https://www.quiverquant.com/congresstrading/
- Sends new trades to Telegram
- Supports TEST_MODE and ERROR_MODE
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
PAGES = [
    ("Pelosi", "https://www.quiverquant.com/congresstrading/politician/Nancy%20Pelosi-P000197"),
    ("All Congress Trades", "https://www.quiverquant.com/congresstrading/")
]
LAST_SEEN_FILE = "last_seen.json"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID")
TEST_MODE = os.getenv("TEST_MODE", "") in ("1", "true", "True")
ERROR_MODE = os.getenv("ERROR_MODE", "") in ("1", "true", "True")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0 Safari/537.36"

# Logging
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
def parse_trades_from_html(html: str, page_name: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    trades = []
    tables = soup.find_all("table")
    for table in tables:
        for tr in table.find_all("tr"):
            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td","th"])]
            if not cols or len(cols) < 3:
                continue
            # Heuristic parsing
            ticker = cols[0]
            transaction = cols[1] if len(cols) > 1 else ""
            date = cols[2] if len(cols) > 2 else ""
            politician = page_name if page_name == "Pelosi" else (cols[3] if len(cols) > 3 else "Unknown")
            trade_id = f"{politician}||{ticker}||{transaction}||{date}"
            summary_text = f"{date} — {transaction} {ticker} — {politician}"
            trades.append({"id": trade_id, "summary_text": summary_text})
    return trades

def fetch_html(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logging.warning("Failed to fetch HTML: %s", e)
        return None

# --- Main logic ---
def main() -> int:
    if TEST_MODE:
        send_telegram("✅ Test message: Bot is running in TEST_MODE.")
        return 0

    last_seen = load_last_seen()
    new_last_seen = last_seen.copy()

    try:
        for page_name, url in PAGES:
            html = fetch_html(url)
            if not html:
                raise RuntimeError(f"Failed to fetch {url}")
            trades = parse_trades_from_html(html, page_name)
            for trade in trades:
                if trade["id"] not in last_seen:
                    message = f"🟢 <b>New Trade Detected</b>\n{trade['summary_text']}\nSource: {url}"
                    if send_telegram(message):
                        new_last_seen[trade["id"]] = {"summary": trade["summary_text"], "timestamp": int(time.time())}

        save_last_seen(new_last_seen)
        return 0

    except Exception as e:
        logging.exception("Error during scrape or send: %s", e)
        if ERROR_MODE:
            send_telegram(f"❌ <b>Bot Error:</b>\n{e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
