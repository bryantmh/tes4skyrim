// Address Library stable IDs for everything MorrowindRuntime touches.
//
// Every ID was derived from the RUNNING Steam build (1.6.1170), whose on-disk
// .text is DRM-encrypted -- `skyrim_disasm.py --live --save-image` gives a
// decrypted image with RVAs matching the running game. Each was then inverted
// through versionlib-1-6-1170-0.bin and checked to exist in all 12 shipped
// databases. Nothing here is a raw RVA.
//
// How each was located, with the disassembly:
// docs/commentary/morrowind_runtime.md#menu-registration

#pragma once

#include <cstddef>
#include <cstdint>

namespace mwruntime::ids {

// The MenuManager singleton POINTER (0x20f6a00 on 1.6.1170), read, never
// called.
//
// 🛑 0xfa32f0 next door is the CONSTRUCTOR, not a getter: it takes placement
// memory in rcx. Calling it with no argument corrupts the manager and the
// game dies reading [r9] at 0xfa40cc. A registration site tests the pointer
// first and only constructs when it is null, which by our DataLoaded it never
// is.
// See: docs/commentary/morrowind_runtime.md#menu-registration
constexpr std::uint64_t kMenuManagerSingleton = 400327;

// MenuManager::Register(this, const char* name, IMenu* (*creator)())
// (0xfa5480). Found as the jmp target shared by 10 distinct menu-name call
// sites; identical to the RVA SKSE hardcodes.
constexpr std::uint64_t kMenuManagerRegister = 82086;

// GFxLoader::LoadMovie(this, IMenu* menu, GFxMovieView** viewOut,
//                      const char* name, int scaleMode, float bgAlpha)
// (0xfb0110). `name` carries NO extension: the callee formats it through
// "Interface/%s.swf". 61 call sites, one per vanilla menu.
constexpr std::uint64_t kGFxLoaderLoadMovie = 82325;

// The GFxLoader singleton POINTER, not a getter (0x35f11c8): menu ctors load
// it with a plain mov from .data.
constexpr std::uint64_t kGFxLoaderSingleton = 402775;

// The Scaleform allocator singleton POINTER (0x3292490). Menus are allocated
// through it, at vtable slot 0x50: Alloc(this, size, 0).
//
// 🛑 The engine FREES a menu through this same allocator, so a menu from
// HeapAlloc is a crash at close, not at open.
constexpr std::uint64_t kScaleformAllocator = 412058;

// The Alloc slot in that allocator's vtable.
constexpr std::size_t kScaleformAllocSlot = 0x50;

// The scale mode every vanilla menu passes to LoadMovie. MessageBoxMenu,
// BookMenu and BarterMenu all push 3 at [rsp+0x20]; SKSE's CustomMenu passes
// kNoBorder, which is the same value.
constexpr int kScaleModeNoBorder = 3;

// GFxMovieView::Render's byte offset in its vtable, read off the tail-jump
// that IS MessageBoxMenu::Render (0x539ac0 on 1.6.659, id 33632):
//   mov rcx,[rcx+0x10] / test rcx,rcx / jz / mov rax,[rcx] / jmp [rax+0x130]
// A menu that does not make this call registers, takes focus and pauses the
// game while drawing nothing.
// See: docs/commentary/morrowind_runtime.md#the-menu-must-render-itself
constexpr std::size_t kMovieViewRenderSlot = 0x130;

}  // namespace mwruntime::ids
