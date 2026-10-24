"""A 2D drawing (AutoCAD DWG/DXF) as plain data for the norm control (cad/acad_check.py).

``read`` takes a DXF file (a DWG is turned into DXF first by cad/acad.py) and
returns a ``Drawing``: the layers, the text styles, the external references and
every object of the model and of each sheet (layout) with its layer, its box and
what the checks need (text and height, dimension value and its override, block
attributes). Reading never changes the file.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

MODEL = "Model"


class DrawingError(RuntimeError):
    """The drawing could not be read: the message is for the designer."""


Box = tuple[float, float, float, float]  # x0, y0, x1, y1


@dataclass
class Entity:
    # DXF type: TEXT, MTEXT, ATTRIB, INSERT, DIMENSION, LINE, LWPOLYLINE, VIEWPORT...;
    # BLOCKTEXT — a text inside a block definition, placed where the block is
    kind: str
    layer: str
    space: str  # MODEL or the name of the sheet (layout)
    box: Box | None = None
    color_bylayer: bool = True
    linetype_bylayer: bool = True
    text: str = ""  # TEXT, MTEXT, ATTRIB: the text; DIMENSION: the override ("" or "<>" — the measured value)
    height: float = 0.0  # text height in drawing units
    style: str = ""  # text style (dimension style for a DIMENSION)
    tag: str = ""  # ATTRIB: the attribute tag
    block: str = ""  # INSERT: the block name
    attribs: dict[str, str] = field(default_factory=dict)  # INSERT: tag (upper case) -> value
    value: float | None = None  # DIMENSION: the value it shows without an override; VIEWPORT: scale
    dim_kind: str = ""  # DIMENSION: linear | angular | diameter | radius | ordinate
    closed: bool = False  # LWPOLYLINE/POLYLINE

    @property
    def x(self) -> float:
        return (self.box[0] + self.box[2]) / 2 if self.box else 0.0

    @property
    def y(self) -> float:
        return (self.box[1] + self.box[3]) / 2 if self.box else 0.0


@dataclass
class Layer:
    name: str
    color: int = 7
    linetype: str = "Continuous"
    on: bool = True
    frozen: bool = False
    plot: bool = True

    @property
    def printed(self) -> bool:
        return self.on and not self.frozen and self.plot and self.name.lower() != "defpoints"


@dataclass
class Xref:
    name: str
    path: str
    found: bool


@dataclass
class Drawing:
    file: str  # the DWG or DXF the designer chose
    layers: dict[str, Layer] = field(default_factory=dict)
    styles: dict[str, str] = field(default_factory=dict)  # text style -> font (file or family)
    entities: list[Entity] = field(default_factory=list)
    spaces: list[str] = field(default_factory=list)  # MODEL first, then the sheets in tab order
    used_layers: set[str] = field(default_factory=set)  # also inside the blocks
    xrefs: list[Xref] = field(default_factory=list)

    @property
    def title(self) -> str:
        return Path(self.file).name if self.file else "Чертёж"

    def in_space(self, space: str) -> list[Entity]:
        return [e for e in self.entities if e.space == space]

    def texts(self, space: str | None = None) -> list[Entity]:
        return [e for e in self.entities
                if e.kind in ("TEXT", "MTEXT", "ATTRIB") and (space is None or e.space == space)]


def _box(entity) -> Box | None:
    from ezdxf import bbox

    try:
        ext = bbox.extents([entity], fast=True)
    except Exception:  # an xref block, a broken entity
        return None
    if not ext.has_data:
        return None
    return (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)


_DIM_KINDS = {0: "linear", 1: "linear", 2: "angular", 3: "diameter", 4: "radius", 5: "angular", 6: "ordinate"}


def _dimension(e, out: Entity) -> None:
    out.dim_kind = _DIM_KINDS.get(e.dimtype & 7, "linear") if hasattr(e, "dimtype") else "linear"
    out.text = e.dxf.get("text", "") or ""
    out.style = e.dxf.get("dimstyle", "")
    value = e.dxf.get("actual_measurement")
    if value is None:
        try:
            value = e.get_measurement()
        except Exception:
            value = None
    if value is not None and not isinstance(value, (int, float)):
        value = None  # ordinate: a point
    if value is not None:
        if out.dim_kind == "angular":
            value = math.degrees(value)
        else:
            try:
                from ezdxf.entities import DimStyleOverride

                value *= float(DimStyleOverride(e).get("dimlfac", 1.0) or 1.0)
            except Exception:
                pass
    out.value = value


def _block_texts(insert, space: str, depth: int = 0) -> list[Entity]:
    """The texts drawn inside a block (a frame with its stamp labels), where they are on the sheet:
    the stamp is often a block with the labels and the values written over it."""
    out: list[Entity] = []
    try:
        parts = list(insert.virtual_entities())
    except Exception:  # an xref, a non-uniform scale ezdxf cannot transform
        return out
    for v in parts:
        kind = v.dxftype()
        if kind == "TEXT":
            out.append(Entity("BLOCKTEXT", v.dxf.get("layer", "0"), space, _box(v), text=v.dxf.get("text", ""),
                              height=v.dxf.get("height", 0.0), style=v.dxf.get("style", "Standard")))
        elif kind == "MTEXT":
            out.append(Entity("BLOCKTEXT", v.dxf.get("layer", "0"), space, _box(v), text=v.plain_text(),
                              height=v.dxf.get("char_height", 0.0), style=v.dxf.get("style", "Standard")))
        elif kind == "INSERT" and depth < 3:
            out.extend(_block_texts(v, space, depth + 1))
    return out


def _entity(e, space: str) -> list[Entity]:
    kind = e.dxftype()
    out = Entity(kind, e.dxf.get("layer", "0"), space, _box(e),
                 color_bylayer=e.dxf.get("color", 256) == 256 and not e.dxf.hasattr("true_color"),
                 linetype_bylayer=str(e.dxf.get("linetype", "BYLAYER")).upper() == "BYLAYER")
    extra: list[Entity] = []
    if kind == "TEXT":
        out.text, out.height, out.style = e.dxf.get("text", ""), e.dxf.get("height", 0.0), e.dxf.get("style", "Standard")
    elif kind == "MTEXT":
        out.text, out.height = e.plain_text(), e.dxf.get("char_height", 0.0)
        out.style = e.dxf.get("style", "Standard")
    elif kind == "INSERT":
        out.block = e.dxf.name
        for a in e.attribs:
            tag = (a.dxf.get("tag", "") or "").strip().upper()
            out.attribs[tag] = a.dxf.get("text", "") or ""
            extra.append(Entity("ATTRIB", a.dxf.get("layer", out.layer), space, _box(a), text=out.attribs[tag],
                                height=a.dxf.get("height", 0.0), style=a.dxf.get("style", "Standard"), tag=tag))
        extra.extend(_block_texts(e, space))
    elif kind == "DIMENSION":
        _dimension(e, out)
    elif kind == "VIEWPORT":
        view_h = e.dxf.get("view_height", 0.0)
        out.value = e.dxf.get("height", 0.0) / view_h if view_h else None
        c, w, h = e.dxf.center, e.dxf.get("width", 0.0), e.dxf.get("height", 0.0)
        out.box = (c.x - w / 2, c.y - h / 2, c.x + w / 2, c.y + h / 2)
    elif kind == "LWPOLYLINE":
        out.closed = bool(e.closed)
    elif kind == "POLYLINE":
        out.closed = bool(e.is_closed)
    return [out] + extra


def read(dxf_path: str | Path, file: str | None = None) -> Drawing:
    """*file*: the DWG the DXF was made from (for the names in the report)."""
    try:
        import ezdxf
        from ezdxf import recover
    except ImportError as exc:
        raise DrawingError("Не установлена библиотека ezdxf: выполните «pip install ezdxf» "
                           "(fluxion-acad.bat ставит её сам).") from exc
    path = Path(dxf_path)
    try:
        doc, _auditor = recover.readfile(str(path))
    except (OSError, ezdxf.DXFStructureError) as exc:
        raise DrawingError(f"Не удалось прочитать {path.name}: {exc}") from exc
    drawing = Drawing(str(file or path))
    for layer in doc.layers:
        drawing.layers[layer.dxf.name] = Layer(layer.dxf.name, abs(layer.dxf.get("color", 7)),
                                               layer.dxf.get("linetype", "Continuous"), layer.is_on(),
                                               layer.is_frozen(), bool(layer.dxf.get("plot", 1)))
    for style in doc.styles:
        font = style.dxf.get("font", "") or ""
        try:
            family = style.get_extended_font_data()[0]
        except Exception:
            family = ""
        drawing.styles[style.dxf.name] = family or font
    base = Path(drawing.file).parent
    for block in doc.blocks:
        record = block.block_record
        if record is not None and record.is_xref:
            xpath = block.block.dxf.get("xref_path", "") or ""
            found = bool(xpath) and ((base / xpath).exists() or Path(xpath).exists())
            drawing.xrefs.append(Xref(block.name, xpath, found))
            continue
        for e in block:
            drawing.used_layers.add(e.dxf.get("layer", "0"))
    layouts = [doc.modelspace()] + [doc.layouts.get(name) for name in doc.layouts.names_in_taborder()
                                    if name.lower() != "model"]
    for layout in layouts:
        space = MODEL if layout.is_modelspace else layout.name
        drawing.spaces.append(space)
        for e in layout:
            if e.dxftype() == "VIEWPORT" and e.dxf.get("id", 0) == 1:  # the sheet itself, not a view of the model
                continue
            drawing.entities.extend(_entity(e, space))
    drawing.used_layers.update(e.layer for e in drawing.entities)
    return drawing
