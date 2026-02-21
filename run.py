import socketio, requests, time, os, hashlib, hmac, threading, json, math
from dotenv import load_dotenv

# ========= CONFIG =========
load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("SECRET_KEY")

BASE_URL = "https://fapi.pi42.com"
WS_URL = "https://fawss.pi42.com/"

SYMBOLS = ["XPTINR"]

CAPITAL_PER_TRADE = 65
RISE_PERCENT = 2          # trigger on rise
TP_PERCENT = 1            # TP below entry
TRADE_COOLDOWN = 20

MIN_QTY = {
    "XPTINR": 0.003,
}

sio = socketio.Client(reconnection=True)

prices, positions, orders = {}, {}, {}
last_trade = {s: 0 for s in SYMBOLS}

# ========= SIGNATURE =========
def generate_signature(secret, message):
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

def sign(query):
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

# ========= TARGET =========
def calculate_target(entry):
    return int(round(entry * (1 - TP_PERCENT / 100)))

# ========= QTY CALC =========
def calculate_order_qty(sym):
    price = prices.get(sym)
    if not price:
        return None
    step = MIN_QTY.get(sym, 0.001)
    raw = CAPITAL_PER_TRADE / price
    qty = math.floor(raw / step) * step
    return round(qty, 3) if qty >= step else None

# ========= ORDER DATA =========
def get_highest_buy(sym):
    buys = [
        float(o["price"])
        for o in orders.get(sym, [])
        if o["side"] == "BUY" and o.get("price")
    ]
    return max(buys) if buys else None

def get_trigger_price(sym):
    highest = get_highest_buy(sym)
    if not highest:
        return None
    return round(highest * (1 + RISE_PERCENT / 100), 2)

# ========= PLACE ORDER =========
def place_market_sell(sym):

    qty = calculate_order_qty(sym)
    if not qty:
        return False

    timestamp = str(int(time.time() * 1000))
    entry_price = prices[sym]
    tp = calculate_target(entry_price)

    params = {
        "timestamp": timestamp,
        "placeType": "ORDER_FORM",
        "quantity": qty,
        "side": "SELL",
        "price": 0,
        "symbol": sym,
        "type": "MARKET",
        "reduceOnly": False,
        "marginAsset": "INR",
        "deviceType": "WEB",
        "userCategory": "EXTERNAL",
        "takeProfitPrice": tp
    }

    body = json.dumps(params, separators=(',', ':'))

    signature = generate_signature(API_SECRET, body)

    headers = {
        "api-key": API_KEY,
        "signature": signature,
        "Content-Type": "application/json"
    }

    r = requests.post(
        f"{BASE_URL}/v1/order/place-order",
        data=body,
        headers=headers,
        timeout=15
    )

    print(f"\n🔻 SELL {sym} | Qty:{qty} | Entry:{entry_price} | TP:{tp}")
    print("Response:", r.text)

    return True

# ========= TRADE LOGIC =========
def trade_logic(sym):

    if sym not in prices:
        return

    if time.time() - last_trade[sym] < TRADE_COOLDOWN:
        return

    trigger = get_trigger_price(sym)

    if not trigger:
        return

    if prices[sym] >= trigger:

        print(f"📈 {sym} Trigger Hit → {prices[sym]} >= {trigger}")

        if place_market_sell(sym):

            last_trade[sym] = time.time()

# ========= FETCH POSITIONS =========
def fetch_positions_loop():

    while True:

        try:

            ts = str(int(time.time() * 1000))

            for sym in SYMBOLS:

                query = f"symbol={sym}&sortOrder=desc&pageSize=100&timestamp={ts}"

                headers = {
                    "api-key": API_KEY,
                    "signature": sign(query)
                }

                r = requests.get(
                    f"{BASE_URL}/v1/positions/OPEN?{query}",
                    headers=headers
                )

                data = r.json()

                positions[sym] = next(
                    (p for p in data if p["contractPair"] == sym),
                    None
                )

        except Exception as e:

            print("Position error:", e)

        time.sleep(10)

# ========= FETCH ORDERS =========
def fetch_orders_loop():

    while True:

        try:

            ts = str(int(time.time() * 1000))

            query = f"timestamp={ts}"

            headers = {
                "api-key": API_KEY,
                "signature": sign(query)
            }

            r = requests.get(
                f"{BASE_URL}/v1/order/open-orders?{query}",
                headers=headers
            )

            data = r.json()

            for sym in SYMBOLS:

                orders[sym] = [
                    o for o in data
                    if o.get("symbol") == sym
                ]

        except Exception as e:

            print("Order error:", e)

        time.sleep(12)

# ========= DASHBOARD =========
def display_loop():

    while True:

        print("\n========== SHORT DASHBOARD ==========")

        for sym in SYMBOLS:

            price = prices.get(sym)

            pos = positions.get(sym)

            highest = get_highest_buy(sym)

            trigger = get_trigger_price(sym)

            qty = calculate_order_qty(sym)

            print(f"\n🔹 {sym}")

            print(f"LTP: {price}")

            print(f"Highest Open Buy: {highest}")

            print(f"Trigger Price: {trigger}")

            print(f"Next Sell Qty: {qty}")

            if pos:

                entry = float(pos["entryPrice"])

                q = float(pos["quantity"])

                pnl = (entry - price) * q if price else 0

                print(f"Short Position → Qty:{q} Entry:{entry} PnL:{round(pnl,2)}")

            else:

                print("Position → None")

        time.sleep(4)

# ========= WEBSOCKET =========
@sio.event
def connect():

    print("WS Connected")

    sio.emit(
        'subscribe',
        {'params': [f"{s.lower()}@markPrice" for s in SYMBOLS]}
    )

@sio.on('markPriceUpdate')
def on_price(data):

    sym = data.get('s', '').upper()

    if sym in SYMBOLS:

        prices[sym] = float(data['p'])

        trade_logic(sym)

# ========= MAIN =========
if __name__ == "__main__":

    threading.Thread(target=fetch_positions_loop, daemon=True).start()

    threading.Thread(target=fetch_orders_loop, daemon=True).start()

    threading.Thread(target=display_loop, daemon=True).start()

    while True:

        try:

            sio.connect(WS_URL)

            sio.wait()

        except Exception as e:

            print("WS reconnecting...", e)

            time.sleep(5)
