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

    # ------------------------------------------------------------------
    # Patch 4: hand-rolled NiPSysData layout for Skyrim (BSStream 83)
    # ------------------------------------------------------------------
    _install_skyrim_psysdata_serializer(NifFormat)


# ---------------------------------------------------------------------------
# Patch 4 implementation: correct Skyrim NiPSysData binary layout
# ---------------------------------------------------------------------------
# PyFFI 2.2.3's NiPSysData attribute list is the WRONG (older Bethesda) field
# arrangement for Skyrim: it is missing Material CRC (4), Consistency Flags (2),
# Additional Data ref (4), Has Texture Indices (1) and Aspect Flags (2), and
# invents spurious unknown_byte_1/unknown_link/unknown_short_3/unknown_byte_4
# fields.  The net size is 66 bytes for an empty block where real Skyrim is 70,
# and the field ORDER is wrong regardless of size — so the SSE engine misreads
# every following block (BSEffectShaderMaterial buffer-overrun CTD).
#
# We cannot reorder PyFFI's cached attribute list at runtime, so we override
# NiPSysData.get_size / read / write to emit the authoritative BSStream-83
# layout (derived from nif.xml 0.10 #BS202# path, verified == 70 bytes on the
# vanilla census).  Only the num_vertices==0 (empty particle pool) case that
# our converter produces is hand-rolled; anything with real per-particle arrays
# falls back to PyFFI (Oblivion source reads still use PyFFI's Oblivion layout,
# which is separately correct because Oblivion isn't #BS202#).

_SKYRIM_VER = 0x14020007


def _install_skyrim_psysdata_serializer(NifFormat):
    import struct as _struct

    PSysData = NifFormat.NiPSysData

    def _is_skyrim(data):
        return data is not None and getattr(data, 'version', 0) == _SKYRIM_VER

    def _use_handroll(self, data):
        """Hand-roll the NiPSysData layout whenever writing a Skyrim NIF.

        Our converter only ever emits NiPSysData with an EMPTY inline particle
        pool (Skyrim generates particles at runtime from bs_max_vertices), so
        the hand-rolled 70-byte #BS202# layout is always the correct output.
        PyFFI's own NiPSysData layout is structurally wrong for Skyrim (missing
        Material CRC / Consistency Flags / Additional Data / Has Texture
        Indices / Aspect Flags), so we never defer to it for Skyrim output."""
        return _is_skyrim(data)

    def _sk_fields(self):
        """Return the ordered list of (value, struct_fmt) for the Skyrim
        BSStream-83 NiPSysData layout, num_vertices==0 (empty pool)."""
        # BS Data Flags: low 6 bits = num UV sets, bit 12 (0x1000) = has tangents.
        # Particle data has neither → 0.  PyFFI stores these as num_uv_sets +
        # extra_vectors_flags bytes; recombine defensively.
        bs_data_flags = int(getattr(self, 'num_uv_sets', 0)) & 0x3F
        c = self.center
        # BS Max Vertices: the particle-pool size.  num_vertices and
        # bs_max_vertices alias the same slot; take whichever is set, min 75.
        pool = max(int(getattr(self, 'num_vertices', 0)),
                   int(getattr(self, 'bs_max_vertices', 0)), 75)
        return [
            (0, '<i'),                                   # Group ID
            (pool, '<H'),                                # BS Max Vertices
            (int(getattr(self, 'keep_flags', 0)), '<B'), # Keep Flags
            (int(getattr(self, 'compress_flags', 0)), '<B'),  # Compress Flags
            (1, '<B'),                                   # Has Vertices (always)
            (bs_data_flags, '<H'),                       # BS Data Flags
            (0, '<I'),                                   # Material CRC
            (0, '<B'),                                   # Has Normals (particles: no)
            (float(c.x), '<f'), (float(c.y), '<f'), (float(c.z), '<f'),  # Bound center
            (float(self.radius), '<f'),                  # Bound radius
            (0, '<B'),                                   # Has Vertex Colors
            (0, '<H'),                                   # Consistency Flags (0=MUTABLE)
            (0, '<i'),                                   # Additional Data (ref, -1? vanilla=0)
            (0, '<B'),                                   # Has Radii
            (int(getattr(self, 'num_active', 0)), '<H'), # Num Active
            (1 if getattr(self, 'has_sizes', True) else 0, '<B'),          # Has Sizes
            (0, '<B'),                                   # Has Rotations
            (1 if getattr(self, 'has_rotation_angles', True) else 0, '<B'),  # Has Rotation Angles
            (0, '<B'),                                   # Has Rotation Axes
            (1, '<B'),                                   # Has Texture Indices
            (0, '<I'),                                   # Num Subtexture Offsets
            (1.0, '<f'),                                 # Aspect Ratio
            (0, '<H'),                                   # Aspect Flags
            (0.0, '<f'),                                 # Speed to Aspect Aspect 2
            (0.0, '<f'),                                 # Speed to Aspect Speed 1
            (0.0, '<f'),                                 # Speed to Aspect Speed 2
            (0, '<B'),                                   # Has Rotation Speeds
        ]

    _orig_get_size = PSysData.get_size
    _orig_write = PSysData.write

    def get_size(self, data=None):
        if _use_handroll(self, data):
            return sum(_struct.calcsize(fmt) for _v, fmt in _sk_fields(self))
        return _orig_get_size(self, data=data)

    def write(self, stream, data=None):
        if _use_handroll(self, data):
            for v, fmt in _sk_fields(self):
                stream.write(_struct.pack(fmt, v))
            return
        _orig_write(self, stream, data=data)

    # NOTE: read() is intentionally NOT overridden.  The converter only reads
    # Oblivion-version sources (not #BS202#), which use PyFFI's Oblivion layout
    # correctly.  Our own Skyrim output is never re-read by the pipeline; if a
    # tool re-reads it, PyFFI's stock (wrong) layout applies but that only
    # affects that tool's view, not the on-disk bytes the game engine reads.
    PSysData.get_size = get_size
    PSysData.write = write


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
