"""
KLSE Trading Lab - Server v1.0
===============================
FastAPI backend for KLSE Shariah trading dashboard.
Reads agent state/logs from GitHub and serves live dashboard.

Data flow:
  Agent (Windows) -> uploads state+logs to GitHub after every run
                  -> pushes trades to Render POST (real-time)
  Render server   -> reads from GitHub logs/ (source of truth)
                  -> reads from in-memory store (real-time trades)
  Dashboard       -> fetches from Render API every 15s

API:
  GET /api/health          -- Health check
  GET /api/portfolio       -- Portfolio summary from agent states
  GET /api/{agent}         -- Agent data (position, price, indicators)
  GET /api/trades/{agent}  -- Recent trades
  POST /api/trades/push    -- Receive trade push from agent
  GET /                    -- Dashboard HTML
"""
import os, json, urllib.request
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DASHBOARD_HTML = SCRIPT_DIR / "index.html"

GITHUB_BASE = "https://raw.githubusercontent.com/Nidzam81/klse-trading-lab/master"

ALL_AGENTS = ["unisem", "gamuda", "ioi", "qlr", "mrdiy"]

def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "klse-trading-lab"})
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[FETCH] {url}: {e}")
        return None

def compute_indicators_for_agent(agent):
    state = fetch_json(f"{GITHUB_BASE}/logs/{agent}_state.json") or {}
    trade_data = fetch_json(f"{GITHUB_BASE}/logs/{agent}_trades.json") or {}
    trades = trade_data.get("trades", [])

    latest = state.get("latest_entry", {})
    position_open = state.get("position_open", latest.get("position_open", False))
    entry_price = state.get("entry_price", latest.get("entry_price", 0))
    qty = state.get("qty", latest.get("qty", 0))
    current_price = latest.get("price") or latest.get("current_price")

    live_pnl = None
    if position_open and entry_price and current_price and qty:
        live_pnl = round((current_price - entry_price) * qty, 2)

    buy_trades = [t for t in trades if t.get("side") == "BUY"]
    sell_trades = [t for t in trades if t.get("side") == "SELL"]
    round_trips = min(len(buy_trades), len(sell_trades))
    tp_count = len([t for t in sell_trades if t.get("reason") == "TP"])
    sl_count = len([t for t in sell_trades if t.get("reason") == "SL"])
    realized_pnl = round(sum(t.get("pnl", 0) for t in sell_trades), 2)

    tp = latest.get("tp")
    sl = latest.get("sl")

    return {
        "ticker": agent.upper(),
        "position_open": position_open,
        "entry_price": entry_price,
        "qty": qty,
        "current_price": current_price,
        "live_pnl": live_pnl,
        "tp": tp,
        "sl": sl,
        "entry_time": state.get("entry_time"),
        "last_updated": state.get("last_updated"),
        "total_trades": round_trips,
        "tp_count": tp_count,
        "sl_count": sl_count,
        "realized_pnl": realized_pnl,
        "trades": trades[-20:],
    }

def compute_portfolio():
    positions = []
    total_realized_pnl = 0.0

    for agent in ALL_AGENTS:
        state = fetch_json(f"{GITHUB_BASE}/logs/{agent}_state.json") or {}
        latest = state.get("latest_entry", {})
        position_open = state.get("position_open", latest.get("position_open", False))
        entry_price = state.get("entry_price", latest.get("entry_price", 0))
        qty = state.get("qty", latest.get("qty", 0))
        current_price = latest.get("price")

        trade_data = fetch_json(f"{GITHUB_BASE}/logs/{agent}_trades.json") or {}
        trades = trade_data.get("trades", [])
        sells = [t for t in trades if t.get("side") == "SELL"]
        agent_pnl = round(sum(t.get("pnl", 0) for t in sells), 2)
        total_realized_pnl += agent_pnl

        live_pnl = 0.0
        if position_open and current_price and entry_price and qty:
            live_pnl = round((current_price - entry_price) * qty, 2)

        if position_open:
            positions.append({
                "agent": agent.upper(),
                "entry_price": entry_price,
                "qty": qty,
                "current_price": current_price,
                "live_pnl": live_pnl,
                "tp": latest.get("tp"),
                "sl": latest.get("sl"),
                "entry_time": state.get("entry_time"),
            })

    return {
        "positions": positions,
        "position_count": len(positions),
        "total_realized_pnl": round(total_realized_pnl, 2),
        "total_live_pnl": round(sum(p["live_pnl"] for p in positions), 2),
        "last_updated": datetime.now().isoformat(),
    }

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="KLSE Trading Lab", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_trade_store: dict = {}

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat(), "market": "KLSE"}

@app.get("/api/portfolio")
def portfolio():
    return JSONResponse(compute_portfolio())

@app.get("/api/{agent}")
def agent_data(agent: str):
    agent = agent.lower()
    if agent not in ALL_AGENTS:
        return JSONResponse({"error": "unknown agent"}, status_code=404)
    return JSONResponse(compute_indicators_for_agent(agent))

@app.post("/api/trades/push")
def push_trade(data: dict):
    agent = data.get("agent", "unknown").lower()
    trade = data.get("trade", {})
    print(f"[PUSH] {agent}: {trade.get('side')} {trade.get('reason')} @ {trade.get('price')}")
    if agent not in _trade_store:
        _trade_store[agent] = []
    _trade_store[agent].append(trade)
    _trade_store[agent] = _trade_store[agent][-100:]
    return JSONResponse({"status": "ok", "trades_stored": len(_trade_store[agent])})

@app.get("/api/trades/{agent}")
def recent_trades(agent: str):
    agent = agent.lower()
    trades = _trade_store.get(agent, [])
    if trades:
        return JSONResponse({"trades": trades})
    data = fetch_json(f"{GITHUB_BASE}/logs/{agent}_trades.json")
    if data and data.get("trades"):
        return JSONResponse(data)
    return JSONResponse({"trades": []})

@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
def dashboard():
    if DASHBOARD_HTML.exists():
        return HTMLResponse(DASHBOARD_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>KLSE Trading Lab Dashboard</h1>")

def _seed_trades():
    for agent in ALL_AGENTS:
        data = fetch_json(f"{GITHUB_BASE}/logs/{agent}_trades.json")
        if data and data.get("trades"):
            _trade_store[agent] = data["trades"][-100:]
            print(f"[SEED] {agent}: {len(data['trades'])} trades")

@app.on_event("startup")
async def startup():
    _seed_trades()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    _seed_trades()
    print(f"Starting KLSE Trading Lab v1.0 on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
