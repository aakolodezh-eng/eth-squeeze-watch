import os
import time
import csv
import requests
from datetime import datetime, timezone, timedelta


# ============================================================
# SETTINGS
# ============================================================

API_KEY = os.environ["COINALYZE_API_KEY"]

HEADERS = {
    "api_key": API_KEY
}

BASE = "https://api.coinalyze.net/v1"

CSV_FILE = "eth_hourly.csv"

# Каждый запуск заново обновляет последние 7 суток.
# Более старая история из CSV сохраняется.
REFRESH_HOURS = 168

# Стабильный reference price.
PRICE_SYMBOL = "ETHUSDT_PERP.A"


# ============================================================
# TIME RANGE
# ============================================================

now = datetime.now(timezone.utc)

current_hour = now.replace(
    minute=0,
    second=0,
    microsecond=0
)

# Последняя полностью закрытая свеча
last_bucket = current_hour - timedelta(hours=1)

first_bucket = last_bucket - timedelta(
    hours=REFRESH_HOURS - 1
)

from_ts = int(first_bucket.timestamp())
to_ts = int(last_bucket.timestamp()) + 3599


# ============================================================
# API REQUEST
# ============================================================

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


# ============================================================
# MARKETS
# ============================================================

print("Loading markets...")

markets = api_get("future-markets")


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
    if market.get(
        "has_long_short_ratio_data"
    ) is True
]


print("ETH contracts:", len(all_symbols))
print(
    "Perpetual contracts:",
    len(perpetual_symbols)
)
print(
    "L/S supported contracts:",
    len(ls_symbols)
)


# ============================================================
# HISTORY DOWNLOAD
# ============================================================

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

            if symbol not in result:
                result[symbol] = {}

            for row in item.get(
                "history",
                []
            ):

                result[symbol][
                    int(row["t"])
                ] = row

    return result


# ============================================================
# DOWNLOAD ALL SERIES
# ============================================================

print("")
print("Downloading Open Interest...")

oi_history = get_history(
    "open-interest-history",
    all_symbols,
    {
        "convert_to_usd": "true"
    }
)


print("Downloading Liquidations...")

liq_history = get_history(
    "liquidation-history",
    all_symbols,
    {
        "convert_to_usd": "true"
    }
)


print("Downloading Funding...")

funding_history = get_history(
    "funding-rate-history",
    perpetual_symbols
)


print("Downloading L/S Ratio...")

ls_history = get_history(
    "long-short-ratio-history",
    ls_symbols
)


print("Downloading Price...")

price_history = get_history(
    "ohlcv-history",
    [PRICE_SYMBOL]
)


# ============================================================
# BUILD NEW API ROWS
# ============================================================

fresh_rows = {}


for hour_index in range(
    REFRESH_HOURS
):

    bucket_dt = (
        first_bucket
        + timedelta(hours=hour_index)
    )

    timestamp = int(
        bucket_dt.timestamp()
    )


    # --------------------------------------------------------
    # OPEN INTEREST
    # --------------------------------------------------------

    oi_by_symbol = {}

    for symbol in all_symbols:

        row = (
            oi_history
            .get(symbol, {})
            .get(timestamp)
        )

        if row is None:
            continue

        value = row.get("c")

        if value is not None:
            oi_by_symbol[symbol] = float(
                value
            )


    total_oi = (
        sum(oi_by_symbol.values())
        if oi_by_symbol
        else None
    )


    # --------------------------------------------------------
    # LIQUIDATIONS
    # --------------------------------------------------------

    short_liq = 0.0
    long_liq = 0.0
    liq_count = 0


    for symbol in all_symbols:

        row = (
            liq_history
            .get(symbol, {})
            .get(timestamp)
        )

        if row is None:
            continue

        short_liq += float(
            row.get("s", 0) or 0
        )

        long_liq += float(
            row.get("l", 0) or 0
        )

        liq_count += 1


    if liq_count == 0:
        short_liq = None
        long_liq = None


    # --------------------------------------------------------
    # FUNDING — OI WEIGHTED
    # --------------------------------------------------------

    funding_num = 0.0
    funding_den = 0.0
    funding_count = 0


    for symbol in perpetual_symbols:

        row = (
            funding_history
            .get(symbol, {})
            .get(timestamp)
        )

        if row is None:
            continue

        value = row.get("c")

        if value is None:
            continue

        weight = oi_by_symbol.get(
            symbol
        )

        if weight is None:
            continue

        funding_num += (
            float(value)
            * weight
        )

        funding_den += weight

        funding_count += 1


    funding_rate = (
        funding_num / funding_den
        if funding_den > 0
        else None
    )


    # --------------------------------------------------------
    # LONG / SHORT RATIO — OI WEIGHTED
    # --------------------------------------------------------

    ls_num = 0.0
    ls_den = 0.0
    ls_count = 0


    for symbol in ls_symbols:

        row = (
            ls_history
            .get(symbol, {})
            .get(timestamp)
        )

        if row is None:
            continue

        value = row.get("r")

        if value is None:
            continue

        weight = oi_by_symbol.get(
            symbol
        )

        if weight is None:
            continue

        ls_num += (
            float(value)
            * weight
        )

        ls_den += weight

        ls_count += 1


    ls_ratio = (
        ls_num / ls_den
        if ls_den > 0
        else None
    )


    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    price_row = (
        price_history
        .get(
            PRICE_SYMBOL,
            {}
        )
        .get(timestamp)
    )


    eth_close = (
        float(price_row["c"])
        if price_row
        and price_row.get("c")
        is not None
        else None
    )


    eth_low = (
        float(price_row["l"])
        if price_row
        and price_row.get("l")
        is not None
        else None
    )


    # --------------------------------------------------------
    # RAW ROW
    # --------------------------------------------------------

    fresh_rows[timestamp] = {

        "timestamp": timestamp,

        "utc": bucket_dt.strftime(
            "%Y-%m-%d %H:%M"
        ),

        "eth_close": eth_close,

        "eth_low": eth_low,

        "oi_close_usd": total_oi,

        "short_liquidations_usd":
            short_liq,

        "long_liquidations_usd":
            long_liq,

        "ls_ratio": ls_ratio,

        "funding_rate":
            funding_rate,

        "oi_contracts":
            len(oi_by_symbol),

        "liq_contracts":
            liq_count,

        "funding_contracts":
            funding_count,

        "ls_contracts":
            ls_count
    }


# ============================================================
# LOAD EXISTING CSV
# ============================================================

database = {}


if os.path.exists(CSV_FILE):

    print("")
    print(
        "Loading existing database..."
    )

    with open(
        CSV_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:

            try:

                timestamp = int(
                    float(row["timestamp"])
                )

            except (
                KeyError,
                ValueError,
                TypeError
            ):

                continue


            def old_float(name):

                value = row.get(name)

                if (
                    value is None
                    or value == ""
                    or value == "None"
                ):
                    return None

                try:
                    return float(value)

                except ValueError:
                    return None


            database[timestamp] = {

                "timestamp":
                    timestamp,

                "utc":
                    row.get(
                        "utc",
                        ""
                    ),

                "eth_close":
                    old_float(
                        "eth_close"
                    ),

                "eth_low":
                    old_float(
                        "eth_low"
                    ),

                "oi_close_usd":
                    old_float(
                        "oi_close_usd"
                    ),

                "short_liquidations_usd":
                    old_float(
                        "short_liquidations_usd"
                    ),

                "long_liquidations_usd":
                    old_float(
                        "long_liquidations_usd"
                    ),

                "ls_ratio":
                    old_float(
                        "ls_ratio"
                    ),

                "funding_rate":
                    old_float(
                        "funding_rate"
                    ),

                "oi_contracts":
                    old_float(
                        "oi_contracts"
                    ),

                "liq_contracts":
                    old_float(
                        "liq_contracts"
                    ),

                "funding_contracts":
                    old_float(
                        "funding_contracts"
                    ),

                "ls_contracts":
                    old_float(
                        "ls_contracts"
                    )
            }


print(
    "Existing rows:",
    len(database)
)


# ============================================================
# MERGE
# Fresh API data replaces same timestamps.
# Old history stays.
# ============================================================

database.update(
    fresh_rows
)


timestamps = sorted(
    database.keys()
)


rows = [
    database[timestamp]
    for timestamp in timestamps
]


# ============================================================
# HELPERS FOR DYNAMICS
# ============================================================

def pct_change(
    current,
    previous
):

    if (
        current is None
        or previous is None
        or previous == 0
    ):
        return None

    return (
        (current - previous)
        / previous
        * 100
    )


def absolute_change(
    current,
    previous
):

    if (
        current is None
        or previous is None
    ):
        return None

    return current - previous


def rolling_sum(
    rows,
    index,
    field,
    hours
):

    if index - hours + 1 < 0:
        return None

    values = []

    for i in range(
        index - hours + 1,
        index + 1
    ):

        value = rows[i].get(
            field
        )

        if value is None:
            return None

        values.append(value)

    return sum(values)


def prior_value(
    rows,
    index,
    field,
    hours_back
):

    target = (
        index
        - hours_back
    )

    if target < 0:
        return None

    return rows[target].get(
        field
    )


# ============================================================
# CALCULATE DYNAMICS
# ============================================================

for index, row in enumerate(rows):

    price = row.get(
        "eth_close"
    )

    oi = row.get(
        "oi_close_usd"
    )

    ls = row.get(
        "ls_ratio"
    )

    funding = row.get(
        "funding_rate"
    )


    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    for hours in (
        1,
        3,
        6,
        24
    ):

        old_price = prior_value(
            rows,
            index,
            "eth_close",
            hours
        )

        row[
            f"price_change_{hours}h_pct"
        ] = pct_change(
            price,
            old_price
        )


    # --------------------------------------------------------
    # OI
    # --------------------------------------------------------

    for hours in (
        1,
        3,
        6,
        24
    ):

        old_oi = prior_value(
            rows,
            index,
            "oi_close_usd",
            hours
        )

        row[
            f"oi_change_{hours}h_usd"
        ] = absolute_change(
            oi,
            old_oi
        )

        row[
            f"oi_change_{hours}h_pct"
        ] = pct_change(
            oi,
            old_oi
        )


    # --------------------------------------------------------
    # LIQUIDATIONS
    # --------------------------------------------------------

    for hours in (
        3,
        6,
        24
    ):

        row[
            f"short_liq_{hours}h_usd"
        ] = rolling_sum(
            rows,
            index,
            "short_liquidations_usd",
            hours
        )

        row[
            f"long_liq_{hours}h_usd"
        ] = rolling_sum(
            rows,
            index,
            "long_liquidations_usd",
            hours
        )


    # --------------------------------------------------------
    # LONG / SHORT RATIO CHANGE
    # --------------------------------------------------------

    for hours in (
        1,
        3,
        6,
        24
    ):

        old_ls = prior_value(
            rows,
            index,
            "ls_ratio",
            hours
        )

        row[
            f"ls_change_{hours}h"
        ] = absolute_change(
            ls,
            old_ls
        )


    # --------------------------------------------------------
    # FUNDING CHANGE
    # --------------------------------------------------------

    for hours in (
        1,
        3,
        6,
        24
    ):

        old_funding = prior_value(
            rows,
            index,
            "funding_rate",
            hours
        )

        row[
            f"funding_change_{hours}h"
        ] = absolute_change(
            funding,
            old_funding
        )


# ============================================================
# CSV COLUMNS
# ============================================================

fieldnames = [

    "timestamp",
    "utc",

    "eth_close",
    "eth_low",

    "price_change_1h_pct",
    "price_change_3h_pct",
    "price_change_6h_pct",
    "price_change_24h_pct",

    "oi_close_usd",

    "oi_change_1h_usd",
    "oi_change_1h_pct",

    "oi_change_3h_usd",
    "oi_change_3h_pct",

    "oi_change_6h_usd",
    "oi_change_6h_pct",

    "oi_change_24h_usd",
    "oi_change_24h_pct",

    "short_liquidations_usd",
    "long_liquidations_usd",

    "short_liq_3h_usd",
    "long_liq_3h_usd",

    "short_liq_6h_usd",
    "long_liq_6h_usd",

    "short_liq_24h_usd",
    "long_liq_24h_usd",

    "ls_ratio",

    "ls_change_1h",
    "ls_change_3h",
    "ls_change_6h",
    "ls_change_24h",

    "funding_rate",

    "funding_change_1h",
    "funding_change_3h",
    "funding_change_6h",
    "funding_change_24h",

    "oi_contracts",
    "liq_contracts",
    "funding_contracts",
    "ls_contracts"
]


# ============================================================
# SAVE CSV
# ============================================================

with open(
    CSV_FILE,
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


# ============================================================
# REPORT
# ============================================================

print("")
print(
    "================================"
)
print(
    "ETH DYNAMIC DATABASE COMPLETE"
)
print(
    "================================"
)

print(
    "Total rows:",
    len(rows)
)

print(
    "Fresh rows updated:",
    len(fresh_rows)
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
print(
    "LAST 10 HOURS"
)
print(
    "-------------"
)


for row in rows[-10:]:

    oi_b = (
        row["oi_close_usd"]
        / 1_000_000_000
        if row[
            "oi_close_usd"
        ] is not None
        else None
    )

    doi_m = (
        row[
            "oi_change_1h_usd"
        ]
        / 1_000_000
        if row[
            "oi_change_1h_usd"
        ] is not None
        else None
    )

    short_m = (
        row[
            "short_liquidations_usd"
        ]
        / 1_000_000
        if row[
            "short_liquidations_usd"
        ] is not None
        else None
    )

    long_m = (
        row[
            "long_liquidations_usd"
        ]
        / 1_000_000
        if row[
            "long_liquidations_usd"
        ] is not None
        else None
    )

    print(
        row["utc"],
        "| Price:",
        row["eth_close"],
        "| Price 1h:",
        round(
            row[
                "price_change_1h_pct"
            ],
            2
        )
        if row[
            "price_change_1h_pct"
        ] is not None
        else None,
        "%",
        "| OI:",
        round(
            oi_b,
            3
        )
        if oi_b is not None
        else None,
        "B",
        "| dOI:",
        round(
            doi_m,
            1
        )
        if doi_m is not None
        else None,
        "M",
        "| Short:",
        round(
            short_m,
            3
        )
        if short_m is not None
        else None,
        "M",
        "| Long:",
        round(
            long_m,
            3
        )
        if long_m is not None
        else None,
        "M",
        "| LS:",
        round(
            row["ls_ratio"],
            4
        )
        if row[
            "ls_ratio"
        ] is not None
        else None,
        "| Funding:",
        round(
            row["funding_rate"],
            6
        )
        if row[
            "funding_rate"
        ] is not None
        else None
    )


print("")
print(
    "CSV updated:",
    CSV_FILE
)
