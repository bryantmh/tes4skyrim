# Morrowind scripts

**Code:** NONE — `script_convert/blocks_morrowind.py` was DELETED.

🛑 **TES3 scripts no longer go down the Papyrus path at all.** They run on the
vendored OpenMW interpreter, from the sidecar's `SCPT_source.txt`, where 95% of
them compile against the 30% that reached the game as `.pex`. Two engines
writing the same object state is worse than one lossy engine, so this was a
cutover rather than an addition.
See: [../plans/morrowind_object_scripts.md](../plans/morrowind_object_scripts.md)

This page is kept for the MEASUREMENT below: it is the evidence that the
Papyrus path could not be fixed into correctness, and the reason not to try
again.

## <a id="implicit-blocks"></a>A TES3 script is one implicit block

A TES4 script names the event each block answers:

```
begin GameMode
    ...
end
```

`blocks.BLOCK_MAP` turns that name into a Papyrus event, and `assemble.events`
**drops any block whose type is absent from that table, body and all** — the
table is the vocabulary of what survives conversion at all.

TES3 has no such vocabulary. A script opens with its OWN name:

```
Begin TR_m4_NPC_AndoCTThug01
    ...
End
```

so the script name lands in the block type, never matches `BLOCK_MAP`, and the
whole body is discarded. Measured over TR_Mainland.esm before the fix:

| | |
|---|---|
| Blocks parsed | 3,569 |
| Blocks matching `BLOCK_MAP` | **0** |
| Scripts with an executable body | **0 of 3,569** |

Oblivion.esm and Nehrim.esm sit at 0.4% and 0.5% empty over the same pass, so
this was TES3-only. The parser was never at fault: it parsed all 3,569 without
error and built correct statement trees. Only the routing failed, which is why
the output was a well-formed shell — the properties survive because variable
declarations are hoisted in `parse()` before blocks are assembled.

**A TES3 body runs every frame while its object is loaded**, which is exactly
what `gamemode` means, so the implicit block was routed to that type and reached
`Event OnUpdate()` through the existing map.

🛑 **That routing was necessary but never sufficient**, which is why the whole
path is now gone. Retyping the block made the body survive conversion; it did
not make it WORK. `OnActivate` still became a property nothing ever set, and
`Rotate`/`SetAtStart` still had no emitter — so of the 3,569 bodies, 1,054
produced a `.pex` and many of those were inert. The mismatch is structural: one
block that runs every frame and branches on state has no Papyrus equivalent.

### Recognizing the implicit block

`begin` takes the name two ways, and both mean the same thing:

| Source | Parsed as | Why |
|---|---|---|
| `Begin ScriptName` | `btype='scriptname'` | a bare `IDENT` |
| `Begin "ScriptName"` | `btype=''`, name in `filter` | `_parse_block` takes the type only from an `IDENT`, and a quoted name lexes as a `STRING` |

Both are matched against the script's own EditorID rather than a fixed list,
since the name is arbitrary. Measured over TR_Mainland's 3,569 `Begin` lines:
3,534 name the EditorID exactly and the remaining 35 are the quoted or
CS-truncated spelling of it — **none** names a TES4 block type, so matching on
the script's own name cannot collide with a real one.
