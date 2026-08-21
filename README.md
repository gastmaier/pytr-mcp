# pytr-mcp: Use TradeRepublic in coding harnesses

This is a mcp that uses [pytr](https://github.com/pytr-org/pytr), the library
for the private API of the Trade Republic online brokerage. It is not
affiliated with Trade Republic Bank GmbH.

It uses only the new API (v2), since the approve push notification is a nice bonus.

## Quickstart

The MCP intentionally only implements stdio, to force you to secure it. It is
recommended to use [rmcp-mux](https://github.com/VetCoders/rmcp-mux) to expose
it through a unix socket; then only this socket is readable by the linux
namespace where your coding harness runs. Don't bind the mcp source code or
credentials files to the coding harness namespace, please.
