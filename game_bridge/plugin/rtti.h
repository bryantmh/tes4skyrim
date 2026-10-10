// Runtime RTTI walk: find the vtable a class uses for one of its base classes.
//
// Used by the flight recorder to locate SkyrimVM's BSTEventSink<T> vtables.
// Nothing here is an address or an ID: the walk starts from the class's
// mangled name, which MSVC keeps in the shipped exe, and follows the same
// descriptors the C++ runtime itself uses for dynamic_cast.
//
//   TypeDescriptor (.data)   vfptr, spare, ".?AVSkyrimVM@@"
//   CompleteObjectLocator    one per vtable: {1, offset, cd, pTD, pCHD, pSelf}
//   ClassHierarchyDescriptor {sig, attr, numBases, pBaseClassArray}
//   BaseClassDescriptor      {pTD, numContained, mdisp, pdisp, vdisp, attr, pCHD}
//
// A base's vtable is the one whose locator's `offset` equals that base's
// `mdisp`. Every field is an RVA except the vtable's own locator pointer, which
// is an absolute address -- relocated in the running game, but not in an image
// mapped as a resource. That is why Load takes two bases (see test_recorder).

#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace bridge {

class RttiClass {
public:
    // imageBase: where the sections are mapped. ptrBase: the base the image's
    // absolute pointers were relocated to (== imageBase inside the game).
    bool Load(std::uintptr_t imageBase, std::uintptr_t ptrBase,
              const char* mangledName, std::string* why);

    // The vtable used for `baseMangled` inside this class, or 0 if the class
    // has no such (non-virtual) base.
    std::uintptr_t BaseVtable(const char* baseMangled) const;

    const std::vector<std::pair<std::string, int>>& bases() const { return bases_; }

private:
    std::vector<std::pair<std::string, int>> bases_;   // name, mdisp
    std::unordered_map<int, std::uintptr_t> vtables_;  // offset -> vtable
};

}  // namespace bridge
