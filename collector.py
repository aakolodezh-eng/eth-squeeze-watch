import os
import time
import requests
from datetime import datetime, timezone

API_KEY = os.environ["COINALYZE_API_KEY"]
HEADERS = {"api_key": API_KEY}
BASE = "https://api.coinalyze.net/v1"

PRICE_SYMBOL = "ETHUSDT_PERP.A"  # Binance ETH/USDT perp

# --------------------------------------------------
# Последний полностью закрытый час UTC
# --------------------------------------------------

now = datetime.now(timezone.utc)
current_hour = now.replace(minute=0, second=0, microsecond=0)

bucket_start = int(current_hour.timestamp()) - 3600
bucket_end = bucket_start + 3599

bucket_label = datetime.fromtimestamp(
    bucket_start,
    tz=timezone.utc
).strftime("%Y-%m-%d %H:%M UTC")


# --------------------------------------------------
# HTTP с автоматической обработкой rate limit
# --------------------------------------------------

def api_get(endpoint, params=None):
    while True:
        r = requests.get(
            f"{BASE}/{endpoint}",
            headers=HEADERS,
            params=params,
            timeout=30,
        )

        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "60"))
            print(f"Rate limit reached. Waiting {wait} sec...")
            time.sleep(wait + 1)
            continue

        r.raise_for_status()
        return r.json()


# --------------------------------------------------
# Получаем список ETH-контрактов
# --------------------------------------------------

markets = api_get("future-markets")

eth_markets = [
    m for m in markets
    if m.get("base_asset") == "ETH"
    and m.get("quote_asset") in ("USD", "USDT", "BUSD")
]

symbols = [m["symbol"] for m in eth_markets]

perpetual_symbols = {
    m["symbol"]
    for m in eth_markets
    if m.get("is_perpetual") is True
}

future_symbols = {
    m["symbol"]
    for m in eth_markets
    if m.get("is_perpetual") is False
}


# --------------------------------------------------
# История батчами максимум по 20 symbols
# --------------------------------------------------

def history_for_symbols(endpoint, symbols_list, extra=None):
    result = {}
    batch_size = 20

    for i in range(0, len(symbols_list), batch_size):
        batch = symbols_list[i:i + batch_size]

        params = {
            "symbols": ",".join(batch),
            "interval": "1hour",
            "from": bucket_start,
            "to": bucket_end,
        }

        if extra:
            params.update(extra)

        data = api_get(endpoint, params)

        for item in data:
            history = item.get("history", [])

            if history:
                result[item["symbol"]] = history[-1]

    return result


# --------------------------------------------------
# 1. OPEN INTEREST
# --------------------------------------------------

oi = history_for_symbols(
    "open-interest-history",
    symbols,
    {"convert_to_usd": "true"},
)

perpetual_oi = sum(
    row["c"]
    for symbol, row in oi.items()
    if symbol in perpetual_symbols
)

futures_oi = sum(
    row["c"]
    for symbol, row in oi.items()
    if symbol in future_symbols
)

all_oi = perpetual_oi + futures_oi


# --------------------------------------------------
# 2. LIQUIDATIONS
# --------------------------------------------------

liq = history_for_symbols(
    "liquidation-history",
    symbols,
    {"convert_to_usd": "true"},
)

total_long_liq = sum(
    row["l"]
    for row in liq.values()
)

total_short_liq = sum(
    row["s"]
    for row in liq.values()
)


# --------------------------------------------------
# 3. PRICE
# Пока берём Binance ETHUSDT как контрольную цену.
# Позже сделаем Coinalyze-like Average.
# --------------------------------------------------

price_data = history_for_symbols(
    "ohlcv-history",
    [PRICE_SYMBOL],
)

price = price_data.get(PRICE_SYMBOL)


# --------------------------------------------------
# Форматирование
# --------------------------------------------------

def billions(value):
    return f"${value / 1_000_000_000:.3f}B"


def millions(value):
    return f"${value / 1_000_000:.3f}M"


# --------------------------------------------------
# OUTPUT
# --------------------------------------------------

print("")
print("ETH SQUEEZE WATCH AGGREGATE")
print("===========================")
print("Bucket:", bucket_label)
print("")

print("CONTRACTS")
print("---------")
print("ETH contracts:", len(symbols))
print("Contracts with OI:", len(oi))
print("Contracts with liquidation data:", len(liq))
print("")

print("OPEN INTEREST")
print("-------------")
print("Perpetual OI:", billions(perpetual_oi))
print("Futures OI:  ", millions(futures_oi))
print("ALL OI:      ", billions(all_oi))
print("")

print("LIQUIDATIONS")
print("------------")
print("Short liquidations:", millions(total_short_liq))
print("Long liquidations: ", millions(total_long_liq))
print("")

print("PRICE")
print("-----")

if price:
    print("ETH Price Close:", price["c"])
    print("ETH Price Low:  ", price["l"])
else:
    print("ETH Price Close: NO DATA")
    print("ETH Price Low:   NO DATA")
