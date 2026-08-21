"""Trade Republic Model Context Protocol server."""

import asyncio
import csv
import json
import math
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pytr_mcp.argument_parser import get_arguments
from pytr_mcp.credentials import cookies_file, credentials

mcp = MCPServer("Trade Republic")
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
MUTATION = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)
EXECUTOR = ThreadPoolExecutor(max_workers=1)
CALL_LOCK = asyncio.Lock()
LOCALE = "en"
CURRENCY = "EUR"
ALLOW_ORDERS = False
ALLOW_WATCHLIST = False
AUDIT_LOG = Path.home() / ".local" / "state" / "trade-republic" / "mcp-audit.jsonl"
ISIN_PATTERN = re.compile(r"[A-Z0-9]{12}")
EXCHANGES = {"LSX", "TDG", "LUS", "TUB", "BHS", "B2C"}
EXPIRIES = {"gfd", "gtd", "gtc"}
ORDER_TYPES = {"buy", "sell"}
ORDER_MODES = {"market", "limit", "stopMarket"}
RANGES = {"1d", "5d", "1m", "3m", "1y", "max"}
SENSITIVE_FIELDS = {"authorization", "cookie", "cookies", "credential", "credentials", "pin", "password"}


def new_client():
    from pytr_mcp.api import PytrMcpApi

    number, pin = credentials()
    return PytrMcpApi(number, pin, locale=LOCALE, cookies_file=cookies_file())


def api():
    client = new_client()
    if not client.resume_session():
        raise RuntimeError("Trade Republic session expired. Call renew_session and approve the login in the Trade Republic app.")
    return client


def blocking_renew_session():
    client = new_client()
    if client.resume_session():
        return {"status": "resumed"}
    client.initiate_web_login()
    if client.requiredAction == "AUTHENTICATOR_VERIFICATION":
        raise RuntimeError("Authenticator verification is required and cannot be completed through this MCP server.")
    client.complete_web_login()
    return {"status": "renewed"}


async def call(method, *args):
    async with CALL_LOCK:
        client = api()
        try:
            return await getattr(client, method)(*args)
        finally:
            await client.close()


async def history(isin, range, exchange):
    return await call("aggregate_history_light", isin, range, 86400000, exchange)


def valid_isin(isin):
    normalized = isin.upper()
    if not ISIN_PATTERN.fullmatch(normalized):
        raise ValueError("isin must be a 12-character alphanumeric ISIN")
    return normalized


def positive_number(value, name):
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def redact(value):
    if isinstance(value, dict):
        return {key: "[redacted]" if key.lower() in SENSITIVE_FIELDS else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def audit(action, request, response):
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "request": redact(request),
        "response": redact(response),
    }
    with AUDIT_LOG.open("a") as file:
        file.write(json.dumps(record, default=str) + "\n")


def capabilities():
    return {"priceAlerts": ALLOW_WATCHLIST, "orders": ALLOW_ORDERS, "watchlist": ALLOW_WATCHLIST}


@mcp.tool(annotations=READ_ONLY)
async def renew_session() -> dict:
    """Renew the broker session; approve the prompted login in the Trade Republic app."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, blocking_renew_session)


@mcp.tool(annotations=READ_ONLY)
async def session_status() -> dict:
    """Confirm that the local Trade Republic browser session can be resumed."""
    await call("available_cash")
    return {"authenticated": True, "mode": "read-only" if not any(capabilities().values()) else "mutation-enabled", "capabilities": capabilities()}


@mcp.tool(annotations=READ_ONLY)
async def available_cash() -> list[dict]:
    """Return cash in every Trade Republic cash account; it is separate from brokerage portfolio value."""
    return await call("available_cash")


@mcp.tool(annotations=READ_ONLY)
async def current_orders() -> dict:
    """Return currently open orders. This tool cannot modify or cancel them."""
    return await call("orders")


@mcp.tool(annotations=READ_ONLY)
async def wallet_performance(range: str = "1d") -> str:
    """Return brokerage securities-account chart data as compact CSV, not total wealth or cash-account balance."""
    if range not in RANGES:
        raise ValueError(f"range must be one of {sorted(RANGES)}")
    account_pairs = await call("account_pairs")
    accounts = account_pairs.get("accounts", [])
    account = next(
        (item for item in accounts if item.get("productType") == "DEFAULT" and item.get("securitiesAccountNumber")),
        None,
    )
    if account is None:
        raise RuntimeError("No brokerage securities account is available")
    chart = await call("portfolio_chart", account["securitiesAccountNumber"], range, account.get("currency", CURRENCY))
    lines = [
        "accountScope,currency,openingTime,expectedClosingTime",
        f'brokerage_securities_account,{chart["currency"]},{chart["openingTime"]},{chart["expectedClosingTime"]}',
        "timestamp,netValue,relativeValue,absoluteValue",
    ]
    lines.extend(
        f'{point["timestamp"]},{point["netValue"]},{point["performance"]["relativeValue"]},{point["performance"]["absoluteValue"]}'
        for point in chart["points"]
    )
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
async def wallet_positions() -> list[dict]:
    """Get open brokerage positions and their ISINs.

    Returns: JSON
      [{"isin":"US0378331005","name":"APPLE INC.","quantity":2.0,"averageBuyIn":180.0,"currentPrice":190.0,"currentValue":380.0,"unrealizedPnl":20.0,"unrealizedPnlPct":5.56}]
    """
    result = []
    for position in await call("wallet_positions_with_quotes"):
        current_price = float(position["quote"]["last"]["price"])
        quantity = float(position["netSize"])
        average_buy_in = float(position["averageBuyIn"])
        unrealized_pnl = (current_price - average_buy_in) * quantity
        result.append({
            "isin": position["isin"],
            "name": position["name"],
            "quantity": quantity,
            "averageBuyIn": average_buy_in,
            "currentPrice": current_price,
            "currentValue": round(current_price * quantity, 2),
            "unrealizedPnl": round(unrealized_pnl, 2),
            "unrealizedPnlPct": round(unrealized_pnl / (average_buy_in * quantity) * 100, 2),
        })
    return result


@mcp.tool(annotations=READ_ONLY)
async def instrument_search(query: str, limit: int = 10) -> list[dict]:
    """Search by the required non-empty query string; call with {"query":"Apple","limit":5}.

    Returns: JSON
      [{"wkn":"865985","isin":"US0378331005","name":"APPLE INC.","symbol":"APC","type":null,"source":"LS/isins.json","datasetUpdatedAt":1787226430.0}]
    """
    query = query.strip()
    if not query:
        raise ValueError("query is required")
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    return await call("isins_by_name", query, limit)


@mcp.tool(annotations=READ_ONLY)
async def watchlist() -> str:
    """Get watchlist with daily relative performance

    Returns: CSV
      Name,ISIN,Price,Daily relative
    """
    items = await call("watchlist_details")
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["Name", "ISIN", "Price", "Daily relative"])
    writer.writerows(
        [
            item.get("core.shortName", ""),
            item.get("isin", ""),
            item.get("technical.quoteLastPriceToday", ""),
            f'{float(item["technical.deltaClosingPricePctToday"]):.2%}' if item.get("technical.deltaClosingPricePctToday") is not None else "",
        ]
        for item in items
    )
    return output.getvalue().rstrip("\n")


@mcp.tool(annotations=READ_ONLY)
async def sector_market_overview(limit: int = 200) -> list[dict]:
    """Return market-cap-weighted sector performance, market caps, stock counts, and largest constituents."""
    if not 50 <= limit <= 500:
        raise ValueError("limit must be between 50 and 500")
    return await call("sector_market_overview", limit)


@mcp.tool(annotations=READ_ONLY)
async def market_overview(category: str = "all", limit: int = 10) -> dict:
    """Return today's top gainers, top losers, or most-traded stocks."""
    category = category.strip().lower()
    if category == "most_traded":
        category = "traded"
    if category not in {"all", "gainers", "losers", "traded"}:
        raise ValueError("category must be one of all, gainers, losers, or most_traded")
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    return await call("market_overview", category, limit)


@mcp.tool(annotations=READ_ONLY)
async def instrument(isin: str) -> dict:
    """Return detailed instrument metadata for an ISIN."""
    return await call("instrument", valid_isin(isin))


@mcp.tool(annotations=READ_ONLY)
async def ticker(isin: str, exchange: str = "LSX") -> dict:
    """Return the current bid, ask, last price, open price, and quote quality for an ISIN."""
    exchange = exchange.upper()
    if exchange not in EXCHANGES:
        raise ValueError(f"exchange must be one of {sorted(EXCHANGES)}")
    return await call("ticker", valid_isin(isin), exchange)


@mcp.tool(annotations=READ_ONLY)
async def stock_news(isin: str, limit: int = 10) -> list[dict]:
    """Return recent news articles for a stock instrument."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    articles = await call("neon_news", valid_isin(isin))
    return [
        {
            "id": article.get("id"),
            "publishedAt": datetime.fromtimestamp(article["createdAt"] / 1000, timezone.utc).isoformat(),
            "provider": article.get("provider"),
            "headline": article.get("headline"),
            "summary": article.get("summary"),
            "url": article.get("url"),
        }
        for article in articles[:limit]
    ]


@mcp.tool(annotations=READ_ONLY)
async def stock_details(isin: str) -> dict:
    """Return company, dividend, analyst-rating, event, and similar-stock data for an ISIN."""
    return await call("stock_details", valid_isin(isin))


@mcp.tool(annotations=READ_ONLY)
async def stock_kpis(isin: str) -> dict:
    """Return quarterly and annual revenue, profit, EPS, EBITDA, ROE, and ROA data for an ISIN."""
    return await call("stock_detail_kpis", valid_isin(isin))


def history_range(start: date) -> str:
    days = (datetime.now(timezone.utc).date() - start).days
    if days <= 31:
        return "1m"
    if days <= 92:
        return "3m"
    if days <= 366:
        return "1y"
    return "max"


def daily_bars(result: dict) -> list[dict]:
    return [
        {
            "date": datetime.fromtimestamp(item["time"] / 1000, timezone.utc).date().isoformat(),
            "open": float(item["open"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "close": float(item["close"]),
        }
        for item in result["aggregates"]
    ]


@mcp.tool(annotations=READ_ONLY)
async def historical_prices(isin: str, range: str = "1m", exchange: str = "LSX") -> list[dict]:
    """Return daily OHLC prices. Range is one of 1d, 5d, 1m, 3m, 1y, or max."""
    return daily_bars(await history(valid_isin(isin), range, exchange.upper()))


@mcp.tool(annotations=READ_ONLY)
async def price_change(isin: str, start_date: str, end_date: str | None = None, exchange: str = "LSX") -> dict:
    """Compare closing prices on two ISO dates. Non-trading dates use the previous trading day's close."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date) if end_date else datetime.now(timezone.utc).date()
    if end < start:
        raise ValueError("end_date must not be earlier than start_date")

    result = await history(valid_isin(isin), history_range(start), exchange.upper())
    bars = daily_bars(result)

    def close_on_or_before(target):
        matches = [bar for bar in bars if bar["date"] <= target.isoformat()]
        if not matches:
            raise ValueError(f"No price is available on or before {target.isoformat()}")
        return matches[-1]

    first = close_on_or_before(start)
    last = close_on_or_before(end)
    delta = round(last["close"] - first["close"], 2)
    return {
        "isin": valid_isin(isin),
        "exchange": exchange.upper(),
        "requestedStartDate": start.isoformat(),
        "start": first,
        "requestedEndDate": end.isoformat(),
        "end": last,
        "delta": delta,
        "percentDelta": round(delta / first["close"] * 100, 2),
    }


@mcp.tool(annotations=READ_ONLY)
async def timeline_transactions(after: str | None = None) -> dict:
    """Return transactions newest first. Pass `nextOlderCursor` as `after` to
    retrieve the next, older page. Does not contain price alarm events."""
    result = await call("timeline_transactions", after)
    fields = ("id", "timestamp", "title", "subtitle", "amount", "subAmount", "status", "eventType")
    return {
        "items": [
            {field: item[field] for field in fields if item.get(field) is not None}
            for item in result["items"]
        ],
        "nextOlderCursor": result["cursors"].get("after"),
    }


@mcp.tool(annotations=READ_ONLY)
async def timeline_activity_log(after: str | None = None) -> dict:
    """Return a page of non-transaction timeline activity, such as account and card activity."""
    return await call("timeline_activity_log", after)


def detail_table(sections, title):
    section = next((item for item in sections if item.get("title") == title and isinstance(item.get("data"), list)), None)
    if section is None:
        return {}
    return {
        item["title"]: item["detail"]["text"]
        for item in section["data"]
        if item.get("title") and isinstance(item.get("detail"), dict) and item["detail"].get("text") is not None
    }


@mcp.tool(annotations=READ_ONLY)
async def realized_trades(after: str | None = None) -> list[dict]:
    """Return realized trading gains/losses and broker-reported tax details for a transaction page."""
    timeline = await call("timeline_transactions", after)
    transactions = [item for item in timeline["items"] if item.get("eventType") == "TRADING_TRADE_EXECUTED"]
    results = []
    for transaction in transactions:
        detail = await call("timeline_detail", transaction["id"])
        sections = detail["sections"]
        performance = detail_table(sections, "Performance")
        if not performance:
            continue
        overview = detail_table(sections, "Overview")
        tax_row = next(
            (item for section in sections if section.get("title") == "Overview" for item in section.get("data", []) if item.get("title") == "Tax"),
            {},
        )
        tax_sections = tax_row.get("detail", {}).get("action", {}).get("payload", {}).get("sections", [])
        taxes = detail_table(tax_sections, "Taxes")
        results.append(
            {
                "id": transaction["id"],
                "timestamp": transaction["timestamp"],
                "isin": next((section.get("action", {}).get("payload") for section in sections if section.get("type") == "header"), None),
                "name": transaction.get("title"),
                "transaction": overview.get("Transaction"),
                "realizedPnl": performance.get("Gain") or performance.get("Loss"),
                "realizedPnlPct": performance.get("Profit"),
                "fee": overview.get("Fee"),
                "tax": overview.get("Tax"),
                "capitalGainsTax": taxes.get("Capital gains tax"),
                "solidaritySurcharge": taxes.get("Solidarity surcharge"),
                "churchTax": taxes.get("Church tax"),
            }
        )
    return results


@mcp.tool(annotations=READ_ONLY)
async def price_alarms() -> str:
    """Get active and triggered price alarms as compact CSV with asset names.

    Return example (CSV):
      name,instrumentId,status,createdPrice,targetPrice,createdAt,triggeredAt
      APPLE INC.,US0378331005,active,180.0,190.0,1750000000000
    """
    alarms = await call("price_alarms_with_names")
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["name", "instrumentId", "status", "createdPrice", "targetPrice", "createdAt", "triggeredAt"])
    for alarm in alarms:
        writer.writerow([
            alarm["name"],
            *(alarm.get(field) or "" for field in ("instrumentId", "status", "createdPrice", "targetPrice", "createdAt", "triggeredAt")),
        ])
    return output.getvalue().rstrip("\n")


def configure_mutation_tools(allow_orders, allow_watchlist):
    global ALLOW_ORDERS, ALLOW_WATCHLIST
    ALLOW_ORDERS = allow_orders
    ALLOW_WATCHLIST = allow_watchlist
    enabled = []

    if allow_watchlist:
        enabled.append("watchlist and price-alert mutations")

        @mcp.tool(annotations=MUTATION)
        async def add_to_watchlist(isin: str) -> dict:
            """Add an instrument to the Trade Republic watchlist."""
            request = {"isin": valid_isin(isin)}
            try:
                response = await call("add_to_watchlist", request["isin"])
            except Exception as error:
                audit("add_to_watchlist", request, {"error": str(error)})
                raise
            audit("add_to_watchlist", request, response)
            return response

        @mcp.tool(annotations=MUTATION)
        async def remove_from_watchlist(isin: str) -> dict:
            """Remove an instrument from the Trade Republic watchlist."""
            request = {"isin": valid_isin(isin)}
            try:
                response = await call("remove_from_watchlist", request["isin"])
            except Exception as error:
                audit("remove_from_watchlist", request, {"error": str(error)})
                raise
            audit("remove_from_watchlist", request, response)
            return response

    if allow_watchlist:

        @mcp.tool(annotations=MUTATION)
        async def create_price_alarm(isin: str, target_price: float) -> dict:
            """Create a live price alarm for an ISIN at a positive target price."""
            request = {"isin": valid_isin(isin), "targetPrice": positive_number(target_price, "target_price")}
            try:
                response = await call("create_price_alarm", request["isin"], request["targetPrice"])
            except Exception as error:
                audit("create_price_alarm", request, {"error": str(error)})
                raise
            audit("create_price_alarm", request, response)
            return response

        @mcp.tool(annotations=MUTATION)
        async def cancel_price_alarm(alarm_id: str) -> dict:
            """Cancel a live price alarm by its alarm ID."""
            if not alarm_id:
                raise ValueError("alarm_id is required")
            request = {"alarmId": alarm_id}
            try:
                response = await call("cancel_price_alarm", alarm_id)
            except Exception as error:
                audit("cancel_price_alarm", request, {"error": str(error)})
                raise
            audit("cancel_price_alarm", request, response)
            return response

    if allow_orders:
        enabled.append("order mutations")

        @mcp.tool(annotations=MUTATION)
        async def create_order(isin: str, side: str, mode: str, quantity: float, expiry: str = "gfd", price: float | None = None, price_reference: str | None = None, exchange: str = "LSX") -> dict:
            """Place a live market, limit, or stop-market buy or sell order. A limit price can use bid, ask, or last."""
            normalized_exchange = exchange.upper()
            normalized_side = side.lower()
            normalized_mode = "stopMarket" if mode.lower() == "stopmarket" else mode.lower()
            normalized_expiry = expiry.lower()
            if normalized_exchange not in EXCHANGES:
                raise ValueError(f"exchange must be one of {sorted(EXCHANGES)}")
            if normalized_side not in ORDER_TYPES:
                raise ValueError(f"side must be one of {sorted(ORDER_TYPES)}")
            if normalized_mode not in ORDER_MODES:
                raise ValueError(f"mode must be one of {sorted(ORDER_MODES)}")
            if price_reference is not None:
                price_reference = price_reference.lower()
                if normalized_mode != "limit":
                    raise ValueError("price_reference is only supported for limit orders")
                if price is not None or price_reference not in {"bid", "ask", "last"}:
                    raise ValueError("price_reference must be bid, ask, or last and cannot be combined with price")
                quote = await call("ticker", valid_isin(isin), normalized_exchange)
                price = float(quote[price_reference]["price"])
            if normalized_mode != "market" and price is None:
                raise ValueError("price is required for limit and stopMarket orders")
            if normalized_mode != "market":
                quote = quote if price_reference is not None else await call("ticker", valid_isin(isin), normalized_exchange)
                bid = float(quote["bid"]["price"])
                ask = float(quote["ask"]["price"])
                if normalized_mode == "limit":
                    if (normalized_side == "buy" and price >= ask):
                        raise ValueError("limit buy price must be below the current ask")
                    if (normalized_side == "sell" and price <= bid):
                        raise ValueError("limit sell price must be above the current bid")
                if normalized_mode == "stopMarket":
                    if (normalized_side == "buy" and price <= ask):
                        raise ValueError("stop-market buy trigger must be above the current ask")
                    if (normalized_side == "sell" and price >= bid):
                        raise ValueError("stop-market sell trigger must be below the current bid")
            if normalized_expiry not in EXPIRIES:
                raise ValueError(f"expiry must be one of {sorted(EXPIRIES)}")
            client_process_id = str(uuid.uuid4())
            account_pairs = await call("account_pairs")
            account = next(
                (item for item in account_pairs.get("accounts", []) if item.get("productType") == "DEFAULT" and item.get("securitiesAccountNumber")),
                None,
            )
            if account is None:
                raise RuntimeError("No brokerage securities account is available")
            request = {
                "clientProcessId": client_process_id,
                "isin": valid_isin(isin),
                "side": normalized_side,
                "mode": normalized_mode,
                "quantity": positive_number(quantity, "quantity"),
                "price": positive_number(price, "price") if price is not None else None,
                "priceReference": price_reference,
                "expiry": normalized_expiry,
                "exchange": normalized_exchange,
            }
            try:
                response = await call(
                    "simple_create_order",
                    client_process_id,
                    account["securitiesAccountNumber"],
                    request["isin"],
                    normalized_side,
                    normalized_mode,
                    request["quantity"],
                    request["price"],
                    normalized_expiry,
                    normalized_exchange,
                )
            except Exception as error:
                audit("create_order", request, {"error": str(error)})
                raise
            response["clientProcessId"] = client_process_id
            audit("create_order", request, response)
            return response

        @mcp.tool(annotations=MUTATION)
        async def cancel_order(order_id: str) -> dict:
            """Cancel a live order by its broker order ID."""
            if not order_id:
                raise ValueError("order_id is required")
            request = {"orderId": order_id}
            try:
                response = await call("cancel_order", order_id)
            except Exception as error:
                audit("cancel_order", request, {"error": str(error)})
                raise
            audit("cancel_order", request, response)
            return response

    mcp._lowlevel_server.instructions = (
        "Access to a local Trade Republic session. Live broker mutations can create, cancel, or change account state. "
        f"Enabled capabilities: {', '.join(enabled) if enabled else 'read-only only'}."
    )


def main():
    global LOCALE, CURRENCY

    args = get_arguments()
    LOCALE = args.locale
    CURRENCY = args.currency
    configure_mutation_tools(args.allow_orders, args.allow_watchlist)
    mcp.run()


if __name__ == "__main__":
    main()
