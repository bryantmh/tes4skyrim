"""FormID resolution: which record a property name refers to.

Lookups against the plugin graph, moved out of `constants.py` so that stays
data.
"""

from core.worldspace_names import source_worldspace_edid


def resolve_property_formid(xref, prop_name: str) -> str:
    """EditorID lookup for a (possibly sanitized) property name.

    Reverses each rename `safe_property_name` can apply, in order: the exact
    `d<digits>` leading-digit prefix, the plain name, the legacy digit-DELETING
    scheme, the reserved-word `my` prefix, and the `<Name>Base` ActorBase
    de-collision.  An unreversed rename leaves the property None at runtime.

    A renamed worldspace (`TES4Tamriel`) is reversed first, to the source name.

    See: docs/commentary/script_convert.md#worldspace-property-rename
    """
    low = source_worldspace_edid(prop_name, xref.worldspace_renames)
    fid = ''
    if low.startswith('d') and len(low) > 1 and low[1].isdigit():
        fid = xref.edid_to_formid.get(low[1:], '')
    if not fid:
        fid = xref.edid_to_formid.get(low, '')
    if not fid:
        fid = digit_stripped_formid(xref, low)
    if not fid and low.startswith('my'):
        fid = xref.edid_to_formid.get(low[2:], '')
    if not fid and len(low) > 4 and low.endswith('base'):
        fid = xref.edid_to_formid.get(low[:-4], '')
    return fid


#: Record types that can never BE a property; used to break digit collisions.
_NON_PROPERTY_SIGS = frozenset(
    {'INFO', 'LAND', 'PGRD', 'ROAD', 'NAVM', 'NAVI', 'GMST', 'LTEX', 'REGN',
     'SKIL', 'LSCR', 'ANIO', 'IDLE', 'SCPT', 'SBSP', 'LVLC', 'CLMT', 'WATR',
     'DIAL'}
)


def digit_stripped_formid(xref, low: str) -> str:
    """FormID for a property name whose leading digits were stripped.

    Legacy: the old sanitiser DELETED leading digits, and ~1,400
    Morroblivion declarations still resolve only through this reversal.
    Types that can never BE a property are excluded first, settling 315 of
    337 collisions; the rest bind to nothing rather than guess.
    """
    if not low[:1].isalpha():
        return ''
    rev = getattr(xref, '_digit_stripped_edids', None)
    if rev is None:
        record_type = getattr(xref, 'record_type', None) or {}
        rev = {}
        for edid_low, edid_fid in xref.edid_to_formid.items():
            if not edid_low[:1].isdigit():
                continue
            if record_type.get(edid_fid, '') in _NON_PROPERTY_SIGS:
                continue
            stripped = edid_low.lstrip('0123456789')
            if stripped:
                rev[stripped] = '' if stripped in rev else edid_fid
        try:
            xref._digit_stripped_edids = rev
        except AttributeError:
            pass
    return rev.get(low, '')
