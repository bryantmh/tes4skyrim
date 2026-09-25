"""The pack filter must never withhold a texture something references.

The prune is a blacklist of categories Skyrim cannot load.  Its predecessor
rebuilt a keep-set from references and packed only what it predicted, so any
name it failed to predict was dropped from the BSA while surviving in
`output/` -- invisible in loose-file testing, untextured in a packed install.
These tests pin the categories and, above all, that ordinary asset paths and
generated billboards are never excluded.
"""

from asset_convert.texture import texture_prune as tp


class TestExcludedCategories:
    """Each blacklisted subtree, verified to drop zero mesh-named files."""

    def test_facegen_bakes_are_excluded(self):
        """FaceGen output is keyed by FormID; Skyrim regenerates its own."""
        assert tp.is_excluded('tes4/faces/oblivion.esm/0001a2b3_0.dds')
        assert tp.is_excluded('nehrim/faces/nehrim.esm/000f00d_1.dds')

    def test_oblivion_ui_atlases_are_excluded(self):
        """Skyrim's interface shares no art with Oblivion's."""
        for d in ('menus', 'menus80', 'menus50'):
            assert tp.is_excluded('tes4/%s/icons/quest.dds' % d)

    def test_superseded_lod_is_excluded(self):
        """Our own bake into AutoConvertLOD replaces the shipped tiles."""
        assert tp.is_excluded('tes4/landscapelod/generated/tamriel.32.0.0.dds')
        assert tp.is_excluded('tes4/distantlod/anything.dds')

    def test_lowres_textures_are_packed(self):
        """_far meshes name them, some with no full-res twin to fall back on."""
        assert not tp.is_excluded('tes4/lowres/xullc/rockbeach05.dds')

    def test_only_the_second_segment_matches(self):
        """A blacklisted word deeper in the path is somebody's real asset."""
        assert not tp.is_excluded('tes4/architecture/menus/signpost.dds')
        assert not tp.is_excluded('tes4/clutter/faces/mask01.dds')


class TestNonTextureFiles:
    """Mods ship build junk under textures/; only real textures are packed."""

    def test_stray_data_folder_is_excluded(self):
        """One mod nests a whole 1.1 GB Oblivion Data folder under textures/."""
        base = 'tes4/morroblivion/improved/architecture/mournhold/data/'
        for name in ('beautiful cities of morrowind - textures.bsa',
                     'dlcshiveringisles - meshes.bsa',
                     'beautiful cities of morrowind.esp',
                     'dlclist.txt', 'credits.txt'):
            assert tp.is_excluded(base + name)

    def test_texture_formats_are_packed(self):
        """The two extensions the engine loads from a textures archive."""
        assert not tp.is_excluded('tes4/architecture/anvil/wall.dds')
        assert not tp.is_excluded('tes4/architecture/anvil/wall.tga')

    def test_extensionless_is_excluded(self):
        """A file with no suffix at all is never a texture."""
        assert tp.is_excluded('tes4/architecture/readme')


class TestReferencedAssetsAlwaysShip:
    """The regression the blacklist exists to prevent.

    Generated tree billboards are named after the OUTPUT record, which the old
    keep-set guessed from the EXPORT's `.spt` MODL.  Import fans one SPT out
    into several per-record NIFs, so the two disagreed by construction: 58 of
    63 billboards vanished from Unique Landscapes' archive, 136 from Nehrim.
    Nothing about a generated name may affect whether it ships.
    """

    def test_generated_billboards_ship(self):
        """Billboards named for the record, the model, or with a digit prefix."""
        for stem in ('xulrhshrubeuonymus01', 'ulsvtreewhitepinesnowdead',
                     '1treesnowgumfree', 'shrubeuonymussu'):
            assert not tp.is_excluded('tes4/trees/billboards/%s.dds' % stem)
            assert not tp.is_excluded('tes4/trees/billboards/%s_n.dds' % stem)

    def test_creature_and_character_textures_ship(self):
        """Body and hair maps shipped meshes name, including nested hair."""
        for key in ('tes4/creatures/rat/rat.dds',
                    'tes4/characters/imperial/male/upperbodymale.dds',
                    'tes4/characters/hair/argonian.dds',
                    'nehrim/characters/ren/hair/rengrey.dds'):
            assert not tp.is_excluded(key)

    def test_ordinary_assets_ship(self):
        """Architecture, clutter, landscape and plugin-named folders."""
        for key in ('tes4/architecture/anvil/anvilhouse01.dds',
                    'tes4/clutter/books/book01.dds',
                    'tes4/landscape/grass01_n.dds',
                    'nehrim/nehrim/elevator01.dds'):
            assert not tp.is_excluded(key)


class TestTextureRefsIn:
    """The binary scanner still backs the mesh manifest and the LOD stage."""

    def test_finds_paths_in_binary(self):
        """The walk back stops only at a byte illegal in a path."""
        raw = b'\x00\x04\x00textures\\architecture\\wall.dds\x00junk'
        assert tp.texture_refs_in(raw) == [b'textures\\architecture\\wall.dds']

    def test_rejects_a_stub_shorter_than_three_bytes(self):
        """Fewer than 3 bytes before '.dds' is noise, not a path."""
        assert tp.texture_refs_in(b'\x00\x00.dds\x00') == []
