# BotArbitrage

Simple script to check for funding-rate arbitrage between Hyperliquid and Lighter.

Usage:

```bash
python arbitrage.py [MARKET] [POSITION_USD]
```

- `MARKET` defaults to `ETH` if omitted.
- `POSITION_USD` defaults to `1000`.
- The script prints both the funding rate spread and its percentage representation.
