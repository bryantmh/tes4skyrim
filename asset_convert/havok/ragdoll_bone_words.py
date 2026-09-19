"""Bone-name vocabulary the ragdoll stage classifies chains by.

Names match as whole WORDS, never substrings: `'ear' in name` also matches
"Forearm".  Trailing digits and side letters belong to the chain, not the
word, so 'Bip01 Tail3', 'Canine_Neck2' and 'Bip01 L Ear01' all match.
See: docs/commentary/asset_convert_creature.md#keyframe-bone-sets
"""

import re

#: Chains vanilla leaves UNPINNED on a living actor (dog: Tail1-3, Neck2, Head).
LOOSE_WHILE_ALIVE = frozenset({'tail', 'neck', 'head', 'skull', 'scull',
                               'ponytail', 'ear', 'jaw', 'tongue', 'wing'})
#: The axial (trunk) chain: everything that is NOT a limb.
AXIAL_WORDS = frozenset({'pelvis', 'spine', 'chest', 'ribcage', 'neck', 'head',
                         'skull', 'scull', 'com', 'tail', 'nonaccum', 'torso',
                         'body', 'abdomen', 'thorax'})

_WORD_RE = re.compile(r'[^a-z]+')


def bone_words(name: str) -> set:
    """Lower-case word set of a bone name, digits stripped."""
    return {w for w in _WORD_RE.split(name.lower()) if w}


def is_loose_bone(name: str) -> bool:
    """Whether the bone is a link of a loose-while-alive chain."""
    return bool(bone_words(name) & LOOSE_WHILE_ALIVE)
