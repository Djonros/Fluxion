# Cost estimate and plant norms
<!-- раздел: КОМПАС-3D | инструмент: norms | pro -->
## What it is for

The cost of a product from a KOMPAS-3D assembly: metal with waste, fasteners, bought items, work, overhead and profit — for one product and for a batch.

## How to start

The estimate is made together with the report: **Tools → «Assembly bill of materials and metal weight (Pro)…»**, the **«Смета»** sheet. Norms and prices — **Tools → «Plant norms and prices (Pro)…»**.

## Step by step

1. Open **«Plant norms and prices»**. At first it holds the **recommended norms**: enlarged typical general machine-building time norms (cutting, bending, welding, painting, fitting and assembly) and average metal prices for 2026.
2. Correct any value for your plant. A corrected value is highlighted; right-click a row — «Restore recommended for the row», the «Restore recommended» button — everything at once.
3. **Metal:** the rule is a word looked for in the material of a part («лист», «09г2с», «нерж»); the first one from the top that fits is taken, an empty rule is «everything else». The price per kg and the waste in per cent.
4. **Work:** hours per unit and the rate (0 — the common labour hour rate of the «General» tab). Units: cutting — a part from sheet; bending — a bend (by the build tree: «Сгиб», «Сгиб по линии», «Подсечка»); welding — a metre of seam with a 6 mm leg (a seam with another leg is recalculated: the time grows as the square of the leg); painting — m² to paint; fitting — a part not from sheet; assembly — a component.
5. **Bought items and fasteners:** a part of the name and the price per piece («Ось 35т» — 45000). A fastener without a price is counted by its mass (₽/kg on the «General» tab).
6. **Sheet formats:** the standard sheet up to a thickness, for the purchase list.
7. The table is handy to fill in in Excel: «Export to Excel…», edit, «Import from Excel…». The norms move to another computer the same way.
8. «Save» — and make the assembly report.

## What you get

The **«Смета»** sheet: sections, quantity, price, hours, sum, the total per product. In the row «Изделий в партии» (products in the batch) type your number — the total of the batch follows. The top of the sheet says which norms the estimate is built on: **the recommended ones** or **the plant's**. The chat gets one line with the total and the shares.

## If it does not work

- Rows highlighted in yellow and the **«Не задано»** (not set) list at the bottom of the sheet — no price or norm: fill it in in «Plant norms and prices»; the «Где вписать» column says on which tab.
- «Welding: no data» — the welds of the assembly were not calculated (an assembly without welded units, or KOMPAS did not open them); the estimate does not put a guess in.
- The norms are kept in `%APPDATA%\Fluxion\plant_norms.json`.
