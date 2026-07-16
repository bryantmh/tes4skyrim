#!/usr/bin/env python3
"""Dump decoded TES4 CTDA conditions from the text export.

Usage:
    python tools/tes4_ctda_dump.py --dial Recommendation          # all INFOs of a topic
    python tools/tes4_ctda_dump.py --dial 00007B2F --export export/Knights.esp
    python tools/tes4_ctda_dump.py --quest MG00Join               # QUST's own conditions
    python tools/tes4_ctda_dump.py --info 0001C0E3                # one INFO

Decodes each 24-byte Condition[i].Raw: operator, comparison value, function
(by name), params (resolved to EditorIDs where possible), flags.
"""
import argparse
import struct
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from tes5_import.text_reader import parse_export_file  # noqa: E402

# TES4 condition function names (from wbDefinitionsTES4.pas, the ones that
# matter for dialogue debugging; unknown indices print as Func<N>).
FUNC_NAMES = {
    1: 'GetDistance', 5: 'GetLocked', 6: 'GetPos', 8: 'GetAngle',
    10: 'GetStartingPos', 12: 'GetSecondsPassed', 14: 'GetActorValue',
    18: 'GetCurrentTime', 24: 'GetScale', 27: 'GetLineOfSight',
    32: 'GetInSameCell', 35: 'GetDisabled', 36: 'MenuMode', 39: 'GetDisease',
    40: 'GetVampire', 41: 'GetClothingValue', 42: 'SameFaction',
    43: 'SameRace', 44: 'SameSex', 45: 'GetDetected', 46: 'GetDead',
    47: 'GetItemCount', 48: 'GetGold', 49: 'GetSleeping',
    50: 'GetTalkedToPC', 53: 'GetScriptVariable', 56: 'GetQuestRunning',
    58: 'GetStage', 59: 'GetStageDone', 60: 'GetFactionRankDifference',
    61: 'GetAlarmed', 62: 'IsRaining', 63: 'GetAttacked', 64: 'GetIsCreature',
    65: 'GetLockLevel', 66: 'GetShouldAttack', 67: 'GetInCell',
    68: 'GetIsClass', 69: 'GetIsRace', 70: 'GetIsSex', 71: 'GetInFaction',
    72: 'GetIsID', 73: 'GetFactionRank', 74: 'GetGlobalValue',
    75: 'IsSnowing', 76: 'GetDisposition', 77: 'GetRandomPercent',
    79: 'GetQuestVariable', 80: 'GetLevel', 81: 'GetArmorRating',
    84: 'GetDeadCount', 91: 'GetIsAlerted', 98: 'GetPlayerControlsDisabled',
    99: 'GetQuestCompleted', 101: 'IsWeaponOut', 102: 'IsTorchOut',
    103: 'IsShieldOut', 106: 'IsFacingUp', 107: 'GetKnockedState',
    108: 'GetWeaponAnimType', 109: 'GetWeaponSkillType',
    110: 'GetCurrentAIPackage', 111: 'IsWaiting', 112: 'IsIdlePlaying',
    116: 'GetCrimeGold', 122: 'GetCrime', 125: 'GetIsPlayableRace',
    126: 'GetOffersServicesNow', 127: 'CanPayCrimeGold',
    128: 'GetFatiguePercentage', 129: 'GetPCIsRace', 130: 'GetPCIsSex',
    131: 'GetPCInFaction', 132: 'SameFactionAsPC', 133: 'SameRaceAsPC',
    134: 'SameSexAsPC', 135: 'GetIsReference', 136: 'IsTalking',
    138: 'GetTimeDead', 139: 'GetPlayerTeammate', 149: 'GetCadence',
    153: 'IsSwimming', 161: 'GetWindSpeed', 163: 'GetCurrentAIProcedure',
    166: 'IsWeaponSkillType?', 167: 'GetInCellParam', 169: 'CanHaveFlames',
    170: 'HasFlames', 171: 'IsPlayerInJail', 172: 'GetTalkedToPCParam',
    175: 'GetPCSleepHours', 176: 'SameCell', 180: 'GetIsUsedItemType',
    182: 'GetEquipped', 185: 'IsSneaking', 186: 'IsRunning',
    189: 'GetFriendHit', 190: 'IsInCombat', 193: 'GetPCExpelled',
    195: 'GetPCFactionMurder', 197: 'GetPCEnemyofFaction',
    199: 'GetPCFactionAttack', 201: 'GetPCFactionSubmitAuthority',
    203: 'GetDestroyed', 214: 'HasMagicEffect', 215: 'GetDefaultOpen',
    223: 'IsSpellTarget', 224: 'GetIsPlayerBirthsign',
    225: 'GetPersuasionNumber', 227: 'HasVampireFed', 228: 'GetIsClassDefault',
    229: 'GetClassDefaultMatch', 230: 'GetInCellParam',
    237: 'GetIsGhost', 242: 'GetUnconscious', 244: 'GetRestrained',
    246: 'GetIsUsedItem', 247: 'GetIsUsedItemEquipType', 249: 'GetPCFame',
    251: 'GetPCInfamy', 254: 'GetIsPlayerGrabbedRef', 258: 'IsCarryable',
    259: 'GetConcussed', 264: 'GetBarterGold', 265: 'IsTimePassing',
    266: 'IsPleasant', 267: 'IsCloudy', 274: 'GetArmorRatingUpperBody',
    277: 'GetBaseActorValue', 278: 'IsOwner', 280: 'IsCellOwner',
    282: 'IsHorseStolen', 285: 'IsLeftUp', 286: 'IsSneakingParam?',
    305: 'GetInWorldspace', 306: 'GetPCMiscStat', 309: 'GetWalkSpeed',
    310: 'GetCurrentAIProcedure', 312: 'GetIsFlying', 313: 'IsFlying',
    323: 'GetIgnoreFriendlyHits', 327: 'IsPlayerLastRiddenHorse',
    329: 'GetIsInList?', 332: 'GetIsUsedItemType',
    353: 'IsActor', 354: 'IsEssential', 358: 'GetPlayerMovingIntoNewSpace',
    361: 'GetTimeDead', 362: 'GetPlayerHasLastRiddenHorse', 365: 'IsChild',
}

_OPS = {0x00: '==', 0x20: '!=', 0x40: '>', 0x60: '>=', 0x80: '<', 0xA0: '<='}

# Functions whose param1 is NOT a FormID (literal ints/enums).
_NON_FORM_P1 = {14, 18, 36, 47, 50, 77, 80, 108, 109, 110, 125, 128, 130,
                163, 175, 225, 277, 306, 309}


def build_fid_index(export_dir: Path) -> dict:
    """FormID(hex str) -> 'EDID (SIG)' from every export file present."""
    idx = {}
    for txt in export_dir.glob('*.txt'):
        if txt.name in ('FormID_Mapping.txt',):
            continue
        try:
            for rec in parse_export_file(str(txt)):
                fid = rec.get('FormID')
                if fid:
                    idx[fid.upper()] = (f"{rec.get('EditorID', '?') or '?'}"
                                        f" ({rec.get('Signature', txt.stem)})")
        except Exception:
            continue
    return idx


def fmt_fid(v: int, idx: dict) -> str:
    h = f'{v:08X}'
    name = idx.get(h)
    return f'{h}[{name}]' if name else h


def decode(raw_hex: str, idx: dict) -> str:
    raw = bytes.fromhex(raw_hex)
    raw = raw + b'\0' * max(0, 24 - len(raw))
    t = raw[0]
    comp_f = struct.unpack_from('<f', raw, 4)[0]
    comp_i = struct.unpack_from('<I', raw, 4)[0]
    func = struct.unpack_from('<I', raw, 8)[0]
    p1 = struct.unpack_from('<I', raw, 12)[0]
    p2 = struct.unpack_from('<I', raw, 16)[0]
    op = _OPS.get(t & 0xE0, f'op{t >> 5}')
    flags = []
    if t & 0x01:
        flags.append('OR')
    if t & 0x02:
        flags.append('RunOnTarget')
    if t & 0x04:
        flags.append('UseGlobal')
    fname = FUNC_NAMES.get(func, f'Func{func}')
    comp = (fmt_fid(comp_i, idx) if t & 0x04 else f'{comp_f:g}')
    parts = [fname + '(']
    args = []
    if p1 or func in (72, 71, 58, 59, 56, 79, 53, 74, 67, 68, 69, 73):
        args.append(fmt_fid(p1, idx) if func not in _NON_FORM_P1 else str(p1))
    if p2:
        args.append(fmt_fid(p2, idx) if p2 > 0xFFFF else str(p2))
    parts.append(', '.join(args))
    parts.append(f') {op} {comp}')
    return ''.join(parts) + (('   [' + ','.join(flags) + ']') if flags else '')


def dump_rec(rec: dict, idx: dict, prefix: str = ''):
    i = 0
    while True:
        raw_hex = rec.get(f'{prefix}Condition[{i}].Raw')
        if raw_hex is None:
            break
        try:
            print(f'    C{i}: {decode(raw_hex, idx)}')
        except Exception as e:
            print(f'    C{i}: <decode error {e}> {raw_hex}')
        i += 1


def main():
    ap = argparse.ArgumentParser(description='Decode TES4 CTDA conditions')
    ap.add_argument('--export', default=str(_ROOT / 'export' / 'Oblivion.esm'))
    ap.add_argument('--dial', help='DIAL EditorID or FormID: dump all its INFOs')
    ap.add_argument('--info', help='One INFO FormID')
    ap.add_argument('--quest', help='QUST EditorID or FormID (own conditions + targets)')
    args = ap.parse_args()

    export_dir = Path(args.export)
    print('Indexing EditorIDs...', file=sys.stderr)
    idx = build_fid_index(export_dir)

    if args.dial:
        dials = parse_export_file(str(export_dir / 'DIAL.txt'))
        want = args.dial.upper()
        dial_fid = None
        for d in dials:
            if (d.get('EditorID', '').upper() == want
                    or d.get('FormID', '').upper() == want.zfill(8)):
                dial_fid = d.get('FormID')
                print(f"DIAL {d.get('EditorID')} ({dial_fid}) "
                      f"FULL='{d.get('FULL', '')}' Type={d.get('DATA.Type')} "
                      f"quests={[d.get(f'Quest[{i}]') for i in range(int(d.get('QuestCount', 0) or 0))]}")
                break
        if not dial_fid:
            sys.exit(f'DIAL {args.dial} not found')
        for rec in parse_export_file(str(export_dir / 'INFO.txt')):
            if rec.get('ParentDIAL') != dial_fid:
                continue
            print(f"\n  INFO {rec.get('FormID')} quest={fmt_fid(int(rec.get('QSTI.Quest', '0') or '0', 16), idx)} "
                  f"flags={rec.get('DATA.Flags')}")
            txt = rec.get('Response[0].ResponseText', '')
            print(f"    \"{txt[:100]}\"")
            dump_rec(rec, idx)
            if rec.get('ResultScript'):
                first = rec['ResultScript'].split('\r\n')[0]
                print(f'    Result: {first} ...')

    if args.info:
        want = args.info.upper().zfill(8)
        for rec in parse_export_file(str(export_dir / 'INFO.txt')):
            if rec.get('FormID', '').upper() == want:
                print(f"INFO {want} dial={rec.get('ParentDIAL')} "
                      f"quest={rec.get('QSTI.Quest')}")
                print(f"  \"{rec.get('Response[0].ResponseText', '')[:120]}\"")
                dump_rec(rec, idx)
                if rec.get('ResultScript'):
                    print('  Result: ' + rec['ResultScript'][:300])

    if args.quest:
        want = args.quest.upper()
        for rec in parse_export_file(str(export_dir / 'QUST.txt')):
            if (rec.get('EditorID', '').upper() == want
                    or rec.get('FormID', '').upper() == want.zfill(8)):
                print(f"QUST {rec.get('EditorID')} ({rec.get('FormID')}) "
                      f"flags={rec.get('DATA.Flags')} priority={rec.get('DATA.Priority')} "
                      f"SCRI={rec.get('SCRI', '-')}")
                dump_rec(rec, idx)
                t = 0
                while rec.get(f'Target[{t}].FormID') is not None:
                    print(f"  Target[{t}] -> {fmt_fid(int(rec.get(f'Target[{t}].FormID', '0') or '0', 16), idx)}")
                    dump_rec(rec, idx, prefix=f'Target[{t}].')
                    t += 1


if __name__ == '__main__':
    main()
