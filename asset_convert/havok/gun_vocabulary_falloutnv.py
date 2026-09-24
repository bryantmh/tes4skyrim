"""The FO3/FNV gun vocabulary shared by the clip, graph and record stages.

A leaf module on purpose: the import side (equipment_falloutnv) and the
asset side (gun_anim_falloutnv, gun_graph_falloutnv) both key their indices
on these tables, and the asset side imports the import package.
See: docs/commentary/asset_convert_falloutnv.md#gun-animations
"""

#: FO3/FNV WEAP DNAM anim type -> clip class; melee, thrown and mines are not guns.
ANIM_TYPE_CLASS = {3: '1hp', 4: '1hp', 5: '2hr', 6: '2ha', 7: '2hr',
                   8: '2hh', 9: '2hl'}

#: The prefix FNV gives weapon nodes its actor clips animate (##Clip, ##Slide).
PART_PREFIX = '##'
#: The classes in graph order: iGunClass is an index into this tuple.
GUN_CLASSES = ('1hp', '2hr', '2ha', '2hh', '2hl')

#: wbReloadAnimEnum order: ReloadA..ReloadS, then W, X, Y, Z.
RELOAD_LETTERS = 'abcdefghijklmnopqrswxyz'

#: DNAM.AttackAnim -> clip action (xEdit wbDefinitionsFNV attack enum); 255 = DEFAULT.
ATTACK_ANIMS = {
    26: 'attackleft', 32: 'attackright', 38: 'attack3', 44: 'attack4',
    50: 'attack5', 56: 'attack6', 62: 'attack7', 68: 'attack8',
    74: 'attackloop', 80: 'attackspin', 86: 'attackspin2', 102: 'placemine',
    108: 'placemine2', 114: 'attackthrow', 120: 'attackthrow2',
    126: 'attackthrow3', 132: 'attackthrow4', 138: 'attackthrow5',
    144: 'attack9', 150: 'attackthrow6', 156: 'attackthrow7',
    162: 'attackthrow8',
}

#: Attack actions in graph order: iGunAttack is an index into this tuple.
ATTACK_ACTIONS = ('attackleft', 'attackright', 'attack3', 'attack4',
                  'attack5', 'attack6', 'attack7', 'attack8', 'attack9',
                  'attackloop', 'attackspin', 'attackspin2')
#: The attack action that fires once per loop pass (automatic weapons).
LOOP_ACTION = 'attackloop'
