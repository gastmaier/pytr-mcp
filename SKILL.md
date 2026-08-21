# Skill: Implementing pytr MCP Tools

Use this workflow when adding or changing Trade Republic functionality in the public `pytr` and `pytr-mcp` projects.

## Code structure

- `../pytr/pytr/api.py`: `TradeRepublicApi`: the asynchronous v2 API client and public subscription methods.
- `./pytr_mcp/api.py`: `PytrMcpApi`: async adapter that preserves MCP backend method names and shapes responses for the MCP.
- `./pytr_mcp/mcp.py`: MCP tool definitions, validation, CSV/JSON presentation, mutation gating, and audit logging.
- `./pytr_mcp/credentials.py`: systemd credential and cookie-path handling.
- `./pyproject.toml`: `pytr-mcp` package; for editing both libraries, install `../pytr` as editable.

## Public pytr API design

`TradeRepublicApi` is async. A subscription method returns a subscription coroutine; callers obtain its first result through the public helper:

```python
response = await api.request(api.ticker(isin, exchange="LSX"))
```

When a Trade Republic WebSocket operation is missing, add a small public method to `TradeRepublicApi` in `pytr/api.py` that only constructs the subscription payload:

```python
async def new_operation(self, isin: str):
    return await self.subscribe({"type": "newOperation", "id": isin})
```

Keep response collection in `request()` and keep MCP-specific shaping out of `pytr`. Prefer adding a clearly named public pytr method over calling private websocket internals from `pytr-mcp`.

Current MCP-specific additions to pytr include `account_pairs`, `aggregate_history_light`, `stock_detail_kpis`, and `request`.

## Discovery of undocumented payloads

Do not guess WebSocket payload fields. Inspect the authenticated Trade Republic web app bundle when pytr does not already implement an operation:

1. Open `https://app.traderepublic.com` in an authenticated browser session.
2. List JavaScript resources:
   ```javascript
   JSON.stringify(performance.getEntriesByType('resource')
     .map(entry => entry.name)
     .filter(url => /\.js(\?|$)/.test(url)))
   ```
3. Fetch the loaded bundles in the page context and search for the WebSocket operation name. Return only a small surrounding snippet:
   ```javascript
   (async () => {
     const url = 'https://app.traderepublic.com/assets/<feature-bundle>.js';
     const source = await (await fetch(url)).text();
     const index = source.indexOf('cancelPriceAlarm');
     return JSON.stringify(source.slice(index - 500, index + 1000));
   })()
   ```
4. If the feature bundle imports a minified helper, inspect its import alias and resolve that alias in the imported bundle's export list. Then inspect the helper implementation. The helper constructs the exact protocol request.

   For `cancelPriceAlarm`, this process found the frontend helper:
   ```javascript
   function Ohe(id) {
     return Q({ type: jW.CancelPriceAlarm, id }).pipe($e(1))
   }
   ```
   Therefore the API payload is `{"type": "cancelPriceAlarm", "id": alarm_id}`.

5. Add the discovered payload as a public `TradeRepublicApi` method and validate it read-only before exposing a mutation.

## Adding an MCP tool

1. Add the minimal underlying subscription method to `../pytr/api.py` if needed.
2. Add an async adapter method to `pytr_mcp/api.py`. It should use `await self.tr.request(...)`, preserve the current MCP response contract, and close no connection itself.
3. Add or update the `@mcp.tool` function in `pytr_mcp/mcp.py`.
   - Validate tool arguments at the MCP boundary.
   - Use `await call("adapter_method", ...)`.
   - Document the returned format directly in the docstring with a minimal mock JSON object or CSV header and row.
   - Mark read-only tools with `READ_ONLY`; mutations require the existing capability gate and audit path.
4. Keep calls serialized through `CALL_LOCK`. pytr currently keeps websocket subscription state on the client class, so concurrent MCP calls must not open overlapping pytr sessions.

Example:

```python
@mcp.tool(annotations=READ_ONLY)
async def instrument(isin: str) -> dict:
    """Return instrument metadata.

    Returns: JSON
      {"isin":"US0378331005","shortName":"Apple","typeId":"stock"}
    """
    return await call("instrument", valid_isin(isin))
```

## Authentication

`pytr-mcp` owns authentication entry points and always uses pytr's v2 login plumbing:

- Existing browser cookie dumps are provided by `PYTR_MCP_COOKIES_FILE`.
- The phone number and PIN are read from systemd's `login` credential (`<number>:<pin>`).
- `resume_session()` must be attempted before a broker call.
- `renew_session` invokes pytr's v2 login flow; the user approves it in the Trade Republic app.

Do not store credentials in source files or return them through tools.

## Testing and deployment

Use the development environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Before deployment:

1. Compile the package:
   ```bash
   cd /data/repos/pytr-mcp
   python3 -m py_compile pytr_mcp/*.py
   ```
2. Run relevant pytr tests and style checkers, for example:
   ```bash
   cd ../pytr
   python3 -m pytest tests/test_api_urls.py -q
   ruff check
   cd ../pytr-mcp
   ruff check
   ```
3. Validate the adapter and MCP response contract using read-only calls.
4. Validate `rmcp-mux` against a separate temporary Unix socket before changing the live service.

The live unit is `pytr-mcp.service`; its socket is `/run/user/1000/pytr-mcp/mcp.sock`. Restart only after validation:

```bash
systemctl --user restart pytr-mcp.service
systemctl --user status pytr-mcp.service --no-pager
```

Reconnect the MCP client and call the new read-only tool. Test a live mutation only when the user explicitly requests it; use a reversible action where possible.

## Troubleshooting

- **Session expired:** call `renew_session`, then approve the v2 login in the Trade Republic app.
- **Empty or intermittent concurrent responses:** ensure the MCP adapter path goes through `CALL_LOCK`.
- **Unknown tool or stale schema:** restart `pytr-mcp.service` and reconnect the MCP client.
- **Missing API method:** add the public async pytr subscription method first, then adapt it in `PytrMcpApi`.
