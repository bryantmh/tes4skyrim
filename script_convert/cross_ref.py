"""Cross-reference graph for TES4 FormID/EditorID/Script lookups."""

import mmap
import os
import re
from pathlib import Path

from script_convert.constants import (
    PLACED_REF_SIGS, PLAYER_ALIAS_EXTENDS, SCHOOL_ENCHANT_SHADER, TYPE_MAP,
    papyrus_script_name
)
from script_convert.command_rows import (
    ACTOR_ONLY_FUNCTIONS, OBJREF_SHARED_FUNCTIONS
)
from tes5_import.base.text_reader import parse_export_file
from asset_convert.game_paths import current_namespace
from core.worker_budget import worker_count
from core.worldspace_names import converted_worldspace_edid, renames_for

# ===========================================================================
# Cross-reference graph builder
# ===========================================================================

# Record types the scan skips entirely: they have no EditorIDs and can never
# be referenced from a script, and together they are ~85% of the export bytes
# (LAND.txt alone is ~1.4 GB).
_SCAN_SKIP_SIGS = {'LAND', 'PGRD', 'ROAD'}

# Byte size of one scan job; big files split across workers at this grain.
_SCAN_CHUNK_BYTES = 16 * 1024 * 1024


def master_names(export_dir) -> list:
    """The TES4 master file names listed in an export's _HEADER.txt."""
    header = Path(export_dir) / '_HEADER.txt'
    if not header.is_file():
        return []
    names = []
    for line in header.read_text(encoding='utf-8',
                                 errors='replace').splitlines():
        if line.startswith('Master['):
            _, _, val = line.partition('=')
            val = val.strip()
            if val:
                names.append(val)
    return names


def _scan_chain(export_dir: str) -> tuple:
    """(`_export_dirs_with_masters`, the worldspace renames that chain applies)."""
    dirs = _export_dirs_with_masters(export_dir)
    return dirs, renames_for(dirs)


def _export_dirs_with_masters(export_dir: str) -> list:
    """`export_dir` preceded by its masters' export dirs, deepest first.

    Masters come FIRST so the last-wins merge lets an overriding plugin's own
    version of a record win.  The walk is transitive (a plugin's master may
    itself have masters) and cycle-safe; masters with no export directory are
    skipped silently, which degrades to the old single-directory behaviour.
    """
    root = Path(export_dir)
    ordered: list = []
    seen: set = set()

    def visit(d: Path):
        key = str(d).lower()
        if key in seen or not d.is_dir():
            return
        seen.add(key)
        for name in master_names(d):
            visit(d.parent / name)
        ordered.append(str(d))

    visit(root)
    return ordered


def _new_scan_out() -> dict:
    return {
        'formid_to_edid': {}, 'edid_to_formid': {},
        'script_formid_to_edid': {}, 'script_formid_to_type': {},
        'record_scri': {}, 'record_base': {}, 'record_type': {},
        'quest_edids': set(), 'npc_formids': set(),
        'mgef_shaders': {}, 'spell_effects': {},
        'global_types': {}, 'global_values': {},
        'pack_type': {}, 'actor_packages': {},
        'record_model': {},
        # CELL geometry, for GetInCell: {formid: (is_interior, wrld_fid, x, y)}.
        # An EXTERIOR cell cannot back a Papyrus `Cell` property (see
        # get_cell_family), so its membership test is made from these instead.
        'cell_geom': {},
        # BOOK FormIDs carrying an ENAM: the importer writes these as SCRL, not
        # BOOK, so a property typed from the source signature would not bind.
        'enchanted_books': set(),
    }


def _scan_record_lines(sig: str, lines: list, out: dict):
    """Scan one record's KEY=VALUE lines into the partial result dicts."""
    formid = edid = scri = name_fid = None
    model = None
    schr_type = None
    glob_type = None
    glob_value = None
    mgef_shader = mgef_ench = None
    mgef_school = -1
    spel_effects: list[tuple[str, int]] = []
    pkdt_type = None
    ai_packages: list[str] = []
    cell_flags = None
    cell_wrld = None
    cell_x = cell_y = None
    book_enam = None

    for line in lines:
        line = line.rstrip()
        if line.startswith('FormID='):
            formid = line[7:]
        elif line.startswith('EditorID='):
            edid = line[9:]
        elif line.startswith('SCRI='):
            scri = line[5:]
        elif line.startswith('NAME='):
            name_fid = line[5:]
        elif line.startswith('Model.MODL='):
            model = line[11:]
        elif line.startswith('SCHR.Type='):
            try:
                schr_type = int(line[10:])
            except ValueError:
                pass
        elif sig == 'GLOB' and line.startswith('FNAM.Type='):
            glob_type = line[10:].strip()
        elif sig == 'GLOB' and line.startswith('FLTV.Value='):
            try:
                glob_value = float(line[11:])
            except ValueError:
                pass
        elif sig == 'MGEF' and line.startswith('DATA.EffectShader='):
            mgef_shader = line[18:]
        elif sig == 'MGEF' and line.startswith('DATA.EnchantEffect='):
            mgef_ench = line[19:]
        elif sig == 'MGEF' and line.startswith('DATA.School='):
            try:
                mgef_school = int(line[12:])
            except ValueError:
                pass
        elif sig == 'BOOK' and line.startswith('ENAM='):
            book_enam = line[5:].strip()
        elif sig == 'CELL' and line.startswith('DATA.Flags='):
            try:
                cell_flags = int(line[11:].split()[0], 0)
            except ValueError:
                pass
        elif sig == 'CELL' and line.startswith('ParentWRLD='):
            cell_wrld = line[11:]
        elif sig == 'CELL' and line.startswith('XCLC.X='):
            try:
                cell_x = int(line[7:])
            except ValueError:
                pass
        elif sig == 'CELL' and line.startswith('XCLC.Y='):
            try:
                cell_y = int(line[7:])
            except ValueError:
                pass
        elif sig == 'PACK' and line.startswith('PKDT.Type='):
            try:
                pkdt_type = int(line[10:])
            except ValueError:
                pass
        elif sig in ('NPC_', 'CREA') and line.startswith('AIPackage['):
            m = re.match(r'AIPackage\[\d+\]=(\w+)', line)
            if m:
                ai_packages.append(m.group(1))
        elif sig == 'SPEL' and line.startswith('Effect['):
            m = re.match(r'Effect\[(\d+)\]\.(EFID|ActorValue)=(.*)', line)
            if m:
                idx, key, val = int(m.group(1)), m.group(2), m.group(3)
                while len(spel_effects) <= idx:
                    spel_effects.append(('', -1))
                code, av = spel_effects[idx]
                if key == 'EFID':
                    code = val
                else:
                    try:
                        av = int(val)
                    except ValueError:
                        pass
                spel_effects[idx] = (code, av)

    if not formid:
        return
    if edid:
        out['formid_to_edid'][formid] = edid
        out['edid_to_formid'][edid.lower()] = formid
    if sig == 'SCPT':
        if edid:
            out['script_formid_to_edid'][formid] = edid
        if schr_type is not None:
            out['script_formid_to_type'][formid] = schr_type
    if sig == 'CELL' and cell_flags is not None:
        out['cell_geom'][formid] = (bool(cell_flags & 1), cell_wrld or '',
                                    cell_x, cell_y)
    if sig == 'BOOK' and book_enam and book_enam.strip('0'):
        out['enchanted_books'].add(formid)
    if scri:
        out['record_scri'][formid] = scri
    if name_fid and sig in PLACED_REF_SIGS:
        out['record_base'][formid] = name_fid
    out['record_type'][formid] = sig
    if model:
        out['record_model'][formid] = model
    if sig == 'GLOB' and edid and glob_type:
        out['global_types'][edid.lower()] = glob_type
    if sig == 'GLOB' and edid and glob_value is not None:
        out['global_values'][edid.lower()] = glob_value
    if sig == 'QUST' and edid:
        out['quest_edids'].add(edid.lower())
    if sig in ('NPC_', 'CREA'):
        out['npc_formids'].add(formid)
        if ai_packages:
            out['actor_packages'][formid] = ai_packages
    if sig == 'PACK' and pkdt_type is not None:
        out['pack_type'][formid] = pkdt_type
    if sig == 'MGEF' and edid:
        out['mgef_shaders'][edid.lower()] = (
            mgef_shader or '', mgef_ench or '', mgef_school)
    if sig == 'SPEL' and edid and spel_effects:
        out['spell_effects'][edid.lower()] = spel_effects


def _scan_range(args: tuple) -> dict:
    """Scan the records whose BEGIN delimiter starts in [start, end).

    args = (fpath, sig, start, end). Module-level so it is picklable for
    ProcessPoolExecutor; boundary rule matches text_reader.parse_file_range.
    """

    from tes5_import.base.text_reader import (DELIM_BEGIN, DELIM_END,
                                              find_delim_line)

    fpath, sig, start, end = args
    out = _new_scan_out()
    try:
        f = open(fpath, 'rb')
    except OSError:
        return out
    with f:
        try:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:  # empty file
            return out
        try:
            begin = find_delim_line(mm, DELIM_BEGIN, start)
            while begin != -1 and begin < end:
                nl = mm.find(b'\n', begin)
                if nl < 0:
                    break
                rec_end = find_delim_line(mm, DELIM_END, nl + 1)
                if rec_end < 0:
                    break
                block = mm[nl + 1:rec_end].decode('utf-8', errors='replace')
                _scan_record_lines(sig, block.split('\n'), out)
                begin = find_delim_line(mm, DELIM_BEGIN,
                                         rec_end + len(DELIM_END))
        finally:
            mm.close()
    return out


class CrossRefGraph:
    """Builds FormID->EditorID and EditorID->ScriptName lookup tables."""

    def __init__(self):
        self.formid_to_edid: dict[str, str] = {}
        self.edid_to_formid: dict[str, str] = {}
        #: Source worldspace EDID -> converted EDID; see core/worldspace_names.py.
        self.worldspace_renames: dict[str, str] = renames_for()
        self.script_formid_to_edid: dict[str, str] = {}
        self.script_formid_to_type: dict[str, int] = {}
        self.record_scri: dict[str, str] = {}  # record FormID -> SCRI FormID
        self.record_type: dict[str, str] = {}  # record FormID -> record Signature
        # record FormID -> Model.MODL path as authored (backslashes, any case).
        # Lets the script converter ask the CONVERTED mesh what its physics are
        # (held-until-scripted trap/breakaway), which the animation-group name
        # cannot tell it — see the playgroup release in converter.py.
        self.record_model: dict[str, str] = {}
        # CELL FormID -> (is_interior, parent WRLD FormID, grid X, grid Y).
        # Backs the exterior half of get_cell_family (see there).
        self.cell_geom: dict[str, tuple] = {}
        # BOOK records with an ENAM: written as SCRL, so `Book` would not bind.
        self.enchanted_books: set[str] = set()
        self.record_base: dict[str, str] = {}  # placed ref FormID -> base record FormID (NAME)
        # base FormID (upper) -> its ONE placed ref FormID; lazily inverted
        # from record_base by unique_placed_ref().
        self._base_to_unique_ref: 'dict[str, str] | None' = None
        self.quest_edids: set[str] = set()
        self.npc_formids: set[str] = set()
        # Cross-script ref-as-int analysis: set of (script_name_lower, var_name_lower)
        # where the TES4 `ref` variable is only ever assigned/compared with integers
        self.ref_as_int: set[tuple[str, str]] = set()
        # Cross-script ref-as-BASE-FORM analysis: (script_low, var_low) where the
        # `ref` variable is assigned a BASE record (a MISC/SPEL/WEAP/... item),
        # not a placed reference. Papyrus rejects those into an ObjectReference
        # variable, and the assignment can live in a DIFFERENT script than the
        # declaration (moXscrXtrapXwritedynamicdata writes probe MISCs into
        # moXscrXtrapXmemorystorage.XCURRENTprobeID), so the owning script
        # cannot detect it alone. Such a variable is declared Form.
        self.ref_as_base_form: set[tuple[str, str]] = set()
        # Per-script ref-typed variable names (populated by build_ref_as_int_map)
        self.script_ref_vars: dict[str, set[str]] = {}
        # Cross-script variable accesses: script_name_lower -> set of var_name_lower
        # Variables that are accessed from OTHER scripts (need to be Properties)
        self.cross_script_vars: dict[str, set[str]] = {}
        # Per-script ALL variable declarations: script_name_lower -> dict(var_low -> type_str)
        self.script_all_vars: dict[str, dict[str, str]] = {}
        # Per-script `ref` variables the script itself uses as an ACTOR, i.e. it
        # calls an Actor-only method on them (`myRef.startcombat`).  Those are
        # the only remote ref vars a writer may downcast with `as Actor`; a ref
        # var that only ever holds a marker (MQ16OblivionGate1Script's
        # mySpawnMarker) stays ObjectReference and the cast would null it out.
        self.script_actor_vars: dict[str, set[str]] = {}
        # MGEF EditorID (lower) -> (EffectShader fid, EnchantEffect fid, school int)
        # Used to convert pme/PlayMagicEffectVisuals into EffectShader.Play().
        self.mgef_shaders: dict[str, tuple[str, str, int]] = {}
        # SPEL EditorID (lower) -> [(effect code, actor value int), ...]
        # Used to convert IsSpellTarget into a HasMagicEffect check on the
        # spell's first converted (Skyrim) magic effect.
        self.spell_effects: dict[str, list[tuple[str, int]]] = {}
        # GLOB EditorID (lower) -> TES4 FNAM type char ('s' short, 'l' long,
        # 'f' float).  Decides whether a GetValue() read needs an `as Int`:
        # truncating a float global silently breaks fractional comparisons.
        self.global_types: dict[str, str] = {}
        # GLOB EditorID (lower) -> FLTV value as authored by the plugin.
        # Needed because several TES4 timing idioms hardcode a REAL-SECONDS
        # constant that the author tuned against their own TimeScale (see
        # the chime debounce).  Nehrim ships TimeScale
        # 10, Oblivion 30, so the same script means different things in each.
        self.global_values: dict[str, float] = {}
        # PACK FormID -> PKDT.Type (0 Find, 1 Follow, 2 Escort, 3 Eat,
        # 4 Sleep, 5 Wander, 6 Travel, 7 Accompany, 8 Use Item At, 9 Ambush,
        # 10 Flee Not Combat, 11 Cast Magic — xEdit wbPackageTypeEnum).
        # TES4 `GetCurrentAIPackage` returns this code; Skyrim's
        # Actor.GetCurrentPackage() returns the Package form instead and no
        # Papyrus native exposes a package's type, so a numeric comparison is
        # reconstructed as a disjunction over the actor's own packages of that
        # type (get_actor_packages_of_type).
        self.pack_type: dict[str, int] = {}
        # NPC_/CREA FormID -> [PACK FormID, ...] in AIPackage[n] order.
        self.actor_packages: dict[str, list] = {}

    def load_from_export(self, export_dir: str, workers: int = None):
        """Load cross-reference data from all export .txt files.

        An OVERRIDE plugin's own export holds only the records it authors, so
        every EditorID it merely *references* — the GLOBs, quests and refs that
        live in its masters — is absent.  Unresolvable names fall through
        `_convert_ref` as bare identifiers with no property declared, which the
        compiler rejects ("undefined identifier `SetGewitter`").  So the
        masters' exports are scanned too, TRANSITIVELY and MASTERS FIRST: the
        merge is last-wins, and a plugin that overrides a master's record must
        be the version this graph reports.

        The scan is pure-Python line matching over ~2 GB of text, so files are
        split into byte ranges (record-boundary aligned, same contract as
        text_reader.parse_file_range) and scanned across a process pool.
        """
        if not os.path.isdir(export_dir):
            return

        jobs = []
        dirs, self.worldspace_renames = _scan_chain(export_dir)
        for d in dirs:
            for fname in sorted(os.listdir(d)):
                if not fname.endswith('.txt'):
                    continue
                sig = fname[:-4]
                if sig in _SCAN_SKIP_SIGS:
                    continue
                fpath = os.path.join(d, fname)
                try:
                    size = os.path.getsize(fpath)
                except OSError:
                    continue
                for start in range(0, size, _SCAN_CHUNK_BYTES):
                    jobs.append((fpath, sig,
                                 start, min(start + _SCAN_CHUNK_BYTES, size)))

        if workers is None:
            workers = worker_count()
        workers = min(workers, max(1, len(jobs)))
        if workers <= 1 or len(jobs) <= 2:
            results = map(_scan_range, jobs)
            for out in results:
                self._merge_scan(out)
        else:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=workers) as ex:
                # map preserves job order -> same last-wins merge semantics
                # as the old serial whole-file scan.
                for out in ex.map(_scan_range, jobs):
                    self._merge_scan(out)

    def _merge_scan(self, out: dict):
        """Fold one scan-range result (see _scan_range) into this graph."""
        self.formid_to_edid.update(out['formid_to_edid'])
        self.edid_to_formid.update(out['edid_to_formid'])
        self.script_formid_to_edid.update(out['script_formid_to_edid'])
        self.script_formid_to_type.update(out['script_formid_to_type'])
        self.record_scri.update(out['record_scri'])
        self.record_base.update(out['record_base'])
        self.record_type.update(out['record_type'])
        self.record_model.update(out['record_model'])
        self.cell_geom.update(out['cell_geom'])
        self.enchanted_books.update(out['enchanted_books'])
        self.quest_edids.update(out['quest_edids'])
        self.npc_formids.update(out['npc_formids'])
        self.mgef_shaders.update(out['mgef_shaders'])
        self.spell_effects.update(out['spell_effects'])
        self.global_types.update(out['global_types'])
        self.global_values.update(out['global_values'])
        self.pack_type.update(out['pack_type'])
        self.actor_packages.update(out['actor_packages'])

    def get_extends_class(self, script_formid: str) -> str:
        """The Papyrus extends class for a script.

        For a type-0 script the base must be one EVERY attaching record
        can bind, since Papyrus refuses a script whose declared base
        does not match the form.  A script attached ONLY to the player's
        base NPC_ extends the alias type instead, because the importer
        rehosts it on a quest's PlayerRef alias.

        See: docs/commentary/script_convert.md#player-base-script-needs-quest-alias
        """
        schr_type = self.script_formid_to_type.get(script_formid, 0)

        if schr_type == 1:
            return 'Quest'
        if schr_type == 256:
            return 'ActiveMagicEffect'

        attached = [rec_fid for rec_fid, scri_fid in self.record_scri.items()
                    if scri_fid == script_formid]
        sigs = {self.record_type.get(rec_fid, '') for rec_fid in attached}
        if 'QUST' in sigs:
            return 'Quest'

        if attached and all(self._is_player_base(f) for f in attached):
            return PLAYER_ALIAS_EXTENDS

        if sigs & {'NPC_', 'CREA'} and not (sigs - {'NPC_', 'CREA'}):
            return 'Actor'

        return 'ObjectReference'

    @staticmethod
    def _is_player_base(rec_fid: str) -> bool:
        """True for the player's base NPC_ record (TES4 FormID 0x00000007)."""
        try:
            return (int(rec_fid, 16) & 0x00FFFFFF) == 0x07
        except (TypeError, ValueError):
            return False

    # TES4 magic-school enum -> the EFSH each school's enchantment glow uses.
    # Fallback for MGEFs with neither an EffectShader nor an EnchantEffect
    # (bound armor, summons): the school glow is what Oblivion shows on the
    # enchant anyway, and every one of these EditorIDs exists in Oblivion.esm.
    def get_mgef_shader_edid(self, code: str) -> str:
        """EFSH EditorID for a TES4 magic-effect code (pme/sme argument).

        Preference order mirrors what Oblivion's PlayMagicEffectVisuals shows:
        the effect's own EffectShader, else its EnchantEffect shader, else the
        school's enchantment glow.  Returns '' if the code is unknown.
        """
        entry = self.mgef_shaders.get(code.lower())
        if not entry:
            return ''
        shader_fid, ench_fid, school = entry
        for fid in (shader_fid, ench_fid):
            if fid and int(fid, 16) != 0:
                edid = self.formid_to_edid.get(fid, '')
                if edid:
                    return edid
        fallback = SCHOOL_ENCHANT_SHADER.get(school, '')
        if fallback and fallback in self.edid_to_formid:
            return self.formid_to_edid.get(self.edid_to_formid[fallback], '')
        return ''

    def is_quest_ref(self, name: str) -> bool:
        """Check if a name refers to a known quest."""
        return name.lower() in self.quest_edids

    def unique_placed_ref(self, base_fid: str) -> str:
        """The ONE placed reference of a base record, or '' when there is none
        or more than one.

        Oblivion resolves a unique actor's base EditorID to its placed
        instance, so a script property that names an NPC_/CREA base means the
        ACHR/ACRE — Skyrim's VM refuses the base into a reference-typed
        property and it reads None (see constants.wants_placed_reference).
        Ambiguity binds to nothing new: with several placements the base is
        returned unchanged by the caller, exactly as before this existed.
        """
        if self._base_to_unique_ref is None:
            first: dict[str, str] = {}
            dup: set[str] = set()
            for ref_fid, b in self.record_base.items():
                key = (b or '').upper()
                if not key:
                    continue
                if key in first:
                    dup.add(key)
                else:
                    first[key] = ref_fid
            self._base_to_unique_ref = {k: v for k, v in first.items()
                                        if k not in dup}
        return self._base_to_unique_ref.get((base_fid or '').upper(), '')

    def get_quest_script_type(self, quest_name: str) -> str:
        """Get the Papyrus script class name for a quest, e.g. 'TES4_MyQuestScript'.
        Returns 'Quest' if no attached script is found."""
        low = quest_name.lower()
        fid = self.edid_to_formid.get(low, '')
        if not fid:
            return 'Quest'
        scri_fid = self.record_scri.get(fid, '')
        if not scri_fid:
            return 'Quest'
        script_edid = self.script_formid_to_edid.get(scri_fid, '')
        if not script_edid:
            return 'Quest'
        return papyrus_script_name(script_edid)

    def get_cell_family(self, cell_name: str) -> list:
        """CELL EditorIDs that TES4 `GetInCell <cell_name>` matches.

        TES4 matches GetInCell by EditorID PREFIX, not by identity, so
        `GetInCell Chorrol` is true in all 86 cells whose EditorID starts with
        "Chorrol" (ChorrolCastle, ChorrolMagesGuild, ...).  Oblivion leans on
        this hard: 62 CELL records exist purely as the named anchor for a
        family and hold no refs at all, several saying so outright
        (`FULL=Dummy cell for GetInCell`).  Those anchors are cells the player
        can never stand in, so translating the call as a single equality
        against the named cell yields a condition that is permanently false.

        Returns the matching EditorIDs (original case), the exact-name match
        first so a single-cell result keeps its identity.  Empty if unknown.
        """
        low = cell_name.lower()
        fids = self.record_type
        out = []
        for edid_low, fid in self.edid_to_formid.items():
            if not edid_low.startswith(low):
                continue
            if fids.get(fid, '') != 'CELL':
                continue
            out.append(self.formid_to_edid.get(fid, ''))
        out = [e for e in out if e]
        out.sort(key=lambda e: (e.lower() != low, e.lower()))
        return out

    def split_cell_family(self, cell_name: str) -> tuple:
        """get_cell_family split into (interior EditorIDs, exterior grid keys).

        A Papyrus `Cell` property only ever binds to an INTERIOR cell. Every
        vanilla Skyrim script bears this out -- all 43 of its Cell properties
        name interiors (HelgenKeep, Jorrvaskr, MarkarthAbandonedHouse, ...) and
        not one names an exterior. Declaring a property for an exterior grid
        cell produces, at runtime,

            Property <X> ... cannot be bound because (<fid>) is not the right
            type

        and the property reads None thereafter. That was 773 of the binding
        failures in one session, all of them exterior cells, while the
        interiors of the very same families bound fine (MS08BoatScript: 44
        interior OK, 41 exterior failed, no exceptions).

        Exteriors are still part of the TES4 prefix match, so dropping them
        would quietly narrow the test. They are returned as
        (worldspace EditorID, x, y) grid keys instead, which the emitted helper
        compares against the ref's own worldspace and grid position -- an exact
        equivalent that needs no property binding.
        """
        interior, exterior = [], []
        for edid in self.get_cell_family(cell_name):
            fid = self.edid_to_formid.get(edid.lower(), '')
            geom = self.cell_geom.get(fid)
            if geom is None:
                # No geometry recorded: treat as interior, which is the
                # behaviour before this split and still binds when correct.
                interior.append(edid)
                continue
            is_int, wrld_fid, x, y = geom
            if is_int:
                interior.append(edid)
                continue
            wrld_edid = converted_worldspace_edid(
                self.formid_to_edid.get(wrld_fid, ''), self.worldspace_renames)
            if x is None or y is None:
                # An exterior with no XCLC is the worldspace's own persistent
                # "dummy cell". It holds no grid square, so the faithful test is
                # membership of the WORLDSPACE itself -- and it still cannot
                # back a Cell property, so it must not fall through to one.
                if wrld_edid:
                    exterior.append((wrld_edid, None, None))
                continue
            exterior.append((wrld_edid, x, y))
        return interior, exterior

    def get_script_owner_packages_of_type(self, script_edid: str,
                                          pkg_type: int) -> list:
        """Same as get_actor_packages_of_type for a BARE (self) call.

        A bare `GetCurrentAIPackage` runs on whatever actor the script is
        attached to, so the owner is found by walking SCRI back to the
        NPC_/CREA that names this script.  When the script is attached to more
        than one actor the union is returned: the test must be true whenever
        ANY of them is running a package of that type, and the disjunction the
        caller emits is exactly that.
        """
        want = script_edid.lower()
        script_fid = ''
        for fid, edid in self.script_formid_to_edid.items():
            if edid.lower() == want:
                script_fid = fid
                break
        if not script_fid:
            return []
        out = []
        for actor_fid, scri in self.record_scri.items():
            if scri != script_fid or actor_fid not in self.actor_packages:
                continue
            for pack_fid in self.actor_packages[actor_fid]:
                if self.pack_type.get(pack_fid) != pkg_type:
                    continue
                edid = self.formid_to_edid.get(pack_fid, '')
                if edid and edid not in out:
                    out.append(edid)
        return out

    def get_actor_packages_of_type(self, actor_name: str, pkg_type: int) -> list:
        """PACK EditorIDs of `actor_name`'s own packages whose PKDT.Type matches.

        TES4 `GetCurrentAIPackage` returns the running package's TYPE code;
        Skyrim's `Actor.GetCurrentPackage()` returns the Package form and
        neither vanilla `Package.psc` nor SKSE exposes its type, so the numeric
        comparison has no direct equivalent.  It is reconstructed instead: the
        set of packages an actor can be running is fixed at conversion time by
        its own AIPackage list, so `x.GetCurrentAIPackage == 5` becomes an
        equality against each of x's Wander packages, OR'd together.

        Scoping to the actor's OWN list is what makes this tractable — the
        plugin has 1,820 Wander packages overall but the affected actors carry
        between one and three apiece.

        `actor_name` may name the base NPC_/CREA or a placed ACHR/ACRE, which
        is followed through NAME.  Returns [] when the actor or its packages
        are unknown, which keeps the caller on the old no-op path.
        """
        fid = self.edid_to_formid.get(actor_name.lower(), '')
        if not fid:
            return []
        if fid not in self.actor_packages:
            base = self.record_base.get(fid, '')
            if base:
                fid = base
        out = []
        for pack_fid in self.actor_packages.get(fid, ()):
            if self.pack_type.get(pack_fid) != pkg_type:
                continue
            edid = self.formid_to_edid.get(pack_fid, '')
            if edid:
                out.append(edid)
        return out

    def get_base_signature(self, name: str) -> str:
        """Record signature a name ultimately refers to ('ACTI', 'NPC_', ...).

        For a placed reference (REFR/ACHR/ACRE) this follows the NAME chain to
        the BASE record, so `CGPrisonSecretWallRef` reports 'ACTI' (its base
        `prisonSecretWall01`) rather than 'REFR'. Returns '' when unknown.

        Callers use this to tell an ANIMATED OBJECT from an ACTOR, which decide
        completely different animation APIs — see PlayGroup in converter.py.
        """
        fid = self.edid_to_formid.get(name.lower(), '')
        if not fid:
            return ''
        base_fid = self.record_base.get(fid, '')
        if base_fid:
            return self.record_type.get(base_fid, '')
        return self.record_type.get(fid, '')

    def needs_havok_release(self, name: str) -> bool:
        """True if *name*'s mesh ships bodies HELD until a script releases them.

        The converted NIF carries KEYFRAMED bodies that kept a non-zero mass —
        `physics_flags_from_data` bit 1 — which `_convert_collision` writes for
        breakaway pieces and constrained trap islands only.  Those are exactly
        the objects whose `playgroup` must be followed by
        SetMotionType(Motion_Dynamic); every other animated object converts to
        a mass-0 keyframed body that cannot fall no matter what the script does.

        Resolves through a placed reference to its base record, like
        get_base_signature, so `CTrapLogs01Ref.playgroup` works.
        """
        from tes5_import.base.mesh_bounds import get_mesh_physics_flags

        fid = self.edid_to_formid.get(name.lower(), '')
        if not fid:
            return False
        model = self.record_model.get(self.record_base.get(fid, '') or fid, '')
        if not model:
            return False
        key = model.replace('\\\\', '/').replace('\\', '/').lower().lstrip('/')
        ns = current_namespace() + '/'
        if not key.startswith(ns):
            key = ns + key
        return bool(get_mesh_physics_flags(key) & 2)

    def script_owner_needs_havok_release(self, script_edid: str) -> bool:
        """needs_havok_release for a BARE (self) `playgroup`.

        A bare call runs on whatever record the script is attached to, so walk
        SCRI back to the owners.  True if ANY owner's mesh is held — a script
        shared between a held trap and something else still has to release the
        trap, and the release is inert on anything that is not held.
        """
        from tes5_import.base.mesh_bounds import get_mesh_physics_flags

        want = (script_edid or '').lower()
        if not want:
            return False
        script_fid = ''
        for fid, edid in self.script_formid_to_edid.items():
            if edid.lower() == want:
                script_fid = fid
                break
        if not script_fid:
            return False
        for rec_fid, scri in self.record_scri.items():
            if scri != script_fid:
                continue
            model = self.record_model.get(rec_fid, '')
            if not model:
                continue
            key = model.replace('\\\\', '/').replace('\\', '/').lower().lstrip('/')
            ns = current_namespace() + '/'
            if not key.startswith(ns):
                key = ns + key
            if get_mesh_physics_flags(key) & 2:
                return True
        return False

    def get_record_script_type(self, name: str) -> str:
        """Get the Papyrus script class name for any record with an attached script.
        For placed references (ACHR/ACRE/REFR), follows the NAME chain to the
        base record to find the attached script.
        Returns '' if the record has no attached script."""
        low = name.lower()
        # `player`/`playerref` is a converter KEYWORD emitted as
        # `Game.GetPlayer()`, never a bound property — so it must never take a
        # script type, even though the player's base NPC_ has EditorID "Player"
        # and CAN carry a SCRI (Nehrim's GlobalplayerScript).  Typing it made
        # every caller declare `TES4_GlobalplayerScript Property Player`, which
        # then failed to convert to ObjectReference at each use.
        if low in ('player', 'playerref'):
            return ''
        fid = self.edid_to_formid.get(low, '')
        if not fid:
            return ''
        scri_fid = self.record_scri.get(fid, '')
        # For placed refs without own SCRI, follow base form chain
        if not scri_fid:
            base_fid = self.record_base.get(fid, '')
            if base_fid:
                scri_fid = self.record_scri.get(base_fid, '')
        if not scri_fid:
            return ''
        script_edid = self.script_formid_to_edid.get(scri_fid, '')
        if not script_edid:
            return ''
        return papyrus_script_name(script_edid)

    def build_ref_as_int_map(self, scpt_path: str):
        """Scan all SCPT SCTX sources to find ref variables used only as integers.

        TES4 'ref' type can hold both references and integers.  When a ref
        variable is only ever assigned/compared with numeric literals across
        ALL scripts that touch it, it should be typed Int in Papyrus.

        The masters' SCPT sources are scanned alongside the plugin's own, for
        the same reason load_from_export scans their records: an override
        plugin's script reaching into a MASTER's script variable
        (`SomeMasterQuest.someVar`) can only be typed if that script's
        declarations are known here.  Masters first — a plugin that overrides a
        master's SCPT must be the source that wins.
        """
        export_dir = os.path.dirname(scpt_path)
        records = []
        for d in _export_dirs_with_masters(export_dir):
            p = os.path.join(d, os.path.basename(scpt_path))
            if os.path.isfile(p):
                records.extend(parse_export_file(p))

        # Phase A: collect variable declarations per script
        _decl_re = re.compile(r'^\s*ref\s+(\w+)', re.IGNORECASE)
        _all_decl_re = re.compile(r'^\s*(short|long|int|float|ref)\s+(\w+)',
                                  re.IGNORECASE)
        script_ref_vars: dict[str, set[str]] = {}
        script_all_vars: dict[str, dict[str, str]] = {}
        script_actor_vars: dict[str, set[str]] = {}
        script_sources: dict[str, str] = {}
        _actor_only = sorted(ACTOR_ONLY_FUNCTIONS - OBJREF_SHARED_FUNCTIONS)
        _actor_call_re = re.compile(
            r'(\w+)\s*\.\s*(?:' +
            '|'.join(re.escape(f) for f in _actor_only) +
            r')(?:\s|$|\()', re.IGNORECASE)

        for rec in records:
            edid = rec.get('EditorID', '')
            sctx = rec.get('SCTX', '')
            if not edid or not sctx:
                continue
            scn_low = edid.lower()
            script_sources[scn_low] = sctx
            ref_vars = set()
            all_vars: dict[str, str] = {}
            for line in sctx.split('\n'):
                stripped = line.strip()
                m = _decl_re.match(stripped)
                if m:
                    ref_vars.add(m.group(1).lower())
                am = _all_decl_re.match(stripped)
                if am:
                    vtype = am.group(1).lower()
                    vname = am.group(2).lower()
                    all_vars[vname] = TYPE_MAP.get(vtype, 'Int')
            if ref_vars:
                script_ref_vars[scn_low] = ref_vars
                # COMMENTS ARE NOT USES.  DAHermaeusScript's only
                # `target.GetDead` sits behind a `;`, and counting it declared
                # `Actor Property target` on a variable the script never uses
                # as one -- every cross-script write into it then needed a
                # downcast that the owning script's real type does not want.
                actor_used = {m.group(1).lower()
                              for m in _actor_call_re.finditer(
                                  _strip_comments(sctx))}
                actor_used &= ref_vars
                if actor_used:
                    script_actor_vars[scn_low] = actor_used
            if all_vars:
                script_all_vars[scn_low] = all_vars

        # Persist for cross-script type lookups
        self.script_ref_vars = script_ref_vars
        self.script_all_vars = script_all_vars
        self.script_actor_vars = script_actor_vars

        if not script_ref_vars:
            return

        # Phase B: scan ALL scripts for usage of ref vars
        _set_re = re.compile(
            r'\bset\s+(?:(\w+)\.)?(\w+)\s+to\s+(.+)',
            re.IGNORECASE
        )
        # (script_lower, var_lower) -> {'zero', 'int', 'ref'}
        usage: dict[tuple[str, str], set[str]] = {}
        # dest_var -> {source_vars}: `set A.x to y` where y is another variable
        # rather than a record name. Used to propagate 'baseform' along
        # variable-to-variable copies (see Phase C).
        ref_flow: dict[tuple[str, str], set[tuple[str, str]]] = {}

        for scn_low, sctx in script_sources.items():
            for raw_line in sctx.split('\n'):
                line = raw_line.strip()
                if not line or line.startswith(';'):
                    continue

                # Detect ref usage: var.method() patterns on local ref variables
                if scn_low in script_ref_vars:
                    for ref_var in script_ref_vars[scn_low]:
                        if re.search(r'\b' + re.escape(ref_var) + r'\.\w+',
                                     line, re.IGNORECASE):
                            key = (scn_low, ref_var)
                            if key not in usage:
                                usage[key] = set()
                            usage[key].add('ref')

                # Check 'set [obj.]var to value' patterns
                sm = _set_re.match(line)
                if sm:
                    target_obj = (sm.group(1) or '').lower()
                    var_name = sm.group(2).lower()
                    value = sm.group(3).strip()
                    # Strip TES4 inline comments ("; comment text")
                    semi_idx = value.find(';')
                    if semi_idx >= 0:
                        value = value[:semi_idx].strip()
                    if target_obj:
                        owner = target_obj
                    else:
                        owner = scn_low
                    # Resolve owner to its script name
                    owner_script = None
                    if owner in script_ref_vars and var_name in script_ref_vars[owner]:
                        owner_script = owner
                    elif owner != scn_low:
                        base_fid = self.edid_to_formid.get(owner, '')
                        if base_fid:
                            scri_fid = self.record_scri.get(base_fid, '')
                            if scri_fid:
                                se = self.script_formid_to_edid.get(scri_fid, '')
                                if se:
                                    se_low = se.lower()
                                    if se_low in script_ref_vars and var_name in script_ref_vars[se_low]:
                                        owner_script = se_low

                    if owner_script:
                        key = (owner_script, var_name)
                        if key not in usage:
                            usage[key] = set()
                        if re.match(r'^-?\d+(\.\d+)?$', value):
                            if value.strip() == '0':
                                usage[key].add('zero')
                            else:
                                usage[key].add('int')
                        else:
                            usage[key].add('ref')
                            # A BASE record assigned here (not a placed ref):
                            # ObjectReference cannot hold it. TES4 lets a form
                            # name be quoted (`Set X to "0probeUbent"`), so
                            # strip quotes before the EditorID lookup.
                            value = value.strip('"')
                            if re.match(r'^\w+$', value):
                                v_fid = self.edid_to_formid.get(
                                    value.lower(), '')
                                v_rtype = (self.record_type.get(v_fid, '')
                                           if v_fid else '')
                                if v_rtype and v_rtype not in PLACED_REF_SIGS:
                                    usage[key].add('baseform')
                                elif not v_fid:
                                    # Not a record name -- it is another
                                    # variable. Remember the edge so a base
                                    # form reaching THAT variable propagates
                                    # here too (moXscrXtrapXwritedynamicdata
                                    # fills local XLOCALXProbeIDA* with probe
                                    # MISCs, then copies them into
                                    # memorystorage.XCURRENTprobeID).
                                    ref_flow.setdefault(key, set()).add(
                                        (scn_low, value.lower()))
                            else:
                                # `A.x = B.y` -- a QUALIFIED source. Resolve
                                # B to its script so the edge still links the
                                # two variables (XcurrentTRAPeffect is fed
                                # from the same script's XSpellIDA*).
                                qm = re.match(r'^(\w+)\.(\w+)$', value)
                                if qm:
                                    src_owner = qm.group(1).lower()
                                    src_var = qm.group(2).lower()
                                    src_script = None
                                    if (src_owner in script_ref_vars
                                            and src_var
                                            in script_ref_vars[src_owner]):
                                        src_script = src_owner
                                    else:
                                        s_fid = self.edid_to_formid.get(
                                            src_owner, '')
                                        s_scri = (self.record_scri.get(
                                            s_fid, '') if s_fid else '')
                                        s_ed = (self.script_formid_to_edid
                                                .get(s_scri, '')
                                                if s_scri else '')
                                        if s_ed:
                                            src_script = s_ed.lower()
                                    if src_script:
                                        ref_flow.setdefault(key, set()).add(
                                            (src_script, src_var))

        # Phase C: ref vars with ONLY non-zero integer usage -> retype to Int
        for (script_low, var_low), types in usage.items():
            if 'ref' not in types and 'int' in types:
                self.ref_as_int.add((script_low, var_low))
            elif 'baseform' in types:
                self.ref_as_base_form.add((script_low, var_low))
        # Propagate 'holds a base form' along variable-to-variable copies until
        # it stops spreading. A local that received a base record makes every
        # variable it is copied into a base-form holder too, however many hops
        # away and across script boundaries.
        changed = True
        while changed:
            changed = False
            for dest, sources in ref_flow.items():
                if dest in self.ref_as_base_form:
                    continue
                if any(s in self.ref_as_base_form for s in sources):
                    self.ref_as_base_form.add(dest)
                    changed = True

        # Phase D: detect cross-script variable access (Owner.VarName patterns)
        # These variables must be Properties on the owning script so other scripts
        # can access them. Scans SCPT sources, INFO result scripts, and QUST stage scripts.
        _owner_var_re = re.compile(r'\b(\w+)\.(\w+)\b')
        cross_script_vars: dict[str, set[str]] = {}

        def _scan_text_for_cross_access(text):
            for raw_line in text.split('\n'):
                line = raw_line.strip()
                if not line or line.startswith(';'):
                    continue
                semi = line.find(';')
                if semi >= 0:
                    line = line[:semi]
                for match in _owner_var_re.finditer(line):
                    owner = match.group(1).lower()
                    var = match.group(2).lower()
                    target_script = None
                    if owner in script_all_vars and var in script_all_vars[owner]:
                        target_script = owner
                    else:
                        fid = self.edid_to_formid.get(owner, '')
                        if fid:
                            scri_fid = self.record_scri.get(fid, '')
                            if scri_fid:
                                se = self.script_formid_to_edid.get(scri_fid, '')
                                if se:
                                    se_low = se.lower()
                                    if se_low in script_all_vars and var in script_all_vars[se_low]:
                                        target_script = se_low
                    if target_script:
                        if target_script not in cross_script_vars:
                            cross_script_vars[target_script] = set()
                        cross_script_vars[target_script].add(var)

        # Scan all SCPT sources
        for scn_low, sctx in script_sources.items():
            _scan_text_for_cross_access(sctx)

        _scan_plugin_result_scripts(export_dir, _scan_text_for_cross_access)

        self.cross_script_vars = cross_script_vars
    def is_remote_ref_var(self, owner_edid: str, var_name: str) -> bool:
        """Check if a variable on a remote record's script is ref-typed in TES4.

        *owner_edid* is the EditorID of the quest/NPC/object (e.g. 'MQ00').
        *var_name* is the property name (e.g. 'nearOblivionGate').
        Returns True if the remote script declares that variable as 'ref'
        AND it is not a ref-as-int variable (used only as integers).
        """
        var_low = var_name.lower()
        owner_low = owner_edid.lower()
        # Direct script name match
        if owner_low in self.script_ref_vars:
            if var_low in self.script_ref_vars[owner_low]:
                return (owner_low, var_low) not in self.ref_as_int
            return False
        # Resolve owner EditorID -> script name
        fid = self.edid_to_formid.get(owner_low, '')
        if not fid:
            return False
        scri_fid = self.record_scri.get(fid, '')
        if not scri_fid:
            return False
        se = self.script_formid_to_edid.get(scri_fid, '')
        if not se:
            return False
        se_low = se.lower()
        if se_low in self.script_ref_vars:
            if var_low in self.script_ref_vars[se_low]:
                return (se_low, var_low) not in self.ref_as_int
        return False


# ===========================================================================
# Script converter
# ===========================================================================



def _strip_comments(text: str) -> str:
    """TES4 source with `;` comments removed, line structure preserved.

    A scan for uses must not see commented-out code: it is exactly the code
    the author DISABLED, so counting it types variables off statements that
    never run.
    """
    nl = chr(10)
    return nl.join(line.split(';', 1)[0] for line in text.split(nl))


def _scan_plugin_result_scripts(export_dir: str, scan) -> None:
    """Feed THIS plugin's INFO result scripts (both halves) and QUST stage
    scripts to `scan`: a pure union that only ADDS names which must become
    Properties.  The masters' copies are not read -- each master's own run
    emits its fragments."""
    for extra_file, field_name in [('INFO.txt', 'ResultScript'),
                                   ('INFO.txt', 'ResultScriptEnd'),
                                   ('QUST.txt', 'SCTX')]:
        prefix = field_name + '='
        for raw_line in _export_lines(os.path.join(export_dir, extra_file)):
            if raw_line.startswith(prefix):
                text = raw_line[len(prefix):].strip()
                scan(text.replace('\\r\\n', '\n').replace('\\n', '\n'))


def _export_lines(path: str):
    """The lines of one export file; nothing when it is missing or unreadable."""
    if not os.path.isfile(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            yield from f
    except (OSError, UnicodeDecodeError):
        return
