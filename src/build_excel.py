"""Transparent reorder-point workbook: excel/reorder_point_calculator.xlsx"""
import json
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
d = json.loads((ROOT / "outputs/dashboard_data.json").read_text())
act = pd.read_csv(ROOT / "outputs/actions_basics.csv")
sizes = pd.read_csv(ROOT / "outputs/actions_basics_sizes.csv")
we = d["worked_example"]

F = "Arial"
BLUE, BLACK, GREEN = Font(name=F, color="0000FF"), Font(name=F), Font(name=F, color="008000")
B = Font(name=F, bold=True)
H1 = Font(name=F, bold=True, size=14)
NOTE = Font(name=F, italic=True, color="666666", size=9)
YEL = PatternFill("solid", fgColor="FFFF00")
HEAD = PatternFill("solid", fgColor="E8EEF7")
thin = Border(bottom=Side(style="thin", color="CCCCCC"))

wb = Workbook()

# ------------------------------------------------------------------ README
rd = wb.active
rd.title = "Read me"
lines = [
    ("Reorder-point calculator - Retail Floor to Planning Desk", H1),
    ("Synthetic data. Stores, SKUs and suppliers are hypothetical; numbers come from the project's seeded simulation.", NOTE),
    ("", None),
    ("How to use", B),
    ("1. Go to the Calculator sheet. Change any blue cell on a yellow background. Everything else is a formula.", BLACK),
    ("2. The sheet recomputes safety stock, reorder point, order-up-to level and today's order quantity.", BLACK),
    ("3. The Action list sheet repeats the same formulas for every replenished store-SKU on 30 Nov 2026 and checks them against the Python model.", BLACK),
    ("", None),
    ("Colour code", B),
    ("Blue text = input you can change", BLUE),
    ("Black text = formula", BLACK),
    ("Green text = pulled from another sheet", GREEN),
    ("", None),
    ("The formulas", B),
    ("z = NORMSINV(service level)", BLACK),
    ("Safety stock SS = z * SQRT(L * sd_d^2 + d^2 * sd_L^2)   (demand AND lead-time uncertainty)", BLACK),
    ("If lead time were constant (sd_L = 0) this becomes z * sd_d * SQRT(L)", BLACK),
    ("Reorder point ROP = d * L + SS", BLACK),
    ("Order-up-to level S = ROP + d * review period", BLACK),
    ("Inventory position = on hand + on order (no backorders: in a shop an unserved customer is a lost sale)", BLACK),
    ("Order today if inventory position <= ROP; quantity = S - inventory position, rounded up", BLACK),
    ("", None),
    ("Units", B),
    ("d and sd_d in units per day; L, sd_L and review period in days; money in AED.", BLACK),
    ("Use this for continuously replenished basics only. Seasonal lines are bought once and managed with transfers and markdowns, not reorder points.", BLACK),
]
for i, (t, f) in enumerate(lines, 1):
    c = rd.cell(row=i, column=1, value=t)
    if f:
        c.font = f
rd.column_dimensions["A"].width = 120

# ------------------------------------------------------------------ CALCULATOR
ws = wb.create_sheet("Calculator")
ws["A1"] = "Reorder-point calculator"; ws["A1"].font = H1
ws["A2"] = f"Example pre-filled: {we['name']} at {we['store_id']}, snapshot 30 Nov 2026 (synthetic). Change the blue cells."
ws["A2"].font = NOTE

inputs = [
    ("Store", we["store_id"], None, "Example line from the model's action list"),
    ("SKU", f"{we['sku_id']} {we['name']}", None, ""),
    ("Expected demand per day, d (units)", we["d_forecast"], "0.00", "Model: average sales on in-stock days over 6 weeks, adjusted for the planning calendar"),
    ("Daily demand standard deviation, sd_d (units)", we["sd_daily"], "0.00", "Model: SD of daily sales on days every size was in stock"),
    ("Lead time, L (days)", we["lead_time_days"], "0.0", "Model: mean of this supplier's actual lead times this year"),
    ("Lead-time standard deviation, sd_L (days)", we["lead_time_sd_days"], "0.0", "Model: SD of this supplier's actual lead times"),
    ("Service level (cycle)", we["service_level"], "0.0%", "Set by ABC class: A 98%, B 95%, C 90%"),
    ("Review period (days)", 14, "0", "Order-up-to covers one more review cycle"),
    ("On hand (units)", we["on_hand"], "#,##0", "Store stock on the snapshot date"),
    ("On order (units)", we["on_order"], "#,##0", "Open purchase orders not yet received"),
    ("Unit cost (AED)", float(pd.read_csv(ROOT / "config/sku_master.csv").set_index("sku_id").loc[we["sku_id"], "unit_cost"]), "#,##0.00", "From the SKU master"),
    ("Unit margin (AED)", we["unit_margin_aed"], "#,##0.00", "Price minus cost"),
]
ws["A4"] = "Inputs"; ws["A4"].font = B
r0 = 5
for i, (lab, val, fmt, note) in enumerate(inputs):
    r = r0 + i
    ws.cell(row=r, column=1, value=lab).font = BLACK
    c = ws.cell(row=r, column=2, value=val)
    c.font = BLUE; c.fill = YEL
    if fmt:
        c.number_format = fmt
    ws.cell(row=r, column=3, value=note).font = NOTE
# names for readability
ref = {"d": "$B$7", "sd": "$B$8", "L": "$B$9", "sL": "$B$10", "p": "$B$11", "R": "$B$12",
       "oh": "$B$13", "oo": "$B$14", "cost": "$B$15", "margin": "$B$16"}

ws["A18"] = "Results"; ws["A18"].font = B
res = [
    ("z (standard normal value for the service level)", f"=NORMSINV({ref['p']})", "0.000"),
    ("Safety stock, demand + lead-time uncertainty (units)", f"={{z}}*SQRT({ref['L']}*{ref['sd']}^2+{ref['d']}^2*{ref['sL']}^2)", "#,##0.0"),
    ("Safety stock if lead time were constant (units)", f"={{z}}*{ref['sd']}*SQRT({ref['L']})", "#,##0.0"),
    ("Extra safety stock caused by lead-time variability (units)", "={ss}-{ss0}", "#,##0.0"),
    ("Demand over the lead time, d x L (units)", f"={ref['d']}*{ref['L']}", "#,##0.0"),
    ("Reorder point, ROP (units)", "={dl}+{ss}", "#,##0.0"),
    ("Order-up-to level, S (units)", f"={{rop}}+{ref['d']}*{ref['R']}", "#,##0.0"),
    ("Inventory position = on hand + on order (units)", f"={ref['oh']}+{ref['oo']}", "#,##0"),
    ("Order today?", '=IF({ip}<={rop},"YES - order","No - hold")', None),
    ("Order quantity (units)", "=IF({ip}<={rop},ROUNDUP({S}-{ip},0),0)", "#,##0"),
    ("Days of cover on hand", f"=IF({ref['d']}>0,{ref['oh']}/{ref['d']},0)", "0.0"),
    ("Safety stock value at cost (AED)", f"={{ss}}*{ref['cost']}", "#,##0"),
    ("Order value at cost (AED)", f"={{q}}*{ref['cost']}", "#,##0"),
]
keys = ["z", "ss", "ss0", "ssx", "dl", "rop", "S", "ip", "ord", "q", "doc", "ssval", "qval"]
cells = {k: f"$B${19 + i}" for i, k in enumerate(keys)}
for i, (lab, f, fmt) in enumerate(res):
    r = 19 + i
    ws.cell(row=r, column=1, value=lab)
    c = ws.cell(row=r, column=2, value=f.format(**cells))
    c.font = B if keys[i] in ("rop", "q", "ord") else BLACK
    if fmt:
        c.number_format = fmt
ws.cell(row=22, column=3, value="This is why the simple textbook formula is not enough when suppliers are unreliable.").font = NOTE

# size split
ws["A34"] = "Split the order across sizes"; ws["A34"].font = B
ws["A35"] = "Each store's own size curve from in-stock-day sales. Need = S x share - on hand - on order (floored at 0). Order is shared in proportion to need."
ws["A35"].font = NOTE
hdr = ["Size", "Size share", "On hand", "On order", "Target (S x share)", "Need", "Order (rounded)"]
for j, h in enumerate(hdr, 1):
    c = ws.cell(row=36, column=j, value=h); c.font = B; c.fill = HEAD
sz = sizes[(sizes.store_id == we["store_id"]) & (sizes.sku_id == we["sku_id"])].reset_index(drop=True)
for i, row in sz.iterrows():
    r = 37 + i
    ws.cell(row=r, column=1, value=row["size"])
    for col, v, fmt in [(2, row["size_share"], "0.0%"), (3, int(row["on_hand"]), "#,##0"), (4, int(row["on_order"]), "#,##0")]:
        c = ws.cell(row=r, column=col, value=v); c.font = BLUE; c.fill = YEL; c.number_format = fmt
    ws.cell(row=r, column=5, value=f"={cells['S']}*B{r}").number_format = "#,##0.0"
    ws.cell(row=r, column=6, value=f"=MAX(0,E{r}-C{r}-D{r})").number_format = "#,##0.0"
    last = 37 + len(sz) - 1
    ws.cell(row=r, column=7, value=f"=IF(SUM($F$37:$F${last})>0,ROUND({cells['q']}*F{r}/SUM($F$37:$F${last}),0),ROUND({cells['q']}*B{r},0))").number_format = "#,##0"
tr = 37 + len(sz)
ws.cell(row=tr, column=1, value="Total").font = B
for col in "BCDEFG":
    c = ws[f"{col}{tr}"]; c.value = f"=SUM({col}37:{col}{tr-1})"; c.font = B
    c.number_format = "0.0%" if col == "B" else "#,##0"
ws.cell(row=tr + 1, column=1, value="Rounding can move the total by a unit or two; the Python model uses largest-remainder rounding so its split always adds up exactly.").font = NOTE

# sensitivity
sr = tr + 3
ws.cell(row=sr, column=1, value="Service-level sensitivity (same line, other service levels)").font = B
for j, h in enumerate(["Service level", "z", "Safety stock (units)", "Reorder point (units)", "Safety stock value (AED)", "Extra vs 90% (AED)"], 1):
    c = ws.cell(row=sr + 1, column=j, value=h); c.font = B; c.fill = HEAD
levels = [0.85, 0.90, 0.95, 0.98, 0.99, 0.995]
for i, p in enumerate(levels):
    r = sr + 2 + i
    c = ws.cell(row=r, column=1, value=p); c.font = BLUE; c.fill = YEL; c.number_format = "0.0%"
    ws.cell(row=r, column=2, value=f"=NORMSINV(A{r})").number_format = "0.000"
    ws.cell(row=r, column=3, value=f"=B{r}*SQRT({ref['L']}*{ref['sd']}^2+{ref['d']}^2*{ref['sL']}^2)").number_format = "#,##0.0"
    ws.cell(row=r, column=4, value=f"={ref['d']}*{ref['L']}+C{r}").number_format = "#,##0.0"
    ws.cell(row=r, column=5, value=f"=C{r}*{ref['cost']}").number_format = "#,##0"
    ws.cell(row=r, column=6, value=f"=E{r}-$E${sr+3}").number_format = "#,##0;(#,##0);-"
ws.cell(row=sr + 2 + len(levels), column=1,
        value="Each step up in service level buys less extra availability for more stock: this is the trade-off the What-if page shows.").font = NOTE

ws.column_dimensions["A"].width = 56
ws.column_dimensions["B"].width = 18
ws.column_dimensions["C"].width = 16
for col in "DEFG":
    ws.column_dimensions[col].width = 16
ws.column_dimensions["C"].width = 70
ws.freeze_panes = "A4"

# ------------------------------------------------------------------ ACTION LIST
al = wb.create_sheet("Action list")
al["A1"] = "All replenished store-SKUs on 30 Nov 2026 - inputs from the model, results recomputed by Excel formulas"
al["A1"].font = B
al["A2"] = "Blue = inputs exported from the Python model. Black = Excel formulas. 'Check' compares Excel's reorder point with Python's (should be ~0)."
al["A2"].font = NOTE
cols = ["Store", "SKU", "Name", "Supplier", "ABC", "d (units/day)", "sd_d", "L (days)", "sd_L (days)", "Service level",
        "On hand", "On order", "z", "Safety stock", "Reorder point", "Order-up-to", "Inventory position",
        "Order qty", "Python ROP", "Check (Excel - Python)", "Priority score (AED)"]
for j, h in enumerate(cols, 1):
    c = al.cell(row=4, column=j, value=h); c.font = B; c.fill = HEAD
    c.alignment = Alignment(wrap_text=True, vertical="top")
a = act.sort_values("priority_score_aed", ascending=False).reset_index(drop=True)
for i, rw in a.iterrows():
    r = 5 + i
    vals = [rw.store_id, rw.sku_id, rw["name"], rw.supplier_id, rw.abc, rw.d_forecast, rw.sd_daily, rw.lead_time_days,
            rw.lead_time_sd_days, rw.service_level, int(rw.on_hand), int(rw.on_order)]
    for j, v in enumerate(vals, 1):
        c = al.cell(row=r, column=j, value=v); c.font = BLUE
    al.cell(row=r, column=10).number_format = "0.0%"
    al.cell(row=r, column=13, value=f"=NORMSINV(J{r})").number_format = "0.000"
    al.cell(row=r, column=14, value=f"=M{r}*SQRT(H{r}*G{r}^2+F{r}^2*I{r}^2)").number_format = "#,##0.0"
    al.cell(row=r, column=15, value=f"=F{r}*H{r}+N{r}").number_format = "#,##0.0"
    al.cell(row=r, column=16, value=f"=O{r}+F{r}*Calculator!$B$12").number_format = "#,##0.0"
    al.cell(row=r, column=17, value=f"=K{r}+L{r}").number_format = "#,##0"
    c = al.cell(row=r, column=18, value=f"=IF(Q{r}<=O{r},ROUNDUP(P{r}-Q{r},0),0)"); c.number_format = "#,##0"; c.font = B
    c = al.cell(row=r, column=19, value=float(rw.reorder_point)); c.font = BLUE; c.number_format = "#,##0.0"
    al.cell(row=r, column=20, value=f"=O{r}-S{r}").number_format = "0.0;-0.0;-"
    c = al.cell(row=r, column=21, value=float(rw.priority_score_aed)); c.font = BLUE; c.number_format = "#,##0"
last = 4 + len(a)
al.cell(row=last + 2, column=1, value="Totals").font = B
al.cell(row=last + 2, column=18, value=f"=SUM(R5:R{last})").number_format = "#,##0"
al.cell(row=last + 2, column=17, value="Units to order:")
al.cell(row=last + 3, column=17, value="Largest check gap:")
al.cell(row=last + 3, column=18, value=f"=MAX(MAX(T5:T{last}),-MIN(T5:T{last}))").number_format = "0.00"
al.cell(row=last + 4, column=1, value="Inputs are rounded to 2 decimals on export, so Excel and Python can differ by a fraction of a unit on the reorder point and by a unit on some order quantities. Review period links to the Calculator sheet (green would mark a cross-sheet link).").font = NOTE
for j in range(1, len(cols) + 1):
    al.column_dimensions[get_column_letter(j)].width = 13
al.column_dimensions["C"].width = 30
al.column_dimensions["D"].width = 9
al.column_dimensions["E"].width = 6
al.freeze_panes = "D5"
al.auto_filter.ref = f"A4:U{last}"
for r in range(5, last + 1):
    al.cell(row=r, column=16).font = GREEN

out = ROOT / "excel/reorder_point_calculator.xlsx"
wb.save(out)
print("saved", out)
