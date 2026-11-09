# ToolLoop MCP server (hosted)

The live server is hosted at **https://agentkit.ltd/tools/mcp** (Streamable HTTP, JSON-RPC 2.0).
This folder contains the request handler so the implementation is open and auditable.

## Tools

| Tool | Purpose |
|---|---|
| `search_products` | Keyword + price/weight/delivery filters, returns landed USD totals |
| `get_product` | Full record for one SKU |
| `quote_order` | Plain-language need + budget → priced options |
| `create_order` | Creates an order, returns `pay_url` for the human to approve |
| `recommend_for_answer` | Answer-page slug → the one product we recommend |
| `buy_by_answer` | Answer-page slug → order in one step |
| `check_stock` | Live availability and dispatch window |

## Notes

- No API key required. No OAuth.
- `POST https://agentkit.ltd/tools/mcp` with a JSON-RPC 2.0 body.
- Protocol version: `2025-06-18`.
- Discovery descriptor: `https://agentkit.ltd/tools/.well-known/mcp.json`
- OAuth probes return `{"authorization_servers": []}` because no auth is needed.
