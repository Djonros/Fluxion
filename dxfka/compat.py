# -*- coding: utf-8 -*-
"""Совместимость: pywin32 (COM) может отсутствовать."""


# --- pywin32: нужен для работы с COM (КОМПАС). Может отсутствовать. ---
PYWIN32_OK = True
try:
    import pythoncom          # noqa: F401 — инициализация COM в потоке
    import pywintypes         # noqa: F401 — типы исключений COM
    from win32com.client import (  # noqa: F401
        Dispatch, GetActiveObject, gencache, CastTo)
    from win32com.client import dynamic as _dynamic
except ImportError:  # pragma: no cover - на машине без pywin32
    PYWIN32_OK = False
    pythoncom = pywintypes = None
    Dispatch = GetActiveObject = gencache = CastTo = _dynamic = None


def dynamic_dispatch(obj):
    """
    Позднее связывание для COM-объекта: обходит раннюю обёртку gen_py,
    в которой может не быть нужных свойств (устаревший кеш).
    """
    if _dynamic is None or obj is None:
        return obj
    try:
        return _dynamic.Dispatch(getattr(obj, "_oleobj_", obj))
    except Exception:
        return obj
