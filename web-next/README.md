# Calibrain: web front end

Next.js 16 (App Router, React 19) built as a **static export** and served by the Python terminal
service at `http://127.0.0.1:8800/`, on the same origin as the API and the `/ws` socket. There is
no Node server in the data path: a Bybit push reaches the screen as Bybit -> Python -> browser.

## Data flow

- `/ws` (WebSocket) pushes engine events the moment they happen (decisions, trades, account,
  news, settings), plus changed tickers, the order book and the forming candle of the watched
  symbol every 100 ms. Candles go straight into the chart without a React render.
- REST is used only for slow-changing data (universe map, macro, headlines, evaluation metrics).
- State lives in a small external store (`lib/store.ts`); each panel subscribes to its own slice,
  so a tick re-renders the cells that show it, not the whole terminal.

## Commands

```bash
npm install
npm run build        # writes ./out, picked up by the Python server on its next start
npm run dev          # http://localhost:3000, talks to the API on 127.0.0.1:8800
```

If the export has not been built, the Python server answers `/` with a short page showing the build command. A new build is picked up on the next page load, without restarting the server.
