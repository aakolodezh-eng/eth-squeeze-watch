import os
import time
import csv
import requests
from datetime import datetime, timezone, timedelta

API_KEY = os.environ["COINALYZE_API_KEY"]

HEADERS = {
    "api_key": API_KEY
}

BASE = "https://api.coinalyze.net/v1"

HOURS = 168

PRICE_SYMBOL = "ETHUSDT_PERP.A"


# ==================================================
# TIME RANGE
# ==================================================

now = datetime.now(timezone.utc)

current_hour = now.replace(
    minute=0,
    second=0,
    microsecond=0
)

# Последний полностью закрытый bucket
last_bucket = current_hour - timedelta(hours=1)

# Нужны 168 закрытых часов включая последний
first_bucket = last_bucket - timedelta(hours=HOURS - 1)

from_ts = int(first_bucket.timestamp())
to_ts = int(last_bucket.timestamp()) + 3599


# ==================================================
# API REQUEST WITH RATE-LIMIT HANDLING
# ==================================================

def api_get(endpoint, params=None):

    while True:

        response = requests.get(
            f"{BASE}/{endpoint}",
            headers=HEADERS,
            params=params,
            timeout=60
        )

        if response.status_code == 429:

            wait = float(
                response.headers.get(
                    "Retry-After",
                    "60"
                )
            )

            print(
                f"Rate limit reached. "
                f"Waiting {wait:.3f} sec..."
            )

            time.sleep(wait + 1)

            continue

        response.raise_for_status()

        return response.json()


# ==================================================
# MARKET LIST
# ==================================================

markets = api_get("future-markets")


# Те же базовые типы контрактов, которые
# Coinalyze указывает для ETH aggregated OI.
eth_markets = [
    market
    for market in markets
    if market.get("base_asset") == "ETH"
    and market.get("quote_asset") in (
        "USD",
        "USDT",
        "BUSD"
    )
]


all_symbols = [
    market["symbol"]
    for market in eth_markets
]


perpetual_symbols = [
    market["symbol"]
    for market in eth_markets
    if market.get("is_perpetual") is True
]


ls_symbols = [
    market["symbol"]
    for market in eth_markets
    if market.get("has_long_short_ratio_data") is True
]


print("ETH contracts:", len(all_symbols))
print("Perpetual contracts:", len(perpetual_symbols))
print("L/S supported contracts:", len(ls_symbols))


# ==================================================
# HISTORY REQUEST
# ==================================================

def get_history(
    endpoint,
    symbols,
    extra=None
):

    result = {}

    batch_size = 20

    for start in range(
        0,
        len(symbols),
        batch_size
    ):

        batch = symbols[
            start:start + batch_size
        ]

        params = {
            "symbols": ",".join(batch),
            "interval": "1hour",
            "from": from_ts,
            "to": to_ts
        }

        if extra:
            params.update(extra)

        data = api_get(
            endpoint,
            params
        )

        for item in data:

            symbol = item["symbol"]

            result[symbol] = {}

            for row in item.get(
                "history",
                []
            ):

                result[symbol][
                    row["t"]
                ] = row

    return result


# ==================================================
# DOWNLOAD DATA
# ==================================================

print("")
print("Downloading OI...")

oi_history = get_history(
    "open-interest-history",
    all_symbols,
    {
        "convert_to_usd": "true"
    }
)


print("Downloading liquidations...")

liq_history = get_history(
    "liquidation-history",
    all_symbols,
    {
        "convert_to_usd": "true"
    }
)


print("Downloading funding...")

funding_history = get_history(
    "funding-rate-history",
    perpetual_symbols
)


print("Downloading L/S ratio...")

ls_history = get_history(
    "long-short-ratio-history",
    ls_symbols
)


print("Downloading price...")

price_history = get_history(
    "ohlcv-history",
    [PRICE_SYMBOL]
)


# ==================================================
# BUILD HOURLY DATABASE
# ==================================================

rows = []

previous_oi = None


for hour_index in range(HOURS):

    bucket_dt = (
        first_bucket
        + timedelta(hours=hour_index)
    )

    timestamp = int(
        bucket_dt.timestamp()
    )


    # ----------------------------------------------
    # OPEN INTEREST
    # ----------------------------------------------

    oi_values = []

    for symbol in all_symbols:

        row = (
            oi_history
            .get(symbol, {})
            .get(timestamp)
        )

        if row is not None:

            value = row.get("c")

            if value is not None:
                oi_values.append(value)


    total_oi = (
        sum(oi_values)
        if oi_values
        else None
    )


    # ----------------------------------------------
    # LIQUIDATIONS
    # ----------------------------------------------

    total_short_liq = 0.0
    total_long_liq = 0.0

    liq_count = 0


    for symbol in all_symbols:

        row = (
            liq_history
            .get(symbol, {})
            .get(timestamp)
        )

        if row is not None:

            total_short_liq += (
                row.get("s", 0) or 0
            )

            total_long_liq += (
                row.get("l", 0) or 0
            )

            liq_count += 1


    if liq_count == 0:

        total_short_liq = None
        total_long_liq = None


    # ----------------------------------------------
    # FUNDING
    # ----------------------------------------------

    funding_values = []


    for symbol in perpetual_symbols:

        row = (
            funding_history
            .get(symbol, {})
            .get(timestamp)
        )

        if row is not None:

            value = row.get("c")

            if value is not None:

                funding_values.append(
                    value
                )


    avg_funding = (
        sum(funding_values)
        / len(funding_values)
        if funding_values
        else None
    )


    # ----------------------------------------------
    # LONG / SHORT RATIO
    # ----------------------------------------------

    ls_values = []


    for symbol in ls_symbols:

        row = (
            ls_history
            .get(symbol, {})
            .get(timestamp)
        )

        if row is not None:

            value = row.get("r")

            if value is not None:

                ls_values.append(
                    value
                )


    avg_ls = (
        sum(ls_values)
        / len(ls_values)
        if ls_values
        else None
    )


    # ----------------------------------------------
    # PRICE
    # ----------------------------------------------

    price_row = (
        price_history
        .get(
            PRICE_SYMBOL,
            {}
        )
        .get(timestamp)
    )


    price_close = (
        price_row.get("c")
        if price_row
        else None
    )


    price_low = (
        price_row.get("l")
        if price_row
        else None
    )


    # ----------------------------------------------
    # OI DELTA
    # ----------------------------------------------

    delta_oi = None
    delta_oi_pct = None


    if (
        total_oi is not None
        and previous_oi is not None
        and previous_oi != 0
    ):

        delta_oi = (
            total_oi
            - previous_oi
        )

        delta_oi_pct = (
            delta_oi
            / previous_oi
            * 100
        )


    if total_oi is not None:

        previous_oi = total_oi


    # ----------------------------------------------
    # SAVE ROW
    # ----------------------------------------------

    rows.append({

        "timestamp": timestamp,

        "utc": bucket_dt.strftime(
            "%Y-%m-%d %H:%M"
        ),

        "eth_close": price_close,

        "eth_low": price_low,

        "oi_close_usd": total_oi,

        "delta_oi_usd": delta_oi,

        "delta_oi_pct": delta_oi_pct,

        "short_liquidations_usd":
            total_short_liq,

        "long_liquidations_usd":
            total_long_liq,

        "ls_ratio": avg_ls,

        "funding_rate": avg_funding,

        "oi_contracts":
            len(oi_values),

        "liq_contracts":
            liq_count,

        "funding_contracts":
            len(funding_values),

        "ls_contracts":
            len(ls_values)
    })


# ==================================================
# WRITE CSV
# ==================================================

filename = "eth_hourly.csv"


fieldnames = [

    "timestamp",
    "utc",

    "eth_close",
    "eth_low",

    "oi_close_usd",

    "delta_oi_usd",
    "delta_oi_pct",

    "short_liquidations_usd",
    "long_liquidations_usd",

    "ls_ratio",
    "funding_rate",

    "oi_contracts",
    "liq_contracts",
    "funding_contracts",
    "ls_contracts"
]


with open(
    filename,
    "w",
    newline="",
    encoding="utf-8"
) as file:

    writer = csv.DictWriter(
        file,
        fieldnames=fieldnames
    )

    writer.writeheader()

    writer.writerows(rows)


# ==================================================
# SUMMARY
# ==================================================

valid_rows = [
    row
    for row in rows
    if row["oi_close_usd"]
    is not None
]


print("")
print("==============================")
print("ETH HOURLY DATABASE COMPLETE")
print("==============================")

print(
    "Requested hours:",
    HOURS
)

print(
    "Rows created:",
    len(rows)
)

print(
    "Rows with OI:",
    len(valid_rows)
)

print(
    "First bucket:",
    rows[0]["utc"]
)

print(
    "Last bucket:",
    rows[-1]["utc"]
)


print("")
print("LAST 10 HOURS")
print("-------------")


for row in rows[-10:]:

    oi_b = (
        row["oi_close_usd"]
        / 1_000_000_000
        if row["oi_close_usd"]
        is not None
        else None
    )

    delta_m = (
        row["delta_oi_usd"]
        / 1_000_000
        if row["delta_oi_usd"]
        is not None
        else None
    )

    print(
        row["utc"],
        "| Price:",
        row["eth_close"],
        "| OI:",
        round(oi_b, 3)
        if oi_b is not None
        else None,
        "B",
        "| dOI:",
        round(delta_m, 1)
        if delta_m is not None
        else None,
        "M",
        "| Short:",
        round(
            row["short_liquidations_usd"]
            / 1_000_000,
            3
        )
        if row[
            "short_liquidations_usd"
        ] is not None
        else None,
        "M",
        "| Long:",
        round(
            row["long_liquidations_usd"]
            / 1_000_000,
            3
        )
        if row[
            "long_liquidations_usd"
        ] is not None
        else None,
        "M",
        "| LS:",
        round(
            row["ls_ratio"],
            4
        )
        if row["ls_ratio"]
        is not None
        else None,
        "| Funding:",
        round(
            row["funding_rate"],
            6
        )
        if row["funding_rate"]
        is not None
        else None
    )


print("")
print(
    "CSV saved:",
    filename
)
