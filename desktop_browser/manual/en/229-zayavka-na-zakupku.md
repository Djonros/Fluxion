# Purchase list
<!-- раздел: КОМПАС-3D | инструмент: purchase | pro -->
## What it is for

What to buy for several products in their quantities: sheets by grade and thickness with the number of sheets, rolled metal and tubes by mass, fasteners and standard items in pieces, bought items — with prices if the plant norms have them.

## How to start

Menu **Tools → «Purchase list (Pro)…»**.

## Step by step

1. **«Add…»** — an `.a3d` assembly or a snapshot of its report `.json` (the assembly report saves one next to its Excel workbook: a snapshot opens at once, without KOMPAS).
2. Every product has **«Quantity, pcs»**: e.g. 3 trailers and 5 carts.
3. **«Make the purchase list»**. Assemblies are opened read-only; the models do not change.

## What you get

The workbook «Заявка на закупку.xlsx» in `Documents\Fluxion CAD`:

- **«Листы»** (sheets) — material and thickness: the area of the parts (by the mass and the density of the material), waste, the area with waste, the sheet format, **the number of sheets** (rounded up), the mass.
- **«Прокат и трубы»** (rolled metal and tubes) — the mass with waste and the number of parts by sortament.
- **«Крепёж и стандартные»**, **«Покупные»** — pieces for the whole order, the price and sum from «Plant norms and prices», a «Поставщик» (supplier) column for purchasing.
- **«Состав заявки»** — which products and how many.

The chat gets a short summary: tonnes of sheet and the number of sheets, tonnes of rolled metal, pieces of fasteners, positions of bought items.

## If it does not work

- Waste by material and the **sheet formats** (by default 1250×2500 up to 4 mm and 1500×6000 thicker) — **Tools → «Plant norms and prices»**, the «Metal» and «Sheet formats» tabs.
- The sheet thickness is taken from the material of the part («Лист 10…»), and without it — from the sheet body.
- Parts without a material (KOMPAS gives a new part «Сталь 10») are listed separately as «material not set», not ordered as steel 10.
- There is no length of rolled metal in metres: the program has no tables of linear mass, and Fluxion does not write a guess.
