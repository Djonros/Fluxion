"""Fluxion CAD: build simple KOMPAS-3D parts from a text description.

Pipeline: description -> model picks a template and its sizes (``text2cad``)
-> template expands to a list of operations (``spec``) -> operations are
executed through the KOMPAS-3D COM API (``kompas``).
"""
