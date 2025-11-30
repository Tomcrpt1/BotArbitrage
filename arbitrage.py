"""Simple funding rate arbitrage checker for Hyperliquid and Lighter.

The script reads:
- Current funding rates from both exchanges
- Best bid/ask prices to form a mid-price estimate

Then it prints a basic funding-rate spread and a rough profit estimate
for a provided USD notional. This is a **read-only** utility; it does not
place any orders.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
# Lighter's correct public host (the previous api.lighter.xyz does not resolve).
LIGHTER_API = "https://mainnet.zklighter.elliot.ai"
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
    mid_price: Optional[float]

    @property
    def mid(self) -> Optional[float]:
        return self.mid_price


def fetch_hyperliquid(market: str) -> Optional[MarketSnapshot]:
    """Fetch funding rate and mid price from Hyperliquid.

    The `metaAndAssetCtxs` request returns both metadata and live asset contexts
    in one call. Each asset context includes the current funding rate and
    mid/mark prices. We select the requested market and pull `funding` and
    either `midPx` or `markPx` for pricing.
    """

    resp = http_post(HYPERLIQUID_API, {"type": "metaAndAssetCtxs"})
    if not resp:
        return None

    asset_ctxs = resp.get("assetCtxs") or []
    market_upper = market.upper()

    try:
        matching = next(
            ctx
            for ctx in asset_ctxs
            if ctx.get("name", ctx.get("coin", ctx.get("asset"))) == market_upper
        )
    except StopIteration:
        print(f"Hyperliquid market {market_upper} not found in response.", file=sys.stderr)
        return None

    try:
        funding_rate = float(matching.get("funding"))
        mid_px_raw = matching.get("midPx") if matching.get("midPx") is not None else matching.get("markPx")
        mid_price = float(mid_px_raw) if mid_px_raw is not None else None
    except (TypeError, ValueError) as exc:
        print(f"Unexpected Hyperliquid payload format: {exc}", file=sys.stderr)
        return None

    return MarketSnapshot(funding_rate=funding_rate, mid_price=mid_price)


def fetch_lighter(market: str) -> Optional[MarketSnapshot]:
    """Fetch funding rate (and, if available, a mid price) from Lighter.

    Funding rates are provided by `/api/v1/funding-rates`, which returns rates
    for all markets. We scan for the requested symbol. Pricing is optional and
    can be derived from `/api/v1/orderBooks` when present.
    """

    funding_resp = http_get(f"{LIGHTER_API}/api/v1/funding-rates")
    if not funding_resp:
        return None

    market_upper = market.upper()
    funding_rate: Optional[float] = None

    if isinstance(funding_resp, dict):
        markets = funding_resp.get("data") or funding_resp.get("rates") or funding_resp.get("markets") or funding_resp.get("fundingRates")
    else:
        markets = funding_resp

    if isinstance(markets, dict):
        markets = markets.values()

    if isinstance(markets, list) or isinstance(markets, tuple):
        for entry in markets:
            symbol = (
                entry.get("symbol")
                or entry.get("market")
                or entry.get("name")
                or entry.get("pair")
            )
            if symbol and symbol.upper() == market_upper:
                try:
                    funding_rate = float(
                        entry.get("fundingRate")
                        or entry.get("funding_rate")
                        or entry.get("funding")
                    )
                except (TypeError, ValueError):
                    funding_rate = None
                break

    if funding_rate is None:
        print(f"Lighter market {market_upper} not found in funding rates.", file=sys.stderr)
        return None

    # Try to pull a mid price from the optional order book snapshot.
    orderbooks = http_get(f"{LIGHTER_API}/api/v1/orderBooks")
    mid_price: Optional[float] = None
    if orderbooks:
        books = orderbooks.get("data") if isinstance(orderbooks, dict) else orderbooks
        if isinstance(books, list):
            for book in books:
                symbol = (
                    book.get("symbol")
                    or book.get("market")
                    or book.get("name")
                    or book.get("pair")
                )
                if symbol and symbol.upper() == market_upper:
                    try:
                        bids = book.get("bids") or []
                        asks = book.get("asks") or []
                        if bids and asks:
                            best_bid = float(bids[0][0])
                            best_ask = float(asks[0][0])
                            mid_price = (best_bid + best_ask) / 2
                    except (TypeError, ValueError, IndexError):
                        mid_price = None
                    break

    return MarketSnapshot(funding_rate=funding_rate, mid_price=mid_price)


def estimate_arbitrage(hyper: MarketSnapshot, lighter: MarketSnapshot, position_usd: float) -> None:
    """Compute and print a simple funding-rate arbitrage suggestion.

    Steps:
    1) Compute absolute and percentage funding-rate differences
    2) Compute an average mid price between venues (rough entry reference)
    3) Estimate funding PnL for the provided USD position size
    4) Print a suggested long/short direction if a spread exists
    """

    funding_diff = hyper.funding_rate - lighter.funding_rate
    funding_diff_pct = funding_diff * 100
    mid_values = [m for m in (hyper.mid, lighter.mid) if m is not None]
    avg_mid = sum(mid_values) / len(mid_values) if mid_values else None
    profit_estimate = position_usd * funding_diff

    # Present the raw inputs and intermediate calculations for transparency.
    print(f"Hyperliquid funding: {hyper.funding_rate:.6f}")
    print(f"Lighter funding:     {lighter.funding_rate:.6f}")
    print(f"Funding rate diff:   {funding_diff:.6f} ({funding_diff_pct:.4f}%)")
    if avg_mid is not None:
        print(f"Avg mid price:       {avg_mid:.2f}")
    else:
        print("Avg mid price:       unavailable")
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

    if len(sys.argv) > 2:
        try:
            position_usd = float(sys.argv[2])
        except ValueError:
            print(
                "Invalid position size provided; falling back to default",
                f"{DEFAULT_POSITION_USD} USD.",
                file=sys.stderr,
            )
            position_usd = DEFAULT_POSITION_USD
    else:
        position_usd = DEFAULT_POSITION_USD

    hyper = fetch_hyperliquid(market)
    lighter = fetch_lighter(market)

    if not hyper or not lighter:
        print("Could not retrieve market data from one or both exchanges.", file=sys.stderr)
        sys.exit(1)

    estimate_arbitrage(hyper, lighter, position_usd)


if __name__ == "__main__":
    main()
