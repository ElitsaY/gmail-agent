"""
Cost dashboard — generates an HTML file, opens it in your browser,
and pushes it to GitHub Pages so it's always accessible online.
Run anytime: python dashboard.py
"""

import json
import os
import subprocess
import webbrowser
from datetime import datetime, timedelta
from collections import defaultdict

import costs as cost_tracker
import memory as mem

DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "index.html")


def build_dashboard():
    data = cost_tracker.load()
    memory = mem.load()
    runs = data.get("runs", [])

    # ── Aggregate stats ───────────────────────────────────────────────────────

    total_cost = data.get("total_cost_usd", 0.0)
    total_emails = sum(r.get("emails_processed", 0) for r in runs)
    total_filtered = sum(r.get("emails_filtered", 0) for r in runs)
    total_runs = len(runs)

    # Cost by day (last 30 days)
    daily = defaultdict(float)
    for r in runs:
        day = r["timestamp"][:10]
        daily[day] += r["cost_usd"]

    today = datetime.now().date()
    last_30 = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    daily_labels = json.dumps(last_30)
    daily_values = json.dumps([round(daily.get(d, 0), 5) for d in last_30])

    # Cost by agent type
    filter_cost = sum(r["cost_usd"] for r in runs if r["agent"] == "filter")
    digest_cost = sum(r["cost_usd"] for r in runs if r["agent"] == "digest")

    # Cost this month
    this_month = today.strftime("%Y-%m")
    month_cost = sum(r["cost_usd"] for r in runs if r["timestamp"].startswith(this_month))

    # Cost today
    today_cost = daily.get(today.isoformat(), 0.0)

    # Top filtered senders
    sender_stats = memory.get("sender_stats", {})
    top_filtered = sorted(
        [(s, v["times_filtered"]) for s, v in sender_stats.items() if v["times_filtered"] > 0],
        key=lambda x: x[1], reverse=True
    )[:10]
    top_filtered_html = "".join(
        f'<tr><td>{s[:45]}</td><td class="num">{n}</td></tr>'
        for s, n in top_filtered
    ) or "<tr><td colspan='2' style='color:#888'>No data yet</td></tr>"

    learned_filters = memory.get("learned_filters", [])
    learned_html = "".join(f'<li>{f}</li>' for f in learned_filters) or "<li style='color:#888'>None yet</li>"

    frequent_html = "".join(
        f'<li>{c}</li>' for c in memory.get("frequent_contacts", [])
    ) or "<li style='color:#888'>None yet</li>"

    # Recent runs table
    recent_runs = sorted(runs, key=lambda r: r["timestamp"], reverse=True)[:20]
    runs_html = "".join(f"""
        <tr>
            <td>{r['timestamp'][:16].replace('T', ' ')}</td>
            <td><span class="badge {'badge-filter' if r['agent'] == 'filter' else 'badge-digest'}">{r['agent']}</span></td>
            <td>{r['model'].replace('claude-', '')}</td>
            <td class="num">{r['input_tokens']:,}</td>
            <td class="num">{r['output_tokens']:,}</td>
            <td class="num">${r['cost_usd']:.5f}</td>
        </tr>
    """ for r in recent_runs) or "<tr><td colspan='6' style='color:#888'>No runs yet</td></tr>"

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── HTML ──────────────────────────────────────────────────────────────────

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gmail Agent Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f7; color: #1d1d1f; }}
  .header {{ background: #1d1d1f; color: white; padding: 24px 32px; display: flex; justify-content: space-between; align-items: center; }}
  .header h1 {{ font-size: 22px; font-weight: 600; }}
  .header small {{ color: #888; font-size: 13px; }}
  .container {{ max-width: 1100px; margin: 0 auto; padding: 28px 24px; }}
  .cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }}
  .card {{ background: white; border-radius: 12px; padding: 20px 24px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  .card .label {{ font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; }}
  .card .value {{ font-size: 30px; font-weight: 700; }}
  .card .sub {{ font-size: 12px; color: #888; margin-top: 4px; }}
  .card.accent .value {{ color: #0071e3; }}
  .row {{ display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 24px; }}
  .panel {{ background: white; border-radius: 12px; padding: 20px 24px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  .panel h2 {{ font-size: 15px; font-weight: 600; margin-bottom: 16px; color: #1d1d1f; }}
  .chart-wrap {{ position: relative; height: 200px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; padding: 8px 10px; color: #888; font-weight: 500; border-bottom: 1px solid #f0f0f0; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #f9f9f9; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .badge {{ font-size: 11px; padding: 2px 8px; border-radius: 20px; font-weight: 500; }}
  .badge-filter {{ background: #e8f4fd; color: #0071e3; }}
  .badge-digest {{ background: #fdf0e8; color: #d4600a; }}
  ul {{ list-style: none; font-size: 13px; }}
  ul li {{ padding: 5px 0; border-bottom: 1px solid #f5f5f5; color: #333; }}
  .donut-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }}
  .full {{ grid-column: 1 / -1; }}
</style>
</head>
<body>

<div class="header">
  <h1>📬 Gmail Agent — Cost Dashboard</h1>
  <small>Updated {generated}</small>
</div>

<div class="container">

  <!-- Summary cards -->
  <div class="cards">
    <div class="card accent">
      <div class="label">Total spent</div>
      <div class="value">${total_cost:.4f}</div>
      <div class="sub">all time</div>
    </div>
    <div class="card">
      <div class="label">This month</div>
      <div class="value">${month_cost:.4f}</div>
      <div class="sub">{today.strftime("%B %Y")}</div>
    </div>
    <div class="card">
      <div class="label">Today</div>
      <div class="value">${today_cost:.5f}</div>
      <div class="sub">{total_runs} total runs</div>
    </div>
    <div class="card">
      <div class="label">Emails processed</div>
      <div class="value">{total_emails:,}</div>
      <div class="sub">{total_filtered:,} filtered out</div>
    </div>
  </div>

  <!-- Daily cost chart -->
  <div class="panel" style="margin-bottom:24px">
    <h2>Daily cost — last 30 days</h2>
    <div class="chart-wrap">
      <canvas id="dailyChart"></canvas>
    </div>
  </div>

  <!-- Donut + breakdowns -->
  <div class="donut-row">
    <div class="panel">
      <h2>Cost by agent</h2>
      <div style="max-width:180px; margin:0 auto;">
        <canvas id="donutChart"></canvas>
      </div>
      <div style="display:flex; gap:16px; justify-content:center; margin-top:12px; font-size:13px;">
        <span><span style="color:#0071e3">■</span> Filter ${filter_cost:.4f}</span>
        <span><span style="color:#d4600a">■</span> Digest ${digest_cost:.4f}</span>
      </div>
    </div>
    <div class="panel">
      <h2>Top filtered senders</h2>
      <table>
        <tr><th>Sender</th><th style="text-align:right">Times</th></tr>
        {top_filtered_html}
      </table>
    </div>
  </div>

  <!-- Memory state -->
  <div class="row" style="margin-bottom:24px;">
    <div class="panel">
      <h2>Learned filter rules</h2>
      <ul>{learned_html}</ul>
    </div>
    <div class="panel">
      <h2>Frequent contacts</h2>
      <ul>{frequent_html}</ul>
    </div>
  </div>

  <!-- Recent runs -->
  <div class="panel">
    <h2>Recent runs</h2>
    <table>
      <tr>
        <th>Time</th><th>Agent</th><th>Model</th>
        <th style="text-align:right">Input tokens</th>
        <th style="text-align:right">Output tokens</th>
        <th style="text-align:right">Cost</th>
      </tr>
      {runs_html}
    </table>
  </div>

</div>

<script>
const dailyCtx = document.getElementById('dailyChart').getContext('2d');
new Chart(dailyCtx, {{
  type: 'bar',
  data: {{
    labels: {daily_labels},
    datasets: [{{
      label: 'Cost (USD)',
      data: {daily_values},
      backgroundColor: '#0071e3',
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ ticks: {{ callback: v => '$' + v.toFixed(4) }}, grid: {{ color: '#f0f0f0' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 10, font: {{ size: 11 }} }} }}
    }}
  }}
}});

const donutCtx = document.getElementById('donutChart').getContext('2d');
new Chart(donutCtx, {{
  type: 'doughnut',
  data: {{
    labels: ['Filter', 'Digest'],
    datasets: [{{ data: [{filter_cost:.5f}, {digest_cost:.5f}], backgroundColor: ['#0071e3', '#d4600a'], borderWidth: 0 }}]
  }},
  options: {{ plugins: {{ legend: {{ display: false }} }}, cutout: '70%' }}
}});
</script>
</body>
</html>"""

    with open(DASHBOARD_PATH, "w") as f:
        f.write(html)

    return DASHBOARD_PATH


def push_to_github():
    """Commit dashboard.html and data files to GitHub Pages."""
    repo = os.path.dirname(__file__)

    def git(*args):
        result = subprocess.run(["git", "-C", repo] + list(args),
                                capture_output=True, text=True)
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    # Stage only the safe files (no secrets)
    files = ["index.html", "costs.json", "memory.json"]
    for f in files:
        path = os.path.join(repo, f)
        if os.path.exists(path):
            git("add", f)

    # Check if there's anything to commit
    code, out, _ = git("diff", "--cached", "--quiet")
    if code == 0:
        print("GitHub Pages: nothing changed, skipping push.")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    git("commit", "-m", f"dashboard update {timestamp}")
    code, out, err = git("push", "origin", "gh-pages")
    if code == 0:
        print("✓ Dashboard pushed to GitHub Pages.")
    else:
        print(f"✗ Push failed: {err}")


if __name__ == "__main__":
    path = build_dashboard()
    print(f"Dashboard saved to {path}")
    push_to_github()
    webbrowser.open(f"file://{path}")
