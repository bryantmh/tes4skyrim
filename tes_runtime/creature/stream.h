// The engine's BSResource::Stream contract, as measured from the singlefile
// parsers (docs/commentary/asset_convert_creature.md#runtime-animation-cache-composition):
//
//   +0x00  vtable
//   +0x08  uint32 totalSize
//   +0x0c  (1.6.659) / +0x10 (1.6.1170)  uint32 flags: bits 0-11 state,
//          refcount in bits 12+ (AddRef is a lock cmpxchg of +0x1000; Release
//          at zero calls vtable[0](this, 1)). DetectStreamLayout finds which.
//   vtable[0] dtor(this, flag)   flag & 1 -> delete
//   vtable[1] DoOpen(this)        -> ErrorCode (0 = ok)
//   vtable[2] DoClose(this)
//   vtable[6] DoRead(this, void* buf, uint64 n, uint64* read) -> ErrorCode
//
// The parser's line reader pulls ONE byte per DoRead and stops at read == 0,
// so a stream over a memory buffer only has to honour those slots.

#pragma once

#include <cstdint>
#include <string>

namespace tesruntime {

struct EngineStream;   // opaque: only touched through the layout above

// Reads the flags-word offset off the parser's own release sequence
// (`lock cmpxchg [rcx+disp8], edx`) within `scanLen` bytes of `parser`.
// False leaves the default in place.
bool DetectStreamLayout(std::uintptr_t parser, std::size_t scanLen);

// Reads the whole content of a stream the engine opened: sets the open
// bits, DoOpen, DoRead in chunks until totalSize is consumed, DoClose,
// clears the bits. Does NOT release the reference.
bool ReadEngineStream(EngineStream* s, std::string& out);

// Drops one reference exactly as the parser's teardown does.
void ReleaseEngineStream(EngineStream* s);

// A stream over `text` with one reference held for the caller. The engine
// releases it through vtable[0] when the parser finishes.
EngineStream* NewMemoryStream(std::string text);

}  // namespace tesruntime
