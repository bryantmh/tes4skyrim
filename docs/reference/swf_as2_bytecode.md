# SWF ActionScript 2 bytecode

What a SWF's AVM1 action stream IS, and how to read a data path out of one.

**Tool:** `tools/disasm/swf_as2_disasm.py`

## The container

A SWF is a header followed by a flat tag stream. The signature's first byte
gives the compression: `FWS` uncompressed, `CWS` zlib from byte 8, `ZWS` LZMA.
Decompress before anything else; the 8-byte header stays uncompressed and the
length field in it is the *uncompressed* size, which is how a truncated
decompress is caught.

After the header comes a frame RECT whose width is nibble-packed — bits 3-7 of
the first byte give the bits-per-coordinate, and the rect is that times four,
rounded up to a byte — then a 4-byte frame rate and count. Tags follow: a
16-bit code-and-length word, where the low 6 bits are the length unless they
are `0x3F`, in which case a 32-bit length follows.

## Where the code lives

| Tag | Code | Carries |
|---|---|---|
| `DoAction` | 12 | Frame actions, AVM1 |
| `DoInitAction` | 59 | Class init actions, AVM1, prefixed by a 2-byte sprite id |
| `DefineSprite` | 39 | A nested tag stream, which may itself hold `DoAction` |
| `DoABC` | 82 / 72 | AS3 — a different VM, not this format |

`DoInitAction` is where AS2 classes are registered, so a movie compiled from
`.as` files puts nearly all its logic there, one block per class.

## The action stream

Each action is a 1-byte opcode. Opcodes `>= 0x80` carry a 16-bit length and
that many operand bytes; opcodes below it are bare stack operations. `0x00`
ends the block.

Two opcodes matter for reading:

- **`ConstantPool` (0x88)** declares the block's string table. Every later
  `Push` of type 8 or 9 is an index into it, so a disassembler that does not
  track the pool prints indices instead of names and is useless for tracing.
- **`Push` (0x96)** holds a list of typed values: `0` inline string, `1` float,
  `4` register, `6` double (with its two 32-bit halves SWAPPED), `7` int,
  `8`/`9` pool index by byte or word.

Method calls are `Push <name>` then `GetMember` then `CallMethod`, so a symbol
appearing in a pool and pushed near a `CallMethod` or `SetMember` is being
called or assigned — that adjacency is what makes a symbol search locate a
data path without a full decompiler.

## <a id="tracing-a-data-path"></a>Tracing a data path

To find what writes a UI field, search the action blocks for the symbol and
read the pushes around each hit:

```bash
python tools/disasm/swf_as2_disasm.py <movie>.swf --trace SetDescriptionText
python tools/disasm/swf_as2_disasm.py <movie>.swf --trace description --context 6
python tools/disasm/swf_as2_disasm.py <movie>.swf --tags
```

A `SetMember` on the symbol is a write; a `GetMember` feeding a `CallMethod` is
a read. The block label names the tag and sprite the code came from, which is
how a hit is mapped back to a class.

Symbols reaching the movie from the game arrive through Scaleform's invoke
path rather than any tag, so a symbol that appears in a pool but is never
pushed by movie code is a candidate for one the host sets.
