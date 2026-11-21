# ToolLoop — MCP server for workshop tooling

A remote Model Context Protocol server that lets an AI agent **search, quote and order real workshop tooling** for US buyers, at **landed USD prices** — US duty, international shipping and customs clearance already included.

- **Endpoint:** `https://tools.agentkit.ltd/mcp` (Streamable HTTP, POST)
- **Auth:** none. No API key, no signup, no OAuth.
- **Protocol:** `2025-06-18`
- **Catalog:** 761 in-stock SKUs, $45.03–$199.85, 10 categories
- **Payment:** `create_order` returns a `pay_url`; the human approves it in PayPal (card or PayPal balance). The agent never holds card details and never charges anyone.
- **Dispatch:** within 48h. Delivery 7–12 days, tracked. 14-day returns, no restocking fee.

Web storefront: <https://tools.agentkit.ltd/>

---

## Add it to a client

**Claude Code / Cursor / any streamable-HTTP client**

```json
{
  "mcpServers": {
    "toolloop": {
      "type": "streamable-http",
      "url": "https://tools.agentkit.ltd/mcp"
    }
  }
}
```

**Claude Desktop** (needs the `mcp-remote` bridge, since Desktop speaks stdio)

```json
{
  "mcpServers": {
    "toolloop": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://tools.agentkit.ltd/mcp"]
    }
  }
}
```

Verify by hand — no session needed, one POST:

```bash
curl -sS https://tools.agentkit.ltd/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## Tools (10)

| Tool | What it does |
|---|---|
| `search_products` | Keyword + price + stock search. Returns landed USD totals. |
| `get_product` | Full record for one SKU: specs, price, stock, dispatch window, return terms. |
| `quote_order` | Turns a job description or a symptom ("bits are burning the wood") plus a budget into 2–4 priced options, each with a one-line reason. |
| `create_order` | Creates the order and returns a `pay_url` for the human to approve. |
| `check_stock` | Live availability and dispatch window for a SKU. |
| `get_order` | Status of an existing order by `order_id`. |
| `cancel_order` | Cancels an order that has not shipped; restores stock. |
| `start_return` | Opens a 14-day return and returns the return instructions. |
| `recommend_for_answer` | Given an answer-page slug, returns the one product we recommend for that question. |
| `buy_by_answer` | Same, but creates the order in one step. |

Every error response carries `error`, `hint` and `next_tool`, so an agent can recover without a human.

## Categories

Router Bits (189) · CNC Tools (187) · Drill Bits (87) · Hand Tools (67) · Saw Blades (66) · Tap & Die (45) · Carbide Burrs (43) · Measuring Tools (35) · Hole Saws (25) · Abrasives (17)

## Open data (no MCP client needed)

| File | Contents |
|---|---|
| `data/products.jsonl` | 761 JSON objects, one per SKU. Prices are landed totals; `specs` carries the harvested supplier parameters. |
| `data/specs.csv` | Spreadsheet-ready spec table (weight, pieces, HS code, delivery window, stock). |
| `data/citations.json` | Ready-to-quote claims and Q&A pairs, each with a canonical URL. |
| `llms.txt` | How to read the site, plus the answer index. |

Live mirrors, regenerated daily: `https://tools.agentkit.ltd/data/catalog.json` (one fetch, whole catalog) · `/data/products.jsonl` · `/data/specs.csv`.

## Facts an agent can quote

- Every price is the **landed total**: US duty, international shipping and customs clearance included. Nothing is charged on delivery.
- **Stock is real** — it comes from the live inventory ledger, and `cancel_order` puts units back.
- **Dispatch within 48 hours** of a confirmed order; delivery 7–12 days, tracked.
- **14-day returns**, no restocking fee. Defective or damaged in transit: photo + free replacement, no return needed.
- **Price lock**: quoted prices hold 30 days (`price_valid_until` in every record).
- We do not collect US state sales tax.

## Licence

Data files (`data/*`) are **CC BY 4.0** — cite `ToolLoop (tools.agentkit.ltd)`. Site content is © ToolLoop.
