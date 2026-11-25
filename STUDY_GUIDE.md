# Study guide: be able to explain every number

The project only helps you if you can explain it without help. A good interviewer will pick one number and ask "where does that come from?" This guide walks through the numbers you are most likely to be asked about, then the interview questions.

Work through it with the Excel calculator open (`excel/reorder_point_calculator.xlsx`). Change the inputs and watch what moves.

---

## Part 1 – One line, end to end

**Crew-neck T-shirt White (B01) at AUH-01, the tourist flagship, on 30 November 2026.**

| Step | Value | Where it comes from |
|---|---:|---|
| Expected demand, d | 8.56 units/day | Average sales over the last 6 weeks, **using only days that ended with stock**, adjusted for the planning calendar (National Day, the weekend pattern) over the next 27 days |
| Daily demand SD, σd | 4.68 units | Standard deviation of daily sales on days when every size was in stock |
| Lead time, L | 27.3 days | Average of Supplier B's actual lead times this year |
| Lead-time SD, σL | 4.2 days | Standard deviation of those lead times |
| Service level | 98% | B01 is an A-class SKU (top 80% of sales value) |
| z | 2.054 | `NORMSINV(0.98)` |
| Safety stock | 89.3 units | `2.054 × √(27.3 × 4.68² + 8.56² × 4.2²)` |
| …if lead time were constant | 50.2 units | `2.054 × 4.68 × √27.3`. The other 39 units exist only because Supplier B is unreliable |
| Reorder point | 323 units | `8.56 × 27.3 + 89.3` = demand during the lead time + safety stock |
| Order-up-to level | 443 units | `323 + 8.56 × 14` = reorder point + two weeks of demand (the review period) |
| Inventory position | 294 units | 56 on hand + 238 on order. No backorders in a shop |
| Decision | **Order 149** | 294 ≤ 323, so order up to 443: `443 − 294 = 149` (the Python model says 150 because it uses unrounded inputs) |

**Things to be able to say:**

* *"Why is lead-time variability such a big part of it?"* Because the term `d² × σL²` grows with the square of demand. For a fast seller, a few days of supplier delay means a lot of units.
* *"What happens if you raise the service level to 99.5%?"* z goes from 2.05 to 2.58, safety stock goes up about 25%. Each extra point of service costs more stock than the one before.
* *"Why order up to ROP + 14 days?"* So you don't reorder every day. The 14 days is the review cycle.

### The size split

The 149 units are split by AUH-01's own size curve (from in-stock-day sales), giving more to the sizes furthest below target. That's why M and L get most of the order. The benchmark policy splits every order by the same buying ratio for every store, which is how a rail ends up with only XS and XXL.

---

## Part 2 – The headline numbers

| Number | What it is | How it's calculated |
|---|---|---|
| **82.4% → 89.9%** fill rate | Share of true demand for replenished items that was actually sold | Units sold ÷ units demanded, Jan–Nov. Only measurable because the data is synthetic |
| **−7.2%** inventory | Average daily stock value at cost, all items | AED 236,300 → AED 219,277 |
| **+AED 265,592** net contribution | Profit measure for comparing the policies | Revenue − cost of goods sold − holding cost (25% a year) − transfer cost − loss on season-end leftovers |
| **+7.6 pts on 6 seeds** | Robustness | Same comparison on five more random seeds: always better, by 8.1–9.0 pts |
| **−25% → −6%** | Forecast bias | Lines out of stock 15–30% of the time: average of all days vs average of in-stock days, compared with true demand |
| **1,930 vs 1,960** | Lost-sales estimate | The model's estimate from observed data vs the true lost units, last 28 days |
| **1.7×** | Supplier C safety stock | Full formula ÷ constant-lead-time formula, averaged over Supplier C items |
| **82% → 99%** summer sell-through | Seasonal result | Units sold ÷ units bought. Driven by transfers from AUH-02 (over-bought) to AUH-01 (under-bought) plus earlier markdowns |

**Be ready to admit the costs too:** the proposed policy spent AED 34k on 453 transfers and raised 43% more order lines (more frequent, smaller orders). The Ramadan capsule made slightly *less* revenue. Saying this before you're asked earns trust.

---

## Part 3 – The five interview questions

### 1. Why did you use synthetic data?

* **Confidentiality.** I couldn't and wouldn't use data from any employer.
* **Reproducibility.** Anyone can rerun the generator with the same seed and get the same data.
* **I know the truth.** Real data never shows lost sales. Because I generated the true demand, I could measure how wrong a sales-based forecast is, which you can't do with real data.
* **The weakness.** I planted the problems myself, so finding them proves little on its own. That's why I framed the test as "does the method find and rank the planted problems?" and published the generator settings.

### 2. What is the difference between sales and demand?

Sales are what you sold. Demand is what customers wanted. They're the same only when you had stock. When a size sells out, sales stop at the stock level, so the record is *censored*. A forecast built on raw sales learns the item sells less than it does, orders less, stocks out again, and the error feeds on itself. My fix was to estimate demand only from days that ended with stock. That cut the bias from −25% to −6% for frequently out-of-stock lines.

### 3. How did you determine safety stock?

Safety stock covers uncertainty during the lead time. There are two sources: demand varies day to day, and the supplier's lead time varies. The formula `z·√(L·σd² + d²·σL²)` covers both. z comes from the target service level, and I set that by ABC class (A 98%, B 95%, C 90%), because a stockout on a top seller costs more. The trade-off is in the service-level chart: going from 95% to 99.5% buys 2.5 points of fill rate for 22% more inventory.

### 4. When would you transfer stock instead of reordering?

When three things are true:
1. The receiving store will run out **before a new order could arrive** (a reorder can't help in time).
2. Another store has **genuine excess**: in my rule, more than 45 days of cover, and it keeps 30.
3. The **margin saved beats the cost of moving it**: AED 60 per shipment + AED 1.5 per unit, against the margin on units likely to sell (80% probability).

For seasonal lines, transfers matter even more because you can't reorder at all. In summer, the model moved 1,071 units from the over-bought family mall to the under-bought flagship.

### 5. How would you deploy this at a real retailer?

* Data: daily sales, stock and open purchase orders from the ERP and POS.
* Master data first: size scales, supplier lead times, pack sizes, which SKUs are basics or seasonal. Bad master data breaks any model.
* Business rules: minimum display quantities, case packs, DC stock, store capacity.
* Process: the model recommends, a planner approves. Track overrides to improve the rules.
* Start small: one category, a few stores, compare against the current process for a season.

---

## Part 4 – Follow-up questions they might ask

**Why not a reorder point per size?** Daily demand for one size in one store is usually 0, 0, 1, 0, 2. The normal distribution behind the formula doesn't fit that. So I plan at style level and split by size curve.

**Why is fill rate below the service level?** A 98% *cycle* service level means 98% of replenishment cycles don't stock out, not that 98% of units are served. Also, the DC can only ship 373 units a day, and with six sizes one size can run out while the style still has stock.

**What is XYZ?** It classifies items by how much weekly sales swing (coefficient of variation): X steady (below 0.5), Y variable, Z erratic. ABC says how important an item is; XYZ says how predictable. An AX item is easy and important; a CZ item might not deserve much safety stock at all.

**Why didn't the Ramadan capsule improve?** It's a short season with a sharp peak in the last ten nights. By the time sell-through data shows a problem, there's little time left to act, and a simple late markdown does about as well. I report that instead of tuning the rules until it looks better, because tuning on the test data would be cheating.

**Why are there no backorders?** In a distribution centre or B2B, an unfilled order waits. In a shop, the customer leaves. So inventory position = on hand + on order + in transit, with nothing subtracted for backorders.

**How did you use AI?** AI helped me write and test the simulator, SQL and dashboard code faster. The replenishment quantities come from the formulas in `src/planning.py`, and I checked them by hand in Excel. Every number in this guide I can recompute.

**What would you do next?** Test the method on a real public dataset, estimate the calendar effects instead of supplying them, add DC stock limits and case packs, and test markdown timing rules across more seasons.
