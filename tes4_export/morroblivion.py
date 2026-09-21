"""
What Morroblivion supplies for a vanilla Morrowind asset a dependent plugin names.

In Morroblivion mode the vanilla ESMs are never exported, yet a plugin's
records keep naming vanilla meshes. Morroblivion reworked those per RECORD, so
a mesh maps the way records do: the vanilla record owning that `MODL`, the
record index, then the Morroblivion record's own model. Creature meshes are
the exception: Morroblivion renamed its creature records freely, so they pair
through a table.

See: docs/commentary/tes4_export_morrowind.md#morroblivion-meshes
"""

import os
from pathlib import Path

from asset_convert.sources.morrowind_assets import source_meshes
from output_layout import record_dir
from source_paths import resolve_plugin_path
from tes5_import.base.text_reader import parse_export_file

from .morroblivion_axis import (SUBSTITUTION_BLACKLIST, pitch_for_model,
                                z_reseat_for_base)
from .tes3_reader import get_string, get_subrecord, read_file

#: Prefix of the converted Morroblivion plugins whose records supply the models.
MORROBLIVION_PREFIX = 'morrowind_ob'

#: The vanilla ESMs whose records own the vanilla meshes.
VANILLA_ESMS = ('Morrowind.esm', 'Tribunal.esm', 'Bloodmoon.esm')

#: Record type whose models the creature table supplies instead.
_CREATURE = 'CREA'

#: Vanilla creature mesh (lower case, backslashes) -> the Morroblivion CREA EditorID wearing its replacement.
MORROBLIVION_CREATURES = {
    'r\\duskyalit.nif': '0Alit',
    'r\\ancestorghost.nif': '0AncestralGhost',
    'r\\atronach_fire.nif': '0AtronachFlame',
    'r\\atronach_frost.nif': '0AtronachFrost',
    'r\\atronach_storm.nif': '0AtronachStorm',
    'r\\babelfish.nif': '0Slaughterfish', 'r\\slaughterfish.nif': '0Slaughterfish',
    'r\\bear_black_larger.nif': '0bmbearblack',
    'r\\bear_brown_larger.nif': '0bmbearbrown',
    'r\\bear_blond_larger.nif': '0BMsnowbear',
    'r\\bonelord.nif': '0BoneLord', 'r\\bonewalker.nif': '0BoneWalker',
    'r\\greatbonewalker.nif': '0BoneWalkerGreater',
    'r\\cavemudcrab.nif': '0MudCrab',
    'r\\clannfear.nif': '0Clanfear', 'r\\clannfear_daddy.nif': '0Clanfear',
    'r\\cliffracer.nif': '0CliffRacer',
    'r\\cr_draugr.nif': 'fbmwBMCreatureDraugr',
    'r\\draugrlord.nif': 'fbmwBMCreatureDraugr',
    'r\\daedroth.nif': '0Daedroth', 'r\\dreugh.nif': '0Dreugh',
    'r\\dwarvenspecter.nif': '0DwarvenSpectre',
    'r\\g_centurionspider.nif': '0CenturionGrandSpider',
    'r\\sphere_centurions.nif': '0CenturionSphere',
    'r\\spherearcher.nif': '0CenturionProjectile',
    'r\\steam_centurions.nif': '0CenturionSteam',
    'r\\goblin01.nif': 'fbmwGoblinGrunt', 'r\\goblin02.nif': 'fbmwGoblinOfficer',
    'r\\goblin03.nif': 'fbmwTRGoblinChief1',
    'r\\golden saint.nif': '0GoldenSaint',
    'r\\guar.nif': '0guar', 'r\\guar_withpack.nif': '0guarUpack',
    'r\\horker.nif': '0Horker', 'r\\hunger.nif': '0Hunger',
    'r\\ice troll.nif': '0bmicetrolltough',
    'r\\leastkagouti.nif': '0Kagouti',
    'r\\kwama forager.nif': '0KwamaForager', 'r\\kwama queen.nif': '0KwamaQueen',
    'r\\kwama warior.nif': '0KwamaWarrior', 'r\\kwama worker.nif': '0KwamaWorker',
    'r\\liche.nif': 'mwLich', 'r\\skeleton.nif': '0Skeleton1',
    'r\\minescrib.nif': '0Scrib',
    'r\\mount.nif': '0FrostBoar',
    'r\\netch_betty.nif': '0NetchBetty', 'r\\netch_bull.nif': '0NetchBull',
    'r\\nixhound.nif': '0NixHound',
    'r\\packrat.nif': '0RatUPackURerlas', 'r\\rust rat.nif': '0Rat',
    'r\\scamp_fetch.nif': '0Scamp',
    'r\\shalk.nif': '0Shalk', 'r\\spriggan.nif': '0bmSpriggan',
    'r\\undeadwolf_2.nif': '0bmWolfskeleton', 'r\\wolf_black.nif': '0BMWolfGrey',
    'r\\wolf_red.nif': '0bmredwolf', 'r\\wingedtwilight.nif': '0WingedTwilight',
    'r\\ascendedsleeper.nif': '0AscendedSleeper', 'r\\ashghoul.nif': '0AshGhoul',
    'r\\ashslave.nif': '0AshSlave', 'r\\ashvampire.nif': '0DagothAraynys',
    'r\\ashzombie.nif': '0AshZombieEloth',
    'r\\corprus_stalker.nif': '0CorprusStalker', 'r\\lame_corprus.nif': '0CorprusLame',
    'r\\durzog.nif': 'fbmwdurzogUwild', 'r\\durzog_collar.nif': 'fbmwdurzogUwarUtrained',
    'r\\fabricant.nif': 'fbmwFabricantVerm',
    'r\\fabricant_hulking.nif': 'fbmwFabricantHulk',
    'r\\fabricant_imperfect.nif': 'fbmwImperfect',
    'r\\frostgiant.nif': 'fbmwbmKarstaag',
    'r\\hircine_bear_larger.nif': '0HircinesAspectofStrength',
    'r\\hircinewolf.nif': '0HircinesAspectofSpeed',
    'r\\iceminion.nif': '0Riekling', 'r\\icemraider.nif': '0bmRieklingMounted',
    'r\\raven.nif': 'fbmwBMGlenmorilRaven', 'r\\udyrfrykte.nif': 'fbmwUderFrykte',
    'r\\wolf_white.nif': '0BMwolfsnowunique', 'r\\horker_larger.nif': '0bmHorkerlarge',
    'r\\liche_king.nif': '0wormSlord', 'r\\swimmer.nif': 'fbmwbmTheSwimmer',
}


def archive_path(value: str) -> str:
    """A model path in archive form: lower case, backslashes, no leading one."""
    return value.replace('\\\\', '\\').replace('/', '\\').strip().lstrip('\\').lower()


class MorroblivionModels:
    """The Morroblivion replacement for every vanilla mesh a plugin may name.

    `owners` is vanilla mesh -> the vanilla record ids naming it; `models`
    is Morroblivion record FormID (low 24 bits) -> its model path, over
    every record type in the Morroblivion masters' exports; `editor_ids`
    is that same FormID -> EditorID, for the per-base Z re-seat; `creatures`
    is Morroblivion CREA EditorID -> (Model.MODL, [NIFZ]).
    """

    def __init__(self, export_root: str, masters, source_path: str,
                 own_meshes=None):
        """Index the vanilla ESMs beside `source_path` and the masters' exports.

        `own_meshes` is this plugin's extracted mesh tree, which `owns`
        falls back from onto the source archives when it does not exist.
        """
        self.owners, self.models, self.creatures = {}, {}, {}
        self.editor_ids = {}
        self.own_meshes = Path(own_meshes) if own_meshes else None
        self._own_archive = None
        self._source_path = source_path
        for esm in VANILLA_ESMS:
            path = resolve_plugin_path(esm, os.path.dirname(source_path), export_root)
            if os.path.isfile(path):
                self._index_owners(path)
        for name, _path in masters:
            if name.lower().startswith(MORROBLIVION_PREFIX):
                self._index_models(str(record_dir(export_root, name)))

    def _index_owners(self, esm_path: str) -> None:
        """Record which vanilla ids name each mesh, first ESM first."""
        for rec in read_file(esm_path)[1]:
            sub = get_subrecord(rec, 'MODL')
            if rec.deleted or sub is None:
                continue
            path = archive_path(get_string(sub))
            if path:
                self.owners.setdefault(path, []).append(rec.record_id)

    def _index_models(self, export_dir: str) -> None:
        """Record every Morroblivion record's model, and its CREA parts."""
        for name in os.listdir(export_dir):
            if not name.endswith('.txt') or name.startswith('_'):
                continue
            for rec in parse_export_file(os.path.join(export_dir, name)):
                model = rec.get('Model.MODL') or rec.get('Male.WorldModel.MODL')
                form_id = (rec.get('FormID') or '').lower()
                if model and form_id:
                    self.models[form_id[2:]] = model.replace('\\\\', '\\')
                if form_id and rec.get('EditorID'):
                    self.editor_ids[form_id[2:]] = rec['EditorID']
                if name == _CREATURE + '.txt' and model:
                    parts = [rec.get(f'NIFZ[{i}]') or ''
                             for i in range(int(rec.get('NIFZCount', 0) or 0))]
                    self.creatures[(rec.get('EditorID') or '').lower()] = (
                        model.replace('\\\\', '\\'), parts)

    def owns(self, path: str) -> bool:
        """Whether THIS plugin's own source ships the mesh at `path`.

        The extracted tree answers when it exists; otherwise the archives
        beside the source do, because the GUI exports before it extracts.
        See: docs/commentary/tes4_export_morrowind.md#who-owns-a-mesh
        """
        rel = archive_path(path)
        if self.own_meshes is not None \
                and (self.own_meshes / rel.replace(chr(92), '/')).is_file():
            return True
        if self._own_archive is None:
            self._own_archive = source_meshes(self._source_path)
        return rel in self._own_archive


    def replacement(self, path: str, index) -> str:
        """Morroblivion's model for vanilla mesh `path`, resolved through `index`, or ''.

        A blacklisted replacement resolves to '' so the vanilla mesh survives
        and the compatibility patch converts it.
        See: docs/audits/morroblivion_mesh_axis_rotation.md#substitution-blacklist
        """
        for record_id in self.owners.get(archive_path(path), ()):
            form_id = index.lookup(record_id)
            model = self.models.get(str(form_id).lower()[2:]) if form_id else None
            if model and archive_path(model).replace(chr(92), chr(47)) \
                    not in SUBSTITUTION_BLACKLIST:
                return model
        return ''

    def paired_creature(self, record_id: str, index) -> str:
        """The FormID of the Morroblivion creature standing in for vanilla `record_id`, or ''.

        Morroblivion renames its creatures, so a vanilla id finds its stand-in
        through the MESH the two share, the pairing the creature table records.
        See: docs/commentary/tes4_export_morrowind.md#sound-gen-creature
        """
        wanted = record_id.lower()
        for path, owners in self.owners.items():
            editor_id = MORROBLIVION_CREATURES.get(path)
            if editor_id and wanted in (owner.lower() for owner in owners):
                return index.lookup(editor_id) or ''
        return ''

    def creature(self, path: str):
        """(Model.MODL, [NIFZ]) of the Morroblivion creature on vanilla `path`, or None."""
        editor_id = MORROBLIVION_CREATURES.get(archive_path(path))
        return self.creatures.get(editor_id.lower()) if editor_id else None


def remap_vanilla_models(out: dict, ctx) -> tuple:
    """Rewrite every non-creature model line to Morroblivion's model.

    Returns (models changed, records re-seated, references pitched). A mesh the
    plugin ships itself is left alone, and so is one Morroblivion does not
    replace: the compatibility patch converts those.

    See: docs/commentary/tes4_export_morrowind.md#morroblivion-meshes
    See: docs/commentary/tes4_export_morrowind.md#morroblivion-origin-shift
    See: docs/audits/morroblivion_mesh_axis_rotation.md#the-correction
    """
    if ctx.morroblivion is None:
        return 0, 0, 0
    shifts = ctx.origin_shifts
    changed = shifted = 0
    for sig, records in out.items():
        if sig == _CREATURE:
            continue
        for _form_id, lines in records:
            for i, line in enumerate(lines):
                key, sep, value = line.partition('=')
                if not sep or not key.endswith('MODL') or not value:
                    continue
                path = value.replace('\\\\', '\\')
                if ctx.morroblivion.owns(path):
                    continue
                model = ctx.morroblivion.replacement(path, ctx.index)
                if not model:
                    continue
                lines[i] = f'{key}={model.replace(chr(92), chr(92) * 2)}'
                changed += 1
                shift = shifts.shift_for(path, model) if shifts else 0.0
                if shift:
                    lines.append(f'Model.OriginShift={shift!r}')
                    shifted += 1
    return changed, shifted, _fix_placements(
        out, ctx.morroblivion.models, ctx.morroblivion.editor_ids)


def _bump(lines: list, key: str, delta: float) -> bool:
    """Add `delta` to the `key=` line in place; False when there is none."""
    for i, line in enumerate(lines):
        if line.startswith(key):
            lines[i] = f'{key}{float(line[len(key):]) + delta!r}'
            return True
    return False


def _fix_placements(out: dict, models: dict, editor_ids: dict) -> int:
    """Apply Morroblivion's hand corrections to every placed reference.

    Both corrections resolve through the MASTERS' indexes, because the bases
    belong to Morroblivion and their records are never in this plugin's own
    output.  The pitch keys on the mesh; the Z re-seat keys on the base, since
    Morroblivion moved some bases of a mesh and left others alone.

    See: docs/audits/morroblivion_mesh_axis_rotation.md#the-correction
    """
    fixed = 0
    for sig in ('REFR', 'ACHR', 'ACRE'):
        for _form_id, lines in out.get(sig, []):
            base = next((l[len('NAME='):] for l in lines
                         if l.startswith('NAME=')), '')
            if not base:
                continue
            low = base.lower()[2:]
            pitch = pitch_for_model(models.get(low, ''))
            dz = z_reseat_for_base(editor_ids.get(low, ''))
            if pitch and _bump(lines, 'RotX=', pitch):
                fixed += 1
            if dz and _bump(lines, 'PosZ=', dz):
                fixed += 1
    return fixed
