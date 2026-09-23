"""FO3/FNV addon nodes: a mesh's `BSValueNode` names an ADDN by index.

Skyrim resolves the node's value against every loaded ADDN's `DATA` index,
and Skyrim.esm already owns 0-88, so FNV's own 0-36 would spawn vanilla
effects. Both the converted ADDN records and every mesh's addon nodes move
to `addon_index`.
See: docs/commentary/tes4_export_falloutnv.md#addon-nodes
"""

#: Offset added to a FO3/FNV ADDN index, clear of the vanilla and DLC indexes.
ADDON_INDEX_BASE = 20000
_PREFIX = b'AddOnNode'


def addon_index(index: int) -> int:
    """The Skyrim ADDN index of a FO3/FNV one."""
    return ADDON_INDEX_BASE + index


def remap_addon_nodes(data) -> int:
    """Move every `AddOnNode<N>` BSValueNode (name and value) to its
    converted index; returns the number moved. Matched by class name, so
    the importer can share `addon_index` without loading pyffi."""
    moved = 0
    for block in data.blocks:
        if type(block).__name__ == 'BSValueNode' and block.value < ADDON_INDEX_BASE:
            block.value = addon_index(block.value)
            if block.name.startswith(_PREFIX):
                block.name = _PREFIX + str(block.value).encode()
            moved += 1
    return moved
