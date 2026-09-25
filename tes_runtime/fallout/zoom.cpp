#include "zoom.h"

#include <windows.h>

#include <cstdint>
#include <memory>

#include "addresses.h"
#include "engine.h"
#include "fire.h"
#include "guns.h"
#include "ids.h"
#include "log.h"

namespace tesruntime {

namespace {

constexpr std::size_t kCameraWorldFov = 0x13c;   // PlayerCamera world FOV
constexpr std::size_t kCameraFirstFov = 0x140;   // PlayerCamera first-person FOV
constexpr float kFnvDefaultFov = 75.0f;          // FNV fDefaultWorldFOV, the Sight FOV's baseline
constexpr float kZoomSeconds = 0.2f;             // hip <-> iron ease
constexpr float kMaxFrameSeconds = 0.1f;
constexpr float kBlendEdge = 0.001f;             // never exactly on a blender child's anchor
constexpr std::size_t kObjectName = 0x10;        // NiObjectNET::name
constexpr std::size_t kNodeParent = 0x30;        // NiAVObject::parent
constexpr std::size_t kLocalTranslate = 0x6c;    // NiAVObject::local.translate
constexpr std::size_t kWorldRotate = 0x7c;       // NiAVObject::world.rotate, 3x3 row major
constexpr unsigned long kLogIntervalMs = 1000;

using SetFovFn = void (*)(void* state, float fov, bool force, void* camera, bool);
using RecomputeFovFn = void (*)(float fov);
using ObjectByNameFn = void* (*)(void* root, void** name, bool recurse);

// The camera the console's `fov` command writes: the two PlayerCamera
// fields, the render state, the two cached defaults.
struct Camera {
    void** player = nullptr;
    void** renderState = nullptr;
    SetFovFn set = nullptr;
    RecomputeFovFn recompute = nullptr;
    float* defaultWorld = nullptr;
    float* defaultFirst = nullptr;
    float savedWorld = 0.0f, savedFirst = 0.0f;
};

// The blend and what it has written.
struct Zoom {
    bool target = false;      // the key
    bool ticking = false;
    bool firing = false;
    float blend = 0.0f;
    int selector = 0;         // iGunZoom as last written
    LARGE_INTEGER last = {};
    unsigned long lastLog = 0;
};

Camera         g_camera;
Zoom           g_zoom;
ObjectByNameFn g_objectByName = nullptr;
void**         g_player = nullptr;
std::unique_ptr<FixedString> g_sightNode;

void ApplyFov(float world, float first) {
    void* cam = *g_camera.player;
    g_camera.set(*g_camera.renderState, world, false, nullptr, false);
    g_camera.recompute(world);
    At<float>(cam, kCameraWorldFov) = world;
    At<float>(cam, kCameraFirstFov) = first;
    *g_camera.defaultWorld = world;
    *g_camera.defaultFirst = first;
}

float Elapsed() {
    LARGE_INTEGER now, freq;
    QueryPerformanceCounter(&now);
    QueryPerformanceFrequency(&freq);
    const float dt = g_zoom.last.QuadPart
        ? static_cast<float>(now.QuadPart - g_zoom.last.QuadPart) / static_cast<float>(freq.QuadPart)
        : 0.0f;
    g_zoom.last = now;
    return dt < kMaxFrameSeconds ? dt : kMaxFrameSeconds;
}

bool LogDue() {
    const unsigned long now = GetTickCount();
    if (now - g_zoom.lastLog < kLogIntervalMs) return false;
    g_zoom.lastLog = now;
    return true;
}

const char* NameOf(void* object) {
    const char* n = object ? At<const char*>(object, kObjectName) : nullptr;
    return n ? n : "-";
}

// The sighting node's offset from the first-person root, in the root's
// own frame (x right, y forward, z up). False without the node.
bool SightOffset(void* root, float out[3]) {
    void* sight = g_objectByName(root, &g_sightNode->ptr, true);
    if (!sight) return false;
    const float* rw = &At<float>(root, kWorldTranslate);
    const float* sw = &At<float>(sight, kWorldTranslate);
    const float* r = &At<float>(root, kWorldRotate);
    const float d[3] = {sw[0] - rw[0], sw[1] - rw[1], sw[2] - rw[2]};
    for (int c = 0; c < 3; ++c) out[c] = r[c] * d[0] + r[3 + c] * d[1] + r[6 + c] * d[2];
    return true;
}

// Measures the sighting node against the first-person root, once a second,
// for the alignment still to build; nothing is moved.
// See docs/commentary/tes_runtime_guns.md#sight-alignment
void MeasureSights(void* player) {
    if (!LogDue()) return;
    void* root = VCall<void* (*)(void*, bool)>(player, kVtGet3DFirstPerson)(player, true);
    if (!root) return;
    const float* local = &At<float>(root, kLocalTranslate);
    float off[3];
    const bool found = SightOffset(root, off);
    Log("zoom: blend %.2f root '%s' under '%s' local (%.2f %.2f %.2f) sight %s(%.2f %.2f %.2f)",
        g_zoom.blend, NameOf(root), NameOf(At<void*>(root, kNodeParent)),
        local[0], local[1], local[2],
        found ? "" : "missing ", found ? off[0] : 0.0f, found ? off[1] : 0.0f, found ? off[2] : 0.0f);
}

void Stop(void* player) {
    ApplyFov(g_camera.savedWorld, g_camera.savedFirst);
    if (player) SetActorGraphFloat(player, "fGunZoom", 0.0f);
    g_zoom.blend = 0.0f;
    g_zoom.ticking = false;
    g_zoom.last.QuadPart = 0;
    Log("zoom: stop (key %s)", g_zoom.target ? "held" : "released");
}

}  // namespace

bool ZoomActive() {
    return g_zoom.ticking;
}

// One frame: the blend moves towards the key's state; a frame on which
// the gun cannot be read (a transient during the engine's own actions)
// changes nothing, and only a released key ends the tick.
void ZoomTick() {
    void* player = g_player ? *g_player : nullptr;
    const float sightFov = PlayerGunSightFov();
    const float step = Elapsed() / kZoomSeconds;
    if (sightFov <= 0.0f && g_zoom.target) {
        if (LogDue()) Log("zoom: no gun read this frame, holding blend %.2f", g_zoom.blend);
        return;
    }
    const float goal = g_zoom.target ? 1.0f : 0.0f;
    if (g_zoom.blend < goal) g_zoom.blend = g_zoom.blend + step < goal ? g_zoom.blend + step : goal;
    else if (g_zoom.blend > goal) g_zoom.blend = g_zoom.blend - step > goal ? g_zoom.blend - step : goal;
    if (g_zoom.blend <= 0.0f && goal == 0.0f) {
        Stop(player);
        return;
    }
    if (sightFov > 0.0f) {
        const float inside = g_zoom.blend < kBlendEdge ? kBlendEdge
                             : g_zoom.blend > 1.0f - kBlendEdge ? 1.0f - kBlendEdge : g_zoom.blend;
        SetActorGraphFloat(player, "fGunZoom", inside);
        if (!g_zoom.firing && g_zoom.selector != static_cast<int>(goal)) {
            g_zoom.selector = static_cast<int>(goal);
            SetActorGraphInt(player, "iGunZoom", g_zoom.selector);
        }
        const float scale = 1.0f + (sightFov / kFnvDefaultFov - 1.0f) * g_zoom.blend;
        ApplyFov(g_camera.savedWorld * scale, g_camera.savedFirst * scale);
        MeasureSights(player);
    }
}

bool InstallZoom() {
    g_player = reinterpret_cast<void**>(Resolve("PlayerCharacter singleton", ids::kPlayerSingleton, nullptr));
    g_camera.player = reinterpret_cast<void**>(Resolve("PlayerCamera singleton", ids::kPlayerCamera, nullptr));
    g_camera.renderState = reinterpret_cast<void**>(Resolve("render camera state", ids::kRenderCameraState, nullptr));
    g_camera.set = reinterpret_cast<SetFovFn>(Resolve("render SetFOV", ids::kRenderSetFov, nullptr));
    g_camera.recompute = reinterpret_cast<RecomputeFovFn>(Resolve("FOV recompute", ids::kFovRecompute, nullptr));
    g_camera.defaultWorld = reinterpret_cast<float*>(Resolve("cached world FOV", ids::kCachedWorldFov, nullptr));
    g_camera.defaultFirst = reinterpret_cast<float*>(Resolve("cached 1st-person FOV", ids::kCachedFirstFov, nullptr));
    g_objectByName = reinterpret_cast<ObjectByNameFn>(Resolve("NiAVObject::GetObjectByName", ids::kObjectByName, nullptr));
    if (!g_player || !g_camera.player || !g_camera.renderState || !g_camera.set || !g_camera.recompute ||
        !g_camera.defaultWorld || !g_camera.defaultFirst || !g_objectByName) {
        return false;
    }
    g_sightNode.reset(new FixedString("##SightingNode"));
    return true;
}

void ZoomSet(bool zoomed) {
    Log("zoom: key %s", zoomed ? "down" : "up");
    g_zoom.target = zoomed;
    if (g_zoom.ticking || !zoomed || !*g_camera.player) return;
    void* cam = *g_camera.player;
    g_camera.savedWorld = At<float>(cam, kCameraWorldFov);
    g_camera.savedFirst = At<float>(cam, kCameraFirstFov);
    g_zoom.ticking = true;
    Log("zoom: start (sight fov %.0f)", PlayerGunSightFov());
}

void ZoomNoteFiring(bool firing) {
    g_zoom.firing = firing;
    const int want = (g_zoom.target && PlayerGunSightFov() > 0.0f) ? 1 : 0;
    if (firing || g_zoom.selector == want || !g_player || !*g_player) return;
    g_zoom.selector = want;
    SetActorGraphInt(*g_player, "iGunZoom", want);
}

}  // namespace tesruntime
