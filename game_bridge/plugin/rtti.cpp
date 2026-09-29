// Runtime RTTI walk. See rtti.h for the descriptor chain.

#include "rtti.h"

#include <windows.h>

#include <cstring>
#include <unordered_set>

namespace bridge {

namespace {

struct Section {
    std::uintptr_t begin = 0, end = 0;
};

// The first section with this name. The exe has two ".text" sections (the
// second is the DRM stub's), but ".data" and ".rdata" are unique.
Section FindSection(std::uintptr_t base, const char* name) {
    Section s;
    auto dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return s;
    auto nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return s;
    const IMAGE_SECTION_HEADER* sec = IMAGE_FIRST_SECTION(nt);
    for (WORD i = 0; i < nt->FileHeader.NumberOfSections; ++i, ++sec) {
        char n[9] = {};
        std::memcpy(n, sec->Name, 8);
        if (std::strcmp(n, name) != 0) continue;
        s.begin = base + sec->VirtualAddress;
        // SizeOfRawData, not VirtualSize: only the initialised part holds
        // descriptors, and an image mapped as a resource commits nothing past
        // it, so reading to VirtualSize would fault.
        const DWORD size = sec->SizeOfRawData < sec->Misc.VirtualSize
                               ? sec->SizeOfRawData : sec->Misc.VirtualSize;
        s.end = s.begin + size;
        return s;
    }
    return s;
}

// The TypeDescriptor whose name is exactly `mangled`. A descriptor is 8-aligned
// with its name at +16, which rules out a match inside a longer name.
std::uintptr_t FindTypeDescriptor(const Section& data, const char* mangled) {
    const std::size_t len = std::strlen(mangled) + 1;
    for (std::uintptr_t p = data.begin + 16; p + len <= data.end; p += 8) {
        if (std::memcmp(reinterpret_cast<const void*>(p), mangled, len) == 0) return p - 16;
    }
    return 0;
}

#pragma pack(push, 4)
struct Col {
    std::uint32_t sig, offset, cdOffset, pTypeDesc, pClassDesc, pSelf;
};
struct Chd {
    std::uint32_t sig, attributes, numBases, pBaseArray;
};
struct Bcd {
    std::uint32_t pTypeDesc, numContained;
    std::int32_t  mdisp, pdisp, vdisp;
    std::uint32_t attributes, pClassDesc;
};
#pragma pack(pop)

}  // namespace

bool RttiClass::Load(std::uintptr_t imageBase, std::uintptr_t ptrBase,
                     const char* mangledName, std::string* why) {
    bases_.clear();
    vtables_.clear();
    const Section data = FindSection(imageBase, ".data");
    const Section rdata = FindSection(imageBase, ".rdata");
    if (!data.begin || !rdata.begin) {
        if (why) *why = "no .data/.rdata section";
        return false;
    }
    const std::uintptr_t td = FindTypeDescriptor(data, mangledName);
    if (!td) {
        if (why) *why = std::string("no type descriptor ") + mangledName;
        return false;
    }
    const auto tdRva = static_cast<std::uint32_t>(td - imageBase);

    // Every locator for this class (one per vtable), keyed by its absolute
    // address as the vtables store it.
    std::unordered_map<std::uintptr_t, int> colOffset;
    std::uint32_t chdRva = 0;
    for (std::uintptr_t p = rdata.begin; p + sizeof(Col) <= rdata.end; p += 4) {
        auto c = reinterpret_cast<const Col*>(p);
        if (c->sig != 1 || c->pTypeDesc != tdRva) continue;
        if (c->pSelf != static_cast<std::uint32_t>(p - imageBase)) continue;
        colOffset[ptrBase + c->pSelf] = static_cast<int>(c->offset);
        chdRva = c->pClassDesc;
    }
    if (colOffset.empty() || !chdRva) {
        if (why) *why = "no complete object locator";
        return false;
    }

    for (std::uintptr_t p = rdata.begin; p + 16 <= rdata.end; p += 8) {
        auto it = colOffset.find(*reinterpret_cast<const std::uintptr_t*>(p));
        if (it == colOffset.end()) continue;
        if (vtables_.count(it->second)) {
            if (why) *why = "two vtables claim one locator";
            return false;
        }
        vtables_[it->second] = p + 8;
    }

    auto chd = reinterpret_cast<const Chd*>(imageBase + chdRva);
    auto arr = reinterpret_cast<const std::uint32_t*>(imageBase + chd->pBaseArray);
    for (std::uint32_t i = 0; i < chd->numBases; ++i) {
        auto b = reinterpret_cast<const Bcd*>(imageBase + arr[i]);
        // pdisp != -1 is a virtual base, whose offset is not fixed.
        if (b->pdisp != -1) continue;
        const char* name = reinterpret_cast<const char*>(imageBase + b->pTypeDesc + 16);
        bases_.emplace_back(name, b->mdisp);
    }
    return true;
}

std::uintptr_t RttiClass::BaseVtable(const char* baseMangled) const {
    for (const auto& b : bases_) {
        if (b.first != baseMangled) continue;
        auto it = vtables_.find(b.second);
        return it == vtables_.end() ? 0 : it->second;
    }
    return 0;
}

}  // namespace bridge
