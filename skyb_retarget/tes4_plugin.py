"""Write a one-creature TES4 (Oblivion) plugin the converter can export and import.

The record carries the vanilla Land Dreugh's authored values, so the normal
export -> creatures -> import stages build the Skyrim RACE/NPC_/ARMO/ARMA exactly
as they would for Oblivion.esm.  Masterless, so links into Oblivion.esm (spells,
combat style, factions, sounds, loot) are left out.

See: skyb_retarget/README.md#the-test-plugin
"""
import struct

#: FormID of the creature inside the test plugin (load-order index 00).
CREA_FORMID = 0x00000D01

#: Vanilla Land Dreugh (Oblivion.esm 0001FF25) values, per UESP: level 17, health 320, 30 melee.
LAND_DREUGH = {
    'edid': 'SKYBLandDreugh',
    'full': 'Land Dreugh',
    'model': 'Creatures\\LandDreugh\\Skeleton.NIF',
    'parts': ['LandDreugh.NIF', 'Head.NIF', 'TailFin.NIF', 'ClawHair1.NIF',
              'ClawHair2.NIF', 'ClawHair3.NIF', 'ClawHair4.NIF',
              'WingHair1.NIF', 'WingHair2.NIF'],
    'level': 17, 'health': 320, 'attack_damage': 30, 'soul': 4,
    'skills': (60, 40, 30),
    'attributes': (70, 30, 50, 50, 50, 70, 10, 50),
    'aidt': (80, 50, 50, 50),
    'flags': 0x40 | 0x08,
    'reach': 96, 'turning': 0.0, 'scale': 1.0, 'foot_weight': 3.0,
}


def _sub(sig: str, data: bytes) -> bytes:
    """One TES4 subrecord: 4-char type, u16 size, payload."""
    return sig.encode('ascii') + struct.pack('<H', len(data)) + data


def _z(text: str) -> bytes:
    """A null-terminated cp1252 string."""
    return text.encode('cp1252') + b'\x00'


def _record(sig: str, formid: int, payload: bytes, flags: int = 0) -> bytes:
    """A TES4 record: 20-byte header (type, size, flags, formid, vc)."""
    return (sig.encode('ascii') + struct.pack('<IIII', len(payload), flags,
                                              formid, 0) + payload)


def _group(label: str, body: bytes) -> bytes:
    """A top-level GRUP; size counts its own 20-byte header."""
    return (b'GRUP' + struct.pack('<I', len(body) + 20) + label.encode('ascii')
            + struct.pack('<II', 0, 0) + body)


def crea_payload(c: dict) -> bytes:
    """Subrecords of the CREA in Oblivion's authored order."""
    acbs = struct.pack('<IHHHhHH', c['flags'], 0, 0, 0, c['level'], 0, 0)
    aidt = struct.pack('<4BIbB2x', *c['aidt'], 0, 0, 0)
    data = struct.pack('<4BBxH2xH8B', 0, *c['skills'], c['soul'], c['health'],
                       c['attack_damage'], *c['attributes'])
    return b''.join([
        _sub('EDID', _z(c['edid'])), _sub('FULL', _z(c['full'])),
        _sub('MODL', _z(c['model'])), _sub('MODB', struct.pack('<f', 0.0)),
        _sub('NIFZ', b''.join(_z(p) for p in c['parts'])),
        _sub('ACBS', acbs), _sub('AIDT', aidt), _sub('DATA', data),
        _sub('RNAM', bytes([c['reach']])),
        _sub('TNAM', struct.pack('<f', c['turning'])),
        _sub('BNAM', struct.pack('<f', c['scale'])),
        _sub('WNAM', struct.pack('<f', c['foot_weight'])),
    ])


def plugin_bytes(c: dict = None) -> bytes:
    """The whole masterless ESP: TES4 header plus one CREA group."""
    c = c or LAND_DREUGH
    hedr = _sub('HEDR', struct.pack('<fiI', 0.8, 1, CREA_FORMID + 1))
    header = _record('TES4', 0, hedr + _sub('CNAM', _z('skyb_retarget')))
    return header + _group('CREA', _record('CREA', CREA_FORMID,
                                           crea_payload(c)))


def write_plugin(path: str, c: dict = None) -> None:
    """Write the test ESP to `path`."""
    with open(path, 'wb') as f:
        f.write(plugin_bytes(c))
