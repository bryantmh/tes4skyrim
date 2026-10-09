"""VoiceFile Redirector rules for TESRuntime; never duplicate audio assets.

Resolve every folder through the converted master's voice identities. The
runtime first opens the requested resource normally, then tries these rules
only on NotExist. Rules are independent of the language of the source audio.
"""
import json
from pathlib import Path

from core.plugin_masters import export_root, master_chain
from output_layout import converted_master_path


# Original VoiceFile Redirector's generic greeting INFOs in Oblivion.esm.
_HELLO = {'Argonian': 0x062CCB, 'Khajiit': 0x062CCB,
          'Breton': 0x062CC7, 'HighElf': 0x062CAA,
          'DarkElf': 0x062CAA, 'WoodElf': 0x062CAA,
          'Nord': 0x062CB3, 'Orc': 0x062CB3,
          'Redguard': 0x062CC1, 'Imperial': 0x0919A9}
_SHARED = {'DarkElf': 'HighElf', 'WoodElf': 'HighElf', 'Orc': 'Nord'}


def write_voice_redirects(dest_dir, plugin_name, record_dir, output_root,
                          voices, load_map):
    """Stage a small rule sidecar for a converted Oblivion-derived plugin.

    `voices` is Import's authoritative (race EditorID, M/F) -> VTYP map.
    No guessed voice folders, arbitrary races or gender substitutions.
    """
    path = (Path(dest_dir) / 'SKSE' / 'Plugins' / 'TESRuntime'
            / (plugin_name + '.voice_redirects.json'))
    ancestors = master_chain(record_dir)
    if plugin_name.casefold() != 'oblivion.esm':
        name = next((n for n in ancestors if n.casefold() == 'oblivion.esm'), None)
        if not name:
            path.unlink(missing_ok=True)
            return None
    master_path = converted_master_path(output_root, 'Oblivion.esm',
                                        export_root=export_root(record_dir))
    map_path = master_path.with_name(master_path.name + '.voicemap.txt')
    hello_map = load_map(map_path) if map_path.is_file() else {}
    rules = {}
    for (race, gender), voice in sorted((voices or {}).items()):
        alternates = []
        shared = voices.get((_SHARED.get(race), gender))
        imperial = (voices.get(('Imperial', gender)) if race in {
            'Argonian', 'Breton', 'HighElf', 'Nord', 'Redguard',
            'DarkElf', 'WoodElf', 'Orc', 'Khajiit'} else None)
        for target in (shared, imperial):
            if target and target != voice and target not in alternates:
                alternates.append(target)
        hello_race = race if race in _HELLO else 'Imperial'
        # Female Bretons share the Imperial voice, as in the original plugin.
        if hello_race == 'Breton' and gender == 'F':
            hello_race = 'Imperial'
        hello_voice = voices.get((_SHARED.get(hello_race, hello_race), gender))
        hello_id = _HELLO[hello_race]
        prefix = hello_map.get(hello_id, ('', []))[0]
        greeting = (f'sound\\voice\\Oblivion.esm\\{hello_voice}\\'
                    f'{prefix}_{hello_id:08x}_1.wav') if prefix and hello_voice else ''
        # Shared VTYPs have the same policy even when several race EDIDs use it.
        rules.setdefault(voice, {'alternates': alternates, 'greeting': greeting})
    if not rules:
        path.unlink(missing_ok=True)
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'plugin': plugin_name, 'voices': rules},
                               ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path
