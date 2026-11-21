#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转化漏斗报表（markdown 到 stdout）

用法:
    python3 funnel_report.py [funnel.log] [access.log]

    funnel.log  默认 /opt/toolloop/funnel.log；传 - 表示从 stdin 读（便于切片/轮转）
    access.log  默认 /var/log/caddy/access.log；传 - 或 none 表示跳过（content_view 记为 —）

funnel.log 格式（tab 分隔）:
    2026-11-10T13:05:12<TAB>buy_click<TAB>DB-13M
    2026-11-10T13:05:13<TAB>order_created<TAB>ORD-20261110-AC7A15 DB-13M 59.0
    2026-11-10T13:05:20<TAB>pay_view<TAB>ORD-20261110-AC7A15

阶段: buy_click / order_created / pay_view / paid
只用标准库。任何输入缺失或损坏都不崩，输出完整空表。
"""

import datetime
import re
import sys

# ---------------------------------------------------------------- 常量

FUNNEL_DEFAULT = "/opt/toolloop/funnel.log"
ACCESS_DEFAULT = "/var/log/caddy/access.log"

STAGES = ["buy_click", "order_created", "pay_view", "paid"]
STAGE_LABEL = {
    "buy_click": "buy_click 点购买",
    "order_created": "order_created 建单",
    "pay_view": "pay_view 到付款页",
    "paid": "paid 已付款",
}
# content_view = 从 access.log 统计的页面浏览（商品页 + 答案页）
VIEW_LABEL = "content_view 页面浏览"

VIEW_PREFIXES = ("/tools/product/", "/tools/answers/")

RE_URI = re.compile(r'"uri"\s*:\s*"((?:[^"\\]|\\.)*)"')
RE_STATUS = re.compile(r'"status"\s*:\s*(\d{3})')
RE_UA = re.compile(r'"User-Agent"\s*:\s*\[\s*"((?:[^"\\]|\\.)*)"')
RE_METHOD = re.compile(r'"method"\s*:\s*"([A-Za-z]+)"')
RE_ORD = re.compile(r"^ORD-", re.I)
RE_SKU = re.compile(r"^[A-Za-z]{1,6}[0-9A-Za-z]*(?:-[0-9A-Za-z]+)+$")
RE_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
RE_TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})")
RE_HOUR = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2})")


# ---------------------------------------------------------------- 工具


def md_table(headers, rows):
    """生成 markdown 表；rows 为空时只输出表头（空表）。"""
    head = "| " + " | ".join(headers) + " |"
    sep = "|" + "|".join(["---"] * len(headers)) + "|"
    body = ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join([head, sep] + body)


def pct(num, den):
    """转化率。den 为 None(无数据源) 或 0 时返回 —。"""
    if den is None or den == 0:
        return "—"
    return "%.1f%%" % (num / den * 100.0)


def money(x):
    return "%.2f" % x


# ---------------------------------------------------------------- 读 funnel.log


def read_funnel(path):
    """返回 (events, meta)。events: [{ts,hour,stage,extra,fields}]"""
    events, meta = [], {
        "path": path,
        "exists": False,
        "lines": 0,
        "bad": 0,
        "unknown_stage": 0,
        "bad_ts": 0,
        "first": None,
        "last": None,
    }
    if path in ("-", "stdin"):
        try:
            raw = sys.stdin.read()
        except Exception:
            raw = ""
        meta["exists"] = True
        meta["path"] = "<stdin>"
    else:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
            meta["exists"] = True
        except OSError:
            return events, meta

    for line in raw.splitlines():
        meta["lines"] += 1
        s = line.rstrip("\r\n")
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        fields = s.split("\t")
        if len(fields) < 2:
            # 容忍空格分隔的写法
            fields = s.split(None, 2)
        if len(fields) < 2:
            meta["bad"] += 1
            continue
        ts = fields[0].strip()
        stage = fields[1].strip().lower()
        extra = fields[2].strip() if len(fields) > 2 else ""
        if stage not in STAGES:
            meta["unknown_stage"] += 1
            continue
        hour = None
        m = RE_HOUR.match(ts)
        if m:
            hour = int(m.group(2))
        else:
            meta["bad_ts"] += 1
        if meta["first"] is None:
            meta["first"] = ts
        meta["last"] = ts
        events.append({"ts": ts, "hour": hour, "stage": stage, "extra": extra})
    return events, meta


def split_extra(stage, extra):
    """从附加信息里抽 order_id / sku / 金额。"""
    order_id, sku, amount = "", "", None
    tokens = extra.split()
    for tok in tokens:
        if not order_id and RE_ORD.match(tok):
            order_id = tok
            continue
        if not sku and not RE_ORD.match(tok) and RE_SKU.match(tok):
            sku = tok
            continue
        if amount is None and RE_NUM.match(tok):
            try:
                amount = float(tok)
            except ValueError:
                amount = None
    if stage == "buy_click" and not sku and tokens:
        # buy_click 的第一个 token 就是 SKU（哪怕不像标准 SKU）
        sku = tokens[0]
    if stage == "pay_view" and not order_id and tokens:
        order_id = tokens[0]
    return order_id, sku, amount


# ---------------------------------------------------------------- 读 access.log


def read_access(path):
    """只抽需要的三个字段，正则扫。返回 (views, meta)

    views: [{uri, status, ua, kind, method}]
    """
    views, meta = [], {
        "path": path,
        "exists": False,
        "skipped": path in ("-", "none", "off", ""),
        "lines": 0,
        "matched": 0,
    }
    if meta["skipped"]:
        return views, meta
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        meta["exists"] = True
    except OSError:
        return views, meta

    for line in lines:
        meta["lines"] += 1
        mu = RE_URI.search(line)
        if not mu:
            continue
        uri = mu.group(1)
        if not uri.startswith(VIEW_PREFIXES):
            continue
        method = RE_METHOD.search(line)
        method = method.group(1).upper() if method else "GET"
        if method != "GET":
            continue
        ms = RE_STATUS.search(line)
        status = int(ms.group(1)) if ms else 0
        # 只算成功的页面浏览（2xx/3xx），无 status 字段时视为成功
        if status and not (200 <= status < 400):
            continue
        mua = RE_UA.search(line)
        ua = mua.group(1) if mua else ""
        kind = "商品页" if uri.startswith("/tools/product/") else "答案页"
        meta["matched"] += 1
        views.append({"uri": uri, "status": status, "ua": ua, "kind": kind})
    return views, meta


def ua_family(ua):
    """把 UA 归成好读的家族，用于附录。"""
    low = (ua or "").lower()
    table = [
        ("gptbot", "OpenAI GPTBot"), ("oai-searchbot", "OpenAI SearchBot"),
        ("chatgpt-user", "ChatGPT-User"), ("claude", "Anthropic Claude"),
        ("perplexity", "Perplexity"), ("googleother", "Google-Other"),
        ("googlebot", "Googlebot"), ("bingbot", "Bingbot"), ("yandex", "Yandex"),
        ("applebot", "Applebot"), ("ccbot", "CommonCrawl"), ("bytespider", "字节"),
        ("amazonbot", "Amazonbot"), ("meta-external", "Meta"), ("ahrefs", "Ahrefs"),
        ("semrush", "Semrush"), ("petalbot", "华为"), ("mj12", "MJ12"),
    ]
    for key, name in table:
        if key in low:
            return name
    if any(k in low for k in ("bot", "crawler", "spider")):
        return "其他爬虫"
    if not ua or any(k in low for k in ("curl", "python", "wget", "go-http", "httpx")):
        return "脚本/无UA"
    return "浏览器/其他"


# ---------------------------------------------------------------- 汇总


def build_events(events):
    """派生：计数、订单表、SKU 表、小时表。"""
    st = {k: 0 for k in STAGES}
    hourly = {k: {h: 0 for h in range(24)} for k in STAGES}
    sku_clicks = {}
    sku_order_cnt = {}
    sku_order_amt = {}
    orders = {}  # order_id -> {sku, amount}
    paid_orders = set()
    payview_orders = set()
    orphan = 0  # 有 order_id 但找不到 order_created 的 paid/pay_view（去重）

    for ev in events:
        stage = ev["stage"]
        st[stage] += 1
        if ev["hour"] is not None:
            hourly[stage][ev["hour"]] += 1
        order_id, sku, amount = split_extra(stage, ev["extra"])

        if stage == "buy_click":
            if sku:
                sku_clicks[sku] = sku_clicks.get(sku, 0) + 1
        elif stage == "order_created":
            if order_id and order_id not in orders:
                orders[order_id] = {"sku": sku, "amount": amount}
            if sku:
                sku_order_cnt[sku] = sku_order_cnt.get(sku, 0) + 1
                sku_order_amt[sku] = sku_order_amt.get(sku, 0.0) + (amount or 0.0)
        elif stage == "pay_view":
            if order_id:
                payview_orders.add(order_id)
        elif stage == "paid":
            if order_id:
                paid_orders.add(order_id)

    # 二遍扫描：日志未必按时间有序，pay_view/paid 可能先于 order_created 出现
    orphan = len({o for o in (payview_orders | paid_orders) if o not in orders})

    return {
        "stage": st,
        "hourly": hourly,
        "sku_clicks": sku_clicks,
        "sku_order_cnt": sku_order_cnt,
        "sku_order_amt": sku_order_amt,
        "orders": orders,
        "paid_orders": paid_orders,
        "payview_orders": payview_orders,
        "orphan": orphan,
    }


# ---------------------------------------------------------------- 报表


def render(funnel_path, access_path):
    events, fmeta = read_funnel(funnel_path)
    views, ameta = read_access(access_path)
    agg = build_events(events)

    st = agg["stage"]
    content_view = None if ameta["skipped"] or not ameta["exists"] else len(views)

    out = []
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out.append("# 转化漏斗报表")
    out.append("")
    out.append("> 生成时间 %s（服务器本地时间）" % now)
    out.append(">")
    out.append("> funnel.log `%s`%s ｜ access.log `%s`%s" % (
        fmeta["path"],
        "（%d 行）" % fmeta["lines"] if fmeta["exists"] else "（**未找到**）",
        ameta["path"],
        "（跳过）" if ameta["skipped"] else (
            "（%d 行 / 命中 %d）" % (ameta["lines"], ameta["matched"])
            if ameta["exists"] else "（**未找到**）"),
    ))
    if fmeta["first"]:
        out.append(">")
        out.append("> 数据区间 %s → %s" % (fmeta["first"], fmeta["last"]))
    out.append("")

    # ---- 一、五阶段
    out.append("## 一、五阶段漏斗")
    out.append("")
    rows = [[VIEW_LABEL, "—" if content_view is None else "%d" % content_view]]
    for k in STAGES:
        rows.append([STAGE_LABEL[k], "%d" % st[k]])
    out.append(md_table(["阶段", "次数"], rows))
    out.append("")
    if views:
        prod = sum(1 for v in views if v["kind"] == "商品页")
        ans = len(views) - prod
        out.append("页面浏览拆分：商品页 %d ／ 答案页 %d（只计 GET /tools/product/ 与 /tools/answers/，status 2xx–3xx）" % (prod, ans))
        out.append("")

    # ---- 二、转化率
    out.append("## 二、转化率")
    out.append("")
    out.append(md_table(
        ["环节", "分子 / 分母", "转化率"],
        [
            ["view→click（浏览→点购买）", "%d / %s" % (st["buy_click"], "—" if content_view is None else content_view),
             pct(st["buy_click"], content_view)],
            ["click→order（点购买→建单）", "%d / %d" % (st["order_created"], st["buy_click"]),
             pct(st["order_created"], st["buy_click"])],
            ["order→pay（建单→付款）", "%d / %d" % (st["paid"], st["order_created"]),
             pct(st["paid"], st["order_created"])],
        ],
    ))
    out.append("")
    out.append("口径：order→pay 用 `paid` 事件数 ÷ `order_created` 事件数；同一订单重复记 paid 只按事件计，"
               "去重订单数见下节。`pay_view`（到付款页）单列，不进转化率分子。")
    out.append("")

    # ---- 三、按 SKU
    out.append("## 三、按 SKU")
    out.append("")
    out.append("### buy_click 次数 Top 10")
    out.append("")
    if agg["sku_clicks"]:
        top = sorted(agg["sku_clicks"].items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        tot = sum(agg["sku_clicks"].values())
        rows = []
        for i, (sku, n) in enumerate(top, 1):
            rows.append([i, sku, n, "%.1f%%" % (n / tot * 100.0)])
        out.append(md_table(["#", "SKU", "buy_click", "占比"], rows))
    else:
        out.append(md_table(["#", "SKU", "buy_click", "占比"], []))
    out.append("")
    out.append("### order_created 金额合计")
    out.append("")
    if agg["sku_order_amt"] or agg["sku_order_cnt"]:
        skus = sorted(set(agg["sku_order_amt"]) | set(agg["sku_order_cnt"]),
                      key=lambda s: (-agg["sku_order_amt"].get(s, 0.0), -agg["sku_order_cnt"].get(s, 0)))
        rows = []
        for sku in skus:
            rows.append([sku, agg["sku_order_cnt"].get(sku, 0), money(agg["sku_order_amt"].get(sku, 0.0))])
        total_amt = sum(agg["sku_order_amt"].values())
        rows.append(["**合计**", sum(agg["sku_order_cnt"].values()), "**%s**" % money(total_amt)])
        out.append(md_table(["SKU", "建单数", "金额"], rows))
    else:
        out.append(md_table(["SKU", "建单数", "金额"], []))
    out.append("")
    out.append("### 订单级对账")
    out.append("")
    out.append(md_table(
        ["指标", "数量"],
        [
            ["去重订单（order_created）", len(agg["orders"])],
            ["到过付款页的订单（pay_view 去重）", len(agg["payview_orders"])],
            ["已付款订单（paid 去重）", len(agg["paid_orders"])],
            ["有 order_id 但无 order_created 记录", agg["orphan"]],
        ],
    ))
    out.append("")

    # ---- 四、按小时
    out.append("## 四、按小时分布（0–23，服务器本地时间）")
    out.append("")
    rows = []
    for h in range(24):
        bc = agg["hourly"]["buy_click"][h]
        oc = agg["hourly"]["order_created"][h]
        bar = "█" * min(bc, 40) if bc else ""
        rows.append(["%02d:00" % h, bc, oc, bar])
    out.append(md_table(["小时", "buy_click", "order_created", "点击热度"], rows))
    out.append("")

    # ---- 五、UA 附录
    if views:
        out.append("## 五、页面浏览按 UA（Top 5）")
        out.append("")
        fam = {}
        for v in views:
            k = ua_family(v["ua"])
            fam[k] = fam.get(k, 0) + 1
        top = sorted(fam.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        out.append(md_table(["UA 家族", "页面浏览"], [[k, n] for k, n in top]))
        out.append("")

    # ---- 口径与告警
    out.append("## 口径与告警")
    out.append("")
    notes = []
    if not fmeta["exists"]:
        notes.append("`%s` 不存在或读不到 → funnel 各阶段按 0 计。" % fmeta["path"])
    if not ameta["skipped"] and not ameta["exists"]:
        notes.append("`%s` 不存在或读不到 → content_view 记 —，view→click 记 —。" % ameta["path"])
    if ameta["skipped"]:
        notes.append("access.log 已按参数跳过 → content_view 记 —。")
    if fmeta["bad"]:
        notes.append("funnel.log 有 %d 行格式不对，已跳过。" % fmeta["bad"])
    if fmeta["unknown_stage"]:
        notes.append("funnel.log 有 %d 行阶段名不认识，已跳过。" % fmeta["unknown_stage"])
    if fmeta["bad_ts"]:
        notes.append("有 %d 行时间戳解析不了，计数保留但不进小时表。" % fmeta["bad_ts"])
    if agg["orphan"]:
        notes.append("有 %d 条 pay_view/paid 的 order_id 找不到对应 order_created（跨天或日志轮转造成）。" % agg["orphan"])
    if not events:
        notes.append("funnel.log 没有有效事件 → 整张报表为空表（这是正常输出，不是错误）。")
    if not notes:
        notes.append("无异常。")
    out.extend(["- " + n for n in notes])
    out.append("")
    return "\n".join(out)


def main(argv):
    funnel_path = argv[1] if len(argv) > 1 else FUNNEL_DEFAULT
    access_path = argv[2] if len(argv) > 2 else ACCESS_DEFAULT
    try:
        sys.stdout.write(render(funnel_path, access_path))
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
