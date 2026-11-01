# A part in KOMPAS from a description
<!-- раздел: КОМПАС-3D | инструмент: cad -->
## What it is for

To build a simple part in KOMPAS-3D from words: «уголок 50х30 из листа 2 мм, длина 100, 09Г2С» (a bent angle 50×30 from 2 mm sheet, 100 long, 09G2S). Beta.

## How to start

Menu **Tools → «Part in KOMPAS…»** or `/компас description` in the chat.

## Step by step

1. Describe the part: the shape, the sizes in figures, the material.
2. If the main size or the material is missing, Fluxion asks in the chat; «по умолчанию» (default) takes the standard one, «отмена» (cancel) builds nothing.
3. The part is built in your KOMPAS-3D.

## What you get

The part file in `Documents\Fluxion CAD`, and its mass in the answer. For parts bent from sheet a «— развёртка.dxf» flat pattern for the laser is saved next to it.

## If it does not work

- Templates: plate, flange, bush, shaft, angle, nut, bent angle, bracket, U and Z profile, tray, shelf. Fluxion does not build arbitrary geometry or assemblies.
- The material you need is missing — «Materials catalog…».
- Descriptions are understood best in Russian, as the templates and materials are named in Russian.
