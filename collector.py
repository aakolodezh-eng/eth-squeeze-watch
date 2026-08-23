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

# Обновляем только последние 3 полностью закрытых часа
REFRESH_HOURS = 3

# Стабильная reference-price серия
PRICE_SYMBOL = "ETHUSDT_PERP.A"


# ============================================================
# TIME RANGE — STRICT UTC
# ============================================================

now = datetime.now(timezone.utc)

current_hour = now.replace(
    minute=0,
    second=0,
    microsecond=0
)

# Последняя полностью закрытая свеча
last_bucket = current_hour - timedelta(hours=1)

# Берём последние 3 закрытых часа
first_bucket = last_bucket - timedelta(
    hours=REFRESH_HOURS - 1
)

from_ts = int(first_bucket.timestamp())
to_ts = int(last_bucket.timestamp()) + 3599


# Диагностика времени
print("")
print("TIME DIAGNOSTICS")
print("================")
print("NOW UTC:", now.isoformat())
print("CURRENT HOUR UTC:", current_hour.isoformat())
print("FIRST REQUESTED BUCKET:", first_bucket.isoformat())
print("LAST REQUESTED BUCKET:", last_bucket.isoformat())
print("FROM TS:", from_ts)
print("TO TS:", to_ts)
print("")


# ============================================================
# API
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
    if market.get("has_long_short_ratio_data") is True
]

print("ETH contracts:", len(all_symbols))
print("Perpetual contracts:", len(perpetual_symbols))
print("L/S supported contracts:", len(ls_symbols))


# ============================================================
# HISTORY
# ============================================================

def get_history(endpoint, symbols, extra=None):

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

            result.setdefault(
                symbol,
                {}
            )

            for row in item.get(
                "history",
                []
            ):

                result[symbol][
                    int(row["t"])
                ] = row

    return result


# ============================================================
# DOWNLOAD FRESH DATA
# ============================================================

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


# ============================================================
# BUILD FRESH ROWS
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
    # OI
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
    # L/S — OI WEIGHTED
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
        and price_row.get("c") is not None
        else None
    )

    eth_low = (
        float(price_row["l"])
        if price_row
        and price_row.get("l") is not None
        else None
    )


    # --------------------------------------------------------
    # BUILD ROW
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

        "ls_ratio":
            ls_ratio,

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
# LOAD OLD DATABASE
# ============================================================

database = {}

RAW_FIELDS = [
    "eth_close",
    "eth_low",
    "oi_close_usd",
    "short_liquidations_usd",
    "long_liquidations_usd",
    "ls_ratio",
    "funding_rate",
    "oi_contracts",
    "liq_contracts",
    "funding_contracts",
    "ls_contracts"
]


def parse_float(value):

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


if os.path.exists(CSV_FILE):

    print("")
    print("Loading existing CSV...")

    with open(
        CSV_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:

            try:

                timestamp = int(
                    float(
                        row["timestamp"]
                    )
                )

            except (
                KeyError,
                ValueError,
                TypeError
            ):

                continue

            database[timestamp] = {

                "timestamp": timestamp,

                "utc": row.get(
                    "utc",
                    ""
                )
            }

            for field in RAW_FIELDS:

                database[
                    timestamp
                ][field] = parse_float(
                    row.get(field)
                )


print(
    "Existing rows:",
    len(database)
)


# ============================================================
# SMART MERGE
#
# Fresh None never overwrites an existing valid value
# ============================================================

for timestamp, fresh in fresh_rows.items():

    if timestamp not in database:

        database[timestamp] = fresh.copy()

        continue

    old = database[timestamp]

    old["utc"] = fresh.get(
        "utc",
        old.get("utc", "")
    )

    for field in RAW_FIELDS:

        new_value = fresh.get(field)

        if new_value is not None:

            old[field] = new_value


# ============================================================
# SORT DATABASE
# ============================================================

timestamps = sorted(
    database.keys()
)

rows = [
    database[timestamp]
    for timestamp in timestamps
]


# ============================================================
# DYNAMICS HELPERS
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


def prior_value(
    rows,
    index,
    field,
    hours
):

    target = index - hours

    if target < 0:
        return None

    return rows[target].get(
        field
    )


def rolling_sum(
    rows,
    index,
    field,
    hours
):

    start = index - hours + 1

    if start < 0:
        return None

    values = []

    for i in range(
        start,
        index + 1
    ):

        value = rows[i].get(
            field
        )

        if value is None:
            return None

        values.append(value)

    return sum(values)


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
# WRITE CSV
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
    "ETH AUTO REFRESH COMPLETE"
)
print(
    "================================"
)

print(
    "Total database rows:",
    len(rows)
)

print(
    "Fresh hours checked:",
    REFRESH_HOURS
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
    "LAST 6 HOURS"
)
print(
    "------------"
)

for row in rows[-6:]:

    print(
        row["utc"],
        "| Close:",
        row["eth_close"],
        "| Low:",
        row["eth_low"],
        "| OI:",
        round(
            row["oi_close_usd"]
            / 1_000_000_000,
            3
        )
        if row.get(
            "oi_close_usd"
        ) is not None
        else None,
        "B",
        "| dOI:",
        round(
            row["oi_change_1h_usd"]
            / 1_000_000,
            1
        )
        if row.get(
            "oi_change_1h_usd"
        ) is not None
        else None,
        "M",
        "| Short:",
        round(
            row[
                "short_liquidations_usd"
            ] / 1_000_000,
            3
        )
        if row.get(
            "short_liquidations_usd"
        ) is not None
        else None,
        "M",
        "| Long:",
        round(
            row[
                "long_liquidations_usd"
            ] / 1_000_000,
            3
        )
        if row.get(
            "long_liquidations_usd"
        ) is not None
        else None,
        "M"
    )

print("")
print(
    "CSV updated:",
    CSV_FILE
)
