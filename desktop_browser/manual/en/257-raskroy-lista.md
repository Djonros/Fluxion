# Sheet nesting
<!-- раздел: КОМПАС-3D | инструмент: nesting | pro -->
## What it is for

To nest the details of an order on sheets by their true shape from DXF: first on the shop's remnants of the same thickness and grade, then on whole sheets. You get a DXF and a picture of every sheet, an Excel report and a list of what to write off and what to add to the remnants.

## How to start

Menu **Production → «Sheet nesting…»** (or «+» → Tools). In the chat: `/раскрой` or `/раскрой path\to\order.xlsx`.

## Step by step

1. **Order** — an Excel sheet as the plant keeps it: columns «Обозначение», «Наименование», «Кол-во на 1 шт.» (or just «Кол-во»), «Материал» like «Лист 8 (09Г2С)», «Лист 3 (3пс) чечевица». Any column order. A «Файл DXF» column with a path and «Поворот» («нет» — do not turn the detail) may be added.
2. **Products** — taken from the file name («762000-5шт…»), editable: quantity = «на 1 шт.» × products.
3. **Remnants** — the Excel list of remnants: «Толщина листа мм» («8 09Г2С», «4 Чеч-цаСт3»), «Габариты мм» («1250х880»), «Кол-во»; «Эскиз», «Цветовая маркировка», «Задействованные» if present. Rows below a «Целые листы» row are whole sheets of the store.
4. **DXF folder** — where to look for flat patterns besides the folder of the order and its subfolders. A file is found by the designation at the start of its name: «544000.2801121-14 - Ребро большое.dxf»; a file in the folder of the right thickness («…\8\») comes first. «Also search the design archive» — when the archive is indexed.
5. Press **«Check the order»**: the table shows each detail, its material, quantity and the DXF found (red — not found or unreadable).
6. Set the whole sheet (6000×1500 by default), the gap between details and the margin, the least remnant and the turns.
7. **Machine** — which machine to write the cutting program for (or «No program — DXF only»). **Cutting**: the kerf, the lead-in and the overcut past the corner (0 — «from the table» of the machine by thickness). **Bridges**: how many on the outer contour, how wide and from what size of a detail (0 — none). **Small holes**: below what diameter they are not cut (0 — all are cut).
8. Press **«Nest»**.

**Machine profile** — the «Machine profile…» button opens its JSON file (like the machine settings of ProNest): the codes of the start and the end of the program and of a cut, the code that holds the torch height before the end of a cut (M62P36 on HASA), the format of numbers and arcs, the technology table by thickness — kerf, feed, pierce, lead-in, overcut and how many mm before the end to hold the height. The edited copy is kept in %APPDATA%\Fluxion\machines and replaces the shipped one. The «HASA Aria MaxCut250» profile is made after a ProNest program and gives it line by line; «Generic G-code» — for Mach3, PUMOTIX, LinuxCNC.

## What you get

In **Documents\Fluxion CAD\Раскрой\<order> <date>** (or your folder):

- **«Лист 8 09Г2С - 01.dxf»** … — the sheet with the contours of the details (layer «РЕЗ»), their designations (layer «МАРКИРОВКА», not cut) and the cut-off line of a new remnant (layer «ОСТАТОК»);
- **a picture of every sheet** (.png) with the numbers of the details and red pierce points;
- **the cutting program** of every sheet («Лист 8 09Г2С - 01.CNC»): holes first, then the outer contour of the detail shifted by half the kerf, a lead-in into a corner from outside, an overcut, bridges, and at the end the cut-off of the remnant («CROP»);
- **«Отчёт раскроя.xlsx»**: sheets (from a remnant or a whole sheet, utilization, mass of the sheet and the details, scrap, cut length, pierces, new remnant, program file and cutting time), details (needed and nested, on which sheets, which DXF, remarks), remnants taken and new, nesting maps;
- **«Остатки — изменения.xlsx»**: which rows of the list to write off and which new remnants to add, in the form of your list. The list itself is not rewritten, so its photos and sketches stay.

A detail goes only on a sheet of the same thickness, grade and kind (checker plate apart); «Ст3», «3пс», «Ст3сп» are one grade. A remnant whose «Задействованные» names another order is not used. The report is in Russian.

## If it does not work

- **«DXF не найден»** — no file whose name starts with the designation. Choose the DXF folder or put the path into the «Файл DXF» column.
- **«несколько контуров рядом»** — two details or a frame in the DXF: one detail per file.
- **«нет замкнутого контура»** — the contour is not closed (gaps over 0.2 mm).
- **«не помещается ни на один лист»** — the detail is bigger than the sheet less the margins: make the sheet bigger.
- **A remnant with a sketch** is taken as the rectangle of its size and marked «есть эскиз — проверьте форму» in the report.
- Bent details («(Г)», «(Г в св)» in the name) are marked: their DXF must be a flat pattern.
- **The feed and kerf in the machine table are approximate** (except 5, 6 and 12 mm, taken from the ProNest programs). Check them against the ProNest table before the first cut: «Machine profile…» → «technology»: thickness, kerf, feed, pierce, lead-in, overcut, height hold.
- The HASA machine does not show Cyrillic in the comments of a program: as ProNest does, they are written as «?».
