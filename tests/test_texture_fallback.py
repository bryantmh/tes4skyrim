"""An imported mod resolves textures it does not ship through its base.

Master-export blindness, the asset half of it. A mod ships only the files it
changes; everything else lives in the base game's export tree. Deriving the
texture root from the mesh path -- swapping `\\meshes\\` for `\\textures\\` --
can never reach that tree.

Measured on the author's parallax mod: of the 3357 distinct texture paths its
8665 meshes reference, 1464 were in the mod and **1602 only in Nehrim.esm**.
Unreachable means the shape gets no height map and no specular verdict.

A mod with a plugin names its masters in `_HEADER.txt`; an asset-only mod has
no plugin and no header, so the base is recorded at import time.
"""

from asset_convert.nif import shaders
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.nif import nif_converter as nc


def _tree(root, name, textures=(), header_masters=None, base=None):
    """A minimal export/<name>/ with meshes/, textures/ and optional base."""
    d = root / name
    (d / 'meshes').mkdir(parents=True, exist_ok=True)
    for rel in textures:
        p = d / 'textures' / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'DDS ')
    if header_masters is not None:
        (d / '_HEADER.txt').write_text(
            ''.join(f'Master[{i}]={m}\n' for i, m in enumerate(header_masters)),
            encoding='utf-8')
    if base is not None:
        s = d / '_source'
        s.mkdir(parents=True, exist_ok=True)
        (s / shaders.BASE_PLUGINS_FILE).write_text('\n'.join(base) + '\n',
                                              encoding='utf-8')
    return d


class TestMasterTextureRoots:

    def test_a_plugin_mod_finds_its_masters_from_the_header(self, tmp_path):
        _tree(tmp_path, 'Base.esm', textures=['rock/stone.dds'])
        mod = _tree(tmp_path, 'Mod.esp', header_masters=['Base.esm'])
        roots = shaders.master_texture_roots(mod / 'meshes')
        assert len(roots) == 1
        assert roots[0].endswith(str(Path('Base.esm') / 'textures'))

    def test_an_asset_only_mod_uses_the_recorded_base(self, tmp_path):
        # no plugin means no _HEADER.txt, so --base is the only carrier
        _tree(tmp_path, 'Base.esm', textures=['rock/stone.dds'])
        mod = _tree(tmp_path, 'TexturePack', base=['Base.esm'])
        roots = shaders.master_texture_roots(mod / 'meshes')
        assert len(roots) == 1

    def test_no_base_means_no_fallback(self, tmp_path):
        mod = _tree(tmp_path, 'Lonely')
        assert shaders.master_texture_roots(mod / 'meshes') == ()

    def test_a_base_without_an_export_is_skipped(self, tmp_path):
        mod = _tree(tmp_path, 'Mod.esp', header_masters=['Absent.esm'])
        assert shaders.master_texture_roots(mod / 'meshes') == ()


class TestResolutionThroughTheFallback:

    def test_the_mods_own_copy_still_wins(self, tmp_path):
        base = _tree(tmp_path, 'Base.esm', textures=['rock/stone.dds'])
        mod = _tree(tmp_path, 'Mod.esp', textures=['rock/stone.dds'],
                    header_masters=['Base.esm'])
        got = shaders.resolve_source_texture(
            'textures\\tes4\\rock\\stone.dds', str(mod / 'meshes' / 'a.nif'),
            shaders.master_texture_roots(mod / 'meshes'))
        assert got is not None
        assert str(mod) in got and str(base / 'textures') not in got

    def test_a_texture_only_the_base_has_is_found(self, tmp_path):
        # the 1602-path case: without the fallback this returns None
        base = _tree(tmp_path, 'Base.esm', textures=['rock/stone.dds'])
        mod = _tree(tmp_path, 'Mod.esp', header_masters=['Base.esm'])
        args = ('textures\\tes4\\rock\\stone.dds',
                str(mod / 'meshes' / 'a.nif'))
        assert shaders.resolve_source_texture(*args) is None, \
            'no fallback should still miss it'
        got = shaders.resolve_source_texture(
            *args, shaders.master_texture_roots(mod / 'meshes'))
        assert got is not None and str(base) in got

    def test_a_texture_nobody_has_is_still_unresolved(self, tmp_path):
        _tree(tmp_path, 'Base.esm')
        mod = _tree(tmp_path, 'Mod.esp', header_masters=['Base.esm'])
        assert shaders.resolve_source_texture(
            'textures\\tes4\\rock\\absent.dds',
            str(mod / 'meshes' / 'a.nif'),
            shaders.master_texture_roots(mod / 'meshes')) is None


class TestCaseBlindResolution:
    """NIFs keep Oblivion's mixed case; extracted files are lowercase.

    On a case-sensitive filesystem an exact join missed 35,552 of 40,337
    Oblivion shapes' normal maps, and they fell back to the flat default.
    """

    def test_a_mixed_case_path_finds_the_lowercase_file(self, tmp_path):
        mod = _tree(tmp_path, 'Base.esm', textures=['architecture/arstone02_n.dds'])
        got = shaders.resolve_source_texture(
            'textures\\tes4\\Architecture\\ARStone02_n.dds',
            str(mod / 'meshes' / 'a.nif'))
        assert got == str(mod / 'textures' / 'architecture' / 'arstone02_n.dds')

    def test_the_base_is_searched_case_blind_too(self, tmp_path):
        base = _tree(tmp_path, 'Base.esm', textures=['rock/stone.dds'])
        mod = _tree(tmp_path, 'Mod.esp', header_masters=['Base.esm'])
        got = shaders.resolve_source_texture(
            'textures\\tes4\\Rock\\Stone.DDS', str(mod / 'meshes' / 'a.nif'),
            shaders.master_texture_roots(mod / 'meshes'))
        assert got is not None and str(base) in got

    def test_a_mod_folder_named_Textures_is_found(self, tmp_path):
        d = tmp_path / 'Loose.esp'
        (d / 'Meshes').mkdir(parents=True)
        (d / 'Textures' / 'Tower').mkdir(parents=True)
        (d / 'Textures' / 'Tower' / 'Wall.dds').write_bytes(b'DDS ')
        got = shaders.resolve_source_texture(
            'textures\\tes4\\tower\\wall.dds', str(d / 'Meshes' / 'a.nif'))
        assert got == str(d / 'Textures' / 'Tower' / 'Wall.dds')

    def test_a_name_differing_beyond_case_still_misses(self, tmp_path):
        mod = _tree(tmp_path, 'Base.esm', textures=['rock/stone.dds'])
        assert shaders.resolve_source_texture(
            'textures\\tes4\\Rock\\Stone_n.dds',
            str(mod / 'meshes' / 'a.nif')) is None


class TestWearablePlanThroughTheBase:
    """An asset-only tree inherits its base's ARMO/CLOT records.

    Without this no mesh in a merge is ever WORN, so no `_0`/`_1` pair is
    written and the armour silently falls back to the base conversion's
    meshes -- decided without any of the mod's textures. Worn gear is exactly
    what benefits most from a specular map.
    """

    def _armo(self, d, model):
        """One ARMO record in the export text format."""
        lines = ['---RECORD_BEGIN---', 'Signature=ARMO',
                 'FormID=0004938C', 'BMDT.BipedFlags=32',
                 f'Male.BipedModel.MODL={model}', '---RECORD_END---']
        (d / 'ARMO.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def test_a_merge_inherits_the_bases_plan(self, tmp_path):
        from asset_convert.character import wearable_plan as wp
        base = _tree(tmp_path, 'Base.esm')
        self._armo(base, 'armor\iron\cuirass.nif')
        assert wp.build_plan(base), 'base plan should not be empty'

        mod = _tree(tmp_path, 'Merge', base=['Base.esm'])
        assert wp.build_plan(mod) == wp.build_plan(base)

    def test_without_a_base_it_stays_empty(self, tmp_path):
        from asset_convert.character import wearable_plan as wp
        base = _tree(tmp_path, 'Base.esm')
        self._armo(base, 'armor\iron\cuirass.nif')
        mod = _tree(tmp_path, 'Lonely')
        assert not any(v for v in wp.build_plan(mod).values())

    def test_the_trees_own_records_win(self, tmp_path):
        from asset_convert.character import wearable_plan as wp
        base = _tree(tmp_path, 'Base.esm')
        self._armo(base, 'armor\iron\cuirass.nif')
        mod = _tree(tmp_path, 'Merge', base=['Base.esm'])
        self._armo(mod, 'armor\steel\cuirass.nif')
        plan = wp.build_plan(mod)
        # both present: the base's entry survives, the mod's is added
        assert len([k for k in plan if not k.startswith('*')]) == 2


class TestNormalForVariantDiffuse:
    """Which normal map a diffuse gets, and in which order.

    Oblivion stores no normal path -- it appends `_n` to the diffuse -- and
    artists ship ONE normal for a family of color variants:
    `BrumaWoodPost_Dark.dds` and `_Grey.dds` both rely on
    `BrumaWoodPost_n.dds`.  Deriving from the full name alone invents a path
    that does not exist.

    Measured over the merged Nehrim tree: 201 variants rely on the base's
    normal, against 48 that ship their own beside it -- and those 48 are
    unaffected, because the variant's own is tried first.
    """

    @staticmethod
    def _stats(mod):
        return {'_src_path': str(mod / 'meshes' / 'a.nif'),
                '_tex_fallback': shaders.master_texture_roots(mod / 'meshes')}

    def test_own_normal_wins(self, tmp_path):
        mod = _tree(tmp_path, 'Mod.esp',
                    textures=['w/post_dark.dds', 'w/post_dark_n.dds',
                              'w/post_n.dds'])
        got = shaders.resolve_normal_for(r'Textures\tes4\w\post_dark.dds',
                                     self._stats(mod))
        assert got == r'Textures\tes4\w\post_dark_n.dds'

    def test_base_normal_used_when_the_variant_has_none(self, tmp_path):
        """The BrumaWoodPost case."""
        mod = _tree(tmp_path, 'Mod.esp',
                    textures=['w/post_dark.dds', 'w/post_n.dds'])
        got = shaders.resolve_normal_for(r'Textures\tes4\w\post_dark.dds',
                                     self._stats(mod))
        assert got == r'Textures\tes4\w\post_n.dds'

    def test_nothing_anywhere_returns_none(self, tmp_path):
        """None means the caller falls back to the shared flat stand-in."""
        mod = _tree(tmp_path, 'Mod.esp', textures=['w/post_dark.dds'])
        assert shaders.resolve_normal_for(r'Textures\tes4\w\post_dark.dds',
                                      self._stats(mod)) is None

    def test_only_one_separator_is_stripped(self, tmp_path):
        """`a_b_c` may borrow from `a_b`, never from `a`.

        Walking further up would eventually reach a name that has nothing to
        do with the surface, which is how a convenience turns into a wrong
        texture on screen.
        """
        mod = _tree(tmp_path, 'Mod.esp',
                    textures=['w/wall_stone_red.dds', 'w/wall_n.dds'])
        assert shaders.resolve_normal_for(r'Textures\tes4\w\wall_stone_red.dds',
                                      self._stats(mod)) is None

    def test_a_name_without_a_separator_never_borrows(self, tmp_path):
        mod = _tree(tmp_path, 'Mod.esp', textures=['w/post.dds', 'w/_n.dds'])
        assert shaders.resolve_normal_for(r'Textures\tes4\w\post.dds',
                                      self._stats(mod)) is None

    def test_the_base_normal_may_live_in_the_master(self, tmp_path):
        """A retexture ships the variant; the base's normal stays upstream."""
        base = _tree(tmp_path, 'Base.esm', textures=['w/post_n.dds'])
        mod = _tree(tmp_path, 'Mod.esp', textures=['w/post_dark.dds'],
                    header_masters=['Base.esm'])
        got = shaders.resolve_normal_for(r'Textures\tes4\w\post_dark.dds',
                                     self._stats(mod))
        assert got == r'Textures\tes4\w\post_n.dds', \
            f'base normal not found through the master fallback ({base})'


class TestTheRuleIsNotNormalSpecific:
    """Oblivion's base-name fallback applies to EVERY derived map.

    Keeping `_resolve_map_for` generic is the point: `_n` had the rule and
    nothing else would have, so the next slot implemented would have quietly
    reinvented the dangling-path bug.
    """

    @staticmethod
    def _stats(mod):
        return {'_src_path': str(mod / 'meshes' / 'a.nif'),
                '_tex_fallback': shaders.master_texture_roots(mod / 'meshes')}

    def test_glow_uses_the_base_name_too(self, tmp_path):
        mod = _tree(tmp_path, 'Mod.esp',
                    textures=['w/lamp_blue.dds', 'w/lamp_g.dds'])
        got = shaders._resolve_map_for(r'Textures\tes4\w\lamp_blue.dds', '_g',
                                  self._stats(mod))
        assert got == r'Textures\tes4\w\lamp_g.dds'

    def test_the_variants_own_map_still_wins(self, tmp_path):
        mod = _tree(tmp_path, 'Mod.esp',
                    textures=['w/lamp_blue.dds', 'w/lamp_blue_g.dds',
                              'w/lamp_g.dds'])
        got = shaders._resolve_map_for(r'Textures\tes4\w\lamp_blue.dds', '_g',
                                  self._stats(mod))
        assert got == r'Textures\tes4\w\lamp_blue_g.dds'

    def test_absent_everywhere_is_none(self, tmp_path):
        """No stand-in for a glow map: absence of glow IS the neutral state."""
        mod = _tree(tmp_path, 'Mod.esp', textures=['w/lamp_blue.dds'])
        assert shaders._resolve_map_for(r'Textures\tes4\w\lamp_blue.dds', '_g',
                                   self._stats(mod)) is None


class TestGlowBeatsParallax:
    """A shape is shader type 2 OR type 3 -- `skyrim_shader_type` is one value.

    The glow map is AUTHORED by the artist; our height map is DERIVED from the
    diffuse.  Authored wins.  Nehrim happens to have zero shapes asking for
    both (13 glow maps in a random 5734-property sample, none of them flagged
    for parallax), so this guards converted MODS, not the test data.
    """

    @staticmethod
    def _shader_and_set():
        shader = nc.NifFormat.BSLightingShaderProperty()
        ts = nc.NifFormat.BSShaderTextureSet()
        ts.num_textures = 9
        ts.textures.update_size()
        return shader, ts

    def _stats(self, mod):
        return {'_src_path': str(mod / 'meshes' / 'a.nif'),
                '_tex_fallback': shaders.master_texture_roots(mod / 'meshes')}

    def test_glow_sets_type_slot_and_flag(self, tmp_path):
        mod = _tree(tmp_path, 'Mod.esp', textures=['w/lamp_g.dds'])
        shader, ts = self._shader_and_set()
        stats = self._stats(mod)
        assert shaders.apply_glow(shader, ts, b'textures\\w\\lamp_g.dds',
                              stats) is True
        assert int(shader.skyrim_shader_type) == shaders.SHADER_TYPE_GLOWMAP
        assert int(shader.shader_flags_2.slsf_2_glow_map) == 1
        assert int(shader.shader_flags_1.slsf_1_environment_mapping) == 0, \
            'AU: the environment shader is incompatible with glow mapping'
        assert ts.textures[shaders.GLOW_SLOT]
        assert stats['glow_applied'] == 1

    def test_a_named_but_missing_glow_is_not_invented(self, tmp_path):
        """Unlike the normal map, glow has no sensible stand-in."""
        mod = _tree(tmp_path, 'Mod.esp')
        shader, ts = self._shader_and_set()
        stats = self._stats(mod)
        assert shaders.apply_glow(shader, ts, b'textures\\w\\lamp_g.dds',
                              stats) is False
        assert int(shader.skyrim_shader_type) != shaders.SHADER_TYPE_GLOWMAP
        assert stats['glow_unresolved'] == 1

    def test_no_glow_path_leaves_the_shader_alone(self, tmp_path):
        mod = _tree(tmp_path, 'Mod.esp')
        shader, ts = self._shader_and_set()
        before = int(shader.skyrim_shader_type)
        assert shaders.apply_glow(shader, ts, b'', self._stats(mod)) is False
        assert int(shader.skyrim_shader_type) == before


class TestGlowIsDerivedNotOnlyNamed:
    """Oblivion rarely NAMES its glow map -- it derives `<diffuse>_g.dds`.

    Measured over a random 1200-mesh Nehrim sample: 227 shapes have a `_g` on
    disk for their diffuse, only 31 name it in NiTexturingProperty's glow
    slot.  Reading the slot alone missed 86% of the glow content, and left
    those shapes with emissive on a NON-glow shader -- which per Arcane
    University floods the WHOLE mesh instead of glowing per texel.
    """

    @staticmethod
    def _shader_and_set(diffuse=None):
        shader = nc.NifFormat.BSLightingShaderProperty()
        ts = nc.NifFormat.BSShaderTextureSet()
        ts.num_textures = 9
        ts.textures.update_size()
        if diffuse:
            ts.textures[0] = diffuse.encode('utf-8')
        return shader, ts

    def _stats(self, mod):
        return {'_src_path': str(mod / 'meshes' / 'a.nif'),
                '_tex_fallback': shaders.master_texture_roots(mod / 'meshes')}

    def test_derived_from_the_diffuse_when_nothing_is_named(self, tmp_path):
        """The rune-stone case: no glow slot, `_g` sits beside the diffuse."""
        mod = _tree(tmp_path, 'Mod.esp',
                    textures=['r/stone.dds', 'r/stone_g.dds'])
        shader, ts = self._shader_and_set(r'Textures\tes4\r\stone.dds')
        stats = self._stats(mod)
        assert shaders.apply_glow(shader, ts, b'', stats) is True
        assert int(shader.skyrim_shader_type) == shaders.SHADER_TYPE_GLOWMAP
        assert stats['glow_derived'] == 1

    def test_no_glow_texture_means_no_glow_shader(self, tmp_path):
        """The other shape in the same mesh must stay a plain lit surface."""
        mod = _tree(tmp_path, 'Mod.esp', textures=['r/plain.dds'])
        shader, ts = self._shader_and_set(r'Textures\tes4\r\plain.dds')
        before = int(shader.skyrim_shader_type)
        assert shaders.apply_glow(shader, ts, b'', self._stats(mod)) is False
        assert int(shader.skyrim_shader_type) == before

    def test_a_named_glow_wins_over_the_derived_one(self, tmp_path):
        mod = _tree(tmp_path, 'Mod.esp',
                    textures=['r/stone.dds', 'r/stone_g.dds',
                              'r/authored_g.dds'])
        shader, ts = self._shader_and_set(r'Textures\tes4\r\stone.dds')
        stats = self._stats(mod)
        assert shaders.apply_glow(shader, ts, rb'textures\r\authored_g.dds',
                              stats) is True
        assert b'authored_g' in bytes(ts.textures[shaders.GLOW_SLOT])
        assert 'glow_derived' not in stats

    def test_black_emissive_is_lifted_to_vanillas_default(self, tmp_path):
        """Skyrim multiplies the glow map by emissive, so black discards it.

        Vanilla's own type-2 shapes: all 60 sampled set own_emit, the modal
        emissive is white and the modal multiple 1.0.
        """
        mod = _tree(tmp_path, 'Mod.esp',
                    textures=['r/stone.dds', 'r/stone_g.dds'])
        shader, ts = self._shader_and_set(r'Textures\tes4\r\stone.dds')
        stats = self._stats(mod)
        assert shaders.apply_glow(shader, ts, b'', stats) is True
        e = shader.emissive_color
        assert (e.r, e.g, e.b) == (1.0, 1.0, 1.0)
        assert float(shader.emissive_multiple) == 1.0
        assert int(shader.shader_flags_1.slsf_1_own_emit) == 1
        assert stats['glow_emissive_defaulted'] == 1

    def test_an_authored_emissive_color_survives(self, tmp_path):
        """The rune stone's orange must not be overwritten with white.

        It stops flooding the whole surface and starts modulating the glyph --
        that is the entire point of moving to the glow shader.
        """
        mod = _tree(tmp_path, 'Mod.esp',
                    textures=['r/stone.dds', 'r/stone_g.dds'])
        shader, ts = self._shader_and_set(r'Textures\tes4\r\stone.dds')
        shader.emissive_color.r = 1.0
        shader.emissive_color.g = 0.188
        shader.emissive_color.b = 0.0
        stats = self._stats(mod)
        assert shaders.apply_glow(shader, ts, b'', stats) is True
        e = shader.emissive_color
        assert (round(e.r, 3), round(e.g, 3), round(e.b, 3)) == (1.0, 0.188, 0.0)
        assert 'glow_emissive_defaulted' not in stats
