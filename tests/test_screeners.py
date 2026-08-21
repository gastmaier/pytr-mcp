import asyncio

import pytest

from pytr_mcp.api import PytrMcpApi


class FakeTradeRepublicApi:
    def __init__(self, screeners):
        self.screeners_response = screeners
        self.calls = []

    async def screeners(self):
        return self.screeners_response

    async def screener_items(self, screener_id, columns, **kwargs):
        self.calls.append((screener_id, columns, kwargs))
        return {"items": []}


def client_with(screeners):
    client = object.__new__(PytrMcpApi)
    client.tr = FakeTradeRepublicApi(screeners)
    return client


def test_market_overview_uses_the_first_ranked_screener_id():
    client = client_with([
        {"id": "second", "rank": 1},
        {"id": "first", "rank": 0},
    ])

    assert asyncio.run(client.market_overview("gainers", 10)) == {"items": []}
    screener_id, _, kwargs = client.tr.calls[0]
    assert screener_id == "first"
    assert kwargs["sort_by"] == "technical.deltaClosingPricePctToday"
    assert kwargs["sort_order"] == "desc"


def test_market_overview_requires_an_available_screener():
    client = client_with([])

    with pytest.raises(RuntimeError, match="available screener"):
        asyncio.run(client.market_overview("gainers", 10))
