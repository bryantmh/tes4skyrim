"""MOPP bytecode analysis and repair (Oblivion→Skyrim collision pipeline).

MOPP_RL.exe builds its MOPP with Havok's *chunk subdivision* enabled (an
SPU/PS3 streaming feature): the code contains 5-byte `0x70 <abs32>`
chunk-jump instructions that transfer control to 16-byte-aligned sub-trees
stored after the main tree (0xCD alignment filler between them).

Vanilla Skyrim never ships chunked MOPPs (0 of 400 vanilla meshes contain
opcode 0x70), and the PC engine mis-executes them: when a collision query
descends into a 0x70 branch the VM runs away and Skyrim dies with
EXCEPTION_STACK_OVERFLOW inside hkpCollisionDispatcher (the intermittent
"walking over castleint2way.nif" crash — only queries that reach a chunk
jump die, so it looked random).

`dechunk_mopp()` rewrites each 5-byte chunk jump IN PLACE as a 3-byte
0x06 JUMP16 (a vanilla-proven opcode) plus two unreachable pad bytes, so no
other instruction offset moves.  Unreachable bytes (alignment filler and the
dead pad) are then zeroed.  The result is bytecode that only uses opcodes
observed in vanilla Skyrim meshes.

`walk_mopp()` is the underlying verifier: it executes the bytecode
symbolically, following every branch, and reports coverage, triangle shape
keys, and structural errors.  The opcode table is PyFFI's parse_mopp
(niftools reverse engineering) extended with the Skyrim-era commands
(0x52 TERM24, 0x29-0x2B DOUBLE_CUT24, 0x70 CHUNK_JUMP32), validated clean
against 400 vanilla Skyrim SE meshes.
"""

# Walk safety cap
_MAX_STEPS = 2_000_000


def walk_mopp(mopp, size, start=0, follow_chunks=True):
    """Walk MOPP bytecode from `start`, following all branches.

    mopp: byte sequence (list/bytes/bytearray), size: number of valid bytes.
    follow_chunks: when False, 0x70 chunk jumps are recorded but treated as
    leaves (used to delimit one region without entering others).

    Returns dict with:
      visited      : set of byte offsets executed/consumed
      tris         : set of shape keys (triangle ids) encountered
      errors       : list of strings (OOB jumps, unknown opcodes, ...)
      counts       : dict opcode -> executed instruction sites
      chunk_jumps  : list of (site_offset, target_offset) for 0x70 commands
      max_offset   : highest visited offset
    """
    visited = set()
    tris = set()
    errors = []
    counts = {}
    chunk_jumps = []
    seen_states = set()
    stack = [(start, 0)]  # worklist of (offset, triangle_offset)
    steps = 0

    def oob(i, what):
        errors.append('offset %d: %s runs out of bounds (size %d)' % (i, what, size))

    def result():
        return {'visited': visited, 'tris': tris, 'errors': errors,
                'counts': counts, 'chunk_jumps': chunk_jumps,
                'max_offset': max(visited) if visited else -1}

    while stack:
        i, toffset = stack.pop()
        if (i, toffset) in seen_states:
            continue
        seen_states.add((i, toffset))
        ret = False
        while not ret:
            steps += 1
            if steps > _MAX_STEPS:
                errors.append('step limit exceeded (possible cycle)')
                return result()
            if i < 0 or i >= size:
                oob(i, 'instruction pointer')
                break
            code = mopp[i]
            counts[code] = counts.get(code, 0) + 1

            if 0x30 <= code <= 0x4F:                       # TERM4 compact leaf
                visited.add(i)
                tris.add(code - 0x30 + toffset)
                ret = True

            elif 0x50 <= code <= 0x53:                     # TERM 8/16/24/32 leaf
                n = code - 0x50 + 1                        # operand bytes
                if i + n >= size:
                    oob(i, 'TERM%d operands' % (8 * n))
                    break
                key = 0
                for k in range(n):
                    key = (key << 8) | mopp[i + 1 + k]
                visited.update(range(i, i + n + 1))
                tris.add(key + toffset)
                ret = True

            elif code == 0x05:                             # JUMP8
                if i + 1 >= size:
                    oob(i, 'JUMP8 operand'); break
                visited.update((i, i + 1))
                i = i + 2 + mopp[i + 1]

            elif code == 0x06:                             # JUMP16
                if i + 2 >= size:
                    oob(i, 'JUMP16 operands'); break
                visited.update(range(i, i + 3))
                i = i + 3 + (mopp[i + 1] << 8 | mopp[i + 2])

            elif code == 0x07:                             # JUMP24
                if i + 3 >= size:
                    oob(i, 'JUMP24 operands'); break
                visited.update(range(i, i + 4))
                i = i + 4 + (mopp[i + 1] << 16 | mopp[i + 2] << 8 | mopp[i + 3])

            elif code == 0x09:                             # TERM_REOFFSET8
                if i + 1 >= size:
                    oob(i, 'REOFFSET8 operand'); break
                visited.update((i, i + 1))
                toffset += mopp[i + 1]
                i += 2

            elif code == 0x0A:                             # TERM_REOFFSET16
                if i + 2 >= size:
                    oob(i, 'REOFFSET16 operands'); break
                visited.update(range(i, i + 3))
                toffset += mopp[i + 1] << 8 | mopp[i + 2]
                i += 3

            elif code == 0x0B:                             # TERM_REOFFSET32
                if i + 4 >= size:
                    oob(i, 'REOFFSET32 operands'); break
                visited.update(range(i, i + 5))
                toffset = mopp[i + 3] << 8 | mopp[i + 4]
                i += 5

            elif 0x10 <= code <= 0x1C:                     # SPLIT8 (13 dop dirs)
                if i + 3 >= size:
                    oob(i, 'SPLIT8 operands'); break
                visited.update(range(i, i + 4))
                stack.append((i + 4 + mopp[i + 3], toffset))
                i = i + 4

            elif 0x20 <= code <= 0x22:                     # SINGLE_SPLIT (X/Y/Z)
                if i + 2 >= size:
                    oob(i, 'SINGLE_SPLIT operands'); break
                visited.update(range(i, i + 3))
                stack.append((i + 3 + mopp[i + 2], toffset))
                i = i + 3

            elif 0x23 <= code <= 0x25:                     # SPLIT16 (X/Y/Z)
                if i + 6 >= size:
                    oob(i, 'SPLIT16 operands'); break
                visited.update(range(i, i + 7))
                jump1 = mopp[i + 3] << 8 | mopp[i + 4]
                jump2 = mopp[i + 5] << 8 | mopp[i + 6]
                stack.append((i + 7 + jump2, toffset))
                i = i + 7 + jump1

            elif 0x26 <= code <= 0x28:                     # DOUBLE_CUT X/Y/Z
                if i + 2 >= size:
                    oob(i, 'DOUBLE_CUT operands'); break
                visited.update(range(i, i + 3))
                i += 3

            elif 0x29 <= code <= 0x2B:                     # DOUBLE_CUT24 X/Y/Z
                if i + 6 >= size:
                    oob(i, 'DOUBLE_CUT24 operands'); break
                visited.update(range(i, i + 7))
                i += 7

            elif 0x01 <= code <= 0x04:                     # RESCALE (4 bytes)
                if i + 3 >= size:
                    oob(i, 'RESCALE operands'); break
                visited.update(range(i, i + 4))
                i += 4

            elif code == 0x70:                             # CHUNK_JUMP32 (chunked mopp)
                if i + 4 >= size:
                    oob(i, 'CHUNK_JUMP32 operands'); break
                visited.update(range(i, i + 5))
                target = (mopp[i + 1] << 24 | mopp[i + 2] << 16 |
                          mopp[i + 3] << 8 | mopp[i + 4])
                chunk_jumps.append((i, target))
                if not follow_chunks:
                    ret = True
                else:
                    i = target

            else:
                ctx = [('0x%02X' % mopp[j]) for j in range(i, min(size, i + 10))]
                errors.append('offset %d: unknown opcode 0x%02X context=[%s] toffset=%d'
                              % (i, code, ' '.join(ctx), toffset))
                break

    return result()


def dechunk_mopp(mopp_bytes):
    """Rewrite chunked MOPP bytecode (0x70 chunk jumps) as vanilla-style code.

    MOPP_RL emits the main tree followed by 16-byte-aligned chunk sub-trees,
    linked by 5-byte `0x70 <abs32>` chunk-jump instructions (which may point
    forward OR backward).  Vanilla Skyrim's engine cannot execute 0x70, so:

      1. every reachable region (main tree + each chunk) is delimited,
      2. regions are laid out in topological order of the jump graph so all
         chunk links become forward jumps,
      3. each region's bytes are copied verbatim (internal relative jumps
         stay valid), and
      4. every 0x70 site is patched in place to `0x06 <rel16>` (JUMP16, a
         vanilla-proven opcode) + 2 unreachable pad bytes (zeroed).

    Returns the rewritten bytes (alignment filler dropped, so usually
    shorter).  Raises ValueError if the code does not verify before/after,
    the jump graph has a cycle, regions overlap, or a jump exceeds 16 bits.
    """
    mopp = bytes(bytearray(mopp_bytes))
    size = len(mopp)
    before = walk_mopp(mopp, size)
    if before['errors']:
        raise ValueError('mopp does not verify before dechunk: %s'
                         % before['errors'][:3])
    if not before['chunk_jumps']:
        # Already unchunked — just drop the unreachable 0xCD alignment tail
        # MOPP_RL appends and zero any interior unreachable bytes.
        end = before['max_offset'] + 1
        trimmed = bytearray(mopp[:end])
        for j in range(end):
            if j not in before['visited']:
                trimmed[j] = 0
        return bytes(trimmed)

    # --- 1. Delimit regions.  Region roots: offset 0 (main) + jump targets.
    roots = sorted({0} | {t for _s, t in before['chunk_jumps']})

    def region_walk(start):
        """Walk from start, treating 0x70 as a leaf.  Returns (visited, sites)."""
        r = walk_mopp(mopp, size, start=start, follow_chunks=False)
        if r['errors']:
            raise ValueError('region at %d does not verify: %s'
                             % (start, r['errors'][:3]))
        return r['visited'], [s for s, _t in r['chunk_jumps']]

    regions = {}  # root -> dict(start, end, bytes, sites=[(rel_site, target_root)])
    for root in roots:
        visited, sites = region_walk(root)
        lo, hi = min(visited), max(visited)
        if lo < root:
            raise ValueError('region at %d reaches backward to %d' % (root, lo))
        regions[root] = {
            'end': hi,
            'sites': sites,  # absolute offsets of 0x70 instructions
        }

    # Regions must not overlap (each is copied as one contiguous span).
    prev_root, prev_end = None, -1
    for root in roots:
        if root <= prev_end:
            raise ValueError('region at %d overlaps region at %s (end %d)'
                             % (root, prev_root, prev_end))
        prev_root, prev_end = root, regions[root]['end']

    def owner(offset):
        """Region root owning a byte offset."""
        best = None
        for root in roots:
            if root <= offset <= regions[root]['end']:
                best = root
        if best is None:
            raise ValueError('chunk-jump site %d not inside any region' % offset)
        return best

    # --- 2. Topological order of the jump graph (main tree first).
    edges = {root: set() for root in roots}   # root -> target roots
    indeg = {root: 0 for root in roots}
    site_target = dict(before['chunk_jumps'])
    for site, target in before['chunk_jumps']:
        src = owner(site)
        if target not in edges[src]:
            edges[src].add(target)
    for root, outs in edges.items():
        for t in outs:
            indeg[t] += 1
    if indeg[0] != 0:
        raise ValueError('main tree is a chunk-jump target')
    order = []
    ready = [r for r in roots if indeg[r] == 0]
    while ready:
        ready.sort()
        cur = ready.pop(0)
        order.append(cur)
        for t in sorted(edges[cur]):
            indeg[t] -= 1
            if indeg[t] == 0:
                ready.append(t)
    if len(order) != len(roots):
        raise ValueError('chunk-jump graph has a cycle')
    if order[0] != 0:
        # main region has no incoming edges; force it first
        order.remove(0)
        order.insert(0, 0)

    # --- 3. Lay out regions, remembering each region's new start.
    new_start = {}
    out = bytearray()
    for root in order:
        new_start[root] = len(out)
        out.extend(mopp[root:regions[root]['end'] + 1])

    # --- 4. Patch every 0x70 site to a forward JUMP16.
    for root in order:
        base = new_start[root]
        for site in regions[root]['sites']:
            target_root = site_target[site]
            site_new = base + (site - root)
            rel = new_start[target_root] - (site_new + 3)
            if rel < 0 or rel > 0xFFFF:
                raise ValueError('dechunked jump at %d -> %d out of JUMP16 '
                                 'range (rel %d)' % (site_new,
                                                     new_start[target_root], rel))
            out[site_new] = 0x06
            out[site_new + 1] = (rel >> 8) & 0xFF
            out[site_new + 2] = rel & 0xFF
            out[site_new + 3] = 0
            out[site_new + 4] = 0

    # --- 5. Verify: clean walk, no chunk jumps, identical shape-key set.
    after = walk_mopp(out, len(out))
    if after['errors']:
        raise ValueError('mopp does not verify after dechunk: %s'
                         % after['errors'][:3])
    if after['chunk_jumps']:
        raise ValueError('chunk jumps still reachable after dechunk')
    if after['tris'] != before['tris']:
        raise ValueError('triangle key set changed during dechunk')

    # Zero unreachable bytes (dead pad inside regions).
    for j in range(len(out)):
        if j not in after['visited']:
            out[j] = 0
    return bytes(out)
