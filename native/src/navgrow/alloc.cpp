// This module's operator new/delete, on mimalloc (vendored in
// external/mimalloc/, MIT, v2.1.7).  Cells build on the import's threads, and on the process heap
// they queued on its one lock: throughput peaked at 12 threads and fell to
// two-thirds of that at 30.  mimalloc gives each thread its own heap.  Only
// this module's allocations move; GEOS and Python keep their own allocators.
// See: docs/commentary/tes5_import_navmesh.md#allocator

#include "mimalloc/src/static.c"
#include "mimalloc-new-delete.h"

#ifdef _WIN32
#pragma comment(lib, "advapi32.lib")  // the large-page privilege calls in prim/windows
#endif

namespace {

// Freed pages go back to the OS at once rather than after mimalloc's default
// 10 ms: at 30 threads that cut the pool's peak working set from +5.9 GB to
// +4.8 GB (process heap: +4.6 GB) at no measured cost in speed.
struct ReturnFreedAtOnce {
    ReturnFreedAtOnce() { mi_option_set(mi_option_purge_delay, 0); }
} return_freed_at_once;

}  // namespace
