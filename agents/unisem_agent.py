"""
Unisem (5005.KL) KLSE Trading Agent v1.0
==========================================
Strategy: BB(20,2) lower touch + RSI(14) < 40 on 30-min candles
Entry: Price touches BB lower band + RSI < 40
Exit: 2.0% TP / 1.5% SL
Account: FUTUMY (acc_id=286260079099239522)
Backtest: 9 trades, 88.9% WR, PF 99.99, RM119 PnL

Data source: moomoo OpenD (MY.5005)
Timeframe: 30-min klines, 250 bars
Trading hours: 9:00 AM - 5:00 PM MYT (GMT+8)
"""
import subprocess, os, sys, json
from datetime import datetime, timezone, timedelta

MOOMOO_PYTHON = r"C:\ProgramData\chocolatey\bin\python3.13"
MOOMOO_SCRIPTS = r"C:\Users\Nidzam\AppData\Local\hermes\skills\moomooapi\scripts"

ACC_ID = 286260079099239522
TRD_ENV = "REAL"
SECURITY_FIRM = "FUTUMY"
TICKER = "MY.5005"
QTY = 100  # 1 lot (KLSE minimum)

# SAFETY: Set to True to simulate orders (no real trades)
SIMULATE = True

BB_PERIOD = 20
BB_MULT = 2.0
RSI_PERIOD = 14
RSI_THRESHOLD = 40
TP_PCT = 0.02
SL_PCT = 0.015
KLINE_BARS = 250

LOG_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "unisem_agent")
LOG_FILE = os.path.join(LOG_DIR, "trading_log.jsonl")
STATE_FILE = os.path.join(LOG_DIR, "state.json")

def ensure_dirs():
    os.makedirs(LOG_DIR, exist_ok=True)

def log_event(entry):
    ensure_dirs()
    entry["ts"] = datetime.now().isoformat()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[{entry['ts']}] {entry.get('msg', '')}")

def send_telegram(text):
    try:
        r = subprocess.run(["hermes", "send", "--platform", "telegram", "--message", text],
                           capture_output=True, timeout=15, text=True)
    except Exception:
        pass

def upload_trade(side, price, qty, reason, entry_price=0, pnl=0):
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from log_uploader import upload_trade_log_for_agent
        upload_trade_log_for_agent("unisem", side, price, qty, reason, entry_price, pnl)
    except Exception:
        pass

def upload_state(price=None, rsi14=None, bb_lower=None, reason="periodic"):
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from log_uploader import upload_state_for_agent, upload_log_for_agent
        state = load_state()
        ep = state.get("entry_price", 0)
        tp = round(ep * (1 + TP_PCT), 4) if ep > 0 else None
        sl = round(ep * (1 - SL_PCT), 4) if ep > 0 else None
        upload_state_for_agent("unisem", state,
            latest_entry={"position_open": state.get("position_open"), "price": price,
                         "rsi14": rsi14, "bb_lower": bb_lower,
                         "reason": reason, "entry_price": ep,
                         "qty": state.get("qty"), "tp": tp, "sl": sl})
        ensure_dirs()
        if os.path.exists(LOG_FILE):
            lines = []
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            meaningful = []
            for line in lines[-50:]:
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        if entry.get("type") in ("order_filled", "exit_signal", "buy_signal",
                                                   "order_failed", "position_monitor"):
                            meaningful.append(entry)
                    except:
                        pass
            if meaningful:
                upload_log_for_agent("unisem", meaningful[-15:])
    except Exception:
        pass

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f: return json.load(f)
        except: pass
    return {"position_open": False, "entry_price": 0.0, "qty": 0, "order_id": None}

def save_state(state):
    ensure_dirs()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def run_moomoo(script, args):
    cmd = [MOOMOO_PYTHON, os.path.join(MOOMOO_SCRIPTS, script)] + args
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30, text=True)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except Exception as e:
        return "", str(e), -1

def get_json(out, err=""):
    dec = json.JSONDecoder()
    for text in [err, out]:
        if not text: continue
        results = []; pos = 0
        while pos < len(text):
            while pos < len(text) and text[pos] not in '{[': pos += 1
            if pos >= len(text): break
            try:
                obj, end = dec.raw_decode(text, pos)
                results.append(obj); pos = end
            except: pos += 1
        if results:
            for o in reversed(results):
                if isinstance(o, dict) and ("data" in o or "order_id" in o or "error" in o): return o
            return results[-1]
    return None

def is_entry_window():
    # KLSE trading hours: 9:00 AM - 5:00 PM MYT (GMT+8)
    myt = datetime.now(timezone(timedelta(hours=8)))
    mins = myt.hour * 60 + myt.minute
    return 540 <= mins < 1020  # 9:00 AM to 5:00 PM

def fetch_data():
    out, err, rc = run_moomoo(os.path.join("quote", "get_kline.py"),
        [TICKER, "--ktype", "30m", "--num", str(KLINE_BARS), "--json"])
    parsed = get_json(out, err)
    if not parsed or "data" not in parsed or not parsed.get("data"):
        return None, None, None, None
    records = parsed["data"]
    if len(records) < BB_PERIOD + 5:
        return None, None, None, None
    closes = [r["close"] for r in records if "close" in r and r["close"] is not None]
    lows = [r["low"] for r in records if "low" in r and r["low"] is not None]
    if len(closes) < BB_PERIOD + 5:
        return None, None, None, None

    w = closes[-BB_PERIOD:]
    mid = sum(w) / BB_PERIOD
    std = (sum((x - mid)**2 for x in w) / BB_PERIOD) ** 0.5
    bb_lower = mid - BB_MULT * std

    rsi = None
    if len(closes) >= RSI_PERIOD + 1:
        g, l = [], []
        for i in range(1, len(closes)):
            c = closes[i] - closes[i-1]; g.append(max(c, 0)); l.append(max(-c, 0))
        ag = sum(g[-RSI_PERIOD:]) / RSI_PERIOD; al = sum(l[-RSI_PERIOD:]) / RSI_PERIOD
        rsi = round(100 - 100/(1+ag/al), 2) if al > 0 else 100

    return closes[-1], lows[-1] if lows else closes[-1], bb_lower, rsi

def place_order(side, qty):
    if SIMULATE:
        log_event({"type": "simulate_order", "side": side, "qty": qty, "msg": f"SIMULATE: {side} {qty} shares (no real order)"})
        return True, "SIMULATE"
    out, err, rc = run_moomoo(os.path.join("trade", "place_order.py"),
        ["--code", TICKER, "--side", side, "--quantity", str(qty),
         "--order-type", "MARKET", "--trd-env", TRD_ENV,
         "--acc-id", str(ACC_ID), "--security-firm", SECURITY_FIRM, "--json"])
    parsed = get_json(out, err)
    if isinstance(parsed, dict) and "order_id" in parsed:
        return True, parsed["order_id"]
    if isinstance(parsed, dict) and "error" in parsed:
        return False, parsed.get("error", "unknown")
    return False, (out + " | " + err)[:200]

def get_price():
    out, err, rc = run_moomoo(os.path.join("quote", "get_snapshot.py"), [TICKER, "--json"])
    parsed = get_json(out, err)
    if isinstance(parsed, dict) and "data" in parsed:
        d = parsed["data"]
        if isinstance(d, list) and d: return d[0].get("last_price")
        if isinstance(d, dict): return d.get("last_price")
    return None

def main():
    state = load_state()
    position_open = state.get("position_open", False)
    entry_price = state.get("entry_price", 0.0)
    in_window = is_entry_window()
    price = get_price()

    # EXIT: Position open -- check TP/SL
    if position_open and entry_price > 0:
        ep = entry_price
        tp = round(ep * (1 + TP_PCT), 4)
        sl = round(ep * (1 - SL_PCT), 4)

        if price is None:
            log_event({"type": "exit_check", "status": "no_price"})
        elif price >= tp:
            log_event({"type": "exit_signal", "reason": "TP", "entry": ep, "current": price,
                        "msg": f"Unisem TP! Entry={ep:.2f} -> {price:.2f} (+{(price/ep-1)*100:.2f}%)"})
            ok, res = place_order("SELL", state.get("qty", QTY))
            if ok:
                pnl = (price - ep) * state.get("qty", QTY)
                save_state({"position_open": False, "entry_price": 0.0, "qty": 0, "order_id": None})
                log_event({"type": "order_filled", "side": "SELL", "reason": "TP", "order_id": res})
                send_telegram(f"UNISEM TP HIT\nSELL {state.get('qty',QTY)} @ RM{price:.2f}\nEntry: RM{ep:.2f} (+{(price/ep-1)*100:.2f}%)\nOrder: {res}")
                upload_trade("SELL", price, state.get("qty", QTY), "TP", ep, pnl)
            else:
                log_event({"type": "order_failed", "side": "SELL", "reason": "TP", "error": res})
        elif price <= sl:
            log_event({"type": "exit_signal", "reason": "SL", "entry": ep, "current": price,
                        "msg": f"Unisem SL! Entry={ep:.2f} -> {price:.2f} ({(price/ep-1)*100:.2f}%)"})
            ok, res = place_order("SELL", state.get("qty", QTY))
            if ok:
                pnl = (price - ep) * state.get("qty", QTY)
                save_state({"position_open": False, "entry_price": 0.0, "qty": 0, "order_id": None})
                log_event({"type": "order_filled", "side": "SELL", "reason": "SL", "order_id": res})
                send_telegram(f"UNISEM STOP LOSS\nSELL {state.get('qty',QTY)} @ RM{price:.2f}\nEntry: RM{ep:.2f} ({(price/ep-1)*100:.2f}%)\nOrder: {res}")
                upload_trade("SELL", price, state.get("qty", QTY), "SL", ep, pnl)
            else:
                log_event({"type": "order_failed", "side": "SELL", "reason": "SL", "error": res})
        else:
            pct = round((price/entry_price-1)*100, 2) if price else None
            log_event({"type": "position_monitor", "entry": ep, "current": price, "tp": tp, "sl": sl,
                        "pnl_pct": pct})

    upload_state(price=price, reason="exit_check")
    if position_open:
        return

    # ENTRY: No position
    if not in_window:
        log_event({"type": "skip", "reason": "outside_window", "msg": "Outside 9:00-17:00 MYT"})
        return

    current_price, low, bb_lower, rsi = fetch_data()
    if current_price is None or bb_lower is None or rsi is None:
        log_event({"type": "data_fail", "msg": "Failed to fetch Unisem data"})
        return

    log_event({"type": "market_data", "price": current_price, "low": low, "bb_lower": bb_lower, "rsi": rsi,
                "msg": f"Unisem: price={current_price:.2f} low={low:.2f} BB_lower={bb_lower:.2f} RSI={rsi}"})

    if low <= bb_lower and rsi < RSI_THRESHOLD:
        log_event({"type": "buy_signal", "rsi": rsi, "bb_lower": bb_lower, "low": low, "price": current_price,
                    "msg": f"Unisem BUY! Low={low:.2f} <= BB_lower={bb_lower:.2f} RSI={rsi} < {RSI_THRESHOLD}"})
        ok, res = place_order("BUY", QTY)
        if ok:
            save_state({"position_open": True, "entry_price": current_price, "qty": QTY,
                         "order_id": res, "entry_time": datetime.now().isoformat()})
            tp = round(current_price * (1 + TP_PCT), 2)
            sl = round(current_price * (1 - SL_PCT), 2)
            log_event({"type": "order_filled", "side": "BUY", "price": current_price, "qty": QTY, "order_id": res,
                        "msg": f"BUY {QTY} Unisem @ {current_price:.2f} order={res} TP={tp:.2f} SL={sl:.2f}"})
            send_telegram(f"UNISEM BUY SIGNAL\nBUY {QTY} @ RM{current_price:.2f}\nRSI: {rsi} BB_lower: RM{bb_lower:.2f}\nTP: RM{tp:.2f} SL: RM{sl:.2f}\nOrder: {res}")
            upload_trade("BUY", current_price, QTY, "ENTRY", 0, 0)
        else:
            log_event({"type": "order_failed", "side": "BUY", "error": res, "msg": f"Unisem BUY FAILED: {res}"})
    else:
        reasons = []
        if low > bb_lower: reasons.append(f"low({low:.2f}) > BB({bb_lower:.2f})")
        if rsi >= RSI_THRESHOLD: reasons.append(f"RSI({rsi}) >= {RSI_THRESHOLD}")
        log_event({"type": "no_signal", "rsi": rsi, "msg": f"Unisem no signal: {' + '.join(reasons)}"})

    upload_state(price=current_price, rsi14=rsi, bb_lower=bb_lower, reason="scan")

if __name__ == "__main__":
    main()
