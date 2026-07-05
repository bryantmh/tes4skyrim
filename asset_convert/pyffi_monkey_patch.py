"""PyFFI 2.2.3 monkey-patches for correct Skyrim NIF (BSStream 83) output.

PyFFI 2.2.3 ships nif.xml version 0.7.1.1.  This module corrects several
field-condition bugs discovered by comparing against the newer nif.xml 0.9.x
used by NifSkope.  All patches must be applied *before* any NIF read or write
operation.

Usage
-----
Import this module at the very top of any file that uses PyFFI, before
importing NifFormat::

    import asset_convert.pyffi_monkey_patch  # apply patches
    from pyffi.formats.nif import NifFormat

The module is idempotent – importing it multiple times is safe.

Summary of patches
------------------
1. time.clock compatibility
   Python 3.8 removed time.clock().  PyFFI uses it internally.  Replaced with
   time.perf_counter().

2. NiPSysGrowFadeModifier.base_scale  (v0.7.1.1 bug)
   PyFFI: userver="11" — field present only when user_version == 11 (Oblivion).
   v0.9 spec: vercond="User Version 2 >= 34" — present when UV2 >= 34 (Skyrim).
   Effect: without the patch, writing a Skyrim NIF (UV1=12) silently omits the
   4-byte base_scale field, shifting all subsequent bytes by 4.  Particles
   appear invisible (size = 0) or crash the engine.
   Fix: clear the userver constraint so the field is always written for
   version >= 20.2.0.7.

3. NiPSysData.unknown_short_1 / unknown_short_2  (v0.7.1.1 bug)
   PyFFI nif.xml line ~2995:
     vercond="!((Version >= 20.2.0.7) && (User Version == 11))"
   This makes the two shorts ABSENT for FO3 (UV1=11) but PRESENT for Skyrim
   (UV1=12), producing 4 extra bytes in Skyrim NiPSysData binary output.
   v0.9 spec: these are "Num Added Particles" and "Added Particles Base" with
     vercond="!((Version == 20.2.0.7) && (User Version 2 > 0))"
   — absent for ALL Bethesda 20.2 formats including Skyrim.
   Fix: change condition to user_version >= 11 so they are absent in both FO3
   and Skyrim when version == 20.2.0.7.
"""

import time as _time

# ---------------------------------------------------------------------------
# Patch 1: time.clock (removed in Python 3.8)
# ---------------------------------------------------------------------------
if not hasattr(_time, 'clock'):
    _time.clock = _time.perf_counter


# ---------------------------------------------------------------------------
# Patches requiring NifFormat (applied lazily on first import)
# ---------------------------------------------------------------------------
_PYFFI_PATCHED = False


def _apply_nifformat_patches(NifFormat):
    """Apply patches to a loaded NifFormat.  Called once after import."""
    from pyffi.object_models.xml.expression import Expression

    # ------------------------------------------------------------------
    # Patch 2: NiPSysGrowFadeModifier.base_scale
    # ------------------------------------------------------------------
    # The field is defined with userver="11" (exact match on UV1=11, Oblivion).
    # For Skyrim (UV1=12) PyFFI omits it entirely, creating a 4-byte hole that
    # the engine misreads.  v0.9 specifies UV2>=34 (i.e. always present for
    # any modern Bethesda NIF).  Clearing userver removes the restriction so
    # the field is written whenever ver1 (20.2.0.7) is satisfied.
    for _attr in NifFormat.NiPSysGrowFadeModifier._attrs:
        if _attr.name == 'base_scale':
            _attr.userver = None
            break

    # ------------------------------------------------------------------
    # Patch 3: NiPSysData.unknown_short_1 / unknown_short_2
    # ------------------------------------------------------------------
    # PyFFI condition: "!((Version >= 20.2.0.7) && (User Version == 11))"
    #   → absent for FO3 (UV1=11), PRESENT for Skyrim (UV1=12).  WRONG.
    # Correct (per v0.9): absent for ALL Bethesda 20.2 (UV2 > 0).
    # We approximate this as user_version >= 11 (excludes both FO3 & Skyrim)
    # which matches the v0.9 semantics for all platforms we care about.
    # Parenthesization is CRITICAL: Expression parses the unparenthesized
    # '! version >= X && ...' as '((!version) >= X) && ...' = always False,
    # which drops the two shorts from OBLIVION reads as well — every source
    # NIF containing NiPSysData then misaligns by 4 bytes and fails to read
    # (the entire fire/effects/magiceffects [RD] failure list).
    _psy_fixed_expr = Expression(
        '!((version >= 335675399) && (user_version >= 11))'
    )
    for _attr in NifFormat.NiPSysData._attrs:
        if _attr.name in ('unknown_short_1', 'unknown_short_2'):
            _attr.vercond = _psy_fixed_expr


def apply_patches():
    """Import NifFormat and apply all patches.  Safe to call multiple times."""
    global _PYFFI_PATCHED
    if _PYFFI_PATCHED:
        return True
    try:
        from pyffi.formats.nif import NifFormat
        _apply_nifformat_patches(NifFormat)
        _PYFFI_PATCHED = True
        return True
    except ImportError:
        return False


# Apply automatically when this module is imported.
apply_patches()
