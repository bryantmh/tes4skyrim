"""Move Skyblivion skin weights onto Oblivion bone names."""
import numpy as np


def _split(mesh, split) -> tuple:
    """(rear weights, front weights) of the split bone, ramped over its y range."""
    bone, _rear, _front, (y0, y1) = split
    w = mesh.column(bone)
    t = np.clip((mesh.verts[:, 1] - y0) / (y1 - y0), 0.0, 1.0)
    t = t * t * (3 - 2 * t)
    return w * (1 - t), w * t


def mapped_weights(mesh, skin_map: dict, split) -> tuple:
    """(Oblivion bone names, (V, B) weights) for every vertex of `mesh`.

    `skin_map` {Skyblivion bone: Oblivion bone}; `split` is the
    dreugh_map.SPLIT_BODY tuple for the one bone shared by two targets.
    Weights stay normalized per vertex.
    """
    names, cols = [], []

    def add(bone, w):
        """Accumulate `w` into Oblivion bone `bone`'s column."""
        if bone not in names:
            names.append(bone)
            cols.append(np.zeros(len(mesh.verts)))
        cols[names.index(bone)] += w

    for sk, ob in skin_map.items():
        add(ob, mesh.column(sk))
    rear, front = _split(mesh, split)
    add(split[1], rear)
    add(split[2], front)
    unmapped = set(mesh.bones) - set(skin_map) - {split[0]}
    if unmapped:
        raise ValueError(f'Skyblivion bones with no target: {sorted(unmapped)}')
    w = np.stack(cols, axis=1)
    return names, w / np.maximum(w.sum(1, keepdims=True), 1e-9)
