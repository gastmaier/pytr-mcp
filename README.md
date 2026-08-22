# pytr-mcp: Use TradeRepublic in coding harnesses

This is a mcp that uses [pytr](https://github.com/pytr-org/pytr), the library
for the private API of the Trade Republic online brokerage. It is not
affiliated with Trade Republic Bank GmbH.

It uses only the new API (v2), since the approve push notification is a nice bonus.

## Quickstart

The MCP intentionally only implements stdio. `systemd` provides templates on
how to integrate with systemd. `pytr-mcp.socket` uses systemd socket activation
(`Accept=yes`) to give each Unix-socket connection to a separate stdio server
process. Only bind that socket into the coding-harness namespace; do not bind
the MCP source code or credentials files. Install both `pytr-mcp.socket` and
`pytr-mcp@.service` as user units, then enable the socket with
`systemctl --user enable --now pytr-mcp.socket`.
