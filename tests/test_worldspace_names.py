"""The converted-worldspace rename table.

See: docs/commentary/script_convert.md#worldspace-property-rename
"""

from core.worldspace_names import (converted_worldspace_edid,
                                   converted_worldspace_name, renames_for,
                                   set_worldspace_plugins,
                                   source_worldspace_edid)


def test_arktwend_persistent_cell_and_map_name_follow():
    """The persistent cell and display name change with the worldspace."""
    table = renames_for(['Arktwend_English.esm'])
    assert converted_worldspace_edid('WrldMorrowindPersistent', table) == \
        'WrldArktwendPersistent'
    try:
        set_worldspace_plugins(['Arktwend_English.esm'])
        assert converted_worldspace_name('WrldMorrowind',
                                         'Morrowind') == 'Arktwend'
        set_worldspace_plugins(['Morrowind.esm'])
        assert converted_worldspace_name('WrldMorrowind',
                                         'Morrowind') == 'Morrowind'
        assert converted_worldspace_edid('WrldMorrowindPersistent') == \
            'WrldMorrowindPersistent'
    finally:
        set_worldspace_plugins([])


def test_tamriel_renamed_for_every_plugin():
    """Oblivion's Tamriel becomes TES4Tamriel whatever the chain."""
    assert converted_worldspace_edid('Tamriel', renames_for([])) == \
        'TES4Tamriel'


def test_arktwend_gets_its_own_worldspace_name():
    """Arktwend's WrldMorrowind becomes WrldArktwend, and reverses back."""
    table = renames_for(['export/Arktwend_English.esm'])
    assert converted_worldspace_edid('WrldMorrowind', table) == 'WrldArktwend'
    assert source_worldspace_edid('WrldArktwend', table) == 'wrldmorrowind'


def test_morrowind_keeps_wrldmorrowind():
    """Neither Morrowind mode nor a Morrowind dependent renames it."""
    for chain in (['Morrowind.esm'], ['Morrowind_ob.esm', 'Oblivion.esm'],
                  ['TR_Mainland.esm', 'Morrowind.esm', 'Tribunal.esm']):
        assert converted_worldspace_edid('WrldMorrowind',
                                         renames_for(chain)) == 'WrldMorrowind'


def test_a_dependent_of_arktwend_inherits_the_rename():
    """A plugin with Arktwend as a master sees the renamed worldspace."""
    table = renames_for(['ArktwendPatch.esp', 'Arktwend_English.esm'])
    assert converted_worldspace_edid('WrldMorrowind', table) == 'WrldArktwend'
