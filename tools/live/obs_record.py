"""Records a test instance's hidden game window with OBS, itself started on the same hidden desktop.

OBS gets its own "TESRecording" profile and scene collection (a game capture of SkyrimSE.exe), so the user's
OBS setup is untouched; the last-used profile in user.ini is restored when recording stops. Clips are .mkv
(safe against a kill) and are remuxed to .mp4 at the end.

    rec = obs_record.start(desktop, out_dir, 2560, 1440)
    ...
    obs_record.stop(rec)

See: docs/commentary/headless_testing.md#quest-runs
"""
import dataclasses
import glob
import json
import os
import shutil
import subprocess
import time

from core.subprocess_flags import POPEN_FLAGS
from tools.live import hidden_launch

OBS_DIR = r'C:\Program Files\obs-studio\bin\64bit'
NAME = 'TESRecording'
#: Time for OBS's muxer child to close the clip after OBS itself ends.
MUX_CLOSE_SECONDS = 5
PROFILE = """[General]
Name={name}

[Video]
BaseCX={w}
BaseCY={h}
OutputCX={w}
OutputCY={h}
FPSType=0
FPSCommon=60
ColorFormat=NV12
ColorSpace=709
ColorRange=Partial

[Output]
Mode=Simple
FilenameFormatting=%CCYY-%MM-%DD %hh-%mm-%ss

[SimpleOutput]
FilePath={out}
RecFormat2=mkv
RecQuality=HQ
RecEncoder=nvenc_hevc
RecTracks=1
"""
FLAGS = ('--profile %s --collection %s --startrecording --minimize-to-tray --disable-shutdown-check --multi '
         '--disable-updater') % (NAME, NAME)


@dataclasses.dataclass
class Recording:
    """A running OBS: its pid, clip folder, and the user.ini it must put back."""
    pid: int
    out_dir: str
    user_ini: str


def config_dir() -> str:
    """OBS's own config folder."""
    return os.path.join(os.environ['APPDATA'], 'obs-studio')


def scenes(w: int, h: int) -> dict:
    """One scene holding a game capture of SkyrimSE.exe, fitted to w x h."""
    item = {'name': 'Skyrim', 'id': 1, 'visible': True, 'locked': False, 'rot': 0.0, 'align': 5,
            'pos': {'x': 0.0, 'y': 0.0}, 'scale': {'x': 1.0, 'y': 1.0},
            'bounds_type': 2, 'bounds_align': 0, 'bounds': {'x': float(w), 'y': float(h)}}
    capture = {'capture_mode': 'window', 'priority': 2, 'capture_cursor': False,
               'window': 'Skyrim Special Edition:Skyrim Special Edition:SkyrimSE.exe'}
    return {'name': NAME, 'current_scene': 'Game', 'current_program_scene': 'Game', 'scene_order': [{'name': 'Game'}],
            'sources': [{'id': 'scene', 'versioned_id': 'scene', 'name': 'Game',
                         'settings': {'id_counter': 1, 'custom_size': False, 'items': [item]}},
                        {'id': 'game_capture', 'versioned_id': 'game_capture', 'name': 'Skyrim',
                         'settings': capture}]}


def write_config(out_dir: str, w: int, h: int) -> str:
    """Writes the profile and scene collection; returns the path of the backed-up user.ini."""
    conf = config_dir()
    kept = os.path.join(out_dir, 'obs_user.ini.bak')
    shutil.copy(os.path.join(conf, 'user.ini'), kept)
    profile = os.path.join(conf, 'basic', 'profiles', NAME)
    os.makedirs(profile, exist_ok=True)
    with open(os.path.join(profile, 'basic.ini'), 'w', encoding='utf-8') as fh:
        fh.write(PROFILE.format(name=NAME, w=w, h=h, out=out_dir.replace('\\', '/')))
    with open(os.path.join(conf, 'basic', 'scenes', NAME + '.json'), 'w', encoding='utf-8') as fh:
        json.dump(scenes(w, h), fh)
    return kept


def start(desktop: str, out_dir: str, w: int, h: int) -> Recording:
    """Starts OBS recording on hidden desktop `desktop` into `out_dir` at w x h, 60 fps."""
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    kept = write_config(out_dir, w, h)
    pid = hidden_launch.launch(os.path.join(OBS_DIR, 'obs64.exe'), OBS_DIR, desktop, [], {}, FLAGS)
    print('recording with OBS pid %d into %s' % (pid, out_dir))
    return Recording(pid, out_dir, kept)


def stop(rec: Recording) -> None:
    """Ends OBS (its muxer closes the clip), restores user.ini, remuxes every new .mkv to .mp4."""
    hidden_launch.terminate(rec.pid, OBS_DIR)
    time.sleep(MUX_CLOSE_SECONDS)
    shutil.copy(rec.user_ini, os.path.join(config_dir(), 'user.ini'))
    for clip in glob.glob(os.path.join(rec.out_dir, '*.mkv')):
        mp4 = os.path.splitext(clip)[0] + '.mp4'
        if not os.path.exists(mp4):
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', clip, '-c', 'copy', mp4], check=False,
                           **POPEN_FLAGS)
            print('clip %s' % mp4)
