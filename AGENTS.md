# KiwoomNews Agent Rules

## Stock-name trigger

When the user enters only a Korean stock name, a six-digit Korean stock code, or asks for that
stock's `익일 고가`, `고가 터치`, `+7%`, `+11%`, `+13%`, `+15%`, or `종베` forecast:

1. Extract the exact stock name or six-digit code from the request. For example, use `서산` from
   `서산 월요일 고가?`.
2. Run `python3 scripts/next_high_forecast.py --json "<stock name or code>"` from this repository.
3. Treat its local JSON output as the statistical source of truth. Do not recompute cohorts ad hoc.
4. Report the data cutoff, signal close, pattern/score/bucket, touch probabilities, median high and
   price, empirical range, bucket sample count, live/backfill split, same-stock history, and warnings.
5. Keep signal-time prior, current retro statistics, and forward statistics explicitly separate.
6. If `forecast_valid=false`, say that there is no current radar signal. Never turn an old evaluated
   history row into a new forecast.
7. For a future-session prediction, verify fresh news, disclosures, trading restrictions, and market
   state on the web. Keep those current facts separate from the local statistical estimate.
8. State that daily-high touch is not guaranteed execution and that high/low order is unknown from
   daily bars. Do not present the result as a guaranteed return or place trades automatically.

The analyzer is read-only. Do not change ranking or autotrade policy while answering a stock-name
forecast unless the user separately requests a code change.
