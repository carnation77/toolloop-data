#!/usr/bin/env python3
"""Agent 可读购物站 v0：静态页 + 公开只读 API + 下单（返回预填支付链接）。
只用标准库。用法：python3 server.py [port]
"""
import json, os, sys, re, uuid, datetime, pathlib, urllib.parse
import time as _time
import notifier
import mcp_http
from answers import ANSWERS as _A1
from answers_batch2 import NEW as _A2
from answers_batch3 import NEW as _A3
ANSWER_MAP = {a["slug"]: a for a in (_A1 + _A2 + _A3)}

def answer_rec(slug):
    """答案页 → 推荐商品（优先有货、最便宜、与页面主题匹配）"""
    a = ANSWER_MAP.get(slug)
    if not a: return None
    ids = [i for i in (a.get("links") or []) if i in BY_ID]
    cands = [BY_ID[i] for i in ids if stock_of(BY_ID[i]) > 0]
    if not cands:
        cands = [BY_ID[i] for i in ids]
    if not cands: return None
    best = min(cands, key=lambda p: p["price"]["amount"])
    return {"product_id": best["id"], "name": best["name_en"],
            "price": best["price"]["amount"], "currency": best["price"]["currency"],
            "price_includes": ["duty", "shipping", "clearance"],
            "in_stock": stock_of(best) > 0, "stock": stock_of(best),
            "dispatch_within_hours": best.get("dispatch_within_hours", 48),
            "ships_in_days": best["ships_in_days"],
            "answer_url": f'{META["base_url"]}/answers/{slug}.html',
            "reason": f'Recommended by the answer page "{a["q"]}" (cheapest in stock that matches).'}
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

ROOT = pathlib.Path(__file__).parent
OUT = ROOT / "out"
DATA = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
META, PRODUCTS = DATA["meta"], DATA["products"]
BY_ID = {p["id"]: p for p in PRODUCTS}
POLICY = META["policy"]
LOCK_DAYS = int(POLICY["price_lock_days"])
FUNNEL = pathlib.Path(__file__).with_name("funnel.log")

def funnel(stage, extra=""):
    """记录转化漏斗：view -> buy_click -> order -> pay_view -> paid"""
    try:
        with FUNNEL.open("a", encoding="utf-8") as f:
            f.write("%s\t%s\t%s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), stage, extra))
    except Exception:
        pass

LOG = ROOT / "calls.log"
MCP_LOG = ROOT / "mcp_calls.log"
INVENTORY = ROOT / "inventory.json"
LOW_STOCK = 5          # 低于这个数自动标缺货（不接单，但页面仍可见）

def inv_load():
    if INVENTORY.exists():
        try:
            return json.loads(INVENTORY.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def inv_save(d):
    INVENTORY.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def stock_of(p):
    """真实可售库存 = 台账值（没有台账就用 products.json 里的数）"""
    inv = inv_load()
    return int(inv.get(p["id"], p["stock"]))

def inv_take(pid, qty):
    inv = inv_load()
    cur = int(inv.get(pid, next((x["stock"] for x in PRODUCTS if x["id"] == pid), 0)))
    left = max(0, cur - qty)
    inv[pid] = left
    inv_save(inv)
    return left
PAY_BASE = os.environ.get("PAY_BASE_URL", "").rstrip("/")
PAYPAL_BUSINESS = os.environ.get("PAYPAL_BUSINESS", "")
PAYPAL_ME = os.environ.get("PAYPAL_ME", "").strip().lstrip("@")   # paypal.me 用户名
PAYPAL_BUTTON_FILE = ROOT / "paypal_button.html"   # 用户在 PayPal 后台生成的按钮代码，放这里即生效
PATH_PREFIX = urllib.parse.urlsplit(os.environ.get("PAY_BASE_URL","")).path.rstrip("/") or ""      # 你的 PayPal 商业账户邮箱
ORDERS_DB = ROOT / "orders.json"

_RATE = {}
_RATE_RULES = {"/api/order": (8, 60), "/api/buy": (8, 60), "/mcp": (90, 60), "/api/quote": (30, 60)}

def _rate_ok(ip, path):
    """每 IP 每 path 前缀的令牌桶。超限返回 False。"""
    rule = None
    for k, v in _RATE_RULES.items():
        if path.startswith(k):
            rule = v; break
    if not rule:
        return True
    limit, window = rule
    now = _time.time()
    key = (ip, path.split("/")[1] if path.startswith("/") else path)
    bucket = _RATE.setdefault(key, [])
    bucket[:] = [t for t in bucket if now - t < window]
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True

def orders_load():
    if ORDERS_DB.exists():
        try: return json.loads(ORDERS_DB.read_text(encoding="utf-8"))
        except Exception: pass
    return {}

def orders_save(d):
    ORDERS_DB.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": META["site_name"] + " — agent API",
             "version": "1.0.0",
             "description": ("Machine-readable catalog of workshop tooling. "
                             "Prices include duty, shipping and US clearance. "
                             "Ordering returns a pre-filled payment link; no card data is handled.")},
    "servers": [{"url": META["base_url"]}],
    "paths": {
        "/api/search": {"get": {
            "summary": "Search products with hard constraints",
            "parameters": [
                {"name": "q", "in": "query", "schema": {"type": "string"}},
                {"name": "min_price", "in": "query", "schema": {"type": "number"}},
                {"name": "max_price", "in": "query", "schema": {"type": "number"}},
                {"name": "max_weight_g", "in": "query", "schema": {"type": "number"}},
                {"name": "ships_to", "in": "query", "schema": {"type": "string"}, "description": "ISO country code"},
                {"name": "max_days", "in": "query", "schema": {"type": "number"}},
                {"name": "in_stock", "in": "query", "schema": {"type": "boolean"}},
                {"name": "material", "in": "query", "schema": {"type": "string"}},
                {"name": "certified", "in": "query", "schema": {"type": "boolean"}},
                {"name": "limit", "in": "query", "schema": {"type": "integer", "maximum": 50}}
            ],
            "responses": {"200": {"description": "Filtered results with landed_total and availability"}}}},
        "/api/product/{id}": {"get": {
            "summary": "Full record for one product_id",
            "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"description": "All lock fields, price lock date, dispatch time"}}}},
        "/api/order": {"post": {
            "summary": "Create an order and get a payment link",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object",
                "required": ["product_id"],
                "properties": {
                    "product_id": {"type": "string"},
                    "qty": {"type": "integer", "minimum": 1, "maximum": 20},
                    "buyer": {"type": "object", "properties": {
                        "name": {"type": "string"}, "email": {"type": "string"},
                        "country": {"type": "string"}}}}}}}},
            "responses": {"200": {"description": "order_id + pay_url (human confirms payment)"}}}}
    }
}

MIME = {".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".txt": "text/plain; charset=utf-8", ".xml": "application/xml; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
        ".ico": "image/x-icon", ".webp": "image/webp",
        ".jsonl": "application/x-ndjson", ".csv": "text/csv; charset=utf-8"}

SERVICE_INCLUDED = [
    "Export customs + US import clearance (DDP, duty paid)",
    "Door delivery in 7-12 days (tracked)",
    "Real stock count, updated on every order",
    "Returns covered by us (14 days, unopened)",
    "One item, one SKU, no mixed substitutions",
]

def public(p):
    """对外输出：剥离供应商与成本，并补上 agent 比价需要的字段"""
    q = {k: v for k, v in p.items() if k not in ("supplier", "market", "tier")}
    price = float(p["price"]["amount"])
    ship = 0.0                      # 我们做免运费到门（已含在售价里）
    q["unit_price"] = price
    q["shipping_cost"] = ship
    q["landed_total"] = round(price + ship, 2)
    q["price_includes"] = ["duty", "shipping", "clearance"]
    q["service_included"] = SERVICE_INCLUDED
    q["dispatch_within_hours"] = p.get("dispatch_within_hours", 48)
    q["handling_note"] = p.get("handling_note", "")
    q["compare_on"] = "landed_total"
    q["compare_note"] = ("Compare on landed total incl. shipping and duty. "
                         "Cheaper listings often add shipping, duty or 30+ day delivery.")
    return q

def stock_ok(p):
    return stock_of(p) > LOW_STOCK - 1

def _norm(params):
    """兼容两种输入：MCP 的 dict[str,any] 与 HTTP query 的 dict[str,list]"""
    if not isinstance(params, dict): return {}
    out = {}
    for k, v in params.items():
        out[k] = v if isinstance(v, list) else [v]
    return out

def search(params):
    params = _norm(params)
    q = (params.get("q", [""])[0] or "").strip().lower()
    def num(k):
        v = params.get(k, [""])[0]
        try: return float(v) if v not in ("", None) else None
        except ValueError: return None
    min_price, max_price = num("min_price"), num("max_price")
    max_weight = num("max_weight_g")
    max_days = num("max_days")
    ships_to = (params.get("ships_to", [""])[0] or "").upper()
    in_stock = (params.get("in_stock", [""])[0] or "").lower() in ("1", "true", "yes")
    material = (params.get("material", [""])[0] or "").lower()
    certified = (params.get("certified", [""])[0] or "").lower()
    try: limit = min(int(params.get("limit", ["20"])[0] or 20), 50)
    except ValueError: limit = 20

    words = [w for w in q.replace(",", " ").split() if len(w) > 1]
    out = []
    for p in PRODUCTS:
        hay = (p["name"] + " " + p["name_en"] + " " + p["description"] + " " + p["category"]).lower()
        if words and not all(w in hay for w in words):
            continue
        amt = p["price"]["amount"]
        if min_price is not None and amt < min_price: continue
        if max_price is not None and amt > max_price: continue
        if max_weight is not None and p["specs"].get("weight_g", 0) > max_weight: continue
        if max_days is not None and p["ships_in_days"]["max"] > max_days: continue
        if ships_to and ships_to not in [c.upper() for c in p["ships_to"]]: continue
        if in_stock and stock_of(p) <= 0: continue
        if material and material not in str(p["specs"].get("material", "")).lower(): continue
        if certified and not p.get("certifications"): continue
        pub = public(p)
        out.append({
            "product_id": p["id"], "name": p["name_en"], "name_local": p["name"],
            "category": p["category"], "price": amt, "unit_price": amt,
            "shipping_cost": 0.0, "landed_total": round(amt, 2),
            "price_includes": ["duty", "shipping", "clearance"],
            "service_included": SERVICE_INCLUDED, "compare_on": "landed_total",
            "currency": p["price"]["currency"],
            "price_valid_until": (datetime.date.today() + datetime.timedelta(days=LOCK_DAYS)).isoformat(),
            "in_stock": stock_ok(p), "stock": stock_of(p),
            "ships_in_days": p["ships_in_days"], "ships_to": p["ships_to"],
            "weight_g": p["specs"].get("weight_g"), "material": p["specs"].get("material"),
            "hs_code": p.get("hs_code"), "url": f"{META['base_url']}/product/{p['id']}.html",
        })
    def score(r):
        name = (r["name"] or "").lower()
        hit = sum(1 for w in words if w in name)
        return (-hit, not r["in_stock"], r["price"])
    out.sort(key=score)
    return {"query": q or None, "count": len(out[:limit]), "total_matched": len(out), "results": out[:limit]}

def quote(params):
    params = _norm(params)
    """agent 说需求 → 直接给报价（含税到门总价）"""
    def one(k):
        v = params.get(k, [""])
        return (v[0] if v else "") or ""
    need = one("need") or one("q") or ""
    try: qty = max(1, min(int(one("qty") or 1), 20))
    except ValueError: qty = 1
    try: budget = float(one("budget_max")) if one("budget_max") else None
    except ValueError: budget = None
    ships_to = (one("ships_to") or "US").upper()

    cands = []
    for p in PRODUCTS:
        if ships_to not in [c.upper() for c in p["ships_to"]]:
            continue
        if stock_of(p) <= 0:
            continue
        text = (p["name"] + p["name_en"] + p["description"] + p["category"]).lower()
        if need and not any(w in text for w in need.lower().split() if len(w) > 2):
            if need.lower() not in text:
                continue
        total = round(p["price"]["amount"] * qty, 2)
        if budget and total > budget:
            continue
        cands.append({
            "product_id": p["id"], "name": p["name_en"],
            "qty": qty,
            "unit_price": p["price"]["amount"],
            "total": total, "currency": p["price"]["currency"],
            "total_includes": ["duty", "international shipping", "US clearance"],
            "landed_total": total,
            "dispatch_within_hours": p.get("dispatch_within_hours", 48),
            "ships_in_days": p["ships_in_days"],
            "order_endpoint": "POST /api/order",
        })
    cands.sort(key=lambda x: x["total"])
    return {
        "need": need or None, "qty": qty, "ships_to": ships_to,
        "budget_max": budget,
        "count": len(cands[:5]),
        "quotes": cands[:5],
        "note": ("All totals include duty, international shipping and US clearance. "
                 "Dispatch within 48h; delivery 7-12 days. Ordering returns a payment link the human approves."),
    }

def order(body):
    pid = (body.get("product_id") or "").strip()
    p = BY_ID.get(pid)
    if not p: return 404, {"error": "product_not_found", "product_id": pid}
    try: qty = max(1, min(int(body.get("qty", 1)), 20))
    except (TypeError, ValueError): qty = 1
    buyer = body.get("buyer") or {}
    country = (buyer.get("country") or "").upper()
    if country and country not in [c.upper() for c in p["ships_to"]]:
        return 422, {"error": "cannot_ship_to_country", "country": country, "ships_to": p["ships_to"]}
    avail = stock_of(p)
    if avail <= 0:
        return 409, {"error": "out_of_stock", "product_id": pid}
    if qty > avail:
        return 422, {"error": "insufficient_stock", "available": avail}
    is_test_pre = bool(body.get("test"))
    left = (avail - qty) if is_test_pre else inv_take(pid, qty)
    oid = "ORD-" + datetime.datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6].upper()
    total = round(p["price"]["amount"] * qty, 2)
    pay = (PAY_BASE + "/pay/" + oid) if PAY_BASE else ("/pay/" + oid)
    is_test = bool(body.get("test")) or bool(os.environ.get("TEST_MODE"))
    _sup = (p.get("supplier") or {})
    rec = {"order_id": oid, "product_id": pid, "qty": qty, "total": total, "test": is_test,
           "sourcing": {"supplier": _sup.get("supplier"), "spec": _sup.get("spec"),
                        "url": _sup.get("url"), "offer_id": _sup.get("offer_id"),
                        "unit_cny": _sup.get("unit_cny"), "qty": _sup.get("qty", 1),
                        "freight_cny": _sup.get("domestic_freight_cny") or 6,
                        "cost_cny": _sup.get("cost_cny"),
                        "dispatch_hours": _sup.get("dispatch_commit_hours"),
                        "stock_at_check": _sup.get("stock_at_check")},
           "currency": p["price"]["currency"], "buyer": buyer,
           "created": datetime.datetime.now().isoformat(timespec="seconds"),
           "status": "awaiting_payment", "pay_url": pay}
    db = orders_load(); db[oid] = rec; orders_save(db)
    if is_test:
        rec["notified"] = "skipped(test)"; orders_save(db)
        return 200, {"order_id": oid, "status": "test_only", "total": total,
                     "currency": p["price"]["currency"], "pay_url": pay,
                     "note": "Test order — not counted, no email sent."}
    try:
        prod = next((x for x in PRODUCTS if x["id"] == pid), None)
        ok, info = notifier.order_mail(dict(rec, buyer=buyer), prod)
        rec["notified"] = ok
    except Exception as e:
        rec["notified"] = False
    orders_save(db)
    funnel("order_created", oid + " " + pid + " " + str(total))
    return 200, {
        "order_id": oid, "status": "awaiting_payment",
        "product_id": pid, "qty": qty,
        "unit_price": p["price"]["amount"], "total": total, "currency": p["price"]["currency"],
        "pay_url": pay, "pay_url_note": "Open this link to pay. Card is charged only after the human confirms.",
        "expires_at": (datetime.datetime.now() + datetime.timedelta(hours=24)).isoformat(timespec="seconds"),
        "ships_in_days": p["ships_in_days"], "ships_to": p["ships_to"],
        "stock_after": left,
        "buyer": {"name": buyer.get("name", ""), "email": buyer.get("email", ""), "country": country},
        "next_step": "Human opens pay_url → pays → order confirmed. Never ask for card details.",
    }

class H(BaseHTTPRequestHandler):
    server_version = "agent-shop/0.1"

    def log_message(self, *a): pass

    def _log(self, method, path, status, extra=""):
        try:
            ua = (self.headers.get("User-Agent") or "")[:160].replace("\t", " ")
            ref = (self.headers.get("Referer") or "")[:120].replace("\t", " ")
            ip = self.client_address[0] if self.client_address else ""
            is_bot = any(k in ua.lower() for k in (
                "bot", "crawler", "spider", "gpt", "claude", "anthropic", "perplexity",
                "chatgpt", "google", "bing", "yandex", "applebot", "ccbot", "muse", "curl", "python"))
            tag = "BOT" if is_bot else "HUMAN"
            with LOG.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}\t{tag}\t{method}\t{path}\t{status}\t{extra}\t{ip}\t{ua}\t{ref}\n")
        except OSError:
            pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else (
            json.dumps(body, ensure_ascii=False, indent=2).encode() if not isinstance(body, str) else body.encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
        if not _rate_ok(self.client_address[0], path):
            self._log("GET", self.path, 429, "RATE_LIMITED")
            return self._send(429, {"error": "rate_limited", "retry_after_seconds": 30})
        if path in ("/api/search", "/api/search/"):
            res = search(qs)
            self._log("GET", self.path, 200, f"n={res['count']}")
            return self._send(200, res)
        m = re.fullmatch(r"/api/buy/([\w\-]+)/?", path)
        if m:
            pid = m.group(1)
            p = BY_ID.get(pid)
            if not p:
                return self._send(404, {"error": "product_not_found", "product_id": pid})
            funnel("buy_click", pid)
            code, res = order({"product_id": pid, "qty": 1, "buyer": {"country": "US"}})
            if code != 200:
                return self._send(code, res)
            self._log("GET", self.path, 302, pid)
            self.send_response(302)
            self.send_header("Location", PATH_PREFIX + "/pay/" + res["order_id"])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        m = re.fullmatch(r"/api/product/([\w\-]+)/?", path)
        if m:
            pid = m.group(1)
            p = BY_ID.get(pid)
            if not p:
                self._log("GET", self.path, 404)
                return self._send(404, {"error": "product_not_found", "product_id": pid})
            data = dict(public(p))
            data["price_valid_until"] = (datetime.date.today() + datetime.timedelta(days=LOCK_DAYS)).isoformat()
            data["in_stock"] = stock_ok(p)
            data["stock"] = stock_of(p)
            self._log("GET", self.path, 200, pid)
            return self._send(200, data)
        if path in ("/api/quote", "/api/quote/"):
            res = quote(qs)
            self._log("GET", self.path, 200, f"n={res['count']}")
            return self._send(200, res)
        if path in ("/openapi.json", "/api/openapi.json"):
            self._log("GET", self.path, 200, "openapi")
            return self._send(200, OPENAPI)
        if path == "/.well-known/ai-plugin.json":
            return self._send(200, {
                "schema_version": "v1",
                "name_for_human": META["site_name"],
                "name_for_model": "workshop_tooling",
                "description_for_model": ("Search and order workshop tooling (router bits, drills, saw blades). "
                                          "Prices include duty and shipping. Ordering returns a payment link."),
                "description_for_human": "Agent-first catalog of workshop tooling.",
                "auth": {"type": "none"},
                "api": {"type": "openapi", "url": META["base_url"] + "/openapi.json"},
                "logo_url": META["base_url"] + "/logo.png",
                "contact_email": META["contact"],
                "legal_info_url": META["base_url"] + "/ai.html"})
        if path.rstrip("/") == "/admin/funnel":
            if qs.get("token", [""])[0] != os.environ.get("ADMIN_TOKEN", "toolloop"):
                return self._send(403, {"error": "forbidden"})
            rows = []
            if FUNNEL.exists():
                rows = [ln.split("\t") for ln in FUNNEL.read_text(encoding="utf-8").splitlines() if ln]
            from collections import Counter
            c = Counter(r[1] for r in rows if len(r) > 1)
            views = c.get("view", 0); clicks = c.get("buy_click", 0)
            orders = c.get("order_created", 0); pays = c.get("pay_view", 0)
            def pct(a, b):
                return round(a * 100.0 / b, 1) if b else 0.0
            return self._send(200, {
                "window_note": "counts since funnel.log was created",
                "stages": {"buy_click": clicks, "order_created": orders,
                           "pay_view": pays, "paid": c.get("paid", 0)},
                "conversion": {"click_to_order_pct": pct(orders, clicks),
                               "order_to_pay_view_pct": pct(pays, orders)},
                "recent": rows[-25:]})
        m2 = re.fullmatch(r"/pay/(ORD-[A-Za-z0-9\-]+)/?", path)
        if m2:
            funnel("pay_view", m2.group(1))
        if m2:
            oid = m2.group(1)
            rec = orders_load().get(oid)
            if not rec:
                return self._send(404, {"error": "order_not_found", "order_id": oid})
            amt = f"{rec['total']:.2f}"
            enabled = bool(PAYPAL_BUSINESS or PAYPAL_ME)
            btn = PAYPAL_BUTTON_FILE.read_text(encoding="utf-8") if PAYPAL_BUTTON_FILE.exists() else ""
            if btn:
                form = btn.replace("{{AMOUNT}}", amt).replace("{{ORDER}}", oid)
            elif PAYPAL_ME:
                me_url = f"https://paypal.me/{PAYPAL_ME}/{amt}USD"
                form = (f'<a href="{me_url}" style="display:inline-block;font:16px -apple-system;'
                        f'padding:14px 28px;border-radius:8px;background:#0070ba;color:#fff;'
                        f'text-decoration:none">Pay ${amt} with PayPal</a>'
                        f'<p style="color:#5f5a52;font-size:13px;margin-top:12px">'
                        f'Secure payment page by PayPal. Amount is fixed at ${amt} USD.</p>')
            else:
                form = (f'<form action="https://www.paypal.com/cgi-bin/webscr" method="post">'
                    f'<input type="hidden" name="cmd" value="_xclick">'
                    f'<input type="hidden" name="business" value="{PAYPAL_BUSINESS}">'
                    f'<input type="hidden" name="item_name" value="ToolLoop order {oid}">'
                    f'<input type="hidden" name="invoice" value="{oid}">'
                    f'<input type="hidden" name="amount" value="{amt}">'
                    f'<input type="hidden" name="currency_code" value="{rec["currency"]}">'
                    f'<input type="hidden" name="no_shipping" value="1">'
                    f'<input type="hidden" name="return" value="https://agentkit.ltd/tools/paid.html?order={oid}">'
                        f'<button type="submit" style="font:16px -apple-system;padding:14px 28px;border:0;'
                        f'border-radius:8px;background:#0070ba;color:#fff;cursor:pointer">Pay ${amt} with PayPal</button>'
                        f'</form>')
            if not enabled:
                form = ('<p style="color:#8a3b1f"><b>Payment is not connected yet.</b> '
                        'The seller has not linked a payment account. This order is recorded; '
                        'contact hello@agentkit.ltd to complete payment.</p>')
            body = (f'<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                    f'<title>Pay {oid}</title></head><body style="font:16px -apple-system;max-width:520px;'
                    f'margin:60px auto;padding:0 20px;color:#1c1a17">'
                    f'<h1 style="font-size:22px">Order {oid}</h1>'
                    f'<p>{rec["qty"]} x {rec["product_id"]} — total <b>${amt} {rec["currency"]}</b><br>'
                    f'<span style="color:#5f5a52">Includes duty, international shipping and US clearance. '
                    f'Dispatch within 48h, delivery 7–12 days.</span></p>{form}'
                    f'<p style="color:#5f5a52;font-size:13px;margin-top:28px">Sold by ToolLoop. '
                    f'Questions: hello@agentkit.ltd</p></body></html>')
            self._log("GET", self.path, 200, oid)
            return self._send(200, body, "text/html; charset=utf-8")

        m3 = re.fullmatch(r"/admin/sourcing/(ORD-[A-Za-z0-9\-]+)/?", path)
        if m3:
            _q = urllib.parse.parse_qs(parsed.query)
            tok = _q.get("token", [""])[0]
            if tok != os.environ.get("ADMIN_TOKEN", "toolloop"):
                return self._send(403, {"error": "forbidden"})
            rec = orders_load().get(m3.group(1))
            if not rec:
                return self._send(404, {"error": "order_not_found"})
            s2 = rec.get("sourcing") or {}
            cost = s2.get("cost_cny") or 0
            landed = cost / 7.1 + 60 / 7.1
            pr = rec.get("total") or 0
            gross = pr - landed - pr * 0.109 - pr * 0.06
            link = (f'<a href="{s2["url"]}" target="_blank" rel="noopener">{s2["url"]}</a>'
                    if s2.get("url") else f'1688 搜索 offer ID <b>{s2.get("offer_id","")}</b>')
            rowsx = "".join(f'<tr><td>{k}</td><td><b>{v}</b></td></tr>' for k, v in [
                ("订单号", rec["order_id"]), ("商品", rec["product_id"]),
                ("客户买了几件", rec["qty"]), ("客户付", f'${pr:.2f}'),
                ("供应商", s2.get("supplier")), ("规格", s2.get("spec")),
                ("下单链接", link), ("单价", f'¥{s2.get("unit_cny","")}'),
                ("采购件数", s2.get("qty")), ("国内运费", f'¥{s2.get("freight_cny") or 6}'),
                ("采购成本合计", f'¥{cost}'), ("到岸成本", f'${landed:.1f}'),
                ("预计毛利", f'${gross:.1f}（{gross/pr*100:.0f}%）' if pr else "—"),
                ("供应商承诺发货", f'{s2.get("dispatch_hours","")} 小时'),
                ("实读库存", s2.get("stock_at_check")),
            ])
            body = (f'<html><head><meta charset="utf-8"><title>采购单 {rec["order_id"]}</title>'
                    f'<style>body{{font:15px -apple-system;max-width:640px;margin:40px auto;padding:0 20px}}'
                    f'table{{border-collapse:collapse;width:100%}}td{{border-bottom:1px solid #eee;padding:8px}}'
                    f'td:first-child{{color:#666;width:140px}}</style></head><body>'
                    f'<h2>采购单</h2><table>{rowsx}</table>'
                    f'<p style="color:#a33;margin-top:20px"><b>客户付款后再下单。未付款不要采购。</b></p>'
                    f'<p><a href="{PATH_PREFIX}/admin/orders?token={tok}">← 返回订单列表</a></p></body></html>')
            return self._send(200, body, "text/html; charset=utf-8")

        if path == "/admin/orders":
            tok = (params.get("token", [""])[0] if (params := qs) else "")
            want = os.environ.get("ADMIN_TOKEN", "toolloop")
            if tok != want:
                return self._send(403, {"error": "forbidden"})
            db = orders_load()
            if (qs.get("clear", [""])[0]) == "test":
                db = {k: v for k, v in db.items() if not v.get("test")}
                orders_save(db)
            show_all = (qs.get("all", [""])[0]) == "1"
            shown = db if show_all else {k: v for k, v in db.items() if not v.get("test")}
            real_n = sum(1 for v in db.values() if not v.get("test"))
            test_n = len(db) - real_n
            rows = "".join(
                f'<tr><td>{o["order_id"]}</td><td>{o["product_id"]}</td><td>{o["qty"]}</td>'
                f'<td>${o["total"]}</td><td>{o.get("buyer",{}).get("country","")}</td>'
                f'<td>{o.get("status","")}</td><td>{o.get("created","")}</td>'
                f'<td><a href="{PATH_PREFIX}/admin/sourcing/{o["order_id"]}?token={want}">采购</a></td></tr>'
                for o in sorted(shown.values(), key=lambda x: x.get("created",""), reverse=True))
            body = (f'<html><head><meta charset="utf-8"><title>Orders</title></head>'
                    f'<body style="font:14px -apple-system;padding:24px">'
                    f'<h2>真实订单（{real_n}）· 测试单（{test_n}，{"" if show_all else "已隐藏"}）</h2><table border="1" cellpadding="6" '
                    f'style="border-collapse:collapse"><tr><th>订单号</th><th>商品</th><th>数量</th>'
                    f'<th>金额</th><th>国家</th><th>状态</th><th>时间</th><th>采购</th></tr>{rows}</table>'
                    f'<p style="color:#666">通知邮件：{"已配置" if notifier.configured() else "未配置"}</p>'
                    f'</body></html>')
            return self._send(200, body, "text/html; charset=utf-8")

        if path in ("/favicon.ico", "/favicon.png"):
            f = ROOT / "out" / "favicon.svg"
            if f.exists():
                self._log("GET", self.path, 200, "favicon")
                return self._send(200, f.read_bytes(), "image/svg+xml")
        if path in ("/api/pricing", "/api/call", "/api/search/"):
            # 有人在找通用调用口：告知真实端点
            return self._send(200, {"moved": True,
                                    "products": f"{PATH_PREFIX}/api/products.json",
                                    "search": f"{PATH_PREFIX}/api/search?q=router",
                                    "mcp": f"{PATH_PREFIX}/mcp",
                                    "note": "Use /mcp for tool calls, /api/* for HTTP JSON."})
        if path in ("/mcp", "/mcp/"):
            self._log("GET", self.path, 405, "mcp-needs-post")
            return self._send(405, {"error": "mcp_requires_post",
                                    "post": "JSON-RPC 2.0 to this URL",
                                    "protocol": mcp_http.PROTOCOL})

        if path == "/.well-known/oauth-protected-resource":
            # MCP 客户端会先探这个端点。我们不要求鉴权，返回最小元数据。
            return self._send(200, {"resource": f"{PATH_PREFIX}/mcp",
                                    "authorization_servers": [],
                                    "bearer_methods_supported": [],
                                    "resource_documentation": f"{PATH_PREFIX}/.well-known/mcp.json"})
        if path == "/.well-known/oauth-authorization-server":
            return self._send(404, {"error": "no_oauth",
                                    "note": "This MCP server requires no authorization."})
        if path in ("/.well-known/mcp.json", "/.well-known/mcp"):
            return self._send(200, {
                "name": "toolloop", "version": "1.0.0",
                "description": ("Workshop tooling and replacement parts, landed USD pricing for US buyers. "
                                "Duty, international shipping and clearance included."),
                "transport": {"type": "http", "url": f"{PATH_PREFIX}/mcp", "method": "POST",
                              "protocol": mcp_http.PROTOCOL},
                "tools": [t["name"] for t in mcp_http.TOOLS],
                "site": f"{PATH_PREFIX}/", "llms": f"{PATH_PREFIX}/llms.txt"})
        if path in ("/api/health", "/healthz"):
            return self._send(200, {"status": "ok", "products": len(PRODUCTS),
                                    "price_lock_days": LOCK_DAYS})
        if path in ("/api/order",):
            return self._send(405, {"error": "use_POST"})
        # static
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (OUT / rel).resolve()
        if OUT.resolve() not in f.parents and f != OUT.resolve():
            return self._send(403, {"error": "forbidden"})
        if f.is_file():
            self._log("GET", self.path, 200, "static")
            return self._send(200, f.read_bytes(), MIME.get(f.suffix, "application/octet-stream"))
        self._log("GET", self.path, 404)
        return self._send(404, {"error": "not_found", "path": path})

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        if not _rate_ok(self.client_address[0], parsed.path):
            self._log("POST", self.path, 429, "RATE_LIMITED")
            return self._send(429, {"error": "rate_limited", "retry_after_seconds": 30})
        if parsed.path.rstrip("/") in ("/mcp", "/.well-known/mcp"):
            try:
                n = int(self.headers.get("Content-Length", "0") or 0)
                req = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except (ValueError, json.JSONDecodeError):
                return self._send(400, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": -32700, "message": "parse_error"}})
            api = {"search": lambda a: search(a),
                   "quote": quote, "order": order,
                   "public": public, "by_id": lambda pid: BY_ID.get(pid),
                   "stock": stock_of, "answer_rec": answer_rec}
            res = mcp_http.handle(req, api)
            try:
                _m = req.get("method") or ""
                _p = req.get("params") or {}
                _tool = _p.get("name") or ""
                _args = json.dumps(_p.get("arguments") or {}, ensure_ascii=False)[:400]
                _ua = (self.headers.get("User-Agent") or "")[:90].replace("\t", " ")
                _ip = self.client_address[0] if self.client_address else ""
                with MCP_LOG.open("a", encoding="utf-8") as f:
                    f.write(f'{datetime.datetime.now().isoformat(timespec="seconds")}\t{_m}\t{_tool}\t{_args}\t{_ip}\t{_ua}\n')
            except OSError:
                pass
            self._log("POST", self.path, 200, (req.get("method") or "")[:40])
            if res is None:
                return self._send(202, {"jsonrpc": "2.0", "result": "accepted"})
            return self._send(200, res)
        if parsed.path.rstrip("/") == "/api/reorder":
            try:
                n = int(self.headers.get("Content-Length", "0") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except (ValueError, json.JSONDecodeError):
                return self._send(400, {"error": "invalid_json"})
            code, payload = order(body)
            self._log("POST", self.path, code, body.get("product_id", ""))
            return self._send(code, payload)
        if parsed.path.rstrip("/") != "/api/order":
            return self._send(404, {"error": "not_found"})
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "invalid_json"})
        code, payload = order(body)
        self._log("POST", self.path, code, body.get("product_id", ""))
        return self._send(code, payload)

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in (("Access-Control-Allow-Origin", "*"),
                     ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
                     ("Access-Control-Allow-Headers", "Content-Type")):
            self.send_header(k, v)
        self.end_headers()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"agent-shop v0 -> http://127.0.0.1:{port}  (products: {len(PRODUCTS)})")
    srv.serve_forever()
