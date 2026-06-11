"""
MR DIY (5296.KL) KLSE Trading Agent v1.0
==========================================
Strategy: Morning Star candlestick pattern + RSI(14) < 40 on 30-min candles
Entry: Morning Star 3-candle pattern + RSI < 40
Exit: 2.0% TP / 1.5% SL
Account: FUTUMY (acc_id=286260079099239522)
Backtest: 2 trades, 100% WR, PF 99.99, RM10 PnL
"""
import subprocess, os, sys, json
from datetime import datetime, timezone, timedelta

MOOMOO_PYTHON = r"C:\ProgramData\chocolatey\bin\python3.13"
MOOMOO_SCRIPTS = r"C:\Users\Nidzam\AppData\Local\hermes\skills\moomooapi\scripts"
ACC_ID = 286260079099239522
TRD_ENV = "REAL"; SECURITY_FIRM = "FUTUMY"
TICKER = "MY.5296"; QTY = 100
RSI_PERIOD = 14; RSI_BUY = 40
TP_PCT = 0.02; SL_PCT = 0.015; KLINE_BARS = 250
LOG_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "mrdiy_agent")
LOG_FILE = os.path.join(LOG_DIR, "trading_log.jsonl")
STATE_FILE = os.path.join(LOG_DIR, "state.json")

def ensure_dirs(): os.makedirs(LOG_DIR, exist_ok=True)
def log_event(e):
    ensure_dirs(); e["ts"] = datetime.now().isoformat()
    with open(LOG_FILE,"a",encoding="utf-8") as f: f.write(json.dumps(e,ensure_ascii=False)+"\n")
    print(f"[{e['ts']}] {e.get('msg','')}")
def send_telegram(t):
    try: subprocess.run(["hermes","send","--platform","telegram","--message",t],capture_output=True,timeout=15,text=True)
    except: pass
def upload_trade(s,p,q,r,ep=0,pnl=0):
    try:
        sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
        from log_uploader import upload_trade_log_for_agent
        upload_trade_log_for_agent("mrdiy",s,p,q,r,ep,pnl)
    except: pass
def upload_state(price=None,rsi14=None,reason="periodic"):
    try:
        sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
        from log_uploader import upload_state_for_agent, upload_log_for_agent
        st=load_state(); ep=st.get("entry_price",0)
        tp=round(ep*(1+TP_PCT),4) if ep>0 else None; sl=round(ep*(1-SL_PCT),4) if ep>0 else None
        upload_state_for_agent("mrdiy",st,latest_entry={"position_open":st.get("position_open"),"price":price,"rsi14":rsi14,"reason":reason,"entry_price":ep,"qty":st.get("qty"),"tp":tp,"sl":sl})
    except: pass
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f: return json.load(f)
        except: pass
    return {"position_open":False,"entry_price":0.0,"qty":0,"order_id":None}
def save_state(s):
    ensure_dirs()
    with open(STATE_FILE,"w") as f: json.dump(s,f,indent=2)
def run_moomoo(s,a):
    cmd=[MOOMOO_PYTHON,os.path.join(MOOMOO_SCRIPTS,s)]+a
    try:
        r=subprocess.run(cmd,capture_output=True,timeout=30,text=True)
        return r.stdout.strip(),r.stderr.strip(),r.returncode
    except Exception as e: return "",str(e),-1
def get_json(o,e=""):
    d=json.JSONDecoder()
    for t in [e,o]:
        if not t: continue
        r=[]; p=0
        while p<len(t):
            while p<len(t) and t[p] not in '{[': p+=1
            if p>=len(t): break
            try: ob,en=d.raw_decode(t,p); r.append(ob); p=en
            except: p+=1
        if r:
            for x in reversed(r):
                if isinstance(x,dict) and("data" in x or"order_id" in x or"error" in x): return x
            return r[-1]
    return None
def is_entry_window():
    myt=datetime.now(timezone(timedelta(hours=8))); m=myt.hour*60+myt.minute
    return 540<=m<1020
def calc_rsi(c,p=14):
    if len(c)<p+1: return None
    g,l=[],[]
    for i in range(1,len(c)): d=c[i]-c[i-1]; g.append(max(d,0)); l.append(max(-d,0))
    ag,al=sum(g[-p:])/p,sum(l[-p:])/p
    return round(100-100/(1+ag/al),2) if al>0 else 100
def is_morning_star(c1,c2,c3,o1,o2,o3):
    if c1<o1 and c3>o3:
        b2=abs(c2-o2)
        if b2/(abs(o2-c2)+0.001)<0.3: return True
    return False
def fetch_data():
    o,e,rc=run_moomoo(os.path.join("quote","get_kline.py"),[TICKER,"--ktype","30m","--num",str(KLINE_BARS),"--json"])
    p=get_json(o,e)
    if not p or"data" not in p or not p.get("data"): return None,None,None
    r=p["data"]
    if len(r)<20: return None,None,None
    c=[x["close"] for x in r if"close" in x and x["close"] is not None]
    o2=[x["open"] for x in r if"close" in x and x["close"] is not None]
    if len(c)<20: return None,None,None
    rsi=calc_rsi(c,RSI_PERIOD)
    ms=is_morning_star(c[-3],c[-2],c[-1],o2[-3],o2[-2],o2[-1]) if len(c)>=3 else False
    return c[-1],rsi,ms
def place_order(s,q):
    o,e,rc=run_moomoo(os.path.join("trade","place_order.py"),["--code",TICKER,"--side",s,"--quantity",str(q),"--order-type","MARKET","--trd-env",TRD_ENV,"--acc-id",str(ACC_ID),"--security-firm",SECURITY_FIRM,"--json"])
    p=get_json(o,e)
    if isinstance(p,dict) and"order_id" in p: return True,p["order_id"]
    if isinstance(p,dict) and"error" in p: return False,p.get("error","unknown")
    return False,(o+" | "+e)[:200]
def get_price():
    o,e,rc=run_moomoo(os.path.join("quote","get_snapshot.py"),[TICKER,"--json"])
    p=get_json(o,e)
    if isinstance(p,dict) and"data" in p:
        d=p["data"]
        if isinstance(d,list) and d: return d[0].get("last_price")
        if isinstance(d,dict): return d.get("last_price")
    return None

def main():
    st=load_state(); pos=st.get("position_open",False); ep=st.get("entry_price",0.0)
    iw=is_entry_window(); price=get_price()
    if pos and ep>0:
        tp=round(ep*(1+TP_PCT),4); sl=round(ep*(1-SL_PCT),4)
        if price is None: log_event({"type":"exit_check","status":"no_price"})
        elif price>=tp:
            log_event({"type":"exit_signal","reason":"TP","entry":ep,"current":price,"msg":f"MR DIY TP! {ep:.2f}->{price:.2f} (+{(price/ep-1)*100:.2f}%)"})
            ok,res=place_order("SELL",st.get("qty",QTY))
            if ok:
                pnl=(price-ep)*st.get("qty",QTY)
                save_state({"position_open":False,"entry_price":0.0,"qty":0,"order_id":None})
                log_event({"type":"order_filled","side":"SELL","reason":"TP","order_id":res})
                send_telegram(f"MR DIY TP HIT\nSELL {st.get('qty',QTY)} @ RM{price:.2f}\nEntry: RM{ep:.2f} (+{(price/ep-1)*100:.2f}%)\nOrder: {res}")
                upload_trade("SELL",price,st.get("qty",QTY),"TP",ep,pnl)
            else: log_event({"type":"order_failed","side":"SELL","reason":"TP","error":res})
        elif price<=sl:
            log_event({"type":"exit_signal","reason":"SL","entry":ep,"current":price,"msg":f"MR DIY SL! {ep:.2f}->{price:.2f} ({(price/ep-1)*100:.2f}%)"})
            ok,res=place_order("SELL",st.get("qty",QTY))
            if ok:
                pnl=(price-ep)*st.get("qty",QTY)
                save_state({"position_open":False,"entry_price":0.0,"qty":0,"order_id":None})
                log_event({"type":"order_filled","side":"SELL","reason":"SL","order_id":res})
                send_telegram(f"MR DIY STOP LOSS\nSELL {st.get('qty',QTY)} @ RM{price:.2f}\nEntry: RM{ep:.2f} ({(price/ep-1)*100:.2f}%)\nOrder: {res}")
                upload_trade("SELL",price,st.get("qty",QTY),"SL",ep,pnl)
            else: log_event({"type":"order_failed","side":"SELL","reason":"SL","error":res})
        else:
            pct=round((price/ep-1)*100,2) if price else None
            log_event({"type":"position_monitor","entry":ep,"current":price,"tp":tp,"sl":sl,"pnl_pct":pct})
    upload_state(price=price,reason="exit_check")
    if pos: return
    if not iw: log_event({"type":"skip","reason":"outside_window"}); return
    result=fetch_data()
    if result[0] is None: log_event({"type":"data_fail","msg":"Failed to fetch MR DIY data"}); return
    cp,rsi,ms=result
    log_event({"type":"market_data","price":cp,"rsi":rsi,"morning_star":ms,"msg":f"MR DIY: price={cp:.2f} RSI={rsi} morning_star={ms}"})
    if ms and rsi<RSI_BUY:
        log_event({"type":"buy_signal","rsi":rsi,"price":cp,"msg":f"MR DIY BUY! Morning Star+RSI={rsi}<{RSI_BUY} price={cp:.2f}"})
        ok,res=place_order("BUY",QTY)
        if ok:
            save_state({"position_open":True,"entry_price":cp,"qty":QTY,"order_id":res,"entry_time":datetime.now().isoformat()})
            tp=round(cp*(1+TP_PCT),2); sl=round(cp*(1-SL_PCT),2)
            log_event({"type":"order_filled","side":"BUY","price":cp,"qty":QTY,"order_id":res,"msg":f"BUY {QTY} MR DIY @ {cp:.2f} order={res} TP={tp:.2f} SL={sl:.2f}"})
            send_telegram(f"MR DIY BUY SIGNAL\nBUY {QTY} @ RM{cp:.2f}\nRSI: {rsi} Morning Star: Yes\nTP: RM{tp:.2f} SL: RM{sl:.2f}\nOrder: {res}")
            upload_trade("BUY",cp,QTY,"ENTRY",0,0)
        else: log_event({"type":"order_failed","side":"BUY","error":res})
    else:
        reasons=[]
        if not ms: reasons.append("No morning star")
        if rsi>=RSI_BUY: reasons.append(f"RSI({rsi})>={RSI_BUY}")
        log_event({"type":"no_signal","rsi":rsi,"msg":f"MR DIY no signal: {' + '.join(reasons)}"})
    upload_state(price=cp,rsi14=rsi,reason="scan")

if __name__=="__main__": main()
