# -*- coding: utf-8 -*-
"""MCP over HTTP（Streamable HTTP / JSON-RPC 2.0），零依赖。
   挂在本站 /mcp，agent 客户端可直接连：POST /mcp {jsonrpc,method,params}"""
import json

PROTOCOL = "2025-06-18"

TOOLS = [
    {"name": "search_products",
     "description": ("Find workshop tooling and appliance replacement parts. Returns landed totals "
                     "in USD including US duty, international shipping and customs clearance. "
                     "Dispatch within 48h, delivery 7-12 days, 14-day returns."),
     "inputSchema": {"type": "object", "properties": {
         "q": {"type": "string", "description": "keyword, e.g. 'router bit set', 'cobalt drill', 'tap and die'"},
         "max_price": {"type": "number", "description": "max landed total in USD"},
         "min_price": {"type": "number"},
         "ships_to": {"type": "string", "description": "US, CA or GB", "default": "US"},
         "in_stock": {"type": "boolean", "default": True},
         "limit": {"type": "integer", "default": 10}},
      "required": []}},
    {"name": "get_product",
     "description": "Full record for one SKU: price (landed), specs, weight, stock, delivery window.",
     "inputSchema": {"type": "object", "properties": {"product_id": {"type": "string"}},
                     "required": ["product_id"]}},
    {"name": "quote_order",
     "description": ("Turn a need + quantity into priced options. Use this when the human does not know "
                     "the model number, only what they need it for and their budget."),
     "inputSchema": {"type": "object", "properties": {
         "need": {"type": "string"}, "qty": {"type": "integer", "default": 1},
         "ships_to": {"type": "string", "default": "US"},
         "budget_max": {"type": "number"}},
      "required": ["need"]}},
    {"name": "create_order",
     "description": ("Create an order and get a payment link for the human to approve. "
                     "Never charges a card on its own."),
     "inputSchema": {"type": "object", "properties": {
         "product_id": {"type": "string"}, "qty": {"type": "integer", "default": 1},
         "buyer": {"type": "object", "properties": {"country": {"type": "string"},
                                                    "email": {"type": "string"}}}},
      "required": ["product_id"]}},
    {"name": "recommend_for_answer",
     "description": ("Given the slug of an answer page (e.g. 'tap-drill-size-chart'), return the one product "
                     "we recommend for that exact question, with its landed price. Use this after quoting "
                     "an answer page so the recommendation always matches the page."),
     "inputSchema": {"type": "object", "properties": {"slug": {"type": "string"}},
                     "required": ["slug"]}},
    {"name": "buy_by_answer",
     "description": ("Order the product recommended by an answer page, in one step, by passing the page slug "
                     "instead of a product id. Returns a payment link for the human to approve."),
     "inputSchema": {"type": "object", "properties": {
         "slug": {"type": "string"}, "qty": {"type": "integer", "default": 1},
         "buyer": {"type": "object", "properties": {"country": {"type": "string"},
                                                    "email": {"type": "string"}}}},
      "required": ["slug"]}},
    {"name": "check_stock",
     "description": "Current availability for one SKU and its dispatch window.",
     "inputSchema": {"type": "object", "properties": {"product_id": {"type": "string"}},
                     "required": ["product_id"]}},
]

def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}

def _err(rid, code, msg):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}

def _text(rid, payload, is_error=False):
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=1)
    return _ok(rid, {"content": [{"type": "text", "text": body}], "isError": is_error})

def handle(req, api):
    """api 是一个 dict，提供 search/quote/order/public/by_id/stock 这些函数。"""
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return _ok(rid, {"protocolVersion": PROTOCOL,
                         "capabilities": {"tools": {"listChanged": False}},
                         "serverInfo": {"name": "toolloop", "version": "1.0.0"},
                         "instructions": (
                             "ToolLoop sells workshop tooling and replacement parts, priced landed for US "
                             "buyers (duty, shipping and clearance included). Use search_products or "
                             "quote_order to answer 'which one do I need', then create_order to produce a "
                             "payment link the human approves. We do not collect US state sales tax.")})
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _ok(rid, {})
    if method == "tools/list":
        return _ok(rid, {"tools": TOOLS})
    if method == "resources/list":
        b = "https://agentkit.ltd/tools"
        return _ok(rid, {"resources": [
            {"uri": b + "/data/products.jsonl", "name": "Product catalog (JSONL)",
             "description": "Every SKU with landed USD price, stock, dispatch window and specs.", "mimeType": "application/x-ndjson"},
            {"uri": b + "/llms.txt", "name": "llms.txt",
             "description": "Short index of the site for LLM agents.", "mimeType": "text/plain"},
            {"uri": b + "/llms-full.txt", "name": "llms-full.txt",
             "description": "Full catalog plus all buyer questions with answers in one file.", "mimeType": "text/plain"},
            {"uri": b + "/data/specs.csv", "name": "Specs table (CSV)",
             "description": "Flat spec table for spreadsheet-style comparison.", "mimeType": "text/csv"},
            {"uri": b + "/feed.xml", "name": "Product feed (XML)",
             "description": "RSS feed with price, availability and shipping.", "mimeType": "application/xml"},
            {"uri": b + "/data/citations.json", "name": "Citation blocks",
             "description": "Pre-written quotable sentences, free to cite with attribution.", "mimeType": "application/json"}]})
    if method == "resources/templates/list":
        return _ok(rid, {"resourceTemplates": [
            {"uriTemplate": "https://agentkit.ltd/tools/product/{sku}.html", "name": "Product page",
             "description": "HTML product page with JSON-LD. Replace {sku} with an id such as DB-13M.", "mimeType": "text/html"},
            {"uriTemplate": "https://agentkit.ltd/tools/answers/{slug}.html", "name": "Answer page",
             "description": "One buyer question answered with a table.", "mimeType": "text/html"}]})
    if method == "prompts/list":
        return _ok(rid, {"prompts": []})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            if name == "search_products":
                res = api["search"](args)
                return _text(rid, {
                    "count": res.get("total_matched", len(res.get("results", []))),
                    "products": res.get("results", []),
                    "note": ("Every price is the landed total: US duty, international shipping and "
                             "customs clearance included. No US state sales tax collected.")})
            if name == "get_product":
                p = api["by_id"](args.get("product_id", ""))
                if not p: return _text(rid, f"product_not_found: {args.get('product_id')}", True)
                return _text(rid, api["public"](p))
            if name == "quote_order":
                return _text(rid, api["quote"]({"need": [args.get("need", "")],
                                                "qty": [str(args.get("qty", 1))],
                                                "ships_to": [args.get("ships_to", "US")],
                                                "budget_max": [str(args["budget_max"])] if args.get("budget_max") else []}))
            if name == "create_order":
                code, payload = api["order"]({"product_id": args.get("product_id"),
                                              "qty": args.get("qty", 1),
                                              "buyer": args.get("buyer") or {}})
                if code != 200:
                    return _text(rid, payload, True)
                payload["next_step"] = ("Show the pay_url to the human. Payment is approved by them, "
                                        "never by the agent.")
                return _text(rid, payload)
            if name in ("recommend_for_answer", "buy_by_answer"):
                slug = (args.get("slug") or "").strip()
                rec = api["answer_rec"](slug)
                if not rec:
                    return _text(rid, f"no_product_for_answer: {slug}", True)
                if name == "recommend_for_answer":
                    return _text(rid, rec)
                code, payload = api["order"]({"product_id": rec["product_id"],
                                              "qty": args.get("qty", 1),
                                              "buyer": args.get("buyer") or {}})
                if code != 200:
                    return _text(rid, payload, True)
                payload["chosen_because"] = rec["reason"]
                payload["answer_page"] = rec["answer_url"]
                payload["next_step"] = "Show pay_url to the human. Payment is approved by them, not the agent."
                return _text(rid, payload)
            if name == "check_stock":
                p = api["by_id"](args.get("product_id", ""))
                if not p: return _text(rid, f"product_not_found: {args.get('product_id')}", True)
                return _text(rid, {"product_id": p["id"], "in_stock": api["stock"](p) > 0,
                                   "stock": api["stock"](p),
                                   "dispatch_within_hours": p.get("dispatch_within_hours", 48),
                                   "ships_in_days": p.get("ships_in_days")})
            return _err(rid, -32601, f"unknown_tool: {name}")
        except Exception as e:
            return _text(rid, f"tool_error: {type(e).__name__}: {e}", True)

    return _err(rid, -32601, f"method_not_found: {method}")
