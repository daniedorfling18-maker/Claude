import json
import requests

slugs = [
    "will-abelardo-de-la-espriella-win-the-2026-colombian-presidential-election",
    "israel-x-syria-security-agreement-by-june-30",
    "will-harvey-weinstein-be-sentenced-to-between-10-and-20-years-in-prison",
    "will-russia-capture-lyman-by-june-30-2026-413",
]

for slug in slugs:
    print("\n" + "=" * 120)
    print(slug)

    r = requests.get(
        "https://gamma-api.polymarket.com/markets",
        params={"slug": slug},
        timeout=30,
    )
    print("status:", r.status_code)
    r.raise_for_status()

    data = r.json()
    if isinstance(data, dict):
        data = data.get("data", [])

    if not data:
        print("NO DATA")
        continue

    m = data[0]

    keys = [
        "id",
        "slug",
        "question",
        "active",
        "closed",
        "archived",
        "acceptingOrders",
        "enableOrderBook",
        "endDate",
        "end_date",
        "closedTime",
        "resolvedBy",
        "resolutionSource",
        "umaResolutionStatus",
        "outcomes",
        "clobTokenIds",
        "outcomePrices",
        "volume",
        "liquidity",
        "liquidityNum",
        "volumeNum",
    ]

    for k in keys:
        print(f"{k}: {m.get(k)}")

    print("\nAll keys:")
    print(sorted(m.keys()))
