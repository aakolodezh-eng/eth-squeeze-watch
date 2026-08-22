import os
import requests
import time
from datetime import datetime, timezone

API_KEY = os.environ["COINALYZE_API_KEY"]
HEADERS = {"api_key": API_KEY}

SYMBOL = "ETHUSDT_PERP.A"
INTERVAL = "1hour"

# Текущий UTC-час
now = datetime.now(timezone.utc)
current_hour = now.replace(minute=0, second=0, microsecond=0)

# Последний полностью закрытый bucket:
# если сейчас 20:xx UTC, берём свечу, начавшуюся в 19:00 UTC
bucket_start = int(current_hour.timestamp()) - 3600
bucket_end = bucket_start + 3599

def get(endpoint, extra_params=None):
    params = {
        "symbols": SYMBOL,
        "interval": INTERVAL,
        "from": bucket_start,
        "to": bucket_end,
    }

    if extra_params:
        params.update(extra_params)

    r = requests.get(
        f"https://api.coinalyze.net/v1/{endpoint}",
        headers=HEADERS,
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

def first_history(data):
    if not data:
        return None
    history = data[0].get("history", [])
    if not history:
        return None
    return history[-1]

oi = first_history(
    get(
        "open-interest-history",
        {"convert_to_usd": "true"}
    )
)

funding = first_history(
    get("funding-rate-history")
)

liq = first_history(
    get(
        "liquidation-history",
        {"convert_to_usd": "true"}
    )
)

lsr = first_history(
    get("long-short-ratio-history")
)

price = first_history(
    get("ohlcv-history")
)

bucket_label = datetime.fromtimestamp(
    bucket_start,
    tz=timezone.utc
).strftime("%Y-%m-%d %H:%M UTC")

print("")
print("ETH SQUEEZE WATCH TEST")
print("======================")
print("Symbol:", SYMBOL)
print("Bucket:", bucket_label)

if oi:
    print("OI Close USD:", oi["c"])
else:
    print("OI Close USD: NO DATA")

if liq:
    print("Short liquidations USD:", liq["s"])
    print("Long liquidations USD:", liq["l"])
else:
    print("Liquidations: NO DATA")

if price:
    print("ETH Price Close:", price["c"])
    print("ETH Price Low:", price["l"])
else:
    print("Price: NO DATA")

if lsr:
    print("L/S Ratio:", lsr["r"])
else:
    print("L/S Ratio: NO DATA")

if funding:
    print("Funding Rate:", funding["c"])
else:
    print("Funding Rate: NO DATA")
