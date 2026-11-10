# -*- coding: utf-8 -*-
"""下单通知：邮件（SMTP）。配置全走环境变量，缺配置就静默跳过，绝不影响下单。"""
import os, smtplib, ssl, datetime
from email.mime.text import MIMEText
from email.header import Header

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")          # 例如 862150280@qq.com
SMTP_PASS = os.environ.get("SMTP_PASS", "")          # QQ 邮箱「授权码」，不是登录密码
NOTIFY_TO = os.environ.get("NOTIFY_TO", "862150280@qq.com")

def configured():
    return bool(SMTP_USER and SMTP_PASS)

def send(subject, body):
    if not configured():
        return False, "SMTP 未配置"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = SMTP_USER
    msg["To"] = NOTIFY_TO
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=12) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [NOTIFY_TO], msg.as_string())
        return True, "sent"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

def sourcing_block(product):
    """只有发给店主的内容里才有货源信息。绝不进 API / 页面。"""
    if not product: return ""
    sup = product.get("supplier") or {}
    cost = sup.get("cost_cny")
    price = (product.get("price") or {}).get("amount")
    line = ""
    if sup.get("url"):
        line = f"下单链接  {sup['url']}"
    elif sup.get("offer_id"):
        line = f"offer ID   {sup['offer_id']}  （1688 搜索该编号）"
    margin = ""
    if cost and price:
        landed = cost / 7.1 + 60 / 7.1
        g = price - landed - price * 0.109 - price * 0.06
        margin = f"\n采购成本  ¥{cost}  →  到岸 ${landed:.1f}  预计毛利 ${g:.1f}（{g/price*100:.0f}%）"
    return f"""
──────────────  采购单（只给你看）  ──────────────
供应商    {sup.get('supplier','')}
规格      {sup.get('spec','')}
{line}
采购价    ¥{sup.get('unit_cny','')} x {sup.get('qty',1)} 件{('  + 国内运费 ¥' + str(sup.get('domestic_freight_cny'))) if sup.get('domestic_freight_cny') else ''}{margin}
发货承诺  {sup.get('dispatch_commit_hours','') or sup.get('dispatch_commit','')} 小时内
库存(实读) {sup.get('stock_at_check','')}
──────────────────────────────────────────────
"""

def order_mail(order, product=None):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    pid = order.get("product_id", "")
    name = (product or {}).get("name_en", pid)
    cn = (product or {}).get("name", "")
    body = f"""新订单（等待客户付款）

订单号   {order.get('order_id')}
时间     {now}
商品     {pid}  {name}  {cn}
数量     {order.get('qty')}
金额     ${order.get('total')} {order.get('currency')}
买家国家 {order.get('buyer', {}).get('country', '')}

下一步
1. 客户付完款后，PayPal 会给你到账通知
2. 收到钱后，去 1688 搜同款下单，收货地址填集运仓
3. 出运单号后回填（现在还没有回填界面，先记在表里）

注意：客户可能不付款。未付款就不要采购。
{sourcing_block(product)}"""
    return send(f"[ToolLoop] 新订单 {order.get('order_id')}  ${order.get('total')}", body)

def test_mail():
    return send("[ToolLoop] 通知测试", "如果你收到这封邮件，说明下单通知已经接通。")
