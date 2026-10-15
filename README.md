# gtc-prices

Price feed for the team's Bloomberg Global Trading Challenge 2026 tracker.

- `tickers.txt` – Bloomberg tickers (as in TMSG) to track
- `fetch_prices.py` – pulls prices + FX from Yahoo Finance, runs in GitHub Actions every 30 min on weekdays
- `data/latest.json`, `data/history.json` – output
- `relay/build_docs.py` – turns the output into the tracker page's data

Run the job by hand: Actions → prices → Run workflow.
