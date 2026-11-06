"""RoboTrader MT5 bridge. Runs INSIDE Wine next to the MetaTrader 5 terminal (Windows Python + the
official MetaTrader5 package) and exposes a tiny localhost-only REST API for the Node server.
No tokens: it logs in with the MT5 login / password / server you enter in the dashboard."""
import os, sys, json, time, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import MetaTrader5 as mt5

PORT = int(os.environ.get('MT5_BRIDGE_PORT', '8765'))
TERMINAL = os.environ.get('MT5_PATH', r'C:\Program Files\MetaTrader 5\terminal64.exe')
MAGIC = 7777
SUFFIX = os.environ.get('SYMBOL_SUFFIX', '')
try: SMAP = {k.upper(): v for k, v in json.loads(os.environ.get('SYMBOL_MAP', '{}')).items()}
except Exception: SMAP = {}
LOCK = threading.RLock()
S = {'creds': None, 'offset': int(float(os.environ.get('MT5_SERVER_OFFSET_HOURS', '0')) * 3600), 'known': False, 'sym': {}, 'alias': {}}
TF = {1: mt5.TIMEFRAME_M1, 5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15, 30: mt5.TIMEFRAME_M30, 60: mt5.TIMEFRAME_H1, 240: mt5.TIMEFRAME_H4, 1440: mt5.TIMEFRAME_D1}


class ApiErr(Exception):
    def __init__(self, msg, code=400): super().__init__(msg); self.code = code


def last_err():
    try: c, m = mt5.last_error(); return f'{c}: {m}'
    except Exception: return 'unknown error'


def do_login(c):
    if not os.path.exists(TERMINAL) and os.name == 'nt':
        raise ApiErr('The MT5 terminal is not installed in this container yet. Wait a few minutes after the first start and try again.', 503)
    mt5.shutdown()
    try: login = int(str(c['login']).strip())
    except Exception: raise ApiErr('MT5 login must be the account number (digits only)')
    ok = mt5.initialize(path=TERMINAL, login=login, password=c['password'], server=c['server'], timeout=90000) if os.name == 'nt' \
        else mt5.initialize(login=login, password=c['password'], server=c['server'])
    if not ok:
        raise ApiErr(f'MT5 login failed ({last_err()}). Check login, password and the broker server name exactly as in your broker email.', 502)
    if mt5.account_info() is None:
        raise ApiErr(f'MT5 started but the account is not available ({last_err()})', 502)
    S['sym'].clear(); S['alias'].clear()
    # learn broker-server time offset from a fresh major-pair tick
    for s in ('EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD'):
        try:
            if mt5.symbol_select(s + SUFFIX, True):
                t = mt5.symbol_info_tick(s + SUFFIX)
                if t: learn_offset(t.time)
                if S['known']: break
        except Exception: pass


def ensure():
    if S['creds'] is None: raise ApiErr('Not logged in to MT5 yet — enter your MT5 login in the dashboard and press Connect', 400)
    if mt5.terminal_info() is None or mt5.account_info() is None:
        do_login(S['creds'])


def learn_offset(tick_time):
    d = tick_time - time.time(); q = round(d / 900) * 900
    if abs(d - q) < 240 and abs(q) <= 14 * 3600: S['offset'] = int(q); S['known'] = True


def resolve(sym):
    sym = str(sym or '').strip()
    if not sym: raise ApiErr('symbol is required')
    key = sym.upper()
    if key in S['sym']: return S['sym'][key]
    tries = [SMAP.get(key), sym, sym + SUFFIX]
    name = next((t for t in tries if t and mt5.symbol_info(t) is not None), None)
    if name is None:
        found = [s.name for s in (mt5.symbols_get(f'*{key[:6]}*') or [])]
        pref = sorted([n for n in found if n.upper().startswith(key)], key=len)
        if pref: name = pref[0]
        else: raise ApiErr(f'Symbol {sym} not found on this broker.' + (f' Similar: {", ".join(found[:8])}' if found else ' Use the search box to see the exact names.'), 404)
    if not mt5.symbol_select(name, True): raise ApiErr(f'Could not enable {name} in Market Watch ({last_err()})', 502)
    S['sym'][key] = name; S['alias'][name] = sym
    return name


def tick(name):
    t = mt5.symbol_info_tick(name)
    if t is None: raise ApiErr(f'No price for {name} ({last_err()})', 404)
    learn_offset(t.time)
    utc = t.time - S['offset']
    si = mt5.symbol_info(name)
    return {'bid': float(t.bid), 'ask': float(t.ask), 'time': int(utc), 'tradeable': bool(si and si.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL and time.time() - utc < 300)}


def account():
    a = mt5.account_info()
    if a is None: raise ApiErr(f'Account info unavailable ({last_err()})', 502)
    return {'balance': a.balance, 'equity': a.equity, 'margin': a.margin, 'freeMargin': a.margin_free, 'currency': a.currency, 'login': str(a.login),
            'name': a.name, 'server': a.server, 'tradeMode': int(a.trade_mode), 'isReal': int(a.trade_mode) == getattr(mt5, 'ACCOUNT_TRADE_MODE_REAL', 2)}


def instrument(sym):
    name = resolve(sym); si = mt5.symbol_info(name)
    step = si.volume_step or 0.01
    return {'symbol': str(sym).upper(), 'brokerSymbol': name, 'digits': int(si.digits), 'pip': round(si.point * 10, int(si.digits) + 1), 'minLot': si.volume_min,
            'maxLot': si.volume_max, 'lotStep': step, 'contract': si.trade_contract_size}


def candles(sym, tf, count):
    if tf not in TF: raise ApiErr('Unsupported timeframe')
    name = resolve(sym)
    try: tick(name)
    except ApiErr: pass
    rates = mt5.copy_rates_from_pos(name, TF[tf], 0, max(1, min(count, 5000)))
    if rates is None or len(rates) == 0: raise ApiErr(f'No candles for {name} ({last_err()})', 404)
    now = time.time(); out = []
    for r in rates:
        t = int(r['time']) - S['offset']
        out.append({'t': t, 'o': float(r['open']), 'h': float(r['high']), 'l': float(r['low']), 'c': float(r['close']), 'v': int(r['tick_volume']), 'complete': t + tf * 60 <= now})
    return out


def positions():
    out = []
    for p in (mt5.positions_get() or []):
        out.append({'id': str(p.ticket), 'symbol': S['alias'].get(p.symbol, p.symbol), 'type': 'BUY' if p.type == 0 else 'SELL', 'lots': p.volume, 'entry': p.price_open,
                    'current': p.price_current, 'pnl': float(p.profit + p.swap), 'sl': p.sl, 'tp': p.tp, 'openTime': int(p.time - S['offset']), 'mine': p.magic == MAGIC})
    return out


def filling(si):
    f = si.filling_mode
    if f & 2: return mt5.ORDER_FILLING_IOC
    if f & 1: return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def send(req):
    r = mt5.order_send(req)
    if r is None: raise ApiErr(f'order_send failed ({last_err()})', 502)
    if r.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
        raise ApiErr(f'MT5 rejected the order: {r.comment} (retcode {r.retcode})', 400)
    return r


def open_market(b):
    name = resolve(b.get('symbol')); si = mt5.symbol_info(name); t = mt5.symbol_info_tick(name)
    lots = float(b.get('lots') or 0); step = si.volume_step or 0.01
    if lots < si.volume_min - 1e-9: raise ApiErr(f'Lot size {lots} is below the minimum {si.volume_min} for {name}')
    vol = min(si.volume_max, round(round(lots / step) * step, 8))
    buy = b.get('type') == 'BUY'
    req = {'action': mt5.TRADE_ACTION_DEAL, 'symbol': name, 'volume': vol, 'type': mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
           'price': t.ask if buy else t.bid, 'deviation': 30, 'magic': MAGIC, 'comment': str(b.get('comment') or 'RT')[:20],
           'type_time': mt5.ORDER_TIME_GTC, 'type_filling': filling(si)}
    if b.get('sl'): req['sl'] = round(float(b['sl']), si.digits)
    if b.get('tp'): req['tp'] = round(float(b['tp']), si.digits)
    r = send(req); pid = r.order
    try:
        d = mt5.history_deals_get(ticket=r.deal)
        if d: pid = d[0].position_id or pid
    except Exception: pass
    return {'id': str(pid), 'price': float(r.price)}


def close(b):
    pos = mt5.positions_get(ticket=int(b.get('id')))
    if not pos: raise ApiErr('Position not found (already closed?)', 404)
    p = pos[0]; si = mt5.symbol_info(p.symbol); t = mt5.symbol_info_tick(p.symbol); buy = p.type == 0
    r = send({'action': mt5.TRADE_ACTION_DEAL, 'symbol': p.symbol, 'volume': p.volume, 'type': mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
              'position': p.ticket, 'price': t.bid if buy else t.ask, 'deviation': 30, 'magic': MAGIC, 'comment': 'RT close',
              'type_time': mt5.ORDER_TIME_GTC, 'type_filling': filling(si)})
    pnl = None
    try:
        d = mt5.history_deals_get(ticket=r.deal)
        if d: pnl = float(d[0].profit + d[0].commission + d[0].swap + getattr(d[0], 'fee', 0))
    except Exception: pass
    return {'pnl': pnl if pnl is not None else float(p.profit), 'price': float(r.price)}


def symbols(q):
    q = (q or '').upper()
    return sorted(s.name for s in (mt5.symbols_get() or []) if not q or q in s.name.upper())[:40]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header('content-type', 'application/json'); self.send_header('content-length', str(len(b))); self.end_headers(); self.wfile.write(b)

    def handle_any(self, method):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = {}
        if method == 'POST':
            n = int(self.headers.get('content-length') or 0)
            if n > 65536: return self.reply(413, {'message': 'too large'})
            try: body = json.loads(self.rfile.read(n) or b'{}')
            except Exception: return self.reply(400, {'message': 'bad json'})
        try:
            p = u.path
            if p == '/health':
                return self.reply(200, {'ok': True, 'loggedIn': S['creds'] is not None, 'terminalInstalled': os.path.exists(TERMINAL) or os.name != 'nt', 'offset': S['offset'], 'offsetKnown': S['known']})
            with LOCK:
                if p == '/login' and method == 'POST':
                    if not (body.get('login') and body.get('password') and body.get('server')): raise ApiErr('MT5 login, password and server are all required')
                    c = {'login': body['login'], 'password': body['password'], 'server': body['server']}
                    do_login(c); S['creds'] = c
                    return self.reply(200, account())
                ensure()
                if p == '/account': r = account()
                elif p == '/instrument': r = instrument(q.get('symbol'))
                elif p == '/tick': r = tick(resolve(q.get('symbol')))
                elif p == '/ticks':
                    r = {}
                    for s in (q.get('symbols') or '').split(',')[:30]:
                        try: r[s.upper()] = tick(resolve(s))
                        except ApiErr: pass
                elif p == '/candles': r = candles(q.get('symbol'), int(q.get('tf') or 60), int(q.get('count') or 300))
                elif p == '/positions': r = positions()
                elif p == '/symbols': r = symbols(q.get('q'))
                elif p == '/order' and method == 'POST': r = open_market(body)
                elif p == '/close' and method == 'POST': r = close(body)
                else: raise ApiErr('unknown endpoint', 404)
            self.reply(200, r)
        except ApiErr as e: self.reply(e.code, {'message': str(e)})
        except Exception as e: self.reply(500, {'message': f'bridge error: {e}'})

    def do_GET(self): self.handle_any('GET')
    def do_POST(self): self.handle_any('POST')


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
