# RoboTrader

Automated MetaTrader 5 trading bot with a web dashboard. The bot runs on your server 24/7: it reads candles from MT5, and when a candle **closes above** your chosen moving average it opens a BUY, and when it **closes below** it opens a SELL (the opposite signal closes and reverses). Strategies: Price × MA (EMA/SMA/WMA), MA cross, MACD. Symbols: any forex pair, metal, index or stock your broker offers. Timeframes: M1 to D1.

No Windows install and no VPS are needed on your side. The container runs MT5 under Wine.

## Layout

```
server.js            HTTP + WebSocket server and REST API (no dashboard key)
public/index.html    Web dashboard (single file, no external requests)
src/bot.js           Trading engine (signals, orders, risk, daily-loss stop)
src/strategy.js      Indicators, shared by the server and the dashboard chart
src/brokers/mt5.js   MT5 adapter (talks to the bridge)
src/store.js         State and encrypted credential storage
src/ws.js            WebSocket live prices and events on /ws
mt5_bridge.py        Runs inside Wine next to the MT5 terminal, exposes it on 127.0.0.1:8765
start.sh             Container entry point (virtual display, bridge, then the server)
Dockerfile           Image with Node, Wine, Python and the MT5 terminal
docker-compose.yml   One-command run on any Docker host
```

## Deploy

Any host that runs Docker works: a VM, Render, Railway, Fly.io, Koyeb, or your own machine. Wine plus the MT5 terminal needs a fair amount of memory, so give the container at least 2 GB RAM.

```bash
docker build -t robotrader .
docker run -d --name robotrader --restart unless-stopped \
  -p 3000:3000 -v robotrader-data:/home/user/app/data robotrader
# then open http://<server-address>:3000
```

Or with Compose: `docker compose up -d --build`.

Your host usually sets `PORT` itself; the server listens on whatever `PORT` says (default 3000). Put HTTPS in front of it (most hosts provide it).

The first start takes several minutes while the MT5 terminal installs. The dashboard shows "Connect" once the bridge is ready.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `3000` | HTTP port |
| `DATA_DIR` | `./data` | Where state and the encrypted login are stored. **Mount a persistent volume here.** |
| `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` | empty | Optional: connect automatically at start-up instead of typing them in the dashboard |
| `REQUIRE_LIVE_CONFIRM` | `true` | Ask for confirmation before trading a real-money account |
| `AUTOSTART`, `AUTOSTART_CONFIG` | empty | Optional JSON bot config to start automatically after connecting |
| `CORS_ORIGIN` | empty | Only needed if another website calls the API from a browser |
| `TV_WEBHOOK_SECRET`, `TV_ALLOW_LIVE`, `TV_LOTS` | empty | Optional TradingView webhook (disabled unless the secret is set) |

There is no dashboard API key. See the security note below.

## First use

1. Open the dashboard and enter your MT5 account number, trading password and broker server exactly as shown in your broker's email. Use a **demo account first**.
2. Press **Connect**. Saved logins are encrypted on the server (keep "Save login on the server" ticked if you want the bot to resume after a restart).
3. Choose a symbol and timeframe above the chart. Choose the strategy (default: EMA 50), lot size, stop loss and take profit, and the daily loss limit.
4. Press **Start bot**. Press **Stop bot** to end it. You will be asked whether to keep or close open trades.

## Security

**There is no dashboard password.** Anyone who can open the page can start or stop the bot, place orders, and close positions on the connected account. Before you deploy, do one of these:

- keep the server reachable only from your own network or over a VPN (Tailscale, WireGuard), or
- put it behind your host's access control, or a reverse proxy with login (for example Cloudflare Access, or HTTP basic auth through Caddy or nginx).

Broker credentials are stored encrypted with a random key in `DATA_DIR/.creds-secret`. If that file is lost, saved logins cannot be decrypted and you will need to enter them again.

## Notes

- Free hosts often sleep when idle. A sleeping container stops the bot. Check your host's policy, and choose one that keeps the container running.
- Signals use closed candles only. Trades are sent at market price, with SL and TP set in pips.
- Trading leveraged products carries a high risk of loss.
