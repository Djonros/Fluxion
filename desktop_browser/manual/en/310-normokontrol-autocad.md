# AutoCAD drawing standards check
<!-- раздел: AutoCAD | инструмент: acad | pro -->
## What it is for

To check a DWG or DXF drawing the way a standards controller would, by ESKD or SPDS.

## How to start

Menu **Design → «AutoCAD drawing standards check…»** or `/автокад C:\path\drawing.dwg` in the chat.

## Step by step

1. Choose the drawing. A DWG is opened as a read-only copy in AutoCAD or with the free ODA File Converter; a DXF is read at once.
2. Mark every remark in the window — then the report is made. The marks are remembered for the drawing.

## What you get

The report `Documents\Fluxion CAD\<name> - нормоконтроль.xlsx`: frame and title block, empty fields, the scale in the title block against the frame or the viewport, scales of GOST 2.302, fonts of GOST 2.304, layer 0, «ByLayer», Defpoints, dimensions with a typed-in value, lost references, objects outside the frame. The remarks are in Russian.

## If it does not work

- A DWG does not open — start AutoCAD or install ODA File Converter.
- From the console: `fluxion-acad.bat "drawing.dwg"` (`--check` — remarks only).
