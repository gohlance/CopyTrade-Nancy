check_pelosi_scrape.py

What it does:
- Scrapes: https://www.quiverquant.com/congresstrading/politician/Nancy%20Pelosi-P000197
- Finds the newest trade entry (heuristic parsing)
- Compares its identifier against last_seen.json
- If new, sends a Telegram message and updates last_seen.json

Usage:
- Set environment vars TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (required)
- Optionally set USE_PLAYWRIGHT=1 to force Playwright rendering fallback
- Run on schedule (GitHub Actions recommended for free scheduling)

Dependencies:
pip install requests beautifulsoup4 playwright
# If using Playwright, you must also run:
playwright install --with-deps chromium

Notes:
- This script is written defensively: tweak selectors if Quiver changes layout.
- last_seen.json is read/written in the same directory.
"""

from __future__ import annotations
import os
import re
import sys
import json
import time
@@ -40,16 +27,14 @@
LAST_SEEN_FILE = "last_seen.json"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID")
FORCE_PLAYWRIGHT = os.getenv("USE_PLAYWRIGHT", "") in ("1", "true", "True")

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0 Safari/537.36"

# logging
logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO
)


# --- Helpers: state ---
def load_last_seen() -> Dict[str, Any]:
    if not os.path.exists(LAST_SEEN_FILE):
        return {}
@@ -60,13 +45,10 @@ def load_last_seen() -> Dict[str, Any]:
        logging.warning("Failed to load last seen file: %s", e)
        return {}


def save_last_seen(obj: Dict[str, Any]) -> None:
    with open(LAST_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


# --- Helpers: telegram ---
def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        logging.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")
@@ -87,216 +69,80 @@ def send_telegram(text: str) -> bool:
        logging.exception("Failed to send Telegram message: %s", e)
        return False


# --- Parsing heuristics ---
TICKER_RE = re.compile(r"^[A-Z0-9\.\-]{1,6}$")  # allow dot/dash (e.g., BRK.B)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")  # YYYY-MM-DD style fallback


def row_to_trade(cols: List[str]) -> Optional[Dict[str, Any]]:
    """
    Given a list of cell texts for a table row, try to interpret it as a trade row.
    Expected typical columns (but may vary): [Ticker, Transaction, Filed, Traded, Description, ???]
    Returns a dict with id and summary_text if heuristics match, else None.
    """
    if not cols:
        return None
    # Trim whitespace-only cells
    cols = [c.strip() for c in cols if c and c.strip()]
    if not cols:
        return None

    # Heuristic: find a column looking like a ticker
    ticker = None
    for c in cols[:3]:  # prefer early columns
        if TICKER_RE.match(c):
            ticker = c
            break

    if not ticker:
        return None

    # Find likely transaction (Buy/Sell/Option/...); common words
    transaction = next((c for c in cols if any(w in c.lower() for w in ("buy", "sell", "purchase", "sale", "option"))), "")
    # Find traded/date-like column
    traded = next((c for c in cols if DATE_RE.search(c) or re.search(r"\bQ[1-4]\b", c) or re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", c)), "")
    # Fallback: if many columns, take 2nd or 3rd as transaction/traded
    if not transaction and len(cols) >= 2:
        transaction = cols[1]
    if not traded and len(cols) >= 4:
        traded = cols[3]

    identifier = f"{ticker}||{transaction}||{traded}"
    summary_text = f"{traded} — {transaction} {ticker} — {' | '.join(cols[4:]) if len(cols) > 4 else ''}".strip()
    return {"id": identifier, "raw": cols, "summary_text": summary_text}


def parse_trades_from_html(html: str) -> List[Dict[str, Any]]:
    """
    Return a list of trade dicts (latest first heuristically).
    Scans for table rows and applies heuristics.
    """
    soup = BeautifulSoup(html, "html.parser")
    trades = []

    # Strategy:
    # 1) Look for tables under headings that contain "Trades"
    # 2) Fall back to scanning every <tr>
    # 3) Convert each <tr> to list of cell texts and try to interpret
    # 4) Keep first N valid trades (we only need newest)
    # 5) Return in appearance order (assume top-most is newest)
    # Attempt 1: headings
    possible_tables = []
    for h in soup.find_all(["h2", "h3", "h4", "div"]):
        if h.get_text(strip=True).lower().startswith("trades"):
            # find table sibling or descendant
            parent = h.parent
            if parent:
                possible_tables.extend(parent.find_all("table"))
            # also try next siblings
            sib = h.find_next_sibling()
            if sib:
                possible_tables.extend(sib.find_all("table"))
    # Also gather any table at all
    if not possible_tables:
        possible_tables = soup.find_all("table")

    # Parse table rows
    for table in possible_tables:
        for tr in table.find_all("tr"):
            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            trade = row_to_trade(cols)
            if trade:
                trades.append(trade)
        if trades:
            break  # prefer the first table that yields trades

    # Fallback: scan all <tr> on the page
    if not trades:
        for tr in soup.find_all("tr"):
            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            trade = row_to_trade(cols)
            if trade:
                trades.append(trade)
    # De-dup while preserving order
    seen = set()
    unique = []
    for t in trades:
        if t["id"] not in seen:
            seen.add(t["id"])
            unique.append(t)
    return unique


# --- Fetchers ---
def fetch_via_requests(url: str) -> Optional[str]:
    try:
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logging.warning("requests fetch failed: %s", e)
        return None


def fetch_via_playwright(url: str) -> Optional[str]:
    """
    Uses playwright to render the page and return final HTML.
    Requires playwright installed and browsers installed.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        logging.warning("playwright import failed: %s", e)
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, timeout=45000)
            # Wait for some element that suggests trades table loaded.
            # This is defensive: wait for either "Trades" text or a table row.
            try:
                page.wait_for_selector("text=Trades", timeout=15000)
            except Exception:
                # ignore, we'll grab content anyway
                pass
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        logging.exception("playwright fetch/render failed: %s", e)
        return None


# --- Main logic ---
def main() -> int:
    logging.info("Starting Pelosi trade check for %s", POLITICIAN_PAGE)



    last = load_last_seen()
    last_id = last.get("last_trade_id")

    html = None
    trades = []

    # 1) Try fast requests parse, unless forced to use playwright
    if not FORCE_PLAYWRIGHT:
        logging.info("Attempting requests-based fetch...")
        html = fetch_via_requests(POLITICIAN_PAGE)
        if html:
            trades = parse_trades_from_html(html)
            if trades:
                logging.info("Found %d trades via requests parsing.", len(trades))
            else:
                logging.info("No trades found via requests parsing.")
        else:
            logging.info("Requests fetch returned no HTML.")
    else:
        logging.info("FORCE_PLAYWRIGHT set, skipping requests fetch.")

    # 2) If nothing found, fallback to Playwright (unless already tried / disabled)
    if not trades:
        logging.info("Attempting Playwright rendering fetch as fallback...")
        html2 = fetch_via_playwright(POLITICIAN_PAGE)
        if html2:
            trades = parse_trades_from_html(html2)
            if trades:
                logging.info("Found %d trades via Playwright parsing.", len(trades))
            else:
                logging.info("Playwright parsing found no trades.")
        else:
            logging.error("Playwright render failed or unavailable.")

    if not trades:
        logging.error("No trades could be identified. Exiting.")
        return 2

    latest = trades[0]
    logging.info("Latest trade id: %s", latest["id"])
    if latest["id"] == last_id:
        logging.info("No new trade (id matches last_seen).")
        return 0

    # Compose message
    message = (
        f"🟢 <b>New Pelosi trade detected</b>\n"
        f"{latest.get('summary_text','(no summary)')}\n\n"
        f"Source: {POLITICIAN_PAGE}"
    )

    ok = send_telegram(message)
    if not ok:
        logging.error("Failed to send Telegram message. Not updating last_seen.")
        return 3

    # Update last_seen.json
    now_ts = int(time.time())
    save_last_seen({"last_trade_id": latest["id"], "timestamp": now_ts, "summary": latest.get("summary_text")})
    logging.info("Updated %s with new id.", LAST_SEEN_FILE)
    return 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
