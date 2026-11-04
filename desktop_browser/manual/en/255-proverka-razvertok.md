# Flat pattern check
<!-- раздел: КОМПАС-3D | инструмент: dxf_check | pro -->
## What it is for

To find in DXF flat patterns what a laser or a punch will not make cleanly: a hole smaller than the sheet thickness, a narrow web from a hole to the edge or between holes, a narrow cut-out or slot.

## How to start

By itself — at the end of **«Laser package»**. For any folder of DXF — menu **Production → «Check DXF flat patterns…»**.

## Step by step

1. After «Laser package» the check runs by itself: the thickness is taken from the thickness folder (`…\8\`).
2. For your own folder choose it and, if the thickness is in neither the folders nor the file names («S10», «t8», «лист 12», «8мм»), enter the thickness for the other files (0 — skip them).
3. The limits — **Production → «Plant norms and prices» → the «Sheet» tab**: the least hole diameter, the web to the edge, the web between holes, the width of a cut-out — in sheet thicknesses t. Recommended — 1·t, editable.

## What you get

The chat gets a summary: how many files and remarks of each kind. The folder gets **«Проверка развёрток.xlsx»**: the file, the thickness, what is wrong, where («отверстие ⌀6 ×4»), the size and the limit; the second sheet lists all files and what was not checked. The remarks are in Russian.

## If it does not work

- **Bend lines**: the DXF for the laser has none. «Laser package» saves them apart — in the subfolder «_гибы» of the thickness folder, a file with the same name (not to be cut, not put into the archive for the bot). With them «Check flat patterns» finds: a **hole near a bend** — nearer than 2·t + R to a bend line (it is pulled into an oval; the bend radius R is not in the DXF, R = t is taken), a **bend line across a hole** and a **short flange** — from a bend line to the edge or the next bend less than 3·t (it does not lie on the die). The limits are in «Plant norms and prices» → «Sheet». Flat patterns without a file in «_гибы» skip these checks, the summary says so.
- «No closed contour» — the contour in the DXF is not closed (gaps over 0.2 mm) or is not drawn with lines.
- «Thickness unknown» — no thickness folder and no thickness in the name: run the check from the menu and enter the thickness.
