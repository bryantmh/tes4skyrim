// The sound commands: PlaySound and its 3D/looping/volume-pitch variants,
// StopSound and GetSoundPlaying. Each names a TES3 SOUN id, which SOUN.txt
// maps to the SNDR the import minted, and reaches the engine through a
// GameHook so the headless tests run with the hooks null.
//
// TES3's distinction between PlaySound and PlaySound3D is WHERE the sound
// sits: the non-3D form plays on the listener, the 3D form on a reference.
// Skyrim's Sound.Play takes the source reference either way, so the split is
// which ref is passed, not which native is called.
// See: docs/commentary/morrowind_runtime.md#sound-opcodes

#include <map>
#include <string>
#include <utility>

#include <components/compiler/opcodes.hpp>
#include <components/interpreter/context.hpp>
#include <components/interpreter/opcodes.hpp>

#include "dialogue_state.h"
#include "log.h"
#include "script_ops.h"

namespace tesruntime::mw {

namespace {

char LowerChar(char c) {
    return (c >= 'A' && c <= 'Z') ? static_cast<char>(c - 'A' + 'a') : c;
}

// Keyed by (reference, sound), both case-folded: Morrowind ids are
// case-insensitive, and `StopSound` stops what THIS reference started.
std::pair<std::string, std::string> Key(const std::string& ref,
                                        const std::string& sound) {
    std::string a = ref;
    std::string b = sound;
    for (char& c : a) c = LowerChar(c);
    for (char& c : b) c = LowerChar(c);
    return {a, b};
}

// What this session has started and not yet stopped. Skyrim has no "is this
// instance playing" native, so the playback ids Sound.Play returned are the
// whole answer GetSoundPlaying can give.
class SoundInstances {
public:
    void Started(const std::string& ref, const std::string& sound, int id) {
        live_[Key(ref, sound)] = id;
    }

    bool Playing(const std::string& ref, const std::string& sound) const {
        return live_.count(Key(ref, sound)) != 0;
    }

    // The instance, forgotten in the same step: a stopped sound is not playing.
    int Take(const std::string& ref, const std::string& sound) {
        const auto it = live_.find(Key(ref, sound));
        if (it == live_.end()) return 0;
        const int id = it->second;
        live_.erase(it);
        return id;
    }

private:
    std::map<std::pair<std::string, std::string>, int> live_;
};

SoundInstances& Sounds() {
    static SoundInstances instances;
    return instances;
}

// What a sound plays at when the command carries no volume. TES3 volume and
// pitch are 0..1 multipliers; Skyrim clamps volume the same way and has no
// per-instance pitch, so the VP forms drop the pitch rather than folding it
// into the volume.
constexpr float kFullVolume = 1.0f;

// Plays a sound and remembers the instance, so StopSound and GetSoundPlaying
// have something to name. An unresolved sound leaves nothing to remember.
void Start(const std::string& ref, const std::string& sound, bool loop,
           float volume) {
    if (!Hooks().playSound) {
        Log("sound: '%s' -- no playSound hook", sound.c_str());
        return;
    }
    const int instance = Hooks().playSound(ref, sound, loop, volume);
    Log("sound: %s '%s'%s -> instance %d", loop ? "loop" : "play",
        sound.c_str(), ref.empty() ? "" : " at a ref", instance);
    if (instance) Sounds().Started(ref, sound, instance);
}

// `PlaySound sound [volume] [pitch]`: on the listener, so no reference.
//
// 🛑 An `X` argument is consumed by the COMPILER and pushes nothing, so this
// is an ordinary segment-5 opcode taking the sound alone -- the volume and
// pitch a script writes after it never reach the stack.
class OpPlaySound : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        Start(std::string(), PopString(runtime), false, kFullVolume);
    }
};

// `PlaySoundVP sound volume pitch`: the fixed-argument form.
class OpPlaySoundVp : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string sound = PopString(runtime);
        const float volume = PopFloat(runtime);
        PopFloat(runtime);
        Start(std::string(), sound, false, volume);
    }
};

// `PlaySound3D sound [volume] [pitch]` / `PlayLoopSound3D`: on a reference.
template <class R, bool Loop>
class OpPlaySound3d : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        Start(ref, PopString(runtime), Loop, kFullVolume);
    }
};

// The `VP` forms: volume and pitch are always present.
template <class R, bool Loop>
class OpPlaySound3dVp : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const std::string sound = PopString(runtime);
        const float volume = PopFloat(runtime);
        PopFloat(runtime);
        Start(ref, sound, Loop, volume);
    }
};

// `StopSound sound`: stops what THIS reference started, which is why the
// instance is tracked per (ref, sound) rather than per sound.
template <class R>
class OpStopSound : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const std::string sound = PopString(runtime);
        const int instance = Sounds().Take(ref, sound);
        Log("sound: stop '%s' -> instance %d", sound.c_str(), instance);
        if (instance && Hooks().stopSound) Hooks().stopSound(instance);
    }
};

// `GetSoundPlaying sound`: 1 while this reference's instance is live.
//
// 🛑 Answers from what WE started, not from the engine: Skyrim exposes no
// "is this instance playing" native, and the authored use is a script asking
// about a loop it started itself. Measured over Tamriel Rebuilt and Tamriel
// Data: 243 call sites, 157 of them `== 0` immediately before starting one.
template <class R>
class OpGetSoundPlaying : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const std::string sound = PopString(runtime);
        runtime.push(Sounds().Playing(ref, sound) ? 1 : 0);
    }
};

// `Say file text`: the scripted VOICE channel. OpenMW pops the target, then
// the file, then the subtitle text, plays the file on the actor and shows the
// text; this does the same through the hook, which routes it to the engine's
// own voice channel so the mouth moves.
template <class R>
class OpSay : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const std::string file = PopString(runtime);
        const std::string text = PopString(runtime);
        Log("sound: %s says '%s'", ref.c_str(), file.c_str());
        if (Hooks().say) Hooks().say(ref, file, text);
    }
};

// `SayDone`: 1 once the actor's say has finished, which is what a script
// waits on before moving to its next line.
template <class R>
class OpSayDone : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        runtime.push(Hooks().sayDone ? (Hooks().sayDone(ref) ? 1 : 0) : 1);
    }
};

}  // namespace

void InstallSoundOps(OpcodeInstaller& into) {
    namespace S = Compiler::Sound;
    into.Real<OpSay<Implicit>>(S::opcodeSay);
    into.Real<OpSay<Explicit>>(S::opcodeSayExplicit);
    into.Real<OpSayDone<Implicit>>(S::opcodeSayDone);
    into.Real<OpSayDone<Explicit>>(S::opcodeSayDoneExplicit);
    into.Real<OpPlaySound>(S::opcodePlaySound);
    into.Real<OpPlaySoundVp>(S::opcodePlaySoundVP);

    into.Real<OpPlaySound3d<Implicit, false>>(S::opcodePlaySound3D);
    into.Real<OpPlaySound3d<Explicit, false>>(S::opcodePlaySound3DExplicit);
    into.Real<OpPlaySound3dVp<Implicit, false>>(S::opcodePlaySound3DVP);
    into.Real<OpPlaySound3dVp<Explicit, false>>(S::opcodePlaySound3DVPExplicit);

    into.Real<OpPlaySound3d<Implicit, true>>(S::opcodePlayLoopSound3D);
    into.Real<OpPlaySound3d<Explicit, true>>(
        S::opcodePlayLoopSound3DExplicit);
    into.Real<OpPlaySound3dVp<Implicit, true>>(S::opcodePlayLoopSound3DVP);
    into.Real<OpPlaySound3dVp<Explicit, true>>(
        S::opcodePlayLoopSound3DVPExplicit);

    into.Real<OpStopSound<Implicit>>(S::opcodeStopSound);
    into.Real<OpStopSound<Explicit>>(S::opcodeStopSoundExplicit);
    into.Real<OpGetSoundPlaying<Implicit>>(S::opcodeGetSoundPlaying);
    into.Real<OpGetSoundPlaying<Explicit>>(S::opcodeGetSoundPlayingExplicit);
}

}  // namespace tesruntime::mw
