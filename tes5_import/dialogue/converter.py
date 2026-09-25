"""Oblivion (TES4) -> Skyrim (TES5) dialogue / quest conversion.

Rewritten per the `oblivion-to-skyrim-dialog` skill. The guiding standard is
BEHAVIORAL FIDELITY: a converted plugin should make NPCs say the same lines, to
the same people, at the same times, advancing the same quests — not merely
contain records of the right signatures.

Architecture (the key change from the old universal-quest design):

  * Quest ownership follows the two engines' actual gating models:
      - Oblivion evaluates each INFO only while that INFO's own QSTI quest is
        running (a topic's INFO list is a union across quests).
      - Skyrim's DIAL is owned by exactly ONE quest (QNAM) and its INFOs are
        only evaluated while that quest runs; vanilla models shared subjects
        as one DIAL per quest (Skyrim.esm has ~288 separate HELO topics).
    So: a SINGLE-quest topic is owned by its original quest (remapped) —
    native gating, and the runtime voice path (built from the owning Quest
    EditorID + Topic EditorID + InfoFormID) keeps matching the extracted
    audio. A SHARED (multi-quest) or quest-less topic cannot be faithfully
    owned by any one quest, so it is owned by the always-running synthetic
    quest `TES4DialogueGeneric`, and each of its INFOs gets a
    GetQuestRunning(own QSTI quest) gate reproducing Oblivion's per-INFO
    visibility. (Start-Game-Enabled quests are exempt from the injected gate —
    they run from a new game via the SEQ file, so the gate is redundant.)

  * Skyrim needs structure Oblivion has no source for, which we synthesize:
      - VTYP per speaking NPC (kept as custom TES4* voice types so the converted
        audio folders match) and GetIsVoiceType conditions per INFO.
      - DLBR branches (top-level vs. linked) from topic type + the TCLT graph.
      - One DLVW per owning quest (CK metadata only).

  * AddTopic visibility (no Skyrim equivalent) is re-expressed as conditions:
    GetStage gates derived from `AddTopic`/`SetStage` script analysis, plus
    GetIsID identity gates so conversation topics don't leak to every NPC.

  * Result scripts -> Papyrus VMAD fragments (via script_convert). This is the
    one transform with no data-only path; the fragment is built from the TES4
    SCTX source.

CTDA condition translation lives in dialog_conditions.py.
"""

import re
import struct
from collections import defaultdict

from ..base.text_reader import (get_formid_index_offset, info_result_script,
                                remap_formid)
from ..base.constants import ENGINE_GLOBAL_FORMIDS
from .say_topics import SAY_TOPIC_DISPOSITIONS
from ..base.equivalents import TES4_ITEM_FORMID_TO_SKYRIM
from ..record_types.common import (
    get_formid,
    get_int,
    get_str,
    pack_formid_subrecord,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint8_subrecord,
    pack_uint32_subrecord,
)
from ..base.conditions import convert_ctda_list_with_strings

_PLAYER_FORMID = 0x14
_PLAYER_BASE_FID = 0x07     # NPC_ Player — see text_reader.PLAYER_BASE_FID

#: Remapped fids of TES4 topics with NO INFOs; never emitted, so no TCLT dangles.
EMPTY_DIAL_FIDS: set = set()

#: (info_fid24, resp_num) -> spoken text; the .lip generator needs the transcript.
lip_texts: dict = {}

#: Remapped fids of quests that can ever RUN; QNAM is a hard runtime gate.
startable_quests: set = set()


def get_lip_texts() -> dict:
    """Return the {(info_fid24, resp_num): text} map from the last
    build_dialog_groups run."""
    return lip_texts

# TES4 DIAL.Type enum
DIAL_TYPE_TOPIC = 0
DIAL_TYPE_CONVERSATION = 1
DIAL_TYPE_COMBAT = 2
DIAL_TYPE_PERSUASION = 3
DIAL_TYPE_DETECTION = 4
DIAL_TYPE_SERVICE = 5
DIAL_TYPE_MISC = 6


# ===========================================================================
# QUST conversion
# ===========================================================================













""" TES4 CTDA function indices used when resolving quest-target stage gates. """
_FUNC_GET_STAGE = 58
_FUNC_GET_STAGE_DONE = 59

# CTDA operator = the top 3 bits of the type byte.
_CTDA_OPS = {
    0x00: lambda a, b: a == b,
    0x20: lambda a, b: a != b,
    0x40: lambda a, b: a > b,
    0x60: lambda a, b: a >= b,
    0x80: lambda a, b: a < b,
    0xA0: lambda a, b: a <= b,
}




# TES4 CTDA functions that express quest TIMING (when a line is live). These are
# the conditions a choice-reached response topic must inherit from the greeting
# that reveals it, so a promoted top-level topic doesn't appear before its time.
# 56 GetQuestRunning, 58 GetStage, 59 GetStageDone, 99 GetQuestCompleted.
_QUEST_STATE_FUNCS = frozenset({56, 58, 59, 99})

# Additional PLAYER-progress gates a revealing greeting can carry, inherited by
# its choice targets alongside the quest-state ones. Oblivion questlines often
# track progress as the player's rank in a quest faction rather than a stage —
# Agronak's challenge greetings are gated GetFactionRank(ArenaCombatants)==7
# ON TARGET (the player), and without inheriting that the promoted "Yes, I wish
# to challenge you" topic sits in his menu from the first conversation. Only
# the run-on-target form is a progress gate (the subject form describes the
# SPEAKER, and the target topic already carries its own audience conditions).
# 71 GetInFaction, 73 GetFactionRank.
_PLAYER_PROGRESS_FUNCS = frozenset({71, 73})

# Legacy variable reads (53 GetScriptVariable / 79 GetQuestVariable) are ALSO
# timing gates ("set Arena.ChallengeAgronak to 1" both advances state and
# retires the greeting); they inherit as translated GetVMScriptVariable/
# GetVMQuestVariable conditions with their CIS2 variable name riding along.
_VAR_STATE_FUNCS = frozenset({53, 79})










# FormID -> effective priority (0-100), from the most recent
# compute_quest_priorities() call. _quest_dnam reads this so the WRITTEN
# QUST.DNAM.Priority carries the container clamp — that byte is what the engine
# arbitrates dialogue on (and what pits a quest ALIAS PACKAGE against an actor's
# standing schedule). DIAL PNAM must NOT be derived from it; see convert_DIAL.
_QUEST_PRIORITY_OVERRIDE: dict = {}







# Oblivion shipped TWO journal texts for a control-tutorial stage — a gamepad
# variant and a keyboard/mouse variant — and the engine picked by platform at
# runtime (the same `if isXbox == 0 ... else` split that guards the matching
# MessageBox calls in MQ01Script).  Skyrim has no such selector: it renders
# every QSDT/CNAM pair the record carries, so emitting both left the console
# text showing on PC ("Use the left stick to move around", "press A to equip")
# and, because the objective takes the FIRST non-empty text, made the gamepad
# line the visible objective.  Keep the PC variant only.
#
# Only MQ01 (the tutorial) has these pairs — 7 stages — so the match is
# deliberately narrow: a stage must have MULTIPLE texts and the pair must
# split cleanly into one gamepad-only and one PC-only reading, otherwise all
# texts are kept untouched.
_GAMEPAD_TEXT_RE = re.compile(
    r'left stick|right stick|d-pad|dpad|right trigger|left trigger'
    r'|\bpress [ABXY]\b|\bbumper\b|holding [ABXY]\b|pull the (right|left)',
    re.IGNORECASE)
_PC_TEXT_RE = re.compile(
    r'\bmouse\b|\bshift\b|\bspacebar\b|\bTAB\b|\bctrl\b|\bclick\b'
    r'|number key|&sUActn', re.IGNORECASE)


# Oblivion's journal text embeds control-name tokens (`&sUActnForward;`) that
# its UI expanded to the player's live key binding.  Skyrim has no such
# expansion and prints the token verbatim, so the tutorial read "To move
# forward, &sUActnForward;".  Substitute Skyrim's DEFAULT PC bindings, phrased
# to fit the surrounding sentence ("To move forward, press W").  Only MQ01
# uses these — 9 occurrences.
_CONTROL_TOKENS = {
    'sUActnForward':   'press W',
    'sUActnBack':      'press S',
    'sUActnSldleft':   'press A',
    'sUActnSldright':  'press D',
    'sUActnRun':       'hold Shift',
    'sUActnActivate':  'press E',
    'sUActnMenumode':  'press Tab',
    'sUActnRdyitem':   'press R',
    'sUActnUse':       'click the left mouse button',
    'sUActnBlock':     'click the right mouse button',
    'sUActnCast':      'click the right mouse button',
    'sUActnCrouch':    'press Ctrl',
    'sUActnJump':      'press Space',
}
_CONTROL_TOKEN_RE = re.compile(r'&(\w+);')












# ===========================================================================
# DIAL topic classification (Type -> Category/Subtype/SNAM, bark detection)
# ===========================================================================

# Known reserved EditorID -> (TES5 subtype enum, SNAM 4-char code, category).
# Category enum: 0 Topic, 3 Combat, 5 Detection, 6 Service, 7 Misc.
# Reserved Oblivion EditorID -> (Subtype, SNAM, Category).
#
# **SNAM is the field that matters.** TESTopic::LoadForm (SkyrimSE.exe RVA
# 0x3a6fa8) looks SNAM's 4-character tag up in the engine's subtype table and
# writes BOTH the runtime subtype (the matching row's index) and the category
# from it, overwriting whatever DATA held -- and SNAM is stored after DATA in
# all 15,037 vanilla DIALs, so it always wins. See
# docs/reference/dialogue_engine_contracts.md.
#
# The subtype NUMBERS below therefore only have to be well-formed, not exact;
# they are the values vanilla Skyrim.esm happens to store (Hello=73, GoodBye=72,
# Idle=88, Attack=20, ...). The engine's own table numbers those same tags six
# higher (HELO=79, GBYE=78, IDLE=94, ATCK=26), and vanilla itself carries both
# variants -- 288 HELO records say 73 and 9 say 79 -- which is only possible
# because nothing reads the field. Do not "fix" a converted topic by adjusting
# these numbers; check its SNAM instead.
#
# (Note the on-disk order is flags U8 + SUBTYPE U8 + CATEGORY U16, not the
# reverse: vanilla HELO reads DATA=00 49 07 00 = subtype 0x49, category 7.)
_EDID_SUBTYPE = {
    'GREETING':       (73, b'HELO', 7),
    'HELLO':          (73, b'HELO', 7),
    'GOODBYE':        (72, b'GBYE', 7),
    'IDLE':           (88, b'IDLE', 7),
    'Idle':           (88, b'IDLE', 7),
    'IdleChatter':    (88, b'IDLE', 7),
    'Attack':         (20, b'ATCK', 3),
    'PowerAttack':    (21, b'POAT', 3),
    'Hit':            (23, b'HIT_', 3),
    'Block':          (29, b'BLOC', 3),
    'Bash':           (22, b'BASH', 3),
    'Flee':           (24, b'FLEE', 3),
    'Bleedout':       (25, b'BLED', 3),
    'Yield':          (24, b'FLEE', 3),   # nearest combat de-escalation bark
    'Steal':          (32, b'STEA', 3),
    'Assault':        (36, b'ASSA', 3),
    'Murder':         (37, b'MURD', 3),
    'Trespass':       (43, b'TRES', 3),
    'NoticeCorpse':   (70, b'NOTI', 7),
    'Corpse':         (70, b'NOTI', 7),
    'TimeToGo':       (71, b'TITG', 7),
    'ObserveCombat':  (69, b'OBCO', 7),
    # Detection (category 5)
    'NoticedSomething': (51, b'NOTA', 5),
    'Noticed':        (51, b'NOTA', 5),
    'Seen':           (51, b'NOTA', 5),
    'Lost':           (57, b'LOTN', 5),
    'Unseen':         (57, b'LOTN', 5),
    # Oblivion NPC-to-NPC conversation system topics — never player-selectable
    'AnswerStatus':   (88, b'IDLE', 7),
    'TRANSITION':     (88, b'IDLE', 7),
}

# NOTE on NPC-addressed HELLO lines: an Oblivion HELLO INFO whose
# `GetIsID(<npc>)[Target]` names another NPC is the opening line of an
# engine-scheduled NPC-to-NPC conversation, which Skyrim cannot run (HELO is
# only ever evaluated against the player, and ACAC — tried 2026-08-07 — is the
# "ActorCollidewithActor" bump bark, not a conversation channel).  The
# quest-advancing chains are restored by npc_conversations.py: their heads are
# reparented onto synthesized hidden topics and replayed by a generated driver
# quest.  Heads left behind here stay in HELO, where their target condition
# can never pass — dead weight, equivalent to the deliberate NPC-to-NPC drop.

# Subtypes that are barks (situational, not player-selectable, no DLBR/no BNAM).
_BARK_SUBTYPES = frozenset(
    sub for (sub, _snam, _cat) in _EDID_SUBTYPE.values() if sub != 0
)

# DIAL topics to skip entirely (mechanics with no Skyrim equivalent, or test data)
_SKIP_TYPES = frozenset({DIAL_TYPE_PERSUASION, DIAL_TYPE_SERVICE})
_SKIP_EDIDS = frozenset({
    'CreatureResponses', 'SECreatureResponses', 'TamrielGateResponses', 'ANY',
    # InfoRefusal (DATA.Type 6 Misc, not a Type-3 persuasion topic so not caught
    # by _SKIP_TYPES) is the persuasion/disposition refusal line ("That's
    # privileged information. I'm sorry."). Skyrim has no persuasion mechanic to
    # trigger it, and it is conditionless under the always-running Generic quest,
    # so as an IDLE bark it fired as EVERY NPC's walk-past line. No equivalent.
    'InfoRefusal',
    # Oblivion's EMOTION-RESPONSE channels. These are not topics the player ever
    # picks: Oblivion's engine selects one after a player line to voice the
    # NPC's reaction to it (an angry reply gets AngerReceive, a question gets
    # QuestionGeneral, and so on). Skyrim has no such channel -- its engine
    # picks a response only through a topic the player selected, so there is
    # nothing to route these to. Converted, they became reachable TOPICS: the
    # emulator showed Varel Morvayn with 11 of them ("SadGeneral",
    # "FearGeneral", "AngerReceive", ...) hanging off his greeting, which is
    # both wrong and player-visible nonsense.
    #
    # Deliberately listed by name rather than by their contiguous FormID block
    # 0002410E..0002411C: CharGenEmperor (00024119) sits inside that range and
    # is a real main-quest conversation that must still convert.
    #
    # Rumors (INFOGENERAL) is NOT here on purpose -- it is the one Oblivion
    # conversation channel Skyrim does have (subtype 2 RUMO, special-cased by
    # the engine at 0x595454), so it converts as a normal topic.
    'SadGeneral', 'QuestionGeneral', 'FearGeneral', 'AngerReceive',
    'HappyReceive', 'SurpriseReceive', 'FollowupNegative', 'FollowupPositive',
    'AnswerNegative', 'AnswerPositive', 'AnswerStatus', 'NeutralReceive',
    'Question',
})

# Oblivion Service-type topics that become real Skyrim service dialogue.
# 'Barter'/'Training' hold the voiced lines NPCs speak as those menus open in
# Oblivion; they convert to player-selectable Custom topics whose INFOs open
# the corresponding Skyrim menu via a Papyrus fragment (ShowBarterMenu /
# ShowTrainingMenu). Every other Service topic (BarterExit, ServiceRefusal,
# Repair, Recharge, Travel, ...) stays skipped.
#
# Skyrim's engine does define the whole Service subtype family -- SERU, REPA,
# TRAV, TRAI, BAEX, REEX, RECH, RCEX, TREX, all present in its subtype table --
# so these are not unrepresentable. They are skipped because vanilla Skyrim
# uses NONE of them: zero DIAL records in Skyrim.esm carry a Service subtype,
# because services are driven entirely from Papyrus menus rather than from
# subtype-tagged dialogue. Converting them would produce topics the engine
# never asks for.
# Maps EditorID -> (service kind, player prompt used as the DIAL FULL).
SERVICE_MENU_TOPICS = {
    'Barter':   ('barter', 'What have you got for sale?'),
    'Training': ('training', 'I would like some training.'),
}


def service_menu_kind(rec: dict) -> str:
    """'barter' / 'training' for the two convertible Service topics, else ''."""
    if get_int(rec, 'DATA.Type') != DIAL_TYPE_SERVICE:
        return ''
    info = SERVICE_MENU_TOPICS.get(get_str(rec, 'EditorID', ''))
    return info[0] if info else ''


#: Type-1 topics that are NOT NPC-to-NPC chatter, so they survive the drop.
CONV_KEEP_EDIDS = frozenset({
    # Oblivion's one conversation channel Skyrim also has: subtype RUMO, a real
    # player-selectable topic. Converts correctly already.
    'INFOGENERAL',
    # Engine bark channels that happen to carry DIAL Type 1 (see _EDID_SUBTYPE):
    # HELLO is the genuine ambient greeting, GOODBYE a real Skyrim subtype.
    'HELLO', 'GOODBYE',
    # Bark/idle channels routed by _EDID_SUBTYPE rather than by DATA.Type.
    'IdleChatter',
})


def is_npc_to_npc_conversation(rec: dict) -> bool:
    """True for an Oblivion Type-1 topic that is pure NPC-to-NPC chatter.

    The `*NQDResponses` / `*RumorResponses` / interrogation families,
    never player-selectable in Oblivion and dropped here.

    🛑 A script-driven Type-1 topic is NOT dropped: 293 of 535 are
    spoken by a real `Say`/`SayTo`/`StartConversation` call, including
    every CharGen topic.  `_SAY_TOPIC_DISPOSITIONS` is filled before the
    first skip test so this check can see them.

    See: docs/commentary/tes5_import_dialogue.md#script-driven-type-1-topics
    """
    if get_int(rec, 'DATA.Type') != DIAL_TYPE_CONVERSATION:
        return False
    if get_str(rec, 'EditorID', '') in CONV_KEEP_EDIDS:
        return False
    if not SAY_TOPIC_DISPOSITIONS:
        # Fail SAFE, never silently. An empty map means the caller reached a
        # skip test before the say-driven scan ran (dialog_unlocks does: its
        # build_unlock_plan runs long before build_dialog_groups), and treating
        # that as "nothing is script-driven" would drop all 293 scripted topics
        # including CharGen. Keep the topic instead — build_dialog_groups makes
        # the real decision later, with the map populated.
        return False
    return (get_formid(rec, 'FormID') & 0xFFFFFF) not in SAY_TOPIC_DISPOSITIONS


def should_skip_dial(rec: dict) -> bool:
    dtype = get_int(rec, 'DATA.Type')
    if dtype in _SKIP_TYPES and not service_menu_kind(rec):
        return True
    edid = get_str(rec, 'EditorID', '')
    if edid in _SKIP_EDIDS:
        return True
    if edid.startswith('Test') or edid.startswith('MarkNTest'):
        return True
    if is_npc_to_npc_conversation(rec):
        return True
    return False


def classify_topic(edid: str, dtype: int):
    """Return (category, subtype, snam_code, is_bark) for a DIAL topic.

    Maps the coarse TES4 Type enum + reserved EditorID onto Skyrim's finer
    Category/Subtype/SNAM, per the skill's dial-info mapping.
    """
    info = _EDID_SUBTYPE.get(edid or '')
    if info:
        subtype, snam, category = info
        return category, subtype, snam, (subtype in _BARK_SUBTYPES)

    # Fall back on the TES4 Type enum (Skyrim subtype/category from real data).
    if dtype == DIAL_TYPE_COMBAT:
        return 3, 20, b'ATCK', True       # Attack (category 3 Combat)
    if dtype == DIAL_TYPE_DETECTION:
        return 5, 51, b'NOTA', True       # Notice Alert (category 5 Detection)
    if dtype == DIAL_TYPE_MISC:
        return 7, 88, b'IDLE', True       # Idle (category 7 Misc)
    # Type 0 Topic, 1 Conversation, 3 Persuasion (if not skipped) -> Custom topic
    return 0, 0, b'CUST', False


def convert_DIAL(rec: dict, *, info_count: int, dlbr_fid: int,
                 quest_fid: int, category: int, subtype: int,
                 snam: bytes, priority: float = 50.0,
                 edid_override: str = None, formid_override: int = None) -> bytes:
    """DIAL — Dialog Topic. Order: EDID FULL PNAM [BNAM] QNAM DATA SNAM TIFC.

    quest_fid is the REMAPPED owning quest (original QSTI quest, or the synthetic
    generic dialogue quest for orphan/bark topics). edid_override/formid_override
    let the per-quest bark split emit multiple DIALs from one source record with
    unique EditorIDs and FormIDs.

    priority stays at the vanilla 50.0 DEFAULT and no converter passes it —
    quest arbitration belongs on QUST.DNAM.Priority (see
    compute_quest_priorities). Vanilla leaves PNAM at 50.0 on 5375/6535 player
    topics and 659/664 Misc/greeting topics; ranking a greeting above the topic
    list here cost Pinarus every topic he owned.
    """
    subs = b''
    edid = edid_override if edid_override is not None else get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)
    subs += pack_subrecord('PNAM', struct.pack('<f', priority))
    if dlbr_fid:
        subs += pack_formid_subrecord('BNAM', dlbr_fid)
    if quest_fid:
        subs += pack_formid_subrecord('QNAM', quest_fid)
    # DATA = TopicFlags(U8) + Category(U8) + Subtype(U16), per xEdit
    # wbDefinitionsTES5 and confirmed in the engine: TESTopic::LoadForm reads
    # DATA's 4 bytes into TESTopic+0x30, and the SNAM handler at RVA 0x3a6ff0
    # writes the category to +0x31 (byte 1) and the subtype to +0x32 (the u16).
    # Writing category into the U16 puts the subtype byte where the engine
    # reads category, and an out-of-range category crashes the engine at
    # startup while it indexes its per-category topic dispatch tables.
    #
    # Beware when checking this against export/*.txt: DATA is printed there as
    # a big-endian u32, so vanilla Hello displays as 00490700 while its actual
    # bytes are 00 07 49 00 (category 7 Misc, subtype 0x49 = 73). Reading the
    # printed form as raw bytes makes the two fields look transposed.
    #
    # The values themselves are inert at runtime -- TESTopic::LoadForm
    # re-derives both from SNAM afterwards, which is why vanilla contains stale
    # subtypes (288 HELO records say 73, 9 say 79). SNAM is the field to check
    # when a converted topic misbehaves; see docs/reference/dialogue_engine_contracts.md.
    subs += pack_subrecord('DATA', struct.pack('<BBH', 0, category & 0xFF,
                                               subtype))
    subs += pack_subrecord('SNAM', snam)
    subs += pack_uint32_subrecord('TIFC', info_count)
    formid = (formid_override if formid_override is not None
              else get_formid(rec, 'FormID'))
    return pack_record('DIAL', formid, get_int(rec, 'RecordFlags'), subs)


# ===========================================================================
# INFO conversion
# ===========================================================================

# ENAM flag bits that are bit-compatible between TES4 DATA.Flags and TES5 ENAM:
#   0x01 Goodbye, 0x02 Random, 0x04 Say once, 0x10 Info Refusal, 0x20 Random end
# (0x08 Run Immediately and 0x40 Run for Rumors have no faithful TES5 meaning.)
_ENAM_COMPATIBLE_MASK = 0x37

# "Hours until reset" for an ambient bark line, in the engine's stored form
# trunc(days * 65535).  0.5 hours is vanilla Skyrim's dominant choice: 2809 of
# its 5287 HELO lines (53%) use exactly this value.
#   0.5h / 24h * 65535 = 1365
_BARK_RESET_HOURS = 0.5
_BARK_RESET_TICKS = int(_BARK_RESET_HOURS / 24.0 * 65535)   # 1365


def _build_info_script_properties(result_script: str, xref,
                                  well_known_props: dict = None) -> dict:
    """Build VMAD property bindings for an INFO result script via ScriptConverter.

    Only properties the generated .psc actually DECLARES are emitted.
    `well_known_props` is a name->FormID REGISTRY of synthesized records
    (TES4Unlock_*, TES4Msg_*, TES4Fame, ...) that resolve_property_formid
    cannot see because they exist only in the output; it is looked up per
    declared property, never merged wholesale — the registry holds ~1,880
    entries and copying it into every fragment wrote a 70 KB VMAD onto 4,985
    INFOs (a third of a gigabyte of properties no script declares, each one
    logged by the engine as "cannot be initialized because the script no
    longer contains that property"). Same per-property lookup object_scripts
    already does.
    """
    if not xref:
        return {}
    from script_convert.converter import ScriptConverter
    offset = get_formid_index_offset()
    try:
        conv = ScriptConverter(xref)
        conv.convert_fragment(result_script, 'TopicInfo')
    except Exception:
        return {}
    from script_convert.constants import (resolve_property_formid,
                                          wants_placed_reference)
    well_known = well_known_props or {}
    props = {}
    for prop_edid, ptype in conv._property_refs.items():
        low = prop_edid.lower()
        if low in ('player', 'playerref'):
            props[prop_edid] = (_PLAYER_BASE_FID if ptype == 'ActorBase'
                                else _PLAYER_FORMID)
            continue
        # Engine globals keep their vanilla FormID — see
        # object_scripts.ENGINE_GLOBAL_FORMIDS.
        if low in ENGINE_GLOBAL_FORMIDS:
            props[prop_edid] = ENGINE_GLOBAL_FORMIDS[low]
            continue
        # Synthesized output-only records are already remapped in the registry.
        if prop_edid in well_known:
            props[prop_edid] = well_known[prop_edid]
            continue
        fid_hex = resolve_property_formid(xref, prop_edid)
        if not fid_hex:
            continue
        # A reference-typed property naming a BASE means the placed instance;
        # the VM refuses an NPC_/CREA/ACTI/LIGH base into it and the property
        # reads None. Bind the base's one placed ref instead (see
        # constants.wants_placed_reference).
        if wants_placed_reference(ptype) and \
                xref.record_type.get(fid_hex, '') in ('NPC_', 'CREA',
                                                      'ACTI', 'LIGH'):
            ref_hex = xref.unique_placed_ref(fid_hex)
            if ref_hex:
                fid_hex = ref_hex
        try:
            raw_fid = int(fid_hex, 16)
        except (ValueError, TypeError):
            continue
        if raw_fid == 0:
            continue
        props[prop_edid] = (TES4_ITEM_FORMID_TO_SKYRIM.get(raw_fid)
                            or remap_formid(raw_fid, offset))
    return props


# Shared static fragment scripts (script_convert/static_scripts) attached to
# the synthesized service-menu FALLBACK INFO (_build_service_fallback_info),
# which has no source record and so no per-INFO TES4_TIF__ fragment.  Every
# real INFO keeps its own fragment; script_convert appends the menu call to
# the fragments of service-topic lines.
SERVICE_MENU_SCRIPTS = {
    'barter': 'TES4_ShowBarterMenu',
    'training': 'TES4_ShowTrainingMenu',
}


def convert_INFO(rec: dict, *, injected_ctdas: bytes = b'',
                 fid_to_edid: dict = None, well_known_props: dict = None,
                 xref=None, reveal_props: dict = None,
                 service_menu: str = '', bark_dial_fids: set = None,
                 menu_topic_fids=(), script_vars: dict = None) -> bytes:
    """INFO record: EDID [VMAD] ENAM CNAM [TCLT...] [TRDT NAM1 NAM2 NAM3]* CTDAs.

    injected_ctdas: packed Skyrim-required gates. reveal_props
    ({global_name: GLOB formid}): AddTopic globals its End fragment sets.
    service_menu ('barter'/'training'): the menu its fragment opens.
    bark_dial_fids / menu_topic_fids (bark INFOs only): the TCLT filter's
    inputs, see _info_tclt.

    See: docs/commentary/tes5_import_dialogue.md#info-tclt-choice-filter
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += _info_vmad(rec, reveal_props, service_menu, xref,
                       well_known_props)
    subs += _info_enam(rec, bark_dial_fids)
    subs += pack_subrecord('CNAM', struct.pack('<B', 0))
    subs += _info_tclt(rec, bark_dial_fids, menu_topic_fids)
    subs += _info_responses(rec)
    subs += _info_conditions(rec, injected_ctdas, script_vars)
    return pack_record('INFO', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


def _info_vmad(rec: dict, reveal_props, service_menu: str, xref,
               well_known_props) -> bytes:
    """The VMAD subrecord for this INFO's TES4_TIF__<fid> fragment, or b''.

    Eligibility is `info_needs_fragment` — the SAME function script_convert's
    emitter calls, never re-derived locally. reveal_props / service_menu are
    this INFO's already-resolved answers to the questions that function
    otherwise looks up in its maps, so they are passed as single-entry maps.

    See: docs/commentary/tes5_import_dialogue.md#info-fragment-emission
    """
    info_fid = get_str(rec, 'FormID') or ''
    result_script = info_result_script(rec)
    code_lines = []
    if result_script:
        code_lines = [ln for ln in result_script.strip().splitlines()
                      if ln.strip() and not ln.strip().startswith(';')]
    from script_convert.pipeline import info_needs_fragment
    _fid24 = 0
    try:
        _fid24 = int(info_fid, 16) & 0xFFFFFF
    except (TypeError, ValueError):
        pass
    _reveals = {_fid24: list(reveal_props)} if reveal_props else {}
    _services = ({(get_str(rec, 'ParentDIAL') or ''): service_menu}
                 if service_menu else {})
    if not (info_fid and info_needs_fragment(rec, _reveals, _services)):
        return b''
    from script_convert.pipeline import build_vmad_info_fragment
    prop_vals = (_build_info_script_properties(result_script, xref,
                                               well_known_props)
                 if code_lines else {})
    if reveal_props:
        prop_vals.update(reveal_props)
    return pack_subrecord('VMAD', build_vmad_info_fragment(
        info_fid, property_values=prop_vals or None))


def _info_enam(rec: dict, bark_dial_fids) -> bytes:
    """ENAM (Flags U16 + Reset U16) from TES4 DATA.Flags.

    Reset is the re-play lockout TES4 has no field for; an ambient bark line
    gets _BARK_RESET_TICKS, except SAY-ONCE (0x04) lines, which are already
    permanently locked after one play.

    See: docs/commentary/tes5_import_dialogue.md#info-enam-reset-timer
    """
    tes4_flags = get_int(rec, 'DATA.Flags')
    reset = 0
    if bark_dial_fids is not None and not (tes4_flags & 0x04):
        reset = _BARK_RESET_TICKS
    return pack_subrecord('ENAM', struct.pack(
        '<HH', tes4_flags & _ENAM_COMPATIBLE_MASK, reset))


def _info_tclt(rec: dict, bark_dial_fids, menu_topic_fids=()) -> bytes:
    """TCLT choice links (follow-up topics), in source order.

    A bark INFO keeps only choices pointing at a CONVERSATION topic that has
    no top-level branch; a choice into another bark (split/merged, so the link
    would dangle), into a menu topic, or into a zero-INFO topic is dropped.

    See: docs/commentary/tes5_import_dialogue.md#info-tclt-choice-filter
    """
    def _keep_choice(cfid: int) -> bool:
        """True when this choice target survives the bark/menu/empty filters."""
        if not cfid:
            return False
        if bark_dial_fids is not None and (cfid in bark_dial_fids
                                           or cfid in menu_topic_fids):
            return False
        if cfid in EMPTY_DIAL_FIDS:
            return False
        return True

    out = b''
    choice_count = get_int(rec, 'ChoiceCount')
    if choice_count > 0:
        for i in range(choice_count):
            cfid = get_formid(rec, f'Choice[{i}]')
            if _keep_choice(cfid):
                out += pack_formid_subrecord('TCLT', cfid)
    else:
        cfid = get_formid(rec, 'TCLT.Choice')
        if _keep_choice(cfid):
            out += pack_formid_subrecord('TCLT', cfid)
    return out


def _info_responses(rec: dict) -> bytes:
    """TRDT [NAM1] NAM2 NAM3 per response, in source order.

    TES4's 12-byte TRDT becomes TES5's 24-byte one: EmotionType(U32)
    EmotionVal(U32) Unused(4) RespNum(U8) Unused(3) Sound(FormID=0)
    Flags(U8=1 UseEmotionAnim) Unused(3). Text and emotion are preserved.
    """
    out = b''
    rc = get_int(rec, 'ResponseCount')
    for i in range(rc):
        emotion = get_int(rec, f'Response[{i}].EmotionType')
        emotion_val = max(0, min(100, get_int(rec, f'Response[{i}].EmotionValue')))
        text = get_str(rec, f'Response[{i}].ResponseText')
        actor_notes = get_str(rec, f'Response[{i}].ActorNotes')
        resp_num = get_int(rec, f'Response[{i}].ResponseNumber') or (i + 1)
        out += pack_subrecord('TRDT', struct.pack('<IiI B3x I B3x',
                                                  emotion, emotion_val, 0,
                                                  resp_num, 0, 1))
        if text:
            out += pack_string_subrecord('NAM1', text)
        out += pack_string_subrecord('NAM2', actor_notes or '')
        out += pack_string_subrecord('NAM3', '')
    return out


def _info_conditions(rec: dict, injected_ctdas: bytes,
                     script_vars) -> bytes:
    """injected_ctdas first, then the translated TES4 CTDAs (each + any CIS2).

    The strings variant turns legacy GetScriptVariable/GetQuestVariable reads
    into GetVMScriptVariable/GetVMQuestVariable, whose variable NAME travels in
    a CIS2 right after the CTDA — what makes script-variable-gated dialogue
    evaluate in Skyrim. A Say-driven parent topic retargets or drops its
    RunOn=Target conditions per SAY_TOPIC_DISPOSITIONS.
    """
    out = injected_ctdas
    say_disp = SAY_TOPIC_DISPOSITIONS.get(
        get_formid(rec, 'ParentDIAL') & 0xFFFFFF)
    say_ref = say_disp[1] if say_disp and say_disp[0] == 'ref' else None
    say_drop = bool(say_disp) and say_disp[0] == 'drop'
    for ctda, cis2 in convert_ctda_list_with_strings(
            rec, script_vars,
            run_on_target_ref=say_ref, drop_run_on_target=say_drop):
        out += pack_subrecord('CTDA', ctda)
        if cis2:
            out += pack_string_subrecord('CIS2', cis2)
    return out


# ===========================================================================
# DLBR / DLVW synthesis
# ===========================================================================

def make_dlbr(fid: int, edid: str, quest_fid: int, dial_fid: int,
              top_level: bool) -> bytes:
    """DLBR — Dialog Branch. top_level controls menu visibility vs link-only."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_formid_subrecord('QNAM', quest_fid)
    subs += pack_uint32_subrecord('TNAM', 0)            # Player
    subs += pack_uint32_subrecord('DNAM', 1 if top_level else 0)
    subs += pack_formid_subrecord('SNAM', dial_fid)
    return pack_record('DLBR', fid, 0, subs)


def make_dlvw(fid: int, edid: str, quest_fid: int,
              branch_fids: list, topic_fids: list) -> bytes:
    """DLVW — Dialog View (CK metadata; no runtime effect)."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_formid_subrecord('QNAM', quest_fid)
    for bfid in branch_fids:
        subs += pack_formid_subrecord('BNAM', bfid)
    for tfid in topic_fids:
        subs += pack_formid_subrecord('TNAM', tfid)
    subs += pack_uint32_subrecord('ENAM', 0)
    subs += pack_uint8_subrecord('DNAM', 0)
    return pack_record('DLVW', fid, 0, subs)


# ===========================================================================
# Voice file naming
# ===========================================================================

def voice_file_prefix(quest_edid: str, topic_edid: str) -> str:
    """The `<quest>_<topic>` prefix Skyrim uses to resolve a voice file.

    Runtime path: Sound\\Voice\\<plugin>\\<VoiceType>\\<prefix>_<fid8>_<n>.fuz

    TRANSCRIBED FROM THE ENGINE (GOG/AE SkyrimSE.exe, function at file
    offset 0x3a5460 / va 0x1403a6060; the sibling at 0x1403a62b9 builds
    the same prefix for the "%s_%08X_%u" case). Do NOT re-derive this from
    observed filenames — Oblivion's own names follow a DIFFERENT rule, and
    fitting them produces a prefix the engine never asks for.

    The engine loads the TOPIC's owning quest ([rcx+0x40]) EditorID into
    buffer A and the topic's own EditorID into buffer B, then:

        lenA = strlen(A); lenB = strlen(B)
        if lenA + lenB > 25:            # cmp rax,0x19 / jbe
            if lenA > 10:               # cmp rcx,0xa / jbe
                A[10] = 0               # mov byte[rsp+0x14a],0
                B[15] = 0               # lea rax,[rsp+0x3f]
            else:
                B[25 - lenA] = 0        # lea rax,[rsp+0x49]; sub rax,rcx
        sprintf(out, "%s_%s", A, B)

    So a combined length of 25 or less is used verbatim; past that the
    quest is only cut when it exceeds 10, and the topic absorbs the rest.
    This matches Skyblivion's `Skyblivion - Copy voice files.pas`
    (InfoFileName), which was right all along.

    Topic with no EditorID: quest + '_' (double underscore before the
    FormID). All lowercase; the FormID component is the 8-hex value with
    the load-order byte zeroed.
    """
    if topic_edid:
        q, t = quest_edid, topic_edid
        if len(q) + len(t) > 25:
            if len(q) > 10:
                q, t = q[:10], t[:15]
            else:
                t = t[:25 - len(q)]
        return f"{q}_{t}".lower()
    return f"{quest_edid}_".lower()


# ===========================================================================
# Pre-scan helpers
# ===========================================================================

def collect_tclt_target_fids(by_type: dict) -> set:
    """DIAL FormIDs that are TCLT choice targets (reachable only via a parent
    INFO's choice list) -> these get a Normal (non-top-level) branch."""
    targets = set()
    for rec in by_type.get('INFO', []):
        cc = get_int(rec, 'ChoiceCount')
        for i in range(cc):
            cfid = get_formid(rec, f'Choice[{i}]')
            if cfid:
                targets.add(cfid)
        cfid = get_formid(rec, 'TCLT.Choice')
        if cfid:
            targets.add(cfid)
    return targets


# owner quest fid -> converted GREETING topic fid, filled while bark topics are
# split per quest.  A ForceGreet PACKAGE must name the topic it opens (PDTO),
# and Skyrim keeps one bark topic per subtype per quest, so the package needs
# THIS quest's greeting rather than a single global one.
GREET_TOPIC_BY_QUEST: dict = {}

def _index_race_voices(by_type: dict) -> tuple:
    """(RACE fid24 -> per-gender voice race, RACE fid24 -> the plugin's EditorID).

    The plugin's own EditorID wins over the hardcoded Oblivion table, which
    cannot name a race the plugin invented and misnames one that reuses an
    Oblivion FormID for something else.

    See: docs/commentary/tes5_import_dialogue.md#voice-types-are-created-from-scratch
    """
    race_voice, race_edids = {}, {}
    for rr in by_type.get('RACE', []):
        rfid = get_formid(rr, 'FormID') & 0x00FFFFFF
        if not rfid:
            continue
        edid = get_str(rr, 'EditorID')
        if edid:
            race_edids[rfid] = edid
        m = get_formid(rr, 'VNAM.MaleVoice') & 0x00FFFFFF
        f = get_formid(rr, 'VNAM.FemaleVoice') & 0x00FFFFFF
        race_voice[rfid] = {'Male': m or rfid, 'Female': f or rfid}
    return race_voice, race_edids


def build_npc_to_vtyp_map(by_type: dict, num_new_masters: int,
                          master_export: dict = None) -> dict:
    """NPC/CREA FormID (remapped) -> VTYP FormID, from the VOICE the actor used.

    FO3/FNV actors name their voice type outright (NPC_.VTCK); Oblivion resolves
    it through the RACE record's VNAM per-gender override, not the literal race.
    `master_export` adds the masters' actors, read FIRST so this plugin's own
    records overwrite them, and each is keyed on its master_export KEY -- the id
    in OUR index space, which is what every consumer looks up.

    See: docs/commentary/tes5_import_dialogue.md#voice-types-are-created-from-scratch
    See: docs/commentary/tes4_export_falloutnv.md#voice-files
    """
    from ..base.equivalents import TES4_RACE_FID_TO_EDID, VOICE_TYPE_MAP
    from ..base.owned_records import FALLOUT_VTYP_BY_EDID
    authored_vtyp = {get_formid(v, 'FormID') & 0x00FFFFFF: fid
                     for v in by_type.get('VTYP', ())
                     for fid in (FALLOUT_VTYP_BY_EDID.get(
                         (get_str(v, 'EditorID') or '').strip().lower()),)
                     if fid}
    race_voice, race_edids = _index_race_voices(by_type)
    npc_to_vtyp = {}
    actor_sources = [((k, r) for k, r in (master_export or {}).items()
                      if r.get('Signature') in ('NPC_', 'CREA')),
                     ((r.get('FormID', '0'), r) for sig in ('NPC_', 'CREA')
                      for r in by_type.get(sig, []))]
    for fid_str, rec in (p for src in actor_sources for p in src):
        try:
            remapped = remap_formid(int(fid_str, 16), num_new_masters,
                                    is_own_id=True)
        except (TypeError, ValueError):
            continue
        vtyp = authored_vtyp.get(get_formid(rec, 'VTCK.Voice') & 0x00FFFFFF)
        if not vtyp:
            gender = 'Female' if (get_int(rec, 'ACBS.Flags') & 1) else 'Male'
            race_fid = get_formid(rec, 'RNAM.Race') & 0x00FFFFFF
            voice_race = race_voice.get(race_fid, {}).get(gender, race_fid)
            race_edid = (race_edids.get(voice_race)
                         or TES4_RACE_FID_TO_EDID.get(voice_race, 'Imperial'))
            vtyp = (VOICE_TYPE_MAP.get((race_edid, gender))
                    or VOICE_TYPE_MAP.get(('Imperial', gender), 0))
        if vtyp:
            npc_to_vtyp[remapped] = vtyp
    return npc_to_vtyp


# ===========================================================================
# Main dialogue group builder
# ===========================================================================

def make_generic_quest(writer, edid: str, full: str,
                        master_index=None) -> int:
    """Mint the catch-all quest that owns topics with no TES4 quest.

    See: docs/commentary/tes5_import_dialogue.md#script-driven-type-1-topics
    """
    if master_index is not None:
        existing = master_index.find_by_edid(b'QUST', edid)
        if existing:
            print(f"    reusing master's {edid} ({existing:08X})")
            return existing
    fid = writer.derive_formid('SYNTH_QUST', edid)
    q = pack_string_subrecord('EDID', edid)
    q += pack_string_subrecord('FULL', full)
    q += pack_subrecord('DNAM', struct.pack('<HBBII', 0x0011, 0, 0, 0, 0))
    q += pack_subrecord('NEXT', b'')
    q += pack_uint32_subrecord('ANAM', 0)
    writer.add_record('QUST', pack_record('QUST', fid, 0, q))
    return fid


def make_player_script_quest(writer, master_index=None) -> int:
    """Mint the start-game quest whose PlayerRef alias hosts player-base
    scripts.

    A TES4 script on the PLAYER base has no Skyrim equivalent host, so it
    runs from a quest alias instead.

    See: docs/commentary/tes5_import_dialogue.md#script-driven-type-1-topics
    """
    from script_convert.pipeline import build_vmad_quest_fragments
    from ..base.object_scripts import get_player_alias_scripts

    scripts = get_player_alias_scripts()
    if not scripts:
        return 0

    edid = 'TES4PlayerScripts'
    if master_index is not None:
        existing = master_index.find_by_edid(b'QUST', edid)
        if existing:
            print(f"    reusing master's {edid} ({existing:08X})")
            return existing

    fid = writer.derive_formid('SYNTH_QUST', edid)
    alias_id = 0
    # Skyrim subrecord order is EDID VMAD FULL DNAM — unanimous across all 912
    # vanilla QUSTs that carry a VMAD.
    q = pack_string_subrecord('EDID', edid)
    q += pack_subrecord('VMAD', build_vmad_quest_fragments(
        edid, [], None, None,
        alias_scripts=[(alias_id, scripts)], quest_fid=fid))
    q += pack_string_subrecord('FULL', 'TES4 Player Scripts')
    # Flags 0x0011 = StartGameEnabled + StartsEnabled; priority 0.
    q += pack_subrecord('DNAM', struct.pack('<HBBII', 0x0011, 0, 0, 0, 0))
    q += pack_subrecord('NEXT', b'')
    q += pack_uint32_subrecord('ANAM', alias_id + 1)   # Next Alias ID
    # The PlayerRef alias itself.  Same shape convert_QUST writes for forced-ref
    # aliases; PlayerRef always fills, but Optional (0x0002) keeps a fill
    # failure from taking the whole quest down with it.
    q += pack_uint32_subrecord('ALST', alias_id)
    q += pack_string_subrecord('ALID', 'Player')
    q += pack_uint32_subrecord('FNAM', 0x00000292)
    q += pack_formid_subrecord('ALFR', 0x00000014)
    q += pack_formid_subrecord('VTCK', 0)
    q += pack_subrecord('ALED', b'')
    writer.add_record('QUST', pack_record('QUST', fid, 0, q))
    names = ', '.join(s for s, _ in scripts)
    print(f"    player-base scripts hosted on {edid} alias 'Player': {names}")
    return fid


# Fake source-space DIAL FormIDs for the synthesized conversation-head topics
# (npc_conversations.py).  High in the 24-bit object space so they can never
# collide with a real plugin record; verified against the loaded DIALs anyway.
CONV_FAKE_FID_BASE = 0x00F40000
# Upper bound on synthesized chains, so import_plugin can RESERVE the whole
# window before hashing starts (these ids bypass derive_formid, so nothing
# else would know they are taken — the header then lands below them and a
# derived record can hash onto one).
CONV_FAKE_FID_COUNT = 0x1000
_CONV_FAKE_FID_BASE = CONV_FAKE_FID_BASE


def register_conversation_chains(plan, dials, infos, offset):
    """Register each NPC-to-NPC chain so its topics survive the Type-1 drop.

    See: docs/commentary/tes5_import_dialogue.md#script-driven-type-1-topics
    """
    used_low24 = {get_formid(d, 'FormID') & 0xFFFFFF for d in dials}
    assert not any((_CONV_FAKE_FID_BASE + i) & 0xFFFFFF in used_low24
                   for i in range(len(plan['chains']))), \
        'synthetic conversation DIAL fid range collides with a real DIAL'

    info_by_fid = {}
    for rec in infos:
        try:
            info_by_fid[int(rec.get('FormID', '0') or '0', 16)] = rec
        except ValueError:
            pass

    fake_by_index = {}
    for chain in plan['chains']:
        head = info_by_fid.get(chain['head_fid'])
        if head is None:
            continue
        if chain['index'] >= CONV_FAKE_FID_COUNT:
            raise ValueError(
                f"NPC-conversation chain index {chain['index']} exceeds the "
                f"reserved fake-FormID window of {CONV_FAKE_FID_COUNT}; raise "
                f"CONV_FAKE_FID_COUNT (import_plugin reserves exactly this "
                f"many, so a larger index lands on an unreserved id).")
        fake = _CONV_FAKE_FID_BASE + chain['index']
        fake_by_index[chain['index']] = fake
        head['ParentDIAL'] = f'{fake:08X}'
        dials.append({
            'Signature': 'DIAL',
            'FormID': f'{fake:08X}',
            'EditorID': chain['head_topic_edid'],
            'RecordFlags': '0',
            'QuestCount': '1',
            'Quest[0]': f"{chain['owner_quest_fid']:08X}",
            'DATA.Type': str(DIAL_TYPE_CONVERSATION),
            '_synth_conv': str(chain['index']),
        })
        listener = remap_formid(chain['tgt']['ref_fid'], offset)
        SAY_TOPIC_DISPOSITIONS[fake & 0xFFFFFF] = ('ref', listener)
        for tfid in chain['undrop_topic_fids']:
            SAY_TOPIC_DISPOSITIONS.setdefault(tfid & 0xFFFFFF,
                                              ('drop', None))
    return fake_by_index


def make_conversation_quest(writer, plan, synth_topic_fids, bark_topic_fids,
                             offset, plugin_stem):
    """Emit the TES4NPCConv<plugin> driver quest with its VMAD bound inline.

    Every property value is known by now (chain refs and topics are remapped
    TES4 records; head topics were just emitted), so the bindings are packed
    directly — no name-resolution pass, no placeholders.  The matching .psc
    is generated by script_convert.pipeline from the SAME plan (the
    message_menus mirroring contract).
    """
    from .conversations import chain_property_bindings
    from script_convert.pipeline import build_vmad_quest_fragments

    def _remap(fid):
        return remap_formid(fid, offset)

    props = {}
    for chain in plan['chains']:
        i = chain['index']

        def _resolve_hop(hop):
            if 'head_chain' in hop:
                return synth_topic_fids.get(hop['head_chain'], 0)
            edid = hop['topic_edid']
            _cat, subtype, _snam, is_bark = classify_topic(edid, 1)
            if is_bark:
                key = (_remap(chain['owner_quest_fid']), subtype)
                return bark_topic_fids.get(key, 0)
            return _remap(hop['topic_fid'])

        props.update(chain_property_bindings(chain, _remap, _resolve_hop))
        t0 = synth_topic_fids.get(i, 0)
        if t0:
            props[f'Conv{i}T0'] = t0

    edid = plan['quest_edid']
    fid = writer.derive_formid('SYNTH_QUST', edid)
    q = pack_string_subrecord('EDID', edid)
    q += pack_subrecord('VMAD', build_vmad_quest_fragments(
        edid, [], None,
        attached_script=(plan['script_name'], props), quest_fid=fid))
    q += pack_string_subrecord('FULL', 'TES4 NPC Conversations')
    # StartGameEnabled + StartsEnabled, priority 0 (it owns no dialogue).
    q += pack_subrecord('DNAM', struct.pack('<HBBII', 0x0011, 0, 0, 0, 0))
    q += pack_subrecord('NEXT', b'')
    q += pack_uint32_subrecord('ANAM', 0)
    writer.add_record('QUST', pack_record('QUST', fid, 0, q))
    print(f"    NPC conversations: {len(plan['chains'])} chains on {edid} "
          f"({len(plan['skipped'])} skipped), {len(props)} bound properties")
    return fid
