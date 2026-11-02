# Assembly bill of materials and metal weight
<!-- раздел: КОМПАС-3D | инструмент: assembly_report | pro -->
## What it is for

From a finished KOMPAS-3D assembly — the summary bill of materials, metal weight, welding, painting and design hours in one Excel workbook, with a standards check before the report.

## How to start

Menu **Production → «Assembly bill of materials and metal weight…»**, choose the `.a3d` file.

## Step by step

1. Fluxion opens the assembly **read-only** and invisibly; the model does not change.
2. The window **«Before the report»** opens: bought items not in «Other items», no material, no designation, zero mass, models from other projects, and for sheet parts — a sheet body thicker or thinner than the sheet of the material and, on parts with bends, a bend radius too small (for steel — less than the sheet thickness, for duralumin — less than 2.5 thicknesses).
3. Mark every row «Checked». For bought items clear «Bought» if it is made by you. Many identical remarks — pick a row and «Mark all such» (or right-click).
4. **«Make the report»** is enabled when everything is marked. The marks are remembered for the product.
5. A double click on a row opens the model in KOMPAS.

## What you get

An Excel workbook in `Documents\Fluxion CAD`: warnings, the summary bill of materials as a tree, parts, metal weight, other items, welding, painting, design hours, **«Технологичность»** (manufacturability) and **«Смета»** (the cost estimate, see «Cost estimate and plant norms»). Designations are links to the files. A snapshot of the report («… - снимок.json») is saved next to it for «Compare assembly versions» and the purchase list.

The **«Технологичность»** sheet has advice that does not hold the report up:

- **Fasteners:** bolts, screws, studs, nuts and washers by type and size — how many positions and pieces per product. Hints: close lengths of one diameter («M12: 35 and 40 mm — check whether one will do»), one fastener under different standards, different strength classes. Fluxion does not know the grip of a joint, so this is only «check».
- **Twin parts:** parts with the same material, mass and area under different designations — possibly one part drawn twice. Executions «-01» are not twins.

The summary of manufacturability is also in the chat message.

## If it does not work

- «Cancel — I will fix the model» keeps the marks made.
- The design hour norms of the department — `%APPDATA%\Fluxion\time_norms.json`.
- To fix bought items and materials right in the model — see «Fix in the model and Set material».
- The texts of the report and of the remarks are in Russian.
