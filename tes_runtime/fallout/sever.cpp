#include "sever.h"

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <vector>

#include "addresses.h"
#include "engine.h"
#include "hook.h"
#include "ids.h"
#include "json.h"
#include "log.h"

namespace tesruntime {

namespace {

// --------------------------------------------------------- engine layout ----

constexpr std::size_t kHitPosition  = 0x00;   // NiPoint3, game units
constexpr std::size_t kHitDirection = 0x0c;
constexpr std::size_t kHitAggressor = 0x18;   // handle

constexpr std::size_t kGeometrySkin  = 0x130;
constexpr std::size_t kSkinPartCount = 0x88;
constexpr std::size_t kSkinPartitions = 0x90;
constexpr std::size_t kSkinSummary   = 0x98;

// FO3 partition numbering (wbBodyLocationEnum): sections 0-14, limb caps
// 100+type, torso caps 200+type, adjoining torso sections 1000*type.
constexpr int kLimbCapBase   = 100;
constexpr int kTorsoCapBase  = 200;
constexpr int kTorsoSectionMul = 1000;

// A hit farther than this from every severable part node severs nothing.
constexpr float kMaxLimbDistance = 40.0f;
// FNV severs a limb on most killing blows; iCombatDismemberPartChance is
// the engine's leftover roll, applied here on top of a fatal hit.
constexpr int kSeverChancePercent = 75;
// The loose limb's pop, game units per second along the hit direction.
constexpr float kLimbImpulse = 12.0f;
constexpr int kImpulseRetryFrames = 30;

constexpr std::uint32_t kSaveRecord = 'SEVR';
constexpr std::uint32_t kSaveVersion = 1;

using ApplyHitFn = void (*)(void* actor, void* hitData);
using ReapplyFn = void (*)(void* actor);
using BodyPartDataFn = void* (*)(void* actor);
using RttiCastFn = void* (*)(void* rtti, void* object);
using GetObjectByNameFn = void* (*)(void* self, void* fixedString);
using Get3DFn = void* (*)(void* self);
using AsNodeFn = void* (*)(void* self);
using PlaceAtMeFn = void* (*)(void* vm, std::uint32_t stack, void* self, void* form,
                              std::int32_t count, bool persist, bool disabled);
using SetPositionFn = void (*)(void* vm, std::uint32_t stack, void* self,
                               float x, float y, float z);
using ImpulseFn = void (*)(void* vm, std::uint32_t stack, void* self,
                           float x, float y, float z, float magnitude);
using LookupByHandleFn = void* (*)(std::uint32_t* handle, void** out);

struct Part {
    std::string node;
    int type = 0;
    bool severable = false;
    std::uint32_t limbLocal = 0;
    void* limbForm = nullptr;
};

struct PendingRecord {
    std::uint32_t local = 0;
    std::string file, limbFile;
    std::vector<Part> parts;
};

struct Engine {
    ApplyHitFn   originalApplyHit = nullptr;
    ReapplyFn    originalReapply = nullptr;
    BodyPartDataFn bodyPartData = nullptr;
    RttiCastFn   rttiCast = nullptr;
    void*        dismemberRtti = nullptr;
    PlaceAtMeFn  placeAtMe = nullptr;
    SetPositionFn setPosition = nullptr;
    ImpulseFn    applyImpulse = nullptr;
    LookupByHandleFn lookupByHandle = nullptr;
};

Engine g_engine;
std::vector<PendingRecord> g_pending;
std::map<std::uint32_t, std::vector<Part>> g_parts;     // BPTD FormID -> parts
std::map<std::uint32_t, std::set<int>> g_severed;       // actor FormID -> types
std::mutex g_mutex;

// ---------------------------------------------------------------- sidecar ----

void LoadSidecar(const std::string& name, const Json& doc) {
    if (doc["version"].asInt() != 2) {
        Log("sever: %s has version %d, want 2", name.c_str(), doc["version"].asInt());
        return;
    }
    for (const auto& kv : doc["bodyparts"].fields()) {
        PendingRecord rec;
        rec.local = kv.second["local"].asU32();
        rec.file = kv.second["file"].asString();
        rec.limbFile = doc["limb_file"].asString();
        for (const auto& p : kv.second["parts"].items()) {
            Part part;
            part.node = p["node"].asString();
            part.type = p["type"].asInt();
            part.severable = p["severable"].asBool() || p["explodable"].asBool();
            part.limbLocal = p["limb_local"].asU32();
            if (part.type > 0 && !part.node.empty()) rec.parts.push_back(part);
        }
        g_pending.push_back(std::move(rec));
    }
    Log("sever: %s: %zu body part records", name.c_str(), doc["bodyparts"].size());
}

// ------------------------------------------------------------- 3D helpers ----

void* FindNode(void* root, const std::string& name) {
    FixedString fs(name.c_str());
    return VCall<GetObjectByNameFn>(root, kVtGetObjectByName)(root, &fs.ptr);
}

float Distance(void* node, const float* p) {
    const float* t = &At<float>(node, kWorldTranslate);
    const float dx = t[0] - p[0], dy = t[1] - p[1], dz = t[2] - p[2];
    return std::sqrt(dx * dx + dy * dy + dz * dz);
}

// Sets the visibility byte of every partition numbered `bodyPart` under
// `node`; the engine's own routine (id 37641) does exactly this walk.
int SetPartitionVisible(void* node, int bodyPart, bool visible) {
    if (!node) return 0;
    int changed = 0;
    if (void* asNode = VCall<AsNodeFn>(node, kVtAsNode)(node)) {
        const std::uint16_t n = At<std::uint16_t>(asNode, kNodeChildCount);
        void** children = At<void**>(asNode, kNodeChildren);
        for (std::uint16_t i = 0; i < n; ++i) {
            if (children[i]) changed += SetPartitionVisible(children[i], bodyPart, visible);
        }
        return changed;
    }
    if (!VCall<AsNodeFn>(node, kVtAsGeometry)(node)) return 0;
    void* skin = At<void*>(node, kGeometrySkin);
    if (!skin) return 0;
    void* dismember = g_engine.rttiCast(g_engine.dismemberRtti, skin);
    if (!dismember) return 0;
    const std::uint32_t count = At<std::uint32_t>(dismember, kSkinPartCount);
    std::uint8_t* parts = At<std::uint8_t*>(dismember, kSkinPartitions);
    if (!parts) return 0;
    std::uint8_t summary = 0;
    for (std::uint32_t i = 0; i < count; ++i) {
        std::uint8_t* entry = parts + i * 4;
        std::uint16_t part;
        std::memcpy(&part, entry + 2, sizeof(part));
        if (part == bodyPart) {
            entry[0] = visible ? 1 : 0;
            ++changed;
        }
        summary |= entry[0];
    }
    At<std::uint8_t>(dismember, kSkinSummary) = summary;
    return changed;
}

int HidePart(void* root, int type) {
    int n = SetPartitionVisible(root, type, false);
    n += SetPartitionVisible(root, kTorsoSectionMul * type, false);
    n += SetPartitionVisible(root, kLimbCapBase + type, true);
    n += SetPartitionVisible(root, kTorsoCapBase + type, true);
    return n;
}

float Health(void* actor) {
    void* owner = static_cast<char*>(actor) + kActorValueOwner;
    return VCall<float (*)(void*, int)>(owner, 1)(owner, kActorValueHealth);
}

// ------------------------------------------------------------- the limb ----

// Pops the loose limb once its 3D exists; a placed reference loads its 3D a
// frame or two after PlaceAtMe, so this re-queues itself until then.
class ImpulseTask : public TaskDelegate {
public:
    ImpulseTask(void* ref, const float* dir, int frames)
        : ref_(ref), frames_(frames) { std::memcpy(dir_, dir, sizeof(dir_)); }
    void Run() override {
        if (!VCall<Get3DFn>(ref_, kVtGet3D)(ref_)) {
            if (frames_-- > 0) RunOnMainThread(new ImpulseTask(ref_, dir_, frames_));
            return;
        }
        if (g_engine.applyImpulse) {
            g_engine.applyImpulse(g_api.vm, 0, ref_, dir_[0], dir_[1], dir_[2], kLimbImpulse);
        }
    }
    void Dispose() override { delete this; }

private:
    void* ref_;
    float dir_[3];
    int frames_;
};

void DropLimb(void* actor, void* root, const Part& part, const float* dir) {
    if (!part.limbForm || !g_engine.placeAtMe || !g_engine.setPosition || !g_api.vm) return;
    void* ref = g_engine.placeAtMe(g_api.vm, 0, actor, part.limbForm, 1, false, false);
    if (!ref) {
        Log("sever: PlaceAtMe of the limb for %s returned nothing", part.node.c_str());
        return;
    }
    float pos[3];
    void* node = FindNode(root, part.node);
    if (node) {
        const float* t = &At<float>(node, kWorldTranslate);
        pos[0] = t[0]; pos[1] = t[1]; pos[2] = t[2];
    } else if (!RefPosition(actor, pos)) {
        return;
    }
    g_engine.setPosition(g_api.vm, 0, ref, pos[0], pos[1], pos[2] + 5.0f);
    float d[3] = {dir[0], dir[1], dir[2] + 0.5f};
    const float len = std::sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
    if (len < 0.001f) { d[0] = 0; d[1] = 0; d[2] = 1; }
    RunOnMainThread(new ImpulseTask(ref, d, kImpulseRetryFrames));
}

// ---------------------------------------------------------------- severing ----

const std::vector<Part>* PartsOf(void* actor) {
    void* bpd = g_engine.bodyPartData(actor);
    if (!bpd) return nullptr;
    auto it = g_parts.find(At<std::uint32_t>(bpd, kFormID));
    return it == g_parts.end() ? nullptr : &it->second;
}

const Part* NearestPart(const std::vector<Part>& parts, void* root, const float* pos) {
    const Part* best = nullptr;
    float bestDist = kMaxLimbDistance;
    for (const Part& p : parts) {
        if (!p.severable) continue;
        void* node = FindNode(root, p.node);
        if (!node) continue;
        const float d = Distance(node, pos);
        if (d < bestDist) { bestDist = d; best = &p; }
    }
    return best;
}

bool HitOrigin(void* hitData, void* actor, float* pos) {
    const float* hp = &At<float>(hitData, kHitPosition);
    if (hp[0] != 0 || hp[1] != 0 || hp[2] != 0) {
        pos[0] = hp[0]; pos[1] = hp[1]; pos[2] = hp[2];
        return true;
    }
    // A melee HitData carries no impact point: measure from the attacker.
    std::uint32_t handle = At<std::uint32_t>(hitData, kHitAggressor);
    void* attacker = nullptr;
    if (g_engine.lookupByHandle) g_engine.lookupByHandle(&handle, &attacker);
    if (!attacker) return RefPosition(actor, pos);
    const bool ok = RefPosition(attacker, pos);
    ReleaseRef(attacker);
    return ok;
}

void Sever(void* actor, void* root, const Part& part, const float* dir) {
    const int n = HidePart(root, part.type);
    g_severed[At<std::uint32_t>(actor, kFormID)].insert(part.type);
    DropLimb(actor, root, part, dir);
    Log("sever: %08X %s (type %d): %d partitions, limb %s", At<std::uint32_t>(actor, kFormID),
        part.node.c_str(), part.type, n, part.limbForm ? "dropped" : "none");
}

void OnFatalHit(void* hitData, void* actor) {
    const std::vector<Part>* parts = PartsOf(actor);
    if (!parts) return;
    void* root = VCall<Get3DFn>(actor, kVtGet3D)(actor);
    if (!root) return;
    float pos[3];
    if (!HitOrigin(hitData, actor, pos)) return;
    const Part* best = NearestPart(*parts, root, pos);
    if (!best) return;
    if ((std::rand() % 100) >= kSeverChancePercent) return;
    Sever(actor, root, *best, &At<float>(hitData, kHitDirection));
}

// Replacement for the hit-apply routine every melee and projectile hit
// reaches: a hit that takes the actor from alive to dead is fatal.
void ApplyHitHook(void* actor, void* hitData) {
    const bool actorLike = actor && At<std::uint8_t>(actor, kFormType) == kFormTypeActor;
    const float before = actorLike ? Health(actor) : 0.0f;
    g_engine.originalApplyHit(actor, hitData);
    if (!actorLike || before <= 0.0f || Health(actor) > 0.0f) return;
    std::lock_guard<std::mutex> lk(g_mutex);
    OnFatalHit(hitData, actor);
}

void Reapply(void* actor) {
    auto it = g_severed.find(At<std::uint32_t>(actor, kFormID));
    if (it == g_severed.end()) return;
    void* root = VCall<Get3DFn>(actor, kVtGet3D)(actor);
    if (!root) return;
    for (int type : it->second) HidePart(root, type);
}

// Replacement for the engine's own on-3D-load dismemberment re-apply.
void ReapplyHook(void* actor) {
    g_engine.originalReapply(actor);
    if (!actor || At<std::uint8_t>(actor, kFormType) != kFormTypeActor) return;
    std::lock_guard<std::mutex> lk(g_mutex);
    Reapply(actor);
}

class ReapplyAllTask : public TaskDelegate {
public:
    void Run() override {
        if (!g_api.lookupForm) return;
        std::lock_guard<std::mutex> lk(g_mutex);
        for (const auto& kv : g_severed) {
            void* actor = g_api.lookupForm(kv.first);
            if (actor && At<std::uint8_t>(actor, kFormType) == kFormTypeActor) Reapply(actor);
        }
    }
    void Dispose() override { delete this; }
};

}  // namespace

bool InstallSevering() {
    ForEachSidecar("bodyparts.json", LoadSidecar);
    if (g_pending.empty()) {
        Log("sever: nothing to sever; hooks not installed");
        return false;
    }
    const std::uintptr_t applyHit = Resolve("Actor::ApplyHit", ids::kApplyHit, nullptr);
    const std::uintptr_t reapply = Resolve("ReapplyDismemberment", ids::kReapplyDismember, nullptr);
    const std::uintptr_t bpd = Resolve("Actor::GetBodyPartData", ids::kActorBodyPartData, nullptr);
    const std::uintptr_t cast = Resolve("NiRTTI cast", ids::kRttiCast, nullptr);
    const std::uintptr_t rtti = Resolve("BSDismemberSkinInstance RTTI", ids::kDismemberSkinRtti, nullptr);
    if (!applyHit || !reapply || !bpd || !cast || !rtti) {
        Log("sever: an address is unresolved; limb severing disabled");
        return false;
    }
    g_engine.originalApplyHit = reinterpret_cast<ApplyHitFn>(applyHit);
    g_engine.originalReapply = reinterpret_cast<ReapplyFn>(reapply);
    g_engine.bodyPartData = reinterpret_cast<BodyPartDataFn>(bpd);
    g_engine.rttiCast = reinterpret_cast<RttiCastFn>(cast);
    g_engine.dismemberRtti = reinterpret_cast<void*>(rtti);
    g_engine.placeAtMe = reinterpret_cast<PlaceAtMeFn>(
        Resolve("ObjectReference.PlaceAtMe", ids::kPlaceAtMe, nullptr));
    g_engine.setPosition = reinterpret_cast<SetPositionFn>(
        Resolve("ObjectReference.SetPosition", ids::kSetPosition, nullptr));
    g_engine.applyImpulse = reinterpret_cast<ImpulseFn>(
        Resolve("ObjectReference.ApplyHavokImpulse", ids::kApplyHavokImpulse, nullptr));
    g_engine.lookupByHandle = reinterpret_cast<LookupByHandleFn>(
        Resolve("LookupReferenceByHandle", ids::kLookupByHandle, nullptr));
    const int a = PatchAllCalls(applyHit, reinterpret_cast<void*>(&ApplyHitHook), "ApplyHit");
    const int b = PatchAllCalls(reapply, reinterpret_cast<void*>(&ReapplyHook), "ReapplyDismember");
    return a > 0 && b > 0;
}

void ResolveSeverForms() {
    std::lock_guard<std::mutex> lk(g_mutex);
    int records = 0, limbs = 0;
    for (const PendingRecord& rec : g_pending) {
        void* form = FormFromFile(rec.local, rec.file);
        if (!form) continue;
        std::vector<Part> parts = rec.parts;
        for (Part& p : parts) {
            if (p.limbLocal) p.limbForm = FormFromFile(p.limbLocal, rec.limbFile);
            if (p.limbForm) ++limbs;
        }
        g_parts[At<std::uint32_t>(form, kFormID)] = std::move(parts);
        ++records;
    }
    Log("sever: %d of %zu body part records resolved, %d limb statics", records,
        g_pending.size(), limbs);
}

void SeverSave(SKSESerializationInterface* intfc) {
    std::lock_guard<std::mutex> lk(g_mutex);
    std::vector<std::uint32_t> blob;
    for (const auto& kv : g_severed) {
        if (kv.second.empty()) continue;
        blob.push_back(kv.first);
        blob.push_back(static_cast<std::uint32_t>(kv.second.size()));
        for (int t : kv.second) blob.push_back(static_cast<std::uint32_t>(t));
    }
    intfc->WriteRecord(kSaveRecord, kSaveVersion, blob.data(),
                       static_cast<UInt32>(blob.size() * sizeof(std::uint32_t)));
}

void SeverLoad(SKSESerializationInterface* intfc) {
    std::lock_guard<std::mutex> lk(g_mutex);
    g_severed.clear();
    UInt32 type = 0, version = 0, length = 0;
    while (intfc->GetNextRecordInfo(&type, &version, &length)) {
        if (type != kSaveRecord || version != kSaveVersion) continue;
        std::vector<std::uint32_t> blob(length / sizeof(std::uint32_t));
        intfc->ReadRecordData(blob.data(), length);
        for (std::size_t i = 0; i + 1 < blob.size();) {
            std::uint32_t fid = blob[i], n = blob[i + 1];
            i += 2;
            std::uint32_t resolved = 0;
            const bool ok = intfc->ResolveFormId(fid, &resolved);
            for (std::uint32_t k = 0; k < n && i < blob.size(); ++k, ++i) {
                if (ok) g_severed[resolved].insert(static_cast<int>(blob[i]));
            }
        }
    }
    Log("sever: %zu severed actors loaded", g_severed.size());
}

void SeverRevert(SKSESerializationInterface*) {
    std::lock_guard<std::mutex> lk(g_mutex);
    g_severed.clear();
}

void SeverReapplyAll() {
    RunOnMainThread(new ReapplyAllTask());
}

}  // namespace tesruntime
