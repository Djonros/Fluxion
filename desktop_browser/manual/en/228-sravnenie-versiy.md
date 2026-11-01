# Compare assembly versions
<!-- раздел: КОМПАС-3D | инструмент: compare | pro -->
## What it is for

To see what changed between two versions of a product: which positions were added and removed, which changed their quantity, material, mass or section of the specification, and how this affected the mass, metal weight, fasteners, welding, painting and the cost. Handy for a change notice and for purchasing.

## How to start

Menu **Tools → «Compare assembly versions (Pro)…»**.

## Step by step

1. **«Before»** — the old version: an `.a3d` assembly (e.g. a copy from the archive) or a **snapshot of an earlier report** `.json`. The assembly report saves a snapshot next to its Excel workbook («… - снимок.json»), so the old model need not be kept.
2. **«After»** — the new version, also `.a3d` or `.json`.
3. **«Compare»**. Assemblies are opened read-only and invisibly; the models do not change.

## What you get

The workbook «… - изменения.xlsx» in `Documents\Fluxion CAD`:

- **«Итоги»** (totals) — before, after and the difference: the mass of the product, the number of positions, welds, painting, design hours, cost (if both snapshots have it), metal weight by material, fasteners by size.
- **«Изменения»** (changes) — every added (green), removed (red) and changed position.
- **«Покупные»** (bought) — changes of standard and bought items: what to order more or cancel.

The chat gets a short summary: «+5 pos., −2 pos., 7 changed; mass +34.5 kg; cost +12 400 ₽».

## If it does not work

- Positions are matched by designation, without the change notice («792720.2801010 Изм2» is 792720.2801010), and without a designation — by name and model file. If a part got a new number in the new version, it is «removed» and «added».
- The same part in different subassemblies is summed: the quantity per product is compared.
- Snapshots exist from Fluxion 0.25 on — for earlier reports choose the old `.a3d` assembly.
