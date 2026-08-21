import asyncio

import pytest

import pytr_mcp.mcp as broker_mcp


def test_price_for_order_rejects_invalid_order_type():
    with pytest.raises(ValueError, match="invalid exchange or order_type"):
        asyncio.run(broker_mcp.price_for_order("US0378331005", "hold"))


def test_search_suggested_tags_requires_a_query():
    with pytest.raises(ValueError, match="query is required"):
        asyncio.run(broker_mcp.search_suggested_tags("  "))


def test_size_available_for_order_uses_normalized_exchange(monkeypatch):
    calls = []

    async def fake_call(method, *args):
        calls.append((method, args))
        return {"size": 1}

    monkeypatch.setattr(broker_mcp, "call", fake_call)

    assert asyncio.run(broker_mcp.size_available_for_order("us0378331005", "lsx")) == {"size": 1}
    assert calls == [("size_available_for_order", ("US0378331005", "LSX"))]
