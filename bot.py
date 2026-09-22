"""
Paper-trading crypto bot (NO real money, NO API keys needed).
Strategy: SMA crossover on 1-hour BTC/USD candles from Kraken's free public API.
Includes: trading fees, stop-loss, take-profit, position sizing, and a kill switch.

Run modes:
  python bot.py            -> live paper trading (one check per run)
  python bot.py backtest   -> test the strategy on the last ~30 days of data
"""
import csv, json, os, sys, urllib.request
from datetime import datetime, timezone

# ============ SETTINGS (safe to edit) ============
PAIR          = "XBTUSD"  # BTC/USD. Try "ETHUSD" for Ethereum
INTERVAL      = 60        # candle size in minutes (60 = 1 hour)
FAST_SMA      = 20        # fast moving average length
SLOW_SMA      = 50        # slow moving average length
START_CASH    = 10000.0   # fake starting money (USD)
POSITION_SIZE = 0.25      # use 25% of cash per trade
STOP_LOSS     = 0.03      # exit if price falls 3% below entry
TAKE_PROFIT   = 0.06      # exit if price rises 6% above entry
FEE           = 0.0026    # 0.26% per trade (Kraken's standard fee)
MAX_DRAWDOWN  = 0.20      # kill switch: stop bot if account drops 20%
# =================================================

STATE_FILE, TRADES_FILE = "state.json", "trades.csv"


def fetch_candles():
    url = f"https://api.kraken.com/0/public/OHLC?pair={PAIR}&interval={INTERVAL}"
    req = urllib.request.Request(url, headers={"User-Agent": "paper-bot"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if data.get("error"):
        raise RuntimeError(f"Kraken error: {data['error']}")
    key = next(k for k in data["result"] if k != "last")
    rows = data["result"][key][:-1]  # drop the still-forming candle
    return [(int(row[0]), float(row[4])) for row in rows]  # (time, close)


def sma(values, n):
    return sum(values[-n:]) / n


def get_signal(closes):
    if len(closes) < SLOW_SMA + 1:
        return None
    f_now, s_now = sma(closes, FAST_SMA), sma(closes, SLOW_SMA)
    f_prev, s_prev = sma(closes[:-1], FAST_SMA), sma(closes[:-1], SLOW_SMA)
    if f_prev <= s_prev and f_now > s_now:
        return "BUY"
    if f_prev >= s_prev and f_now < s_now:
        return "SELL"
    return None


def new_state():
    return {"cash": START_CASH, "position": None, "last_ts": 0,
            "halted": False, "trades": 0, "wins": 0}


def fmt_time(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def close_position(state, ts, price, reason, log):
    pos = state["position"]
    proceeds = pos["qty"] * price * (1 - FEE)
    pnl = proceeds - pos["cost"]
    state["cash"] += proceeds
    state["trades"] += 1
    state["wins"] += 1 if pnl > 0 else 0
    state["position"] = None
    log([fmt_time(ts), "SELL", reason, round(price, 2), round(pnl, 2), round(state["cash"], 2)])


def step(state, ts, price, signal, log):
    if state["halted"]:
        return
    pos = state["position"]
    if pos:
        reason = None
        if price <= pos["stop"]:
            reason = "STOP-LOSS"
        elif price >= pos["target"]:
            reason = "TAKE-PROFIT"
        elif signal == "SELL":
            reason = "SIGNAL"
        if reason:
            close_position(state, ts, price, reason, log)
    elif signal == "BUY":
        spend = state["cash"] * POSITION_SIZE
        qty = spend * (1 - FEE) / price
        state["cash"] -= spend
        state["position"] = {"qty": qty, "entry": price, "cost": spend,
                             "stop": price * (1 - STOP_LOSS),
                             "target": price * (1 + TAKE_PROFIT)}
        log([fmt_time(ts), "BUY", "SIGNAL", round(price, 2), 0, round(state["cash"], 2)])

    # Kill switch
    if equity(state, price) < START_CASH * (1 - MAX_DRAWDOWN):
        if state["position"]:
            close_position(state, ts, price, "KILL-SWITCH", log)
        state["halted"] = True
        print("KILL SWITCH TRIGGERED - bot halted. Delete state.json to reset.")


def equity(state, price):
    pos = state["position"]
    return state["cash"] + (pos["qty"] * price if pos else 0)


def summary(state, price, label):
    eq = equity(state, price)
    wr = (state["wins"] / state["trades"] * 100) if state["trades"] else 0
    print(f"\n===== {label} =====")
    print(f"BTC price:     ${price:,.2f}")
    print(f"Account value: ${eq:,.2f}  ({(eq / START_CASH - 1) * 100:+.2f}%)")
    print(f"Closed trades: {state['trades']}  |  Win rate: {wr:.0f}%")
    print(f"Open position: {'YES @ $%.2f' % state['position']['entry'] if state['position'] else 'None'}")
    print(f"Halted:        {state['halted']}")


def run_live():
    state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else new_state()
    candles = fetch_candles()
    ts, price = candles[-1]
    if ts <= state["last_ts"]:
        print("No new candle yet. Nothing to do.")
        return

    new_file = not os.path.exists(TRADES_FILE)
    with open(TRADES_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time", "action", "reason", "price", "pnl_usd", "cash_after"])
        def log(row):
            w.writerow(row)
            print("TRADE:", row)
        step(state, ts, price, get_signal([c for _, c in candles]), log)

    state["last_ts"] = ts
    json.dump(state, open(STATE_FILE, "w"), indent=2)
    summary(state, price, "LIVE PAPER STATUS")


def run_backtest():
    candles = fetch_candles()
    state = new_state()
    closes = [c for _, c in candles]
    log = lambda row: print("TRADE:", row)
    for i in range(SLOW_SMA + 1, len(candles) + 1):
        ts, price = candles[i - 1]
        step(state, ts, price, get_signal(closes[:i]), log)
    last = closes[-1]
    summary(state, last, f"BACKTEST ({fmt_time(candles[0][0])} to {fmt_time(candles[-1][0])})")
    hold = (last / closes[SLOW_SMA] - 1) * 100
    print(f"Buy & hold same period: {hold:+.2f}%  <- compare with this!")


if __name__ == "__main__":
    run_backtest() if len(sys.argv) > 1 and sys.argv[1] == "backtest" else run_live()
