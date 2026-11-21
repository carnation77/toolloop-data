#!/bin/bash
# 运维脚本部署：漏斗报表 + 每日 cron
set -e
mkdir -p /opt/toolloop/ops
cp -f /tmp/tl/ops/funnel_report.py /opt/toolloop/ops/funnel_report.py
chmod +x /opt/toolloop/ops/funnel_report.py
SECRET=$(cat /opt/toolloop/.report_dir 2>/dev/null || (head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n' > /opt/toolloop/.report_dir && cat /opt/toolloop/.report_dir))
OUT=/var/www/agentkit/r/$SECRET
mkdir -p "$OUT"
python3 /opt/toolloop/ops/funnel_report.py /opt/toolloop/funnel.log /var/log/caddy/access.log > "$OUT/.tmp" 2>/dev/null || echo "no data yet" > "$OUT/.tmp"
chmod 644 "$OUT/.tmp"; mv "$OUT/.tmp" "$OUT/funnel-report.md"
cat > /etc/cron.d/toolloop-funnel <<CRON
30 9 * * * root python3 /opt/toolloop/ops/funnel_report.py /opt/toolloop/funnel.log /var/log/caddy/access.log > /var/www/agentkit/r/$SECRET/.tmp 2>/dev/null; chmod 644 /var/www/agentkit/r/$SECRET/.tmp; mv /var/www/agentkit/r/$SECRET/.tmp /var/www/agentkit/r/$SECRET/funnel-report.md
CRON
chmod 644 /etc/cron.d/toolloop-funnel
echo "report_path=/r/$SECRET/funnel-report.md"
echo "lines=$(wc -l < $OUT/funnel-report.md)"
