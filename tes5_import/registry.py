"""Which converter handles which TES4 record signature.

Separate from `constants.py`: building these tables needs the converter
modules, and a DATA module must never import UP into them. Registration
order is irrelevant -- phase 1 walks `sorted(simple_types)`, so the output
ESM stays byte-reproducible however these are filled.
"""

from .base.constants import FALLOUT_BASE_TYPES

#: TES4 signature -> the function converting one record of that type.
IMPORT_DISPATCH = {}

#: TES4 signature -> the TES5 signature it is written as, when they differ.
TYPE_MAP = {}

#: TES4 signatures deliberately not converted.
SKIP_TYPES = set()

#: NOT records: runtime data keyed by TES3 string id, never read by the importer.
RUNTIME_ONLY_TYPES = frozenset({'MWDI', 'MWIN'})

def _init_dispatch() -> None:
    """Populate the three tables from the converter modules.

    See: docs/reference/record_mapping.md#dispatch-table-membership
    """
    from .record_types.actor_common import (convert_CLAS, convert_FACT)
    from .record_types.common import (convert_GLOB, convert_GMST)
    from .record_types.creature import convert_CREA
    from .record_types.items import (convert_LVLC, convert_LVLI,
                                 convert_LVLN, convert_LVSP)
    from .record_types.npc import (convert_EYES, convert_HAIR, convert_NPC_)
    from .dialogue.converter import (
        convert_DIAL,
        convert_INFO,
    )
    from .dialogue.quest import convert_QUST
    from .record_types.weather import convert_CLMT
    from .record_types.equipment import (
        convert_ALCH,
        convert_AMMO,
        convert_APPA,
        convert_ARMO,
        convert_BOOK,
        convert_CLOT,
        convert_ENCH,
        convert_INGR,
        convert_SGST,
        convert_SPEL,
        convert_WEAP,
    )
    from .record_types.magic import convert_MGEF
    from .record_types.message_falloutnv import convert_MESG
    from .record_types.projectile_falloutnv import convert_PROJ
    from .record_types.impact_falloutnv import convert_IPCT, convert_IPDS
    from .record_types.music_falloutnv import convert_MUSC
    from .record_types.reference_falloutnv import (
        convert_ECZN,
        convert_FLST,
        convert_IMGS,
        convert_LGTM,
        convert_TXST,
    )
    from .record_types.items import (
        convert_ACTI,
        convert_ANIO,
        convert_CONT,
        convert_DOOR,
        convert_FLOR,
        convert_FURN,
        convert_GRAS,
        convert_KEYM,
        convert_LIGH,
        convert_MISC,
        convert_SLGM,
        convert_STAT,
        convert_TREE,
    )
    from .record_types.region import (
        convert_LSCR,
        convert_REGN,
        convert_WATR,
    )
    from .record_types.world import (
        convert_ACHR,
        convert_ACRE,
        convert_CELL,
        convert_EFSH,
        convert_LAND,
        convert_REFR,
        convert_WRLD,
    )
    from .navmesh.from_pgrd import convert_PGRD

    IMPORT_DISPATCH.update({sig: (convert_STAT if kind == 'STAT' else convert_ACTI)
                            for sig, kind in FALLOUT_BASE_TYPES.items()})
    IMPORT_DISPATCH['MESG'] = convert_MESG
    IMPORT_DISPATCH['PROJ'] = convert_PROJ
    IMPORT_DISPATCH['IPCT'] = convert_IPCT
    IMPORT_DISPATCH['IPDS'] = convert_IPDS
    IMPORT_DISPATCH['FLST'] = convert_FLST
    IMPORT_DISPATCH['TXST'] = convert_TXST
    IMPORT_DISPATCH['IMGS'] = convert_IMGS
    IMPORT_DISPATCH['LGTM'] = convert_LGTM
    IMPORT_DISPATCH['ECZN'] = convert_ECZN
    IMPORT_DISPATCH['MUSC'] = convert_MUSC
    IMPORT_DISPATCH.update({
        'STAT': convert_STAT,
        'ACTI': convert_ACTI,
        'MISC': convert_MISC,
        'KEYM': convert_KEYM,
        'DOOR': convert_DOOR,
        'FLOR': convert_FLOR,
        'FURN': convert_FURN,
        'GRAS': convert_GRAS,
        'TREE': convert_TREE,
        'LIGH': convert_LIGH,
        'SLGM': convert_SLGM,
        'ANIO': convert_ANIO,
        'CONT': convert_CONT,
        'SBSP': convert_STAT,
        'WEAP': convert_WEAP,
        'ARMO': convert_ARMO,
        'CLOT': convert_CLOT,
        'AMMO': convert_AMMO,
        'BOOK': convert_BOOK,
        'MGEF': convert_MGEF,
        'ENCH': convert_ENCH,
        'SPEL': convert_SPEL,
        'ALCH': convert_ALCH,
        'INGR': convert_INGR,
        'SGST': convert_SGST,
        'APPA': convert_APPA,
        'NPC_': convert_NPC_,
        'CREA': convert_CREA,
        'FACT': convert_FACT,
        'EYES': convert_EYES,
        'HAIR': convert_HAIR,
        'CLAS': convert_CLAS,
        'GLOB': convert_GLOB,
        'GMST': convert_GMST,
        'LVLI': convert_LVLI,
        'LVLN': convert_LVLN,
        'LVLC': convert_LVLC,
        'LVSP': convert_LVSP,
        'CELL': convert_CELL,
        'WRLD': convert_WRLD,
        'REFR': convert_REFR,
        'ACHR': convert_ACHR,
        'ACRE': convert_ACRE,
        'LAND': convert_LAND,
        'REGN': convert_REGN,
        'LSCR': convert_LSCR,
        'EFSH': convert_EFSH,
        'PGRD': convert_PGRD,
        'QUST': convert_QUST,
        'DIAL': convert_DIAL,
        'INFO': convert_INFO,
        'WATR': convert_WATR,
        'CLMT': convert_CLMT,
    })

    TYPE_MAP.update({
        'CREA': 'NPC_',
        'CLOT': 'ARMO',
        'LVLC': 'LVLN',
        'HAIR': 'HDPT',
        'SGST': 'SCRL',
        'APPA': 'MISC',
        'SBSP': 'STAT',
        'ACRE': 'ACHR',
    })

    SKIP_TYPES.update({
        'ROAD',   # Roads → NavMesh (not enough structured data for conversion)
        'SCPT',   # Scripts → Papyrus
        'SKIL',   # Hardcoded in TES5
        'BSGN',   # Birthsigns → no equivalent
        'RACE',   # NPCs map to Skyrim races
        'CSTY',   # Combat Style -> Completely restructured
        'IDLE',   # Animation system different
        'GMST',   # Game settings differ between TES4/TES5
        'EYES',   # Do not convert — NPCs map to Skyrim head parts
    })

_init_dispatch()
