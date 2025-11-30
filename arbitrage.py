"""Simple funding rate arbitrage checker for Hyperliquid and Lighter.

The script queries public HTTP APIs to pull current funding rates and
best bid/ask quotes for a chosen perpetual market on both venues, then
computes a rough funding-rate spread and an estimated profit for a given
position size.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
LIGHTER_API = "https://api.lighter.xyz/v1/public"
DEFAULT_MARKET = "ETH"
DEFAULT_POSITION_USD = 1000.0


def http_post(url: str, payload: dict) -> Optional[dict]:
    """Perform a JSON POST request and return a parsed response.

    Returns None if the request fails or the response cannot be decoded.
    """

    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Request to {url} failed: {exc}", file=sys.stderr)
        return None


def http_get(url: str) -> Optional[dict]:
    """Perform a JSON GET request and return a parsed response."""

    req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Request to {url} failed: {exc}", file=sys.stderr)
        return None


@dataclass
class MarketSnapshot:
    funding_rate: float
    best_bid: float
    best_ask: float

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2


def fetch_hyperliquid(market: str) -> Optional[MarketSnapshot]:
    """Fetch funding rate and top-of-book prices from Hyperliquid.

    Hyperliquid exposes a single `info` endpoint that accepts a JSON body
    with different `type` values. Here we use `funding` for the current
    rate and `l2Book` to grab the best bid/ask levels.
    """

    funding_resp = http_post(HYPERLIQUID_API, {"type": "funding", "coin": market})
    book_resp = http_post(HYPERLIQUID_API, {"type": "l2Book", "coin": market})

    if not funding_resp or not book_resp:
        return None

    try:
        funding_rate = float(funding_resp["funding"]["fundingRate"])
        bids = book_resp["levels"]["bids"]
        asks = book_resp["levels"]["asks"]
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
    except (KeyError, ValueError, IndexError) as exc:
        print(f"Unexpected Hyperliquid payload: {exc}", file=sys.stderr)
        return None

    return MarketSnapshot(funding_rate=funding_rate, best_bid=best_bid, best_ask=best_ask)


def fetch_lighter(market: str) -> Optional[MarketSnapshot]:
    """Fetch funding rate and top-of-book prices from Lighter.

    Lighter exposes REST endpoints for public market data. Funding rates
    are available through `/funding-rate`, while the order book lives at
    `/orderbook`. Both endpoints take the `symbol` query parameter.
    """

    funding_resp = http_get(f"{LIGHTER_API}/funding-rate?symbol={market}")
    book_resp = http_get(f"{LIGHTER_API}/orderbook?symbol={market}")

    if not funding_resp or not book_resp:
        return None

    try:
        funding_rate = float(funding_resp["fundingRate"])
        best_bid = float(book_resp["bids"][0][0])
        best_ask = float(book_resp["asks"][0][0])
    except (KeyError, ValueError, IndexError) as exc:
        print(f"Unexpected Lighter payload: {exc}", file=sys.stderr)
        return None

    return MarketSnapshot(funding_rate=funding_rate, best_bid=best_bid, best_ask=best_ask)


def estimate_arbitrage(hyper: MarketSnapshot, lighter: MarketSnapshot, position_usd: float) -> None:
    """Compute and print a simple funding-rate arbitrage suggestion."""

    funding_diff = hyper.funding_rate - lighter.funding_rate
    funding_diff_pct = funding_diff * 100
    avg_mid = (hyper.mid + lighter.mid) / 2
    profit_estimate = position_usd * funding_diff

    print(f"Hyperliquid funding: {hyper.funding_rate:.6f}")
    print(f"Lighter funding:     {lighter.funding_rate:.6f}")
    print(f"Funding rate diff:   {funding_diff:.6f} ({funding_diff_pct:.4f}%)")
    print(f"Avg mid price:       {avg_mid:.2f}")
    print(f"Position size (USD): {position_usd:.2f}\n")

    if abs(funding_diff) < 1e-6:
        print("No arbitrage opportunity: funding rates are identical.")
        return

    if funding_diff > 0:
        direction = "Short Hyperliquid / Long Lighter"
    else:
        direction = "Long Hyperliquid / Short Lighter"

    print(
        "Arbitrage: "
        f"{direction}, Expected funding profit = {profit_estimate:.2f}$ per funding period"
    )


def main() -> None:
    market = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MARKET
    position_usd = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_POSITION_USD

    hyper = fetch_hyperliquid(market)
    lighter = fetch_lighter(market)

    if not hyper or not lighter:
        print("Could not retrieve market data from one or both exchanges.", file=sys.stderr)
        sys.exit(1)

    estimate_arbitrage(hyper, lighter, position_usd)


if __name__ == "__main__":
    main()
