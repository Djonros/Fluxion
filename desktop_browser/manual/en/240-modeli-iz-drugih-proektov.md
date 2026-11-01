# Models from other projects and libraries
<!-- раздел: КОМПАС-3D | инструмент: assembly_report | pro -->
## What it is for

To find the components of an assembly whose files lie outside the product folder: an edit there changes your product, and the file does not go into the archive of the product.

## How to start

These are warnings in the «Before the report» window: **«Model from another project»** and **«Model from a shared library»** (folders «Покупные», «Изделия», «Библиотека»).

## Step by step

1. The row shows the path to the file. **A double click on the path** opens its folder, on the designation — the model itself.
2. It is meant so (a bought item from a library) — mark «Checked»; the mark is remembered.
3. It is not — copy the model into the product folder and replace the component.

## What you get

The product folder is the folder with the product number above the assembly (`…\792720`), otherwise the folder of the assembly itself.

## If it does not work

- Standard items and the KOMPAS libraries are not marked; for a borrowed subassembly only the subassembly itself is.
