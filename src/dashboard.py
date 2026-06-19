"""Web dashboard — Flask server. Private by default (127.0.0.1); DASHBOARD_PUBLIC=1 opens it.

Endpoints:
  GET /           → HTML dashboard (auto-refreshes every 30s)
  GET /api/data   → JSON snapshot of all sleeve state (for Chart.js + cards)

Run via run_dashboard.py (systemd service). On startup it sends the URL to Telegram so the
user can pin it in their chat.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Openbook — trading dashboard</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;--muted:#8b949e;
        --green:#3fb950;--red:#f85149;--yellow:#d29922;--blue:#388bfd;--accent:#1f6feb}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,
       "Segoe UI",sans-serif;font-size:14px;line-height:1.5;padding:20px}
  h1{font-size:20px;font-weight:600;margin-bottom:4px}
  .subtitle{color:var(--muted);font-size:12px;margin-bottom:24px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;
        margin-bottom:24px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px}
  .card h2{font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;
           letter-spacing:.05em;margin-bottom:12px}
  .metric{display:flex;justify-content:space-between;align-items:baseline;
          border-bottom:1px solid var(--border);padding:6px 0}
  .metric:last-child{border-bottom:none}
  .metric-label{color:var(--muted);font-size:12px}
  .metric-value{font-weight:500;font-size:15px}
  .green{color:var(--green)}.red{color:var(--red)}.muted{color:var(--muted)}
  .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
  .badge-paper{background:#1f3a5f;color:#6cb6ff}
  .badge-live{background:#3d1f1f;color:#f85149}
  .chart-wrap{background:var(--card);border:1px solid var(--border);border-radius:8px;
              padding:16px;margin-bottom:24px}
  .chart-wrap h2{font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;
                 letter-spacing:.05em;margin-bottom:12px}
  .chart-container{position:relative;height:220px}
  table{width:100%;border-collapse:collapse;font-size:12px}
  th{color:var(--muted);text-align:left;font-weight:500;padding:4px 8px;border-bottom:1px solid var(--border)}
  td{padding:4px 8px;border-bottom:1px solid #21262d}
  tr:last-child td{border-bottom:none}
  .pos{color:var(--green)}.neg{color:var(--red)}
  .refresh-note{color:var(--muted);font-size:11px;margin-top:16px;text-align:right}
  .halted{color:var(--red);font-weight:600}
  .paper-banner{background:#1f3a5f;border:1px solid #1f6feb;border-radius:6px;padding:10px 14px;
                margin-bottom:20px;color:#6cb6ff;font-size:12px}
</style>
</head>
<body style="background:#0d1117;color:#e6edf3">
<h1>Openbook</h1>
<p class="subtitle" id="mode-line">loading…</p>
<div id="error-banner" style="display:none;background:#3d1f1f;border:1px solid #f85149;border-radius:6px;padding:10px 14px;margin-bottom:16px;color:#f85149;font-size:12px"></div>
<div id="paper-banner" class="paper-banner" style="display:none">
  Practice Money mode — no real money at risk. Everything here is simulated.
</div>

<div class="grid" id="overview-cards"></div>

<div class="chart-wrap">
  <h2>Equity curves</h2>
  <div class="chart-container"><canvas id="eq-chart"></canvas></div>
</div>

<div class="chart-wrap">
  <h2>Stock sleeves vs benchmark</h2>
  <div class="chart-container"><canvas id="stock-chart"></canvas></div>
</div>

<div class="card">
  <h2>Recent agent decisions</h2>
  <table id="feed-table">
    <thead><tr><th>Date</th><th>Instrument</th><th>Side</th><th>P&amp;L</th><th>Reason</th></tr></thead>
    <tbody></tbody>
  </table>
</div>

<p class="refresh-note">Auto-refreshes every 30s &nbsp;·&nbsp; last updated: <span id="ts">—</span></p>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
let eqChart = null, stockChart = null;

function fmt(x){
  const s = x >= 0 ? '+' : '-';
  return s + '$' + Math.abs(x).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
}
function pct(end,start){
  if(!start) return '—';
  const p = (end-start)/start*100;
  const decimals = Math.abs(p) < 1 ? 2 : 1;
  return (p>=0?'+':'')+p.toFixed(decimals)+'%';
}
function colorClass(x){ return x >= 0 ? 'pos' : 'neg'; }

async function refresh(){
  let data;
  try{
    const resp = await fetch('/api/data');
    if(!resp.ok) throw new Error('HTTP ' + resp.status);
    data = await resp.json();
    document.getElementById('error-banner').style.display = 'none';
  } catch(e){
    document.getElementById('error-banner').style.display = 'block';
    document.getElementById('error-banner').textContent = 'Failed to load data: ' + e.message;
    return;
  }

  // Mode line
  const isLive = data.mode === 'live';
  document.getElementById('mode-line').textContent =
    (isLive ? '🔴 LIVE — real money' : '📋 Practice Money') +
    '  ·  Running ' + (data.days_live||0) + ' days';
  document.getElementById('paper-banner').style.display = isLive ? 'none' : 'block';
  document.getElementById('ts').textContent = new Date().toLocaleTimeString();

  // Overview cards
  const cards = document.getElementById('overview-cards');
  cards.innerHTML = '';
  for(const sleeve of (data.sleeves||[])){
    const dd = sleeve.drawdown_pct ? sleeve.drawdown_pct.toFixed(1)+'%' : '—';
    const retCls = colorClass(sleeve.value - sleeve.start);
    cards.innerHTML += `<div class="card">
      <h2>${sleeve.name}</h2>
      <div class="metric"><span class="metric-label">Value</span>
        <span class="metric-value">$${sleeve.value.toLocaleString('en-US',{minimumFractionDigits:0})}</span></div>
      <div class="metric"><span class="metric-label">Return</span>
        <span class="metric-value ${retCls}">${pct(sleeve.value,sleeve.start)}</span></div>
      ${sleeve.bench_label?`<div class="metric"><span class="metric-label">${sleeve.bench_label}</span>
        <span class="metric-value">${pct(sleeve.bench_value,sleeve.bench_start)}</span></div>`:''}
      <div class="metric"><span class="metric-label">Max drawdown</span>
        <span class="metric-value ${dd>'5%'?'neg':'muted'}">${dd}</span></div>
      ${sleeve.halted?'<div class="metric"><span class="halted">⛔ CIRCUIT BREAKER — halted</span></div>':''}
    </div>`;
  }

  // Equity curve chart
  const eq = data.equity_curve || {};
  if(Object.keys(eq).length){
    const labels = eq.dates || [];
    const datasets = (eq.series||[]).map((s,i)=>({
      label: s.label,
      data: s.values,
      borderColor: ['#388bfd','#3fb950','#d29922','#a371f7'][i%4],
      borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false,
    }));
    if(!eqChart){
      eqChart = new Chart(document.getElementById('eq-chart'),
        {type:'line',data:{labels,datasets},options:{
          responsive:true,maintainAspectRatio:false,
          plugins:{legend:{labels:{color:'#8b949e',font:{size:11}}}},
          scales:{x:{ticks:{color:'#8b949e',maxTicksLimit:8},grid:{color:'#21262d'}},
                  y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}},
        }});
    } else {
      eqChart.data.labels = labels;
      eqChart.data.datasets = datasets;
      eqChart.update('none');
    }
  }

  // Stock benchmark chart
  const sk = data.stock_nav || {};
  if(Object.keys(sk).length){
    const labels = sk.dates || [];
    const datasets = (sk.series||[]).map((s,i)=>({
      label: s.label,
      data: s.values,
      borderColor: ['#388bfd','#adbac7','#3fb950','#8b949e'][i%4],
      borderDash: s.is_bench ? [4,3] : [],
      borderWidth: s.is_bench ? 1 : 1.5, pointRadius: 0, tension: 0.3, fill: false,
    }));
    if(!stockChart){
      stockChart = new Chart(document.getElementById('stock-chart'),
        {type:'line',data:{labels,datasets},options:{
          responsive:true,maintainAspectRatio:false,
          plugins:{legend:{labels:{color:'#8b949e',font:{size:11}}}},
          scales:{x:{ticks:{color:'#8b949e',maxTicksLimit:8},grid:{color:'#21262d'}},
                  y:{ticks:{color:'#8b949e',callback:v=>'$'+v.toLocaleString()},grid:{color:'#21262d'}}},
        }});
    } else {
      stockChart.data.labels = labels;
      stockChart.data.datasets = datasets;
      stockChart.update('none');
    }
  }

  // Agent feed
  const tbody = document.querySelector('#feed-table tbody');
  tbody.innerHTML = '';
  for(const t of (data.recent_trades||[])){
    const pnl = t.pnl_usd != null ? fmt(t.pnl_usd) : '—';
    const cls = t.pnl_usd != null ? colorClass(t.pnl_usd) : '';
    tbody.innerHTML += `<tr>
      <td class="muted">${(t.closed_at||'').slice(0,10)}</td>
      <td>${t.pair}</td>
      <td>${t.side.toUpperCase()}</td>
      <td class="${cls}">${pnl}</td>
      <td class="muted">${t.exit_reason||'—'}</td>
    </tr>`;
  }
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>"""


def _load_data(db_path: str, cfg) -> dict:
    """Read the SQLite DB and return a JSON-serialisable dict for the dashboard."""
    if not Path(db_path).exists():
        return {"mode": cfg.trading_mode, "sleeves": [], "equity_curve": {}, "recent_trades": []}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    def get(key: str, default: str = "") -> str:
        row = conn.execute("SELECT value FROM system_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    mode = cfg.trading_mode

    # Days live
    start_iso = get("system_start", "")
    from datetime import datetime, timezone, timedelta
    SGT = timezone(timedelta(hours=8))
    now = datetime.now(SGT)
    days_live = 0
    if start_iso:
        try:
            start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            days_live = max(0, (now.astimezone(timezone.utc) - start_dt.astimezone(timezone.utc)).days)
        except Exception:
            pass

    # Sleeve cards
    sleeves = []

    # Crypto pairs sleeve
    capital = float(get("capital") or cfg.starting_capital_usd)
    start_cap = cfg.starting_capital_usd
    halted = get("halted") == "1"
    # Reconstruct equity curve from closed trades
    rows = list(conn.execute(
        "SELECT closed_at, pnl_usd, strategy FROM trades WHERE closed_at IS NOT NULL ORDER BY closed_at"
    ))
    daily_crypto: dict[str, float] = {}
    running = start_cap
    for r in rows:
        strat = str(r["strategy"] or "")
        if strat.startswith("factor") or strat == "swing":
            continue
        d = (r["closed_at"] or "")[:10]
        running += (r["pnl_usd"] or 0)
        daily_crypto[d] = running

    # Drawdown
    def _dd(vals: list[float]) -> float:
        if not vals:
            return 0.0
        peak = vals[0]
        md = 0.0
        for v in vals:
            peak = max(peak, v)
            if peak > 0:
                md = max(md, (peak - v) / peak * 100)
        return md

    dd_crypto = _dd(list(daily_crypto.values()))

    sleeves.append({
        "name": "Crypto — market-neutral pairs",
        "value": capital,
        "start": start_cap,
        "bench_label": None,
        "bench_value": None,
        "bench_start": None,
        "drawdown_pct": dd_crypto,
        "halted": halted,
    })

    # Factor sleeves
    try:
        from .stock_factor import SLEEVES as SLEEVE_DEFS
    except Exception:
        SLEEVE_DEFS = []

    stock_nav_series = []
    stock_nav_dates_set: set[str] = set()
    for s in SLEEVE_DEFS:
        history = list(conn.execute(
            "SELECT day, value AS sleeve_value, bench_value AS spy_value FROM sleeve_nav WHERE sleeve=? ORDER BY day",
            (s["tag"],)
        ))
        if not history:
            continue
        first = history[0]
        last = history[-1]
        dd_s = _dd([h["sleeve_value"] for h in history])
        bench_label = "S&P 500" if s["benchmark"] == "SPY" else f"AI sector ({s['benchmark']})"
        sleeves.append({
            "name": f"Stocks — {s['name']}",
            "value": last["sleeve_value"],
            "start": first["sleeve_value"],
            "bench_label": bench_label,
            "bench_value": last["spy_value"],
            "bench_start": first["spy_value"],
            "drawdown_pct": dd_s,
            "halted": False,
        })
        for h in history:
            stock_nav_dates_set.add(h["day"])
        stock_nav_series.append({"label": s["name"], "is_bench": False,
                                  "by_day": {h["day"]: h["sleeve_value"] for h in history}})
        stock_nav_series.append({"label": bench_label, "is_bench": True,
                                  "by_day": {h["day"]: h["spy_value"] for h in history}})

    # Swing sleeve
    swing_hwm = float(get("swing_hwm") or cfg.swing_budget_usd)
    swing_cash = float(get("swing_cash") or cfg.swing_budget_usd)
    swing_halted = get("swing_halted") == "1"
    from .swing import floor_value
    swing_floor = floor_value(cfg.swing_budget_usd, cfg.swing_floor_usd, swing_hwm)
    swing_positions = list(conn.execute(
        "SELECT pair, entry_price, size_usd FROM trades WHERE strategy='swing' AND closed_at IS NULL"
    ))
    # Mark open positions to market using live prices (yfinance, best-effort)
    swing_pos_total = 0.0
    if swing_positions:
        try:
            import yfinance as yf
            tickers = list({dict(r)["pair"] for r in swing_positions})
            prices = {}
            data = yf.download(tickers, period="1d", progress=False, auto_adjust=True)
            for t in tickers:
                try:
                    col = data["Close"] if len(tickers) > 1 else data["Close"]
                    px = float((col[t] if len(tickers) > 1 else col).dropna().iloc[-1])
                    prices[t] = px
                except Exception:
                    pass
            for r in swing_positions:
                row = dict(r)
                px = prices.get(row["pair"])
                if px and row["entry_price"]:
                    swing_pos_total += row["size_usd"] * (px / row["entry_price"])
                else:
                    swing_pos_total += row["size_usd"]
        except Exception:
            swing_pos_total = sum(dict(r)["size_usd"] for r in swing_positions)
    swing_total = swing_cash + swing_pos_total
    if swing_hwm > 0 or swing_cash != cfg.swing_budget_usd:
        sleeves.append({
            "name": "Swing — agentic conviction",
            "value": swing_total,
            "start": cfg.swing_budget_usd,
            "bench_label": "Floor (halt line)",
            "bench_value": swing_floor,
            "bench_start": cfg.swing_floor_usd,
            "drawdown_pct": max(0.0, (cfg.swing_budget_usd - swing_total) / cfg.swing_budget_usd * 100)
                            if swing_total < cfg.swing_budget_usd else 0.0,
            "halted": swing_halted,
        })

    # Equity curve: daily crypto capital
    eq_dates = sorted(daily_crypto.keys())
    eq_values = [daily_crypto[d] for d in eq_dates]

    # Stock NAV series
    stock_dates = sorted(stock_nav_dates_set)
    stock_series_out = []
    for s in stock_nav_series:
        vals = [s["by_day"].get(d) for d in stock_dates]
        stock_series_out.append({"label": s["label"], "is_bench": s["is_bench"], "values": vals})

    # Recent trades (agent feed)
    recent = list(conn.execute(
        """SELECT pair, side, entry_price, exit_price, pnl_usd, closed_at, exit_reason, strategy
           FROM trades WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 25"""
    ))
    recent_trades = [dict(r) for r in recent]

    conn.close()

    return {
        "mode": mode,
        "days_live": days_live,
        "sleeves": sleeves,
        "equity_curve": {"dates": eq_dates, "series": [{"label": "Crypto capital", "values": eq_values}]},
        "stock_nav": {"dates": stock_dates, "series": stock_series_out},
        "recent_trades": recent_trades,
    }


def build_app(db_path: str, cfg):
    from flask import Flask, jsonify, Response

    app = Flask(__name__)

    @app.route("/")
    def index():
        return Response(_HTML, mimetype="text/html")

    @app.route("/api/data")
    def data():
        return jsonify(_load_data(db_path, cfg))

    return app
