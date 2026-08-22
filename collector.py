import os
import requests

API_KEY = os.environ["COINALYZE_API_KEY"]

url = "https://api.coinalyze.net/v1/future-markets"
headers = {"api_key": API_KEY}

response = requests.get(url, headers=headers, timeout=30)
response.raise_for_status()

markets = response.json()

eth_markets = [
    m for m in markets
    if m.get("base_asset") == "ETH" and m.get("is_perpetual") is True
]

print(f"Found {len(eth_markets)} ETH perpetual markets:")

for market in eth_markets:
    print(
        market["symbol"],
        "|",
        market["exchange"],
        "|",
        market["symbol_on_exchange"]
    )
