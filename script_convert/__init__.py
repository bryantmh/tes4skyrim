"""TES4 -> Papyrus script conversion package.

Deliberately EMPTY of imports: importing any leaf module runs this one, so a re-export here dragged the
whole converter (collision extraction, PyFFI, numpy) into every reader of one small table, the window's
start among them. Import from the submodule (`script_convert.converter.ScriptConverter`).

See: docs/commentary/core_gui.md#splash
"""
