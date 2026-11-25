# Quality report

| Check | Result | Detail |
|---|---|---|
| Stock balance reconciles on every row | PASS | 0 of 420,840 rows break the balance |
| Opening stock equals previous day's closing | PASS | 0 breaks |
| No negative inventory | PASS | 0 negative rows |
| Sales never exceed stock available that day | PASS | 0 rows |
| Sales = min(demand, stock); lost = demand - sales | PASS | 0 rows violate it |
| Lead times are whole days between 2 and 90 | PASS | range 3-40 days |
| Receipt date - order date = lead time | PASS | checked every received line |
| Receipts table total = receipts in daily fact | PASS | 91,228 vs 91,228 |
| Same seed reproduces identical demand, sales and stock | PASS | fingerprint 0b1a03e90208ee5d |
| Both policies face identical demand on day 1 | PASS | same seed, same random draws |
| Transfers out = transfers in (+ in transit on last day) | PASS | sent 4,621, received 4,557, in transit 64 |
| Raising service level raises inventory (sensitivity) | PASS | 0.85: AED 81,716 -> 0.9: AED 85,971 -> 0.95: AED 94,919 -> 0.98: AED 104,377 -> 0.995: AED 115,977 |
| Raising service level raises fill rate (sensitivity) | PASS | 0.85: 86.6% -> 0.9: 87.6% -> 0.95: 89.1% -> 0.98: 90.5% -> 0.995: 91.6% |
| A 14-day supplier delay lowers fill rate for both policies | PASS | benchmark 82.4% -> 78.4%; proposed 89.9% -> 87.9% |
