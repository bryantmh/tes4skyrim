"""Which models are effects (cloud decks, ground mist, smoke), not objects.

Shared by object-LOD selection (`lod_gen`) and the import's Full-LOD rule
(`tes5_import/overrides/ref_state.py`), so both judge a model the same way.

See: docs/commentary/asset_convert_terrain.md#object-lod-selection
"""

#: Model folders holding effect meshes, at any depth below the meshes root.
EFFECT_DIRS = frozenset({'effects', 'fx'})


def is_effect_mesh(model: str) -> bool:
    """True when any folder of `model` is named `effects` or `fx`, in any case."""
    parts = [p for p in (model or '').lower().replace('/', '\\').split('\\') if p]
    return bool(EFFECT_DIRS & set(parts[:-1]))
