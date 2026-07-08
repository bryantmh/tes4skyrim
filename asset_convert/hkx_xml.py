"""Minimal Havok hk_2010 packfile XML emitter + hkxcmd compile wrapper.

Emits the exact XML dialect produced/consumed by hkxcmd (`convert -v:XML`),
which the bundled Havok serializer compiles back to a Skyrim LE 32-bit binary
packfile (`convert -v:WIN32`). Round-trip verified byte-count-identical on
vanilla files. Used by the skeleton / character / project / behavior graph
generators for creature conversion.
"""

import os
import subprocess
import sys

HKXCMD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'external', 'hkxcmd', 'hkxcmd.exe')

# class signatures as emitted by hkxcmd for hk_2010.2.0-r1 (grow as needed)
SIGNATURES = {
    'hkRootLevelContainer': '0x2772c11e',
    'hkaAnimationContainer': '0x8dc20333',
    'hkaSkeleton': '0x366e8220',
    'hkaSkeletonMapper': '0x12df42a5',
    'hkMemoryResourceContainer': '0x4762f92a',
    'hkbProjectData': '0x13a39ba7',
    'hkbProjectStringData': '0x76ad60a',
    'hkaSplineCompressedAnimation': '0x792ee0bb',
    'hkaAnimationBinding': '0x66eac971',
}


class HkObject:
    def __init__(self, ref: str, klass: str):
        self.ref = ref
        self.klass = klass
        self.params = []       # list of (name, rendered_body, kind)

    # ---- param helpers -------------------------------------------------
    def param(self, name: str, value):
        """Scalar param: string/number/bool/object-ref rendered inline."""
        if isinstance(value, bool):
            value = 'true' if value else 'false'
        self.params.append((name, str(value), 'inline'))
        return self

    def param_array(self, name: str, items, per_line: int = 16):
        """Numeric/ref array: numelements + whitespace-joined tokens."""
        toks = [str(i) for i in items]
        lines = [' '.join(toks[i:i + per_line]) for i in range(0, len(toks), per_line)]
        self.params.append((name, '\n'.join(lines), f'array:{len(toks)}'))
        return self

    def param_strings(self, name: str, items):
        """Array of hkcstring elements."""
        body = '\n'.join(f'<hkcstring>{_esc(s)}</hkcstring>' for s in items)
        self.params.append((name, body, f'array:{len(items)}'))
        return self

    def param_structs(self, name: str, structs):
        """Array of anonymous nested hkobjects.

        structs: list of lists of (param_name, value) — values rendered inline.
        """
        parts = []
        for fields in structs:
            inner = '\n'.join(
                f'<hkparam name="{n}">{_esc(_scalar(v))}</hkparam>'
                for n, v in fields)
            parts.append(f'<hkobject>\n{_indent(inner)}\n</hkobject>')
        self.params.append((name, '\n'.join(parts), f'array:{len(structs)}'))
        return self

    def param_raw(self, name: str, body: str, numelements=None):
        """Escape hatch: pre-rendered body."""
        kind = 'inline' if numelements is None else f'array:{numelements}'
        self.params.append((name, body, kind))
        return self

    # ---- render --------------------------------------------------------
    def render(self) -> str:
        sig = SIGNATURES.get(self.klass)
        sig_attr = f' signature="{sig}"' if sig else ''
        out = [f'\t<hkobject name="{self.ref}" class="{self.klass}"{sig_attr}>']
        for name, body, kind in self.params:
            if kind == 'inline':
                out.append(f'\t\t<hkparam name="{name}">{body}</hkparam>')
            else:
                n = kind.split(':', 1)[1]
                if body:
                    out.append(f'\t\t<hkparam name="{name}" numelements="{n}">')
                    out.append(_indent(body, 3))
                    out.append('\t\t</hkparam>')
                else:
                    out.append(f'\t\t<hkparam name="{name}" numelements="0"></hkparam>')
        out.append('\t</hkobject>')
        return '\n'.join(out)


class HkxPackfile:
    """A hk_2010.2.0-r1 packfile under construction."""

    def __init__(self, first_id: int = 8):
        self._next = first_id
        self.objects = []

    def new_ref(self) -> str:
        ref = f'#{self._next:04d}'
        self._next += 1
        return ref

    def add(self, klass: str, ref: str = None) -> HkObject:
        obj = HkObject(ref or self.new_ref(), klass)
        self.objects.append(obj)
        return obj

    def render(self, toplevel: HkObject) -> str:
        body = '\n\n'.join(o.render() for o in self.objects)
        return (
            '<?xml version="1.0" encoding="ascii"?>\n'
            f'<hkpackfile classversion="8" contentsversion="hk_2010.2.0-r1" '
            f'toplevelobject="{toplevel.ref}">\n\n'
            '\t<hksection name="__data__">\n\n'
            f'{body}\n\n'
            '\t</hksection>\n\n'
            '</hkpackfile>\n'
        )

    def write_xml(self, path: str, toplevel: HkObject):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='ascii', errors='replace', newline='\n') as f:
            f.write(self.render(toplevel))


def _scalar(v) -> str:
    if isinstance(v, bool):
        return 'true' if v else 'false'
    return str(v)


def _esc(s: str) -> str:
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _indent(text: str, tabs: int = 2) -> str:
    pad = '\t' * tabs
    return '\n'.join(pad + line for line in text.split('\n'))


def fmt_vec(*vals) -> str:
    """Havok tuple literal: (a b c ...)"""
    return '(' + ' '.join(f'{v:.6f}' for v in vals) + ')'


def fmt_qtransform(t, q_xyzw, s=(1.0, 1.0, 1.0)) -> str:
    """referencePose entry: (t)(q xyzw)(s)"""
    return fmt_vec(*t) + fmt_qtransform_rot(q_xyzw) + fmt_vec(*s)


def fmt_qtransform_rot(q_xyzw) -> str:
    return fmt_vec(*q_xyzw)


def _run_hkxcmd(args, out_path):
    # hkxcmd CRASHES (0xC0000417) on forward-slash paths — always pass
    # absolute backslash paths
    res = subprocess.run([HKXCMD] + args, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(
            f'hkxcmd {" ".join(args)} failed ({res.returncode}):\n'
            f'{res.stdout}\n{res.stderr}')
    return res


def compile_hkx(xml_path: str, hkx_path: str, fmt: str = 'WIN32') -> None:
    """Compile packfile XML → binary hkx via hkxcmd. Raises on failure."""
    xml_path = os.path.abspath(xml_path)
    hkx_path = os.path.abspath(hkx_path)
    os.makedirs(os.path.dirname(hkx_path), exist_ok=True)
    _run_hkxcmd(['convert', f'-v:{fmt}', xml_path, hkx_path], hkx_path)


def decompile_hkx(hkx_path: str, xml_path: str) -> None:
    """Binary hkx → packfile XML via hkxcmd (validation aid)."""
    hkx_path = os.path.abspath(hkx_path)
    xml_path = os.path.abspath(xml_path)
    os.makedirs(os.path.dirname(xml_path), exist_ok=True)
    _run_hkxcmd(['convert', '-v:XML', hkx_path, xml_path], xml_path)
