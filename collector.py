import os
import requests
from datetime import datetime, timezone

API_KEY = os.environ["COINALYZE_API_KEY"]
HEADERS = {"api_key": API_KEY}
BASE = "https://api.coinalyze.net/v1"

# Последний полностью закрытый час UTC
now = datetime.now(timezone.utc)
current_hour = now.replace(minute=0, second=0, microsecond=0)
bucket_start = int(current_hour.timestamp()) - 3600
bucket_end = bucket_start + 3599

# 1. Получаем полный список рынков
r = requests.get(
    f"{BASE}/future-markets",
    headers=HEADERS,
    timeout=30,
)
r.raise_for_status()
markets = r.json()

# Оставляем ETH и только валюты, используемые
# в ETH aggregate Coinalyze
eth_markets = [
    m for m in markets
    if m.get("base_asset") == "ETH"
    and m.get("quote_asset") in ("USD", "USDT", "BUSD")
]

perpetual = [
    m for m in eth_markets
    if m.get("is_perpetual") is True
]

futures = [
    m for m in eth_markets
    if m.get("is_perpetual") is False
]

print("")
print("ETH AGGREGATE DIAGNOSTIC")
print("========================")

print(
    "Bucket:",
    datetime.fromtimestamp(
        bucket_start,
        tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")
)

print("ETH contracts found:", len(eth_markets))
print("Perpetual:", len(perpetual))
print("Futures:", len(futures))
print("")


def get_oi(markets_list):
    """
    Coinalyze limits requests by number of symbols.
    Request in small batches.
    """
    results = {}

    symbols = [m["symbol"] for m in markets_list]

    batch_size = 20

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]

        params = {
            "symbols": ",".join(batch),
            "interval": "1hour",
            "from": bucket_start,
            "to": bucket_end,
            "convert_to_usd": "true",
        }

        r = requests.get(
            f"{BASE}/open-interest-history",
            headers=HEADERS,
            params=params,
            timeout=30,
        )

        r.raise_for_status()

        for item in r.json():
            history = item.get("history", [])

            if history:
                results[item["symbol"]] = history[-1]["c"]

    return results


# 2. Получаем OI всех выбранных ETH-контрактов
oi = get_oi(eth_markets)

perpetual_symbols = {
    m["symbol"] for m in perpetual
}

future_symbols = {
    m["symbol"] for m in futures
}

perpetual_oi = sum(
    value
    for symbol, value in oi.items()
    if symbol in perpetual_symbols
)

futures_oi = sum(
    value
    for symbol, value in oi.items()
    if symbol in future_symbols
)

all_oi = perpetual_oi + futures_oi


def billions(value):
    return f"${value / 1_000_000_000:.3f}B"


def millions(value):
    return f"${value / 1_000_000:.1f}M"


print("RESULT")
print("------")

print("PERPETUAL OI:", billions(perpetual_oi))
print("FUTURES OI:  ", millions(futures_oi))
print("ALL OI:      ", billions(all_oi))

print("")
print("Contracts with OI:", len(oi))
print("")

print("OI BY CONTRACT")
print("--------------")

# От большего OI к меньшему
for symbol, value in sorted(
    oi.items(),
    key=lambda x: x[1],
    reverse=True,
):
    market = next(
        m for m in eth_markets
        if m["symbol"] == symbol
    )

    contract_type = (
        "PERP"
        if market.get("is_perpetual")
        else "FUT"
    )

    print(
        symbol,
        "|",
        market.get("exchange"),
        "|",
        contract_type,
        "|",
        billions(value)
        if value >= 1_000_000_000
        else millions(value)
    )
