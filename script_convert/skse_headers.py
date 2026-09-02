"""Build compile-only Papyrus header overlays for SKSE64 natives.

The Creation Kit ships vanilla base headers, while converted OBSE scripts may
legitimately call SKSE64 natives registered on those same scripts. Replacing a
runtime base script would be unsafe, so this module creates augmented copies
used only as compiler headers.
"""

from pathlib import Path
import re
import shutil


_ADDITIONS = {
    "Form.psc": """
; SKSE64 compile declarations used by the TES4 converter
bool Function IsPlayable() native
""",
}

_FUNCTION_RE = re.compile(r"\bFunction\s+(\w+)\s*\(", re.IGNORECASE)


def prepare_skse_headers(vanilla_dir: str, work_dir: Path) -> Path:
    """Create a clean compile-only SKSE64 header overlay."""
    vanilla = Path(vanilla_dir)
    work_dir = Path(work_dir)
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    for name, additions in _ADDITIONS.items():
        text = (vanilla / name).read_text(
            encoding="utf-8-sig", errors="replace")
        existing = {m.group(1).lower() for m in _FUNCTION_RE.finditer(text)}
        missing = []
        for line in additions.strip().splitlines():
            match = _FUNCTION_RE.search(line)
            if match and match.group(1).lower() in existing:
                continue
            missing.append(line)
        merged = text.rstrip() + "\n"
        if missing:
            merged += "\n" + "\n".join(missing) + "\n"
        (work_dir / name).write_text(merged, encoding="utf-8")

    return work_dir
