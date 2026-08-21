import asyncio
import json
import re
from pathlib import Path

from pytr.api import TradeRepublicApi


class PytrMcpApi:
    """Async pytr client with local instrument lookup helpers."""

    _isin_names = None
    _isin_records = None
    _isin_dataset_updated_at = None

    def __init__(self, phone_no, pin, locale="en", cookies_file=None):
        self.tr = TradeRepublicApi(
            phone_no,
            pin,
            locale=locale,
            save_cookies=True,
            cookies_file=Path(cookies_file) if cookies_file else None,
            use_v2_login=True,
        )

    def resume_session(self):
        return self.tr.resume_websession()

    def initiate_web_login(self):
        return self.tr.initiate_weblogin()

    @property
    def requiredAction(self):
        return "AUTHENTICATOR_VERIFICATION" if self.tr.weblogin_needs_authenticator else None

    def complete_web_login(self):
        return self.tr.complete_weblogin()

    async def close(self):
        await self.tr.close()

    async def _request(self, subscription):
        return await self.tr.request(subscription)

    async def cash_available_for_order(self):
        return await self._request(self.tr.cash_available_for_order())

    async def order_overview(self):
        return await self._request(self.tr.order_overview())

    async def account_pairs(self):
        return await self._request(self.tr.account_pairs())

    async def instrument_details(self, isin):
        return await self._request(self.tr.instrument_details(isin))

    async def ticker(self, isin, exchange="LSX"):
        return await self._request(self.tr.ticker(isin, exchange))

    async def news(self, isin):
        return await self._request(self.tr.news(isin))

    async def stock_details(self, isin):
        return await self._request(self.tr.stock_details(isin))

    async def stock_detail_kpis(self, isin):
        return await self._request(self.tr.stock_detail_kpis(isin))

    async def timeline_transactions(self, after=None):
        return await self._request(self.tr.timeline_transactions(after))

    async def timeline_activity_log(self, after=None):
        return await self._request(self.tr.timeline_activity_log(after))

    async def timeline_detail(self, timeline_id):
        return await self._request(self.tr.timeline_detail(timeline_id))

    async def aggregate_history_light(self, isin, range="1m", resolution=86400000, exchange="LSX"):
        return await self._request(self.tr.aggregate_history_light(isin, range, resolution, exchange))

    @classmethod
    def _load_isin_data(cls):
        if cls._isin_names is not None:
            return
        path = Path(__file__).with_name("isins.json")
        items = json.loads(path.read_text())
        cls._isin_names = {item[1]: " ".join(item[2].split()) for item in items}
        cls._isin_records = [
            {
                "wkn": item[0],
                "isin": item[1],
                "name": " ".join(item[2].split()),
                "symbol": item[2].split("\n", 1)[1] if "\n" in item[2] else item[3],
                "type": item[3] if "\n" in item[2] else None,
            }
            for item in items
        ]
        cls._isin_dataset_updated_at = path.stat().st_mtime

    @staticmethod
    def _normalize_instrument_query(value):
        return " ".join(re.findall(r"[A-Z0-9]+", value.upper()))

    async def name_by_isin(self, isin):
        if not isin:
            return ""
        isin = isin.upper()
        type(self)._load_isin_data()
        name = type(self)._isin_names.get(isin)
        if name:
            return name
        instrument = await self.instrument_details(isin)
        name = instrument.get("shortName") or instrument.get("name") or isin
        type(self)._isin_names[isin] = name
        return name

    async def names_by_isin(self, isins):
        return {isin: await self.name_by_isin(isin) for isin in dict.fromkeys(isins)}

    async def price_alarm_overview(self):
        alarms = await self._request(self.tr.price_alarm_overview())
        names = await self.names_by_isin(alarm.get("instrumentId") for alarm in alarms)
        return [{**alarm, "name": names.get(alarm.get("instrumentId"), "")} for alarm in alarms]

    async def compact_portfolio(self):
        portfolio = await self._request(self.tr.compact_portfolio())
        positions = [position for category in portfolio.get("categories", []) for position in category.get("positions", [])]
        names = await self.names_by_isin(position["isin"] for position in positions)
        result = []
        for position in positions:
            isin = position["isin"]
            result.append({
                **position,
                "name": names[isin],
                "quote": await self.ticker(isin, "LSX"),
            })
        return result

    async def search(self, query, limit=10):
        normalized_query = self._normalize_instrument_query(query)
        type(self)._load_isin_data()
        local_results = []
        for record in type(self)._isin_records:
            normalized_name = self._normalize_instrument_query(record["name"])
            normalized_symbol = self._normalize_instrument_query(record["symbol"])
            if normalized_query == record["isin"] or normalized_query in (normalized_name, normalized_symbol, record["wkn"]):
                rank = 0
            elif normalized_name.startswith(normalized_query) or normalized_symbol.startswith(normalized_query):
                rank = 1
            elif normalized_query in normalized_name or normalized_query in normalized_symbol:
                rank = 2
            else:
                continue
            local_results.append((rank, record))
        if local_results:
            return [
                {**record, "source": "LS/isins.json", "datasetUpdatedAt": type(self)._isin_dataset_updated_at}
                for _, record in sorted(local_results, key=lambda item: (item[0], item[1]["name"]))[:limit]
            ]

        results = []
        seen = set()
        for asset_type in ("stock", "fund", "derivative", "crypto"):
            response = await self._request(self.tr.search(query, asset_type=asset_type, page_size=limit))
            for item in response.get("results", []):
                isin = item.get("isin")
                if isin and isin not in seen:
                    seen.add(isin)
                    results.append({
                        "isin": isin,
                        "name": item.get("name"),
                        "type": item.get("instrumentType"),
                        "subtitle": item.get("searchSubtitle") or item.get("subtitle"),
                        "source": "Trade Republic API",
                    })
        return results[:limit]

    async def add_to_watchlist(self, isin):
        return await self._request(self.tr.add_watchlist(isin))

    async def remove_from_watchlist(self, isin):
        return await self._request(self.tr.remove_watchlist(isin))

    async def create_price_alarm(self, isin, target_price):
        return await self._request(self.tr.create_price_alarm(isin, target_price))

    async def cancel_price_alarm(self, alarm_id):
        return await self._request(self.tr.cancel_price_alarm(alarm_id))

    async def cancel_order(self, order_id):
        return await self._request(self.tr.cancel_order(order_id))

    async def market_order(self, isin, exchange, order_type, size, expiry, sell_fractions=False):
        return await self._request(self.tr.market_order(isin, exchange, order_type, size, expiry, sell_fractions))

    async def limit_order(self, isin, exchange, order_type, size, price, expiry):
        return await self._request(self.tr.limit_order(isin, exchange, order_type, size, price, expiry))

    async def stop_market_order(self, isin, exchange, order_type, size, price, expiry):
        return await self._request(self.tr.stop_market_order(isin, exchange, order_type, size, price, expiry))

    async def watchlist(self):
        entries = await self._request(self.tr.watchlist())
        result = []
        for entry in entries:
            isin = entry.get("isin") or entry.get("instrumentId")
            if not isin:
                continue
            instrument = await self.instrument_details(isin)
            quote = await self.ticker(isin)
            result.append({
                "isin": isin,
                "core.shortName": instrument.get("shortName") or instrument.get("name") or isin,
                "technical.quoteLastPriceToday": quote.get("last", {}).get("price"),
                "technical.deltaClosingPricePctToday": (quote.get("delta") or {}).get("relative"),
            })
        return result

    async def market_overview(self, category="all", limit=10):
        def fetch(category):
            sort_by = {
                "gainers": "technical.deltaClosingPricePctToday",
                "losers": "technical.deltaClosingPricePctToday",
                "traded": "technical.volumeToday",
            }
            response = self.tr._websession.post(
                f"{self.tr._host}/api-gateway/screeners/api/v2/screeners/0d0997ac-9021-4796-aeef-7d04e23b85fc/items/query",
                headers=self.tr._login_headers(),
                params=[
                    ("pageSize", limit),
                    ("sortBy", sort_by[category]),
                    ("sortOrder", "asc" if category == "losers" else "desc"),
                    *[("columns", column) for column in ("isin", "core.shortName", "technical.quoteLastPriceToday", "technical.deltaClosingPricePctToday", "technical.volumeToday")],
                ],
                json=[],
            )
            response.raise_for_status()
            return {"items": [
                {"isin": item.get("isin"), "name": item.get("core.shortName"), "price": item.get("technical.quoteLastPriceToday"), "changePct": item.get("technical.deltaClosingPricePctToday"), "volume": item.get("technical.volumeToday")}
                for item in response.json().get("items", [])
            ]}
        if category == "all":
            return {"gainers": (await asyncio.to_thread(fetch, "gainers"))["items"], "losers": (await asyncio.to_thread(fetch, "losers"))["items"], "mostTraded": (await asyncio.to_thread(fetch, "traded"))["items"]}
        return await asyncio.to_thread(fetch, category)

    async def sector_market_overview(self, limit=200):
        def fetch():
            response = self.tr._websession.post(
                f"{self.tr._host}/api-gateway/screeners/api/v2/screeners/0d0997ac-9021-4796-aeef-7d04e23b85fc/items/query",
                headers=self.tr._login_headers(),
                params=[("pageSize", limit), ("sortBy", "fundamental.marketCap"), ("sortOrder", "desc"), *[("columns", column) for column in ("isin", "core.shortName", "descriptive.sectors", "fundamental.marketCap", "technical.deltaClosingPricePctToday")]],
                json=[],
            )
            response.raise_for_status()
            buckets = {}
            for item in response.json().get("items", []):
                market_cap, change = item.get("fundamental.marketCap"), item.get("technical.deltaClosingPricePctToday")
                if market_cap is None or change is None:
                    continue
                sector = next((value for value in item.get("descriptive.sectors", []) if value != "largecap"), "unclassified")
                bucket = buckets.setdefault(sector, {"marketCap": 0.0, "weightedChange": 0.0, "stocks": []})
                bucket["marketCap"] += float(market_cap)
                bucket["weightedChange"] += float(market_cap) * float(change)
                bucket["stocks"].append({"name": item.get("core.shortName"), "isin": item.get("isin"), "marketCap": float(market_cap)})
            return [{"sector": sector.replace("_", " ").title(), "marketCap": round(bucket["marketCap"], 2), "dailyRelativePct": round(bucket["weightedChange"] / bucket["marketCap"] * 100, 2), "stockCount": len(bucket["stocks"]), "topStocks": sorted(bucket["stocks"], key=lambda stock: stock["marketCap"], reverse=True)[:5]} for sector, bucket in sorted(buckets.items(), key=lambda entry: entry[1]["marketCap"], reverse=True)]
        return await asyncio.to_thread(fetch)

    async def portfolio_chart(self, account_number, range="1d", currency="EUR"):
        def fetch():
            response = self.tr._websession.get(
                f"{self.tr._host}/api-gateway/portfolio-chart/v2/chart",
                params={"secAccNo": account_number, "range": range, "currency": currency},
            )
            response.raise_for_status()
            return response.json()
        return await asyncio.to_thread(fetch)
