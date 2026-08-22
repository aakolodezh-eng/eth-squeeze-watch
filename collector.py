import os
import requests

API_KEY = os.environ["COINALYZE_API_KEY"]
headers = {"api_key": API_KEY}

# 1. Получаем справочник бирж
exchanges = requests.get(
    "https://api.coinalyze.net/v1/exchanges",
    headers=headers,
    timeout=30
)
exchanges.raise_for_status()
exchange_map = {x["code"]: x["name"] for x in exchanges.json()}

# 2. Получаем поддерживаемые фьючерсные рынки
response = requests.get(
    "https://api.coinalyze.net/v1/future-markets",
    headers=headers,
    timeout=30
)
response.raise_for_status()
markets = response.json()

eth_markets = [
    m for m in markets
    if m.get("base_asset") == "ETH"
    and m.get("is_perpetual") is True
]

print(f"Found {len(eth_markets)} ETH perpetual markets:")

for market in eth_markets:
    code = market["exchange"]
    print(
        market["symbol"],
        "|",
        exchange_map.get(code, code),
        "|",
        market["symbol_on_exchange"],
        "|",
        market["quote_asset"]
    )
