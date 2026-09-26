"""Per-script conversion state.

`ScriptConverter` carried ~34 mutable fields re-initialised by a `_reset()`
method the constructor also called.  That is a per-script object written as
mutable attributes on a long-lived one: nothing said which fields belonged to
the script and which to the run, and a field that failed to reset leaked into
the next script (a `True` left in `_suppressed_fall_damage` appended a
RestoreFallDamage to an unrelated script's teardown).

`ScriptContext` is that state, declared once.  A new script gets a NEW
instance, so there is no reset to forget.
"""

from dataclasses import dataclass, field


@dataclass
class ScriptContext:
    """Everything the converter knows about the ONE script it is converting."""

    #: EditorID of the script being converted.
    edid: str = ''

    # --- Symbols -----------------------------------------------------------
    #: Papyrus property name -> Papyrus type, the script's property table.
    property_refs: dict = field(default_factory=dict)
    #: Lowercased local variable names, for expression disambiguation.
    local_vars: set = field(default_factory=set)
    #: Lowercased variable name -> Papyrus type.
    var_types: dict = field(default_factory=dict)
    #: Original lowercased name -> Papyrus-safe name, where they differ.
    var_renames: dict = field(default_factory=dict)
    #: OBSE `array_var` declarations; a read of one is inert.
    obse_arrays: set = field(default_factory=set)
    #: Lowercased OBSE user-function parameter names.
    udf_params: set = field(default_factory=set)

    # --- Block structure ---------------------------------------------------
    has_gamemode: bool = False
    has_menumode: bool = False
    has_scripteffectupdate: bool = False

    # --- Feature flags, derived from the PARSE TREE ------------------------
    uses_getsecondspassed: bool = False
    gsp_realtime: bool = False
    uses_hour_window: bool = False
    uses_timer: bool = False
    uses_say: bool = False
    uses_say_timer: bool = False
    #: An event without an action ref read the last activator; OnActivate records it.
    uses_last_activator: bool = False
    #: A poll block calls SetPos/SetAngle: TES4 per-frame motion.
    moves_in_poll: bool = False
    #: Poll SetPos/SetAngle calls stepping from their own axis read -> (axis, step, sign) (poll_motion.relative_sets).
    relative_sets: dict = field(default_factory=dict)
    #: Authored quest-script delay in seconds (FO3/FNV DATA.Delay); 0 = none.
    quest_delay: float = 0.0
    #: Every record the script is attached to is a BOOK, so its OnActivate means "read".
    on_book: bool = False

    # --- Emission bookkeeping ----------------------------------------------
    #: Quest -> latch variable, for stage timers.  Per-script: a latch
    #: registered while converting one script must not declare into the next.
    stage_latches: dict = field(default_factory=dict)
    #: The arm a TES4 `return` must emit before `Return` inside a poll body.
    poll_return_prefix: str = ''
    #: Glide duration expression while a moving poll body is emitted; SetPos/SetAngle there glide over it.
    glide_secs: str = ''
    #: GetInCell family helpers this script needs.
    cell_families: dict = field(default_factory=dict)
    #: SCRO alias map, scoped to ONE fragment.
    scro_aliases: dict = field(default_factory=dict)

    #: Set while a sleep-idiom MenuMode body is being converted: a
    #: `isPCSleeping` read there means "is this a sleep frame", which is the
    #: script-managed flag rather than the engine's sleep state.
    in_sleep_menumode: bool = False

    refwalk_var: str = ''
    refwalk_labels: set = field(default_factory=set)
    block_depth: int = 0

    udf_returns: bool = False
    #: Set while a user function's body converts: its `Self` is the calling reference.
    in_udf: bool = False
    #: Papyrus type of the first `SetFunctionValue`, the UDF's return type.
    udf_return_type: str = 'Int'
    #: Parameter types of this script's OBSE user function, in order; None
    #: when it declares no TES4Call at all.
    udf_signature: list = None
    #: Every `<prop>.TES4Call(...)` emitted, as (property, args).  The callee's
    #: signature is unknown until every script has converted, so the casts are
    #: applied afterwards from this list rather than by re-reading the file.
    udf_calls: list = field(default_factory=list)

    #: Cleared per script: a leaked True would append a RestoreFallDamage to
    #: an unrelated script's teardown event.
    suppressed_fall_damage: bool = False

    #: Button-MessageBox state: MESG names already matched to a call site in
    #: this script, and whether the helpers are due.
    msgbox_used: set = field(default_factory=set)
    uses_msg_buttons: bool = False

    #: Chargen-menu call sites converted here, and whether the re-entrancy
    #: latch declaration is due.
    chargen_menu_seq: int = 0
    uses_chargen_menus: bool = False
