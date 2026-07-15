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

    # ------------------------------------------------------------------
    # Patch 5-7: early-Oblivion (10.0.1.x / 10.1.0.106) layout support
    # ------------------------------------------------------------------
    _install_early_oblivion_layouts(NifFormat)


# ---------------------------------------------------------------------------
# Patches 5-7: early-Oblivion NIF layout support
# ---------------------------------------------------------------------------
# Oblivion.esm's BSAs contain a handful of development-era meshes saved with
# NIF versions 10.0.1.0 / 10.0.1.2 / 10.1.0.106 instead of the shipping
# 10.2.0.0 (e.g. clutter\floorplane01.nif, clutter\farm\oar01.nif,
# architecture\castle\kvatch\..., oblivion\...\scampswitch01.nif).  PyFFI
# 2.2.3 lacks the version guards these layouts need (verified against
# references/nif 0.10.0.0.xml):
#
# 5. Field-presence guards:
#    - bhkWorldObject: extra "Unknown Int" (4B) after Shape, until 10.0.1.2.
#    - HavokMaterial: extra "Unknown Int" (4B) before the material enum,
#      until 10.0.1.2.  (Affects every bhk shape.)
#    - bhkRigidBody CInfo: the 16-byte header (unknown_2_shorts,
#      havok_col_filter_copy, unknown_6_shorts[0:4]) and the 12-byte
#      max_linear/max_angular/penetration_depth group exist only since
#      10.1.0.0; before that only a 4-byte unused field sits between the
#      contact-callback delay and Translation.
#    - bhkMoppBvTreeShape: the hkpMoppCode Offset vector (pyffi origin+scale,
#      16B) exists only since 10.1.0.0.
# 6. NiSingleInterpController.Interpolator: introduced 10.1.0.104 per the
#    newer nif.xml; pyffi guards it at 10.2.0.0, breaking every controller
#    in 10.1.0.106 files.
# 7. bhkConvexSweepShape: block type used by 10.0.1.0-era clutter
#    (handscythe01, oar01), missing from pyffi entirely.  Registered here;
#    the converter unwraps it to its inner shape (Skyrim never ships it).

_V10_0_1_2 = 0x0A000102
_V10_1_0_0 = 0x0A010000
_V10_1_0_104 = 0x0A010068


def _make_attr(NifFormat, xml_attrs, ver1=None, ver2=None, template=None):
    """Build a resolved StructAttribute from an xml-style attrs dict."""
    from pyffi.object_models.xml import StructAttribute
    attr = StructAttribute(NifFormat, xml_attrs)
    attr.ver1 = ver1
    attr.ver2 = ver2
    if template is not None:
        attr.template = template
    return attr


def _refresh_attribute_caches(NifFormat, changed_classes):
    """Recompute the flattened _attribute_list cache for every NifFormat
    class that inherits from one of changed_classes (the caches are built
    once at class-creation time and hold stale copies after _attrs edits)."""
    for name in dir(NifFormat):
        cls = getattr(NifFormat, name, None)
        if not isinstance(cls, type):
            continue
        if not any(issubclass(cls, c) for c in changed_classes):
            continue
        if hasattr(cls, '_get_attribute_list'):
            cls._attribute_list = cls._get_attribute_list()


def _install_early_oblivion_layouts(NifFormat):
    # --- Patch 5a: bhkWorldObject extra int (until 10.0.1.2) --------------
    wo = NifFormat.bhkWorldObject
    if not any(a.name == 'unknown_int_early' for a in wo._attrs):
        extra = _make_attr(NifFormat,
                           {'name': 'Unknown Int Early', 'type': 'uint'},
                           ver2=_V10_0_1_2)
        # Reference order: Shape, Unknown Int (old), Havok Filter.
        wo._attrs.insert(1, extra)

    # --- Patch 5b: HavokMaterial extra int (until 10.0.1.2) ---------------
    hm = NifFormat.HavokMaterial
    if not any(a.name == 'unknown_int_early' for a in hm._attrs):
        extra = _make_attr(NifFormat,
                           {'name': 'Unknown Int Early', 'type': 'uint'},
                           ver2=_V10_0_1_2)
        hm._attrs.insert(0, extra)

    # --- Patch 5c: bhkRigidBody CInfo fields introduced at 10.1.0.0 -------
    rb = NifFormat.bhkRigidBody
    gate_at_10_1 = ('unknown_2_shorts', 'havok_col_filter_copy',
                    'unknown_6_shorts', 'max_linear_velocity',
                    'max_angular_velocity', 'penetration_depth')
    for a in rb._attrs:
        if a.name in gate_at_10_1 and a.ver1 is None:
            a.ver1 = _V10_1_0_0
    if not any(a.name == 'unused_early' for a in rb._attrs):
        # The 4-byte unused field old files DO have where the 16-byte header
        # would sit (reference bhkRigidBodyCInfo550_660 "Unused 04").
        unused = _make_attr(NifFormat,
                            {'name': 'Unused Early', 'type': 'ushort',
                             'arr1': '2'},
                            ver2=_V10_0_1_2)
        idx = next(i for i, a in enumerate(rb._attrs)
                   if a.name == 'unknown_6_shorts')
        rb._attrs.insert(idx + 1, unused)

    # --- Patch 5d: bhkMoppBvTreeShape offset vector since 10.1.0.0 --------
    for a in NifFormat.bhkMoppBvTreeShape._attrs:
        if a.name in ('origin', 'scale') and a.ver1 is None:
            a.ver1 = _V10_1_0_0
        # pyffi reads "mopp_data_size - 1" bytes for files <= 10.0.1.0, but
        # Bethesda 10.0.1.0 meshes store the FULL size (verified byte-by-byte
        # on ungrdltraphingedoor.nif: the +1 shift lands response=1,
        # delay=0xFFFF and a unit quaternion in the following rigid body).
        # Push the old convention below Bethesda's version range.
        if a.name == 'old_mopp_data':
            a.ver2 = 0x0A000100 - 1
        if a.name == 'mopp_data':
            a.ver1 = 0x0A000100

    # --- Patch 5e: bhkNiTriStripsShape scale vector since 10.1.0.0 --------
    # Reference: "Scale" Vector4 since="10.1.0.0" (pyffi splits it into
    # scale Vector3 + unknown_int_3).  Absent in 10.0.1.x (kvatch castle
    # int hallway01, stonepedastellarge01).
    for a in NifFormat.bhkNiTriStripsShape._attrs:
        if a.name in ('scale', 'unknown_int_3') and a.ver1 is None:
            a.ver1 = _V10_1_0_0

    # --- Patch 6: NiSingleInterpController.interpolator since 10.1.0.104 --
    for a in NifFormat.NiSingleInterpController._attrs:
        if a.name == 'interpolator':
            a.ver1 = _V10_1_0_104

    # --- Patch 6a2: NiPSysEmitterCtlr.visibility_interpolator --------------
    # since="10.1.0.104" per reference nif.xml; pyffi gates it at 10.2.0.0.
    for a in NifFormat.NiPSysEmitterCtlr._attrs:
        if a.name == 'visibility_interpolator':
            a.ver1 = _V10_1_0_104

    # --- Patch 6b: NiInterpController "Manager Controlled" byte -----------
    # Exists only in 10.1.0.104..10.1.0.108 (reference nif.xml); sits between
    # NiTimeController.target and NiSingleInterpController.interpolator.
    # Verified on scampswitch01.nif: bytes ... target=0, 01 (this byte),
    # interpolator=5 (the controller's own blend interpolator).
    ic = NifFormat.NiInterpController
    if not any(a.name == 'manager_controlled' for a in ic._attrs):
        mc = _make_attr(NifFormat,
                        {'name': 'Manager Controlled', 'type': 'byte'},
                        ver1=_V10_1_0_104, ver2=0x0A01006C)
        ic._attrs.insert(0, mc)

    # --- Patch 6c: NiBlendInterpolator pre-10.1.0.108 layout --------------
    # 10.1.0.106 blend interpolators store a full runtime blend-item array:
    #   ArraySize(u16) ArrayGrowBy(u16) Items[ArraySize]{ref,f,f,i32,f}
    #   ManagerControlled(u8) WeightThreshold(f) OnlyUseHighestWeight(u8)
    #   InterpCount(u16) SingleIndex(u16) HighPriority(i32) NextHighPriority(i32)
    # pyffi only knows the 10.1.0.112+ 6-byte layout, so every block after a
    # blend interpolator misparses.  We consume the old layout and leave the
    # pyffi attrs at defaults (Skyrim output writes a fresh blend state; the
    # sub-interpolators stay reachable through the controller sequences).
    # Item refs are deliberately NOT pushed on the link stack — the class has
    # no Ref attrs for fix_links to pop, so pushing would desync all links.
    import struct as _struct2
    _bi = NifFormat.NiBlendInterpolator
    _orig_bi_read = _bi.read

    def _bi_read(self, stream, data=None):
        ver = getattr(data, 'version', 0) if data is not None else 0
        if not (_V10_1_0_104 <= ver <= 0x0A01006B):
            _orig_bi_read(self, stream, data=data)
            return
        n, _grow = _struct2.unpack('<HH', stream.read(4))
        stream.read(n * 20)      # InterpBlendItem[n]: ref,weight,normWeight,priority,easeSpinner
        stream.read(1 + 4 + 1)   # managerControlled, weightThreshold, onlyUseHighest
        stream.read(2 + 2)       # interpCount, singleIndex
        stream.read(4 + 4)       # highPriority, nextHighPriority
        # Subclass value snapshot (byte-verified on scampswitch01.nif — each
        # block ends exactly at the next block's zero tag):
        #   Transform: translation(3f) + rotation quat(4f) + scale(1f) = 32B
        #              + 3 valid-flag bytes = 35
        #   Point3:    value(3f) = 12
        #   Float:     value(1f) = 4      (by the same pattern)
        #   Bool:      value(1B) = 1
        extra = {'NiBlendTransformInterpolator': 35,
                 'NiBlendPoint3Interpolator': 12,
                 'NiBlendFloatInterpolator': 4,
                 'NiBlendBoolInterpolator': 1}.get(type(self).__name__, 0)
        if extra:
            stream.read(extra)

    _bi.read = _bi_read

    # --- Patch 7: register bhkConvexSweepShape ----------------------------
    if not hasattr(NifFormat, 'bhkConvexSweepShape'):
        sweep_attrs = [
            _make_attr(NifFormat, {'name': 'Shape', 'type': 'Ref',
                                   'template': 'bhkShape'},
                       template=NifFormat.bhkShape),
            _make_attr(NifFormat, {'name': 'Material',
                                   'type': 'HavokMaterial'}),
            _make_attr(NifFormat, {'name': 'Radius', 'type': 'float'}),
            _make_attr(NifFormat, {'name': 'Unknown', 'type': 'Vector3'}),
        ]

        # Inherit from bhkShape (no _attrs): bhkConvexShape would inject its
        # inherited material+radius BEFORE our fields and shadow them by name,
        # scrambling the read order.
        class bhkConvexSweepShape(NifFormat.bhkShape):
            _attrs = sweep_attrs
            _is_template = False
            _is_abstract = False

        bhkConvexSweepShape.__name__ = 'bhkConvexSweepShape'
        NifFormat.bhkConvexSweepShape = bhkConvexSweepShape

    # --- Refresh flattened attribute caches -------------------------------
    _refresh_attribute_caches(NifFormat, (NifFormat.bhkWorldObject,
                                          NifFormat.HavokMaterial,
                                          NifFormat.bhkRigidBody,
                                          NifFormat.bhkMoppBvTreeShape,
                                          NifFormat.bhkNiTriStripsShape,
                                          NifFormat.NiSingleInterpController,
                                          NifFormat.NiInterpController,
                                          NifFormat.NiPSysEmitterCtlr))


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
            (1, '<B'),                                   # Has Vertex Colors (810/837 vanilla)
            (0, '<H'),                                   # Consistency Flags (0=MUTABLE)
            (-1, '<i'),                                  # Additional Data (NULL ref; 837/837 vanilla = -1.
                                                         #  0 would REF BLOCK 0 = the root node!)
            (1, '<B'),                                   # Has Radii (837/837 vanilla)
            (int(getattr(self, 'num_active', 0)), '<H'), # Num Active
            (1 if getattr(self, 'has_sizes', True) else 0, '<B'),          # Has Sizes
            (0, '<B'),                                   # Has Rotations
            (1 if getattr(self, 'has_rotation_angles', True) else 0, '<B'),  # Has Rotation Angles
            (0, '<B'),                                   # Has Rotation Axes
            (0, '<B'),                                   # Has Texture Indices — MUST be 0 when
                                                         #  Num Subtexture Offsets is 0: the engine does
                                                         #  rand % count for atlas frame selection →
                                                         #  EXCEPTION_INT_DIVIDE_BY_ZERO in the emitter
                                                         #  update.  0/837 vanilla blocks pair 1 with
                                                         #  count=0 (atlas blocks have count 1..128).
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
    _orig_read = PSysData.read

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

    # Field names in _sk_fields order (parallel to the packed tuples), used by
    # the Skyrim reader to restore attribute values.
    _SK_NAMES = [
        'group_id', 'bs_max_vertices', 'keep_flags', 'compress_flags',
        'has_vertices', 'bs_data_flags', 'material_crc', 'has_normals',
        'cx', 'cy', 'cz', 'radius', 'has_vertex_colors',
        'consistency_flags', 'additional_data', 'has_radii', 'num_active',
        'has_sizes', 'has_rotations', 'has_rotation_angles', 'has_rotation_axes',
        'has_texture_indices', 'num_subtexture_offsets', 'aspect_ratio',
        'aspect_flags', 's2a_a2', 's2a_s1', 's2a_s2', 'has_rotation_speeds',
    ]

    def read(self, stream, data=None):
        """Read the authoritative Skyrim #BS202# NiPSysData layout.

        Handles the variable Subtexture Offsets Vector4 array (vanilla fire has
        real atlas offsets) so vanilla particle NIFs parse for analysis.  The
        Additional Data ref is pushed onto the link stack so PyFFI's fix_links
        pass stays consistent."""
        if not _is_skyrim(data):
            _orig_read(self, stream, data=data)
            return
        fmts = [fmt for _v, fmt in _sk_fields(self)]
        vals = {}
        for name, fmt in zip(_SK_NAMES, fmts):
            n = _struct.calcsize(fmt)
            vals[name] = _struct.unpack(fmt, stream.read(n))[0]
        # NOTE: we deliberately read ONLY the fixed 70-byte prefix (matching
        # get_size).  Vanilla files may carry a Subtexture Offsets array after
        # it; PyFFI's loader compares get_size (70) to the declared block_size
        # and seeks past the remainder, so we must NOT consume it here or the
        # next block starts 16*n bytes early.
        nsub = int(vals['num_subtexture_offsets'])
        subs = []
        # Restore the attributes PyFFI/our code reads back.
        self.num_vertices = 0
        self.bs_max_vertices = vals['bs_max_vertices']
        self.keep_flags = vals['keep_flags']
        self.compress_flags = vals['compress_flags']
        self.has_vertices = True
        self.num_uv_sets = vals['bs_data_flags'] & 0x3F
        self.center.x, self.center.y, self.center.z = vals['cx'], vals['cy'], vals['cz']
        self.radius = vals['radius']
        self.num_active = vals['num_active']
        self.has_sizes = bool(vals['has_sizes'])
        self.has_rotation_angles = bool(vals['has_rotation_angles'])
        if hasattr(self, 'has_subtexture_offset_u_vs'):
            self.has_subtexture_offset_u_vs = bool(vals['has_texture_indices'])
            self.num_subtexture_offset_u_vs = nsub
        # Additional Data ref → link stack (block index or -1).
        add = vals['additional_data']
        if hasattr(data, '_link_stack') and data._link_stack is not None:
            data._link_stack.append(add if add >= 0 else -1)
        # Stash decoded subtex for analysis tooling.
        self._sk_subtex_offsets = subs

    PSysData.get_size = get_size
    PSysData.write = write
    PSysData.read = read


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
