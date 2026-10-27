"""σт, σв and E of a material from the Fluxion catalog (cad/materials.json and the user's own).

The calculator takes the materials that have ``strength`` in the catalog; a
user can add a grade with its σт and σв through the catalog file.
"""
from __future__ import annotations

from dataclasses import dataclass

from cad import materials as catalog

from .core import CalcError

E_STEEL = 206_000.0   # MPa, СП 16.13330


@dataclass(frozen=True)
class Strength:
    material: catalog.Material
    yield_: float      # σт, MPa
    ultimate: float    # σв, MPa
    elastic: float     # E, MPa

    @property
    def name(self) -> str:
        return self.material.short

    @property
    def density(self) -> float:
        return self.material.density

    @property
    def alloyed(self) -> bool:
        """Alloy steel (for the scale factor of shafts): a grade with alloying letters, not 08пс or Ст3."""
        short = catalog.compact(self.material.short)
        return any(mark in short for mark in ("х", "г2с", "хн", "нд"))

    def describe(self) -> str:
        text = f"{self.name}: σт {self.yield_:g} МПа, σв {self.ultimate:g} МПа"
        return text + (f" ({self.material.strength_note})" if self.material.strength_note else "")


def with_strength(kinds: tuple[str, ...] | None = None) -> list[catalog.Material]:
    """The catalog materials the calculator can use, of *kinds* first."""
    found = [m for m in catalog.load() if m.has_strength]
    if not kinds:
        return found
    return [m for m in found if m.kind in kinds] + [m for m in found if m.kind not in kinds]


def find(text: str, kinds: tuple[str, ...] | None = None) -> catalog.Material:
    text = (text or "").strip()
    material = None
    for m in catalog.load():   # the exact name or short name first: what the window offers
        if text.casefold() in (m.name.casefold(), m.short.casefold()):
            material = m
            break
    if material is None:
        candidates = with_strength(kinds)
        material = catalog.find(text, candidates, kinds) or catalog.find(text, kinds=kinds)
    if material is None:
        names = ", ".join(m.short for m in with_strength(kinds)[:12])
        raise CalcError(f"материал «{text}» не знаю. Есть: {names}. Свой — в «Каталоге материалов» с σт и σв")
    if not material.has_strength:
        raise CalcError(f"у материала «{material.short}» в каталоге нет σт и σв — добавьте их "
                        f"(Инструменты → «Каталог материалов…», поля strength)")
    return material


def strength(text_or_material, thickness: float | None = None, kinds: tuple[str, ...] | None = None) -> Strength:
    material = text_or_material if isinstance(text_or_material, catalog.Material) else find(text_or_material, kinds)
    yield_, ultimate = material.strength_at(thickness)
    elastic = material.elastic * 1000 if material.elastic else E_STEEL
    return Strength(material, yield_, ultimate, elastic)


def stronger(current: Strength, thickness: float | None = None, same_kind: bool = True) -> list[Strength]:
    """Materials with a higher σт than *current*, weakest first (of the same kind: solid or sheet;
    steel for steel, aluminium for aluminium — by E)."""
    out = []
    for m in with_strength():
        if same_kind and (m.kind == "solid") != (current.material.kind == "solid"):
            continue
        s = strength(m, thickness)
        if abs(s.elastic - current.elastic) / current.elastic > 0.2:
            continue
        if s.yield_ > current.yield_ * 1.05 and m.short != current.material.short:
            out.append(s)
    unique = {}
    for s in sorted(out, key=lambda s: s.yield_):
        unique.setdefault((s.yield_, s.ultimate), s)
    return list(unique.values())
