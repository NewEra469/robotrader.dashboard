# RoboTrader: Node dashboard/server + MetaTrader 5 terminal and bridge under Wine.
# Runs on any host that can run a Docker container (no Windows, no VPS required).
FROM node:22-bookworm-slim AS node

FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive WINEDEBUG=-all DISPLAY=:99 WINEPREFIX=/home/user/.wine \
    PORT=3000 DATA_DIR=/home/user/app/data MT5_BRIDGE_PORT=8765
RUN dpkg --add-architecture i386 && apt-get update && apt-get install -y --no-install-recommends \
      wine wine32:i386 wine64 libwine libwine:i386 xvfb xauth wget ca-certificates cabextract winbind procps \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN useradd -m -u 1000 user && mkdir -p /home/user/app/data && chown -R user /home/user
USER user
WORKDIR /home/user/app
COPY --chown=user package.json server.js mt5_bridge.py start.sh ./
COPY --chown=user src ./src
COPY --chown=user public ./public
RUN chmod +x start.sh

# Build-time: create the Wine prefix, install Windows Python, the MetaTrader5 package and the MT5 terminal.
# Each step may fail here; start.sh retries anything missing at run time.
RUN (Xvfb :99 -screen 0 1024x768x16 & sleep 3; \
     wineboot -i; \
     wget -q -O /tmp/py.exe https://www.python.org/ftp/python/3.9.13/python-3.9.13-amd64.exe && \
       wine /tmp/py.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0 ; \
     wine python -m pip install --no-cache-dir MetaTrader5 ; \
     wget -q -O /tmp/mt5setup.exe https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe && \
       (timeout 300 wine /tmp/mt5setup.exe /auto || true) ; \
     wineserver -k; true)

VOLUME ["/home/user/app/data"]
EXPOSE 3000
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["./start.sh"]
