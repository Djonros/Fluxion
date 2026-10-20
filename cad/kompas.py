"""Execute an operation list (``cad.spec``) in KOMPAS-3D through its COM API.

Windows only, needs ``pywin32`` and an installed KOMPAS-3D (checked on v24).
The part is built with API5 (ksPart / ksEntity), which is what KOMPAS's own
macro recorder emits and is stable across versions.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# Type library GUIDs of KOMPAS-3D (same since v17)
_TLB_API5 = "{0422828C-F174-495E-AC5D-D31014DBBE87}"
_TLB_CONST_3D = "{2CAF168C-7961-4B90-9DA2-701419BEEFE3}"

# Fallback values of Kompas6Constants3D when the type library cannot be loaded
_FALLBACK = {
    "pTop_Part": -1,
    "o3d_planeXOY": 1,
    "o3d_planeXOZ": 2,
    "o3d_planeYOZ": 3,
    "o3d_sketch": 5,
    "o3d_edge": 7,
    "o3d_planeOffset": 14,
    "o3d_bossExtrusion": 25,
    "o3d_cutExtrusion": 26,
    "o3d_chamfer": 33,
    "dtNormal": 0,
    "dtReverse": 1,
    "dtBoth": 2,
    "etBlind": 0,
    "etThroughAll": 1,
}

# Default planes as KOMPAS orients them (checked on v24): the constant, the
# sign of the plane normal along its axis (Z, Y, X) and how the global
# coordinates of the plane, (x, y) / (x, z) / (y, z), map to sketch (u, v).
_PLANES = {
    "XOY": ("o3d_planeXOY", 1, lambda a, b: (a, b)),
    "XOZ": ("o3d_planeXOZ", 1, lambda a, b: (a, -b)),
    "YOZ": ("o3d_planeYOZ", -1, lambda a, b: (-b, -a)),
}


class KompasError(RuntimeError):
    """KOMPAS-3D is missing, failed to start or rejected an operation."""


class _Const:
    def __init__(self, module) -> None:
        self._module = module

    def __getattr__(self, name: str):
        if self._module is not None:
            try:
                return getattr(self._module, name)
            except AttributeError:
                pass
        return _FALLBACK[name]


def connect():
    """Return (KompasObject API5, constants 3D); start KOMPAS if needed."""
    if os.name != "nt":
        raise KompasError("КОМПАС-3D работает только в Windows.")
    try:
        import pythoncom
        from win32com.client import Dispatch, gencache
    except ImportError:
        raise KompasError("Нужен пакет pywin32: pip install pywin32") from None

    pythoncom.CoInitialize()  # COM in a worker thread (the desktop app) needs it
    try:
        app = Dispatch("Kompas.Application.5")
    except Exception as exc:
        raise KompasError(f"КОМПАС-3D не найден или не запускается ({exc}).") from None
    kompas = app
    try:
        api5 = gencache.EnsureModule(_TLB_API5, 0, 1, 0)
        kompas = api5.KompasObject(
            app._oleobj_.QueryInterface(api5.KompasObject.CLSID, pythoncom.IID_IDispatch)
        )
    except Exception:
        pass  # late binding still works for everything used below
    try:
        const3d = gencache.EnsureModule(_TLB_CONST_3D, 0, 1, 0).constants
    except Exception:
        const3d = None
    kompas.Visible = True
    return kompas, _Const(const3d)


def _draw(doc2d, shape: dict, to_local) -> None:
    kind, s = next(iter(shape.items()))
    if kind == "circle":
        u, v = to_local(float(s["cx"]), float(s["cy"]))
        doc2d.ksCircle(u, v, float(s["d"]) / 2, 1)
    elif kind == "rect":
        cx, cy, w, h = (float(s[k]) for k in ("cx", "cy", "w", "h"))
        pts = [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
               (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]
        _polyline(doc2d, [to_local(x, y) for x, y in pts])
    elif kind == "polyline":
        _polyline(doc2d, [to_local(float(x), float(y)) for x, y in s["points"]])


def _polyline(doc2d, pts) -> None:
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        doc2d.ksLineSeg(x1, y1, x2, y2, 1)


def _reason(kompas) -> str:
    """KOMPAS's own explanation of the last failed call, as ": ..." or ""."""
    try:
        text = str(kompas.ksStrResult() or "").strip() if kompas.ksReturnResult() else ""
    except Exception:
        text = ""
    return f": {text}" if text else ""


@dataclass
class BuildResult:
    """A built part: the saved file (None when not saved), volume in mm³ and mass in g;
    for a sheet part the flat pattern: its size (length, width, thickness) from
    KOMPAS's unfolding, the clean DXF and what it holds."""

    volume: float | None
    mass: float | None
    path: Path | None = None
    flat_size: tuple[float, float, float] | None = None
    dxf_path: Path | None = None
    dxf_bends: int | None = None
    dxf_holes: int | None = None

    def mass_text(self) -> str:
        if self.mass is None:
            return ""
        return f"{self.mass / 1000:.3g} кг" if self.mass >= 1000 else f"{self.mass:.3g} г"


def _new_sketch(part, c, plane_name: str, offset: float, draw) -> tuple[object, int]:
    """A sketch in a default plane (moved by *offset* along its axis); draw(doc2d, to_local) fills it."""
    const, normal, to_local = _PLANES[plane_name]
    plane = base_plane = part.GetDefaultEntity(getattr(c, const))
    if abs(offset) > 1e-9:
        plane = part.NewEntity(c.o3d_planeOffset)
        pdef = plane.GetDefinition()
        pdef.SetPlane(base_plane)
        pdef.offset = abs(offset)
        pdef.direction = offset * normal > 0  # True moves along the normal
        plane.hidden = True
        plane.Create()
    sketch = part.NewEntity(c.o3d_sketch)
    sdef = sketch.GetDefinition()
    sdef.SetPlane(plane)
    sketch.Create()
    doc2d = sdef.BeginEdit()
    draw(doc2d, to_local)
    sdef.EndEdit()
    return sketch, normal


# ── sheet metal (API7: ISheetMetalContainer of the part) ────────────────────

_TLB_API7 = "{69AC2981-37C0-4379-84FD-5DD2F3C0A520}"
_KS_API7_DUAL = 2        # ksAPI7Dual for KompasObject.TransferInterface
_KS_DOCUMENT_DRAWING = 1  # ksDocumentDrawing
_VT_ARBITRARY = 2         # vt_Arbitrary, a view of any orientation
_KD_DO_NOT_SAVE = 0       # ksDoNotSaveChanges for IKompasDocument.Close


_KS_API5_AUTO = 1         # ksAPI5Auto
_KS_HIDE_MESSAGE_NO = 2   # ksHideMessageNo: KOMPAS answers its own questions with "No"


class _Quiet:
    """While building, KOMPAS answers "save changes?" and the like with "No" by
    itself (API7 Application.HideMessage) instead of stopping on a dialog;
    the user's setting is put back afterwards."""

    def __enter__(self):
        self.app = None
        try:
            from win32com.client import Dispatch

            self.app = Dispatch("Kompas.Application.7")
            self.old = self.app.HideMessage
            self.app.HideMessage = _KS_HIDE_MESSAGE_NO
        except Exception:
            self.app = None  # an older KOMPAS: build anyway
        return self

    def __exit__(self, *exc):
        if self.app is not None:
            try:
                self.app.HideMessage = self.old
            except Exception:
                pass
        return False


def _api7(kompas, obj):
    """The API7 interface of an API5 object (part, sketch, face)."""
    from win32com.client import gencache

    gencache.EnsureModule(_TLB_API7, 0, 1, 0)
    return kompas.TransferInterface(obj, _KS_API7_DUAL, 0)


def _cast(obj, interface: str):
    from win32com.client import CastTo

    return CastTo(obj, interface)


def _sheet_body(kompas, c, part, op: dict, log, i: int):
    """Sheet body from the open profile: bends at every corner, extruded by the width along +Y."""
    points = [(float(x), float(z)) for x, z in op["profile"]]

    def draw(doc2d, to_local):
        local = [to_local(x, z) for x, z in points]
        for (u1, v1), (u2, v2) in zip(local, local[1:]):
            doc2d.ksLineSeg(u1, v1, u2, v2, 1)

    sketch, _ = _new_sketch(part, c, "XOZ", 0.0, draw)
    container = _cast(_api7(kompas, part), "ISheetMetalContainer")
    body = container.SheetMetalBodies.Add()
    body.Sketch = _api7(kompas, sketch)
    body.Thickness = float(op["thickness"])
    body.Radius = float(op["radius"])          # inner bend radius at every bend
    body.BendCoefficient = float(op["k"])       # neutral layer factor K
    body.ThicknessDirection = True              # material on the right of the sketch line
    body.SetSideParameters(True, 0, float(op["width"]), None)  # blind, along the XOZ normal (+Y)
    if not body.Update():
        raise KompasError(f"Операция {i}: КОМПАС не построил листовое тело{_reason(kompas)}.")
    log(f"  {i}. листовое тело: лист {op['thickness']:g} мм, R{op['radius']:g}, K {op['k']:g}, "
        f"сгибов {op['bends']}, ширина {op['width']:g} мм")
    return container


def _sheet_cut(kompas, c, part, op: dict, log, i: int) -> None:
    """Holes/cut-outs in a flange: a sketch on the flange face, cut through all
    in one direction (into the flange and on, where there is no other flange).
    ISheetMetalCut does not remove material in v24, a plain cut does and the
    flat pattern keeps the holes."""
    def draw(doc2d, to_local):
        for shape in op["shapes"]:
            _draw(doc2d, shape, to_local)

    sketch, normal = _new_sketch(part, c, op.get("plane", "XOY"), float(op.get("offset", 0)), draw)
    entity = part.NewEntity(c.o3d_cutExtrusion)
    edef = entity.GetDefinition()
    edef.cut = True
    side = (c.etThroughAll, 0, 0, False)
    # as for "cut": dtNormal goes against the plane normal
    if (1 if op.get("direction", "-") == "+" else -1) == -normal:
        edef.directionType = c.dtNormal
        edef.SetSideParam(True, *side)
    else:
        edef.directionType = c.dtReverse
        edef.SetSideParam(False, *side)
    edef.SetSketch(sketch)
    if not entity.Create():
        raise KompasError(f"Операция {i}: КОМПАС не сделал вырез в листе{_reason(kompas)}.")
    log(f"  {i}. вырез в листе: {len(op['shapes'])} контур(ов)")


def _flat_size(kompas, c, part, container, fixed) -> tuple[float, float, float]:
    """Unfold the sheet (keeping the face at *fixed* in place), measure, fold back.
    Returns (length, width, thickness) of the flat pattern, largest first."""
    faces = part.EntityCollection(c.o3d_face)
    faces.SelectByPoint(*(float(v) for v in fixed))
    if faces.GetCount() < 1:
        raise KompasError("Не нашлась грань, от которой строить развёртку.")
    unfold = container.SheetMetalBendUnfoldParameters
    unfold.FixedFaces = _api7(kompas, faces.GetByIndex(0))
    unfold.Unfold = True
    unfold.UpdateParam()
    part.Update()
    try:
        g = part.GetGabarit(True, True)[1:]
    finally:
        unfold.Unfold = False
        unfold.UpdateParam()
        part.Update()
    sizes = sorted((abs(g[3] - g[0]), abs(g[4] - g[1]), abs(g[5] - g[2])), reverse=True)
    return sizes[0], sizes[1], sizes[2]


def export_flat_dxf(kompas, model_path: Path, dxf_path: Path) -> "dxf.FlatPattern":
    """A drawing with the flat-pattern view of the saved part -> clean DXF.
    The part must be saved and closed (an open changed part makes KOMPAS ask to save)."""
    import tempfile

    from win32com.client import Dispatch

    from . import dxf

    app7 = Dispatch("Kompas.Application.7")
    drawing = app7.Documents.Add(_KS_DOCUMENT_DRAWING, False)  # invisible
    raw = Path(tempfile.mkdtemp(prefix="fluxion_dxf_")) / "kompas.dxf"
    try:
        views = _cast(drawing, "IKompasDocument2D").ViewsAndLayersManager.Views
        view = _cast(views.Add(_VT_ARBITRARY), "IAssociationView")
        view.SourceFileName = str(model_path)
        view.Unfold = True
        view.BendLinesVisible = True
        view.Scale = 1
        if not view.Update():
            raise KompasError("КОМПАС не построил вид развёртки.")
        # SaveAs of an invisible document ignores the .dxf extension and writes
        # a KOMPAS drawing; the API5 export writes a real DXF
        kompas.TransferInterface(drawing, _KS_API5_AUTO, 0).ksSaveToDXF(str(raw))
    finally:
        drawing.Close(_KD_DO_NOT_SAVE)
    try:
        if not raw.exists() or raw.read_bytes()[:2] == b"PK":
            raise KompasError("КОМПАС не сохранил развёртку в DXF.")
        return dxf.convert(raw, dxf_path)
    finally:
        try:
            raw.unlink()
            raw.parent.rmdir()
        except OSError:
            pass


def _sheet_box(kompas, c, part, op: dict, log, i: int):
    """Box/tray with 4 sides: the bottom plate (outer size minus the bend zones)
    and a bend up on each of its top edges; a wall is added outside the plate,
    so neighbouring walls leave the corner open and never meet."""
    t, r = float(op["thickness"]), float(op["radius"])
    gap = r + t
    w, length = float(op["width"]) - 2 * gap, float(op["length"]) - 2 * gap
    flange = float(op["height"]) - gap

    def draw(doc2d, to_local):
        _polyline(doc2d, [to_local(x, y) for x, y in ((0, 0), (w, 0), (w, length), (0, length))])

    sketch, _ = _new_sketch(part, c, "XOY", 0.0, draw)
    container = _cast(_api7(kompas, part), "ISheetMetalContainer")
    body = container.SheetMetalBodies.Add()
    body.Sketch = _api7(kompas, sketch)
    body.Thickness, body.Radius, body.BendCoefficient = t, r, float(op["k"])
    if not body.Update():
        raise KompasError(f"Операция {i}: КОМПАС не построил дно короба{_reason(kompas)}.")
    for x, y in ((w / 2, 0), (w, length / 2), (w / 2, length), (0, length / 2)):
        edges = part.EntityCollection(c.o3d_edge)
        edges.SelectByPoint(x, y, t)
        if edges.GetCount() < 1:
            raise KompasError(f"Операция {i}: не нашлось ребро дна для борта.")
        bend = container.SheetMetalBends.Add()
        bend.BendObjects = [_api7(kompas, edges.GetByIndex(0))]
        bend.Angle = 90.0
        bend.Length = flange       # straight part of the wall after the bend
        bend.Radius = r            # inner radius
        bend.BendCoefficient = float(op["k"])
        if not bend.Update():
            raise KompasError(f"Операция {i}: КОМПАС не построил борт{_reason(kompas)}.")
    log(f"  {i}. короб: дно {op['width']:g}×{op['length']:g}, борта {op['height']:g} мм, лист {t:g}, R{r:g}")
    return container


def build_part(spec: dict, save_path: str | os.PathLike | None = None, log=print) -> BuildResult:
    """Create a new part in KOMPAS from ``spec``; save it when a path is given.
    A sheet part is also unfolded: its flat size is measured and, when saved,
    the flat pattern goes to "<name> — развёртка.dxf" next to the .m3d."""
    kompas, c = connect()
    with _Quiet():
        return _build(kompas, c, spec, save_path, log)


def _build(kompas, c, spec: dict, save_path, log) -> BuildResult:
    doc = kompas.Document3D()
    if not doc.Create(False, True):
        raise KompasError("КОМПАС не создал новую деталь.")
    part = doc.GetPart(c.pTop_Part)
    try:
        part.name = spec.get("name") or "Деталь Fluxion"
    except Exception:
        pass

    sketch = None
    normal = 1  # sign of the last sketch's normal along its plane axis
    container = None  # ISheetMetalContainer when the part is sheet metal
    fixed = None
    for i, op in enumerate(spec["ops"], 1):
        kind = op["op"]
        if kind == "sketch":
            plane_name = op.get("plane", "XOY")
            offset = float(op.get("offset", op.get("z", 0)))

            def draw(doc2d, to_local, shapes=op["shapes"]):
                for shape in shapes:
                    _draw(doc2d, shape, to_local)

            sketch, normal = _new_sketch(part, c, plane_name, offset, draw)
            where = f"на высоте {offset:g} мм" if plane_name == "XOY" else f"в плоскости {plane_name} ({offset:g} мм)"
            log(f"  {i}. эскиз {where}: {len(op['shapes'])} контур(ов)")
        elif kind in ("extrude", "cut"):
            entity = part.NewEntity(c.o3d_bossExtrusion if kind == "extrude" else c.o3d_cutExtrusion)
            edef = entity.GetDefinition()
            if kind == "cut":
                edef.cut = True
            through = bool(op.get("through"))
            depth = float(op.get("depth", 0))
            side = (c.etThroughAll if through else c.etBlind, depth, 0, False)
            direction = op.get("direction", "+" if kind == "extrude" else "-")
            # dtNormal of an extrusion goes along the sketch normal, of a cut
            # against it (into the material); the side parameters must match
            along = normal if kind == "extrude" else -normal
            if direction == "both":
                edef.directionType = c.dtBoth
                edef.SetSideParam(True, *side)
                edef.SetSideParam(False, *side)
            elif (1 if direction == "+" else -1) == along:
                edef.directionType = c.dtNormal
                edef.SetSideParam(True, *side)
            else:
                edef.directionType = c.dtReverse
                edef.SetSideParam(False, *side)
            edef.SetSketch(sketch)
            if not entity.Create():
                raise KompasError(f"Операция {i} ({kind}) не выполнилась в КОМПАСе{_reason(kompas)}.")
            log(f"  {i}. {'выдавливание' if kind == 'extrude' else 'вырез'}"
                f" {'насквозь' if through else f'на {depth:g} мм'}")
        elif kind == "chamfer":
            size = float(op["size"])
            entity = part.NewEntity(c.o3d_chamfer)
            cdef = entity.GetDefinition()
            cdef.tangent = False
            cdef.SetChamferParam(True, size, size)
            chosen = cdef.array()
            for x, y, z in op["points"]:
                edges = part.EntityCollection(c.o3d_edge)
                edges.SelectByPoint(float(x), float(y), float(z))
                if edges.GetCount() < 1:
                    raise KompasError(f"Операция {i}: нет ребра в точке ({x:g}, {y:g}, {z:g}).")
                chosen.Add(edges.GetByIndex(0))
            if not entity.Create():
                raise KompasError(f"Операция {i} (фаска) не выполнилась в КОМПАСе{_reason(kompas)}.")
            log(f"  {i}. фаска {size:g} мм на {len(op['points'])} ребр(ах)")
        elif kind == "sheet":
            container = _sheet_body(kompas, c, part, op, log, i)
            fixed = op.get("fixed")
        elif kind == "sheet_box":
            container = _sheet_box(kompas, c, part, op, log, i)
            fixed = op.get("fixed")
        elif kind == "sheet_cut":
            _sheet_cut(kompas, c, part, op, log, i)

    material = spec.get("material")
    if material:
        # density in g/cm³; KOMPAS shows the name in the part properties and
        # counts the mass with it
        if not part.SetMaterial(material["name"], float(material["density"])):
            raise KompasError(f"КОМПАС не принял материал «{material['name']}»{_reason(kompas)}.")
        log(f"  материал: {material['name']}, {float(material['density']):g} г/см³")
    part.Update()
    result = BuildResult(volume=None, mass=None)
    try:
        props = part.CalcMassInertiaProperties(1)  # ST_MIX_MM: mm³ and grams
        result.volume, result.mass = float(props.v), float(props.m)
    except Exception:
        pass  # mass is a bonus: the part itself is built
    if container is not None and fixed is not None:
        result.flat_size = _flat_size(kompas, c, part, container, fixed)
        log("  развёртка: {:.2f} × {:.2f} мм, толщина {:.2f}".format(*result.flat_size))

    if not save_path:
        return result
    path = Path(save_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # never overwrite: an earlier part may have been edited by hand, and a
    # file still open in KOMPAS cannot be written; take "name (2).m3d" and so on
    for n in range(1, 100):
        target = path if n == 1 else path.with_name(f"{path.stem} ({n}){path.suffix}")
        if target.exists() or (container is not None and _dxf_path(target).exists()):
            continue
        if doc.SaveAs(str(target)):
            result.path = target
            break
    else:
        raise KompasError(f"Не удалось сохранить {path}.")

    if container is not None:
        # the drawing view reads the part from the file: close the part without
        # the "save changes?" question, export, and open the part again
        _api7(kompas, doc).Close(_KD_DO_NOT_SAVE)
        try:
            pattern = export_flat_dxf(kompas, result.path, _dxf_path(result.path))
            result.dxf_path = _dxf_path(result.path)
            result.dxf_bends, result.dxf_holes = pattern.bend_lines, pattern.holes
            log("  DXF развёртки: {} ({} линий сгиба, {} отверстий)".format(
                result.dxf_path.name, pattern.bend_lines, pattern.holes))
        finally:
            kompas.Document3D().Open(str(result.path), False)
    return result


def _dxf_path(model: Path) -> Path:
    return model.with_name(f"{model.stem} — развёртка.dxf")


def default_save_dir() -> Path:
    return Path(os.path.expanduser("~")) / "Documents" / "Fluxion CAD"


def part_path(name: str, folder: str | os.PathLike | None = None) -> Path:
    """Where a part named *name* is saved: <folder or Документы\\Fluxion CAD>\\<name>.m3d."""
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "Деталь"
    return (Path(folder) if folder else default_save_dir()) / f"{safe}.m3d"
