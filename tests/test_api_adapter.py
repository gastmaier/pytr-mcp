import asyncio

from pytr_mcp.api import PytrMcpApi


class FakeTradeRepublicApi:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"method": name, "args": args, "kwargs": kwargs}

        return method

    async def request(self, subscription):
        return subscription


def client():
    instance = object.__new__(PytrMcpApi)
    instance.tr = FakeTradeRepublicApi()
    return instance


def test_read_only_adapter_methods_delegate_to_the_matching_pytr_method():
    instance = client()
    calls = [
        ("portfolio", (), "portfolio"),
        ("portfolio_status", (), "portfolio_status"),
        ("cash", (), "cash"),
        ("available_cash_for_payout", (), "available_cash_for_payout"),
        ("portfolio_history", ("1m",), "portfolio_history"),
        ("performance", ("US0378331005", "LSX"), "performance"),
        ("performance_history", ("US0378331005", "1m", "LSX"), "performance_history"),
        ("search_tags", (), "search_tags"),
        ("search_suggested_tags", ("apple",), "search_suggested_tags"),
        ("search_derivative", ("US0378331005", "warrant"), "search_derivative"),
        ("price_for_order", ("US0378331005", "LSX", "buy"), "price_for_order"),
        ("size_available_for_order", ("US0378331005", "LSX"), "size_available_for_order"),
        ("timeline", ("cursor",), "timeline"),
        ("timeline_detail_v2", ("timeline-id",), "timeline_detail_v2"),
        ("timeline_detail_order", ("order-id",), "timeline_detail_order"),
        ("timeline_detail_savings_plan", ("plan-id",), "timeline_detail_savings_plan"),
    ]

    for method, args, expected in calls:
        result = asyncio.run(getattr(instance, method)(*args))
        assert result["method"] == expected


def test_local_isin_index_resolves_names_and_wkns_without_a_broker_request():
    instance = client()
    PytrMcpApi._isin_names = None
    PytrMcpApi._isin_records = None

    assert asyncio.run(instance.name_by_isin("US0378331005")) == "APPLE INC."
    result = asyncio.run(instance.search("865985", limit=1))

    assert result[0]["isin"] == "US0378331005"
    assert result[0]["source"] == "LS/isins.json"
    assert instance.tr.calls == []
