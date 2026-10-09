// Functional resource-open cases; the callback represents loose/BSA reads.
#include "voice_redirects.h"

#include <cstdio>
#include <map>
#include <string>
#include <vector>

using namespace tesruntime;
namespace {
std::map<std::string, int> files;
std::vector<std::string> opened;
int forcedError = 0;
int failures = 0;
int OpenFile(const char* path, void** stream, std::uint8_t, void*) {
    const std::string name(path);
    opened.push_back(name);
    if (forcedError) return forcedError;
    auto it = files.find(name);
    if (it == files.end()) return 1;
    *stream = &it->second;
    return 0;
}
void Check(bool ok, const char* what) {
    std::printf("[%s] %s\n", ok ? "ok" : "FAIL", what);
    if (!ok) ++failures;
}
int Read(const VoiceRedirects& rules, const std::string& path,
         int* value = nullptr, std::uint8_t writable = 0) {
    opened.clear();
    void* stream = nullptr;
    const int rc = rules.Open(path.c_str(), &stream, writable, nullptr, OpenFile);
    if (value && stream) *value = *static_cast<int*>(stream);
    return rc;
}
}  // namespace

int main() {
    VoiceRedirects rules;
    rules.Add(Json::Parse(R"({"plugin":"Morrowind_ob.esm","voices":{
      "TES4MaleНорд":{"alternates":["TES4MaleИмперец"],
        "greeting":"sound/voice/Oblivion.esm/TES4MaleНорд/genericnor_hello_00062cb3_1.wav"},
      "TES4FemaleВысокийэльф":{"alternates":["TES4FemaleИмперец"],"greeting":""},
      "TES4MaleDarkElf":{"alternates":["TES4MaleВысокийэльф","TES4MaleИмперец"],"greeting":""}
    }})"));
    const std::string root = "sound\\voice\\morrowind_ob.esm\\";
    const std::string leaf = "morrodefau_topic_003211ca_1.fuz";
    const std::string own = root + "tes4maleНорд\\" + leaf;
    const std::string imperial = root + "tes4maleИмперец\\" + leaf;
    files[own] = 10; files[imperial] = 20;
    int value = 0;
    Check(Read(rules, own, &value) == 0 && value == 10 && opened.size() == 1,
          "existing recording wins over a fallback");
    files.erase(own);
    Check(Read(rules, own, &value) == 0 && value == 20 && opened.back() == imperial,
          "missing Nord recording reads the same-sex Imperial resource");
    files.erase(imperial);
    files[root + "tes4maleВысокийэльф\\" + leaf] = 30;
    Check(Read(rules, own) == 1 && opened.size() == 2,
          "Nord never borrows an arbitrary High Elf recording");
    Check(Read(rules, root + "tes4maledarkelf\\" + leaf, &value) == 0 && value == 30,
          "Diverse Voices Dark Elf tries the shared High Elf voice first");
    const std::string hello = "sound\\voice\\oblivion.esm\\tes4maleНорд\\genericnor_hello_00062cb3_1.fuz";
    files[hello] = 40;
    const std::string greeting = root + "tes4maleНорд\\morrodefau_greeting_023a00_00018aaa_1.fuz";
    Check(Read(rules, greeting, &value) == 0 && value == 40 && opened.back() == hello,
          "missing first greeting reads the master's hello with its FUZ/lip container");
    Check(Read(rules, root + "tes4maleНорд\\morrodefau_greeting_023a00_00018aaa_2.fuz") == 1,
          "later greeting responses are not replaced by hello");
    Check(Read(rules, root + "tes4maleNord\\" + leaf) == 1 && opened.size() == 1,
          "English aliases of a Russian master voice are not accepted");
    Check(Read(rules, "sound\\voice\\other.esp\\tes4maleНорд\\" + leaf) == 1 && opened.size() == 1,
          "unstaged plugins keep ordinary Skyrim behavior");
    Check(Read(rules, own, nullptr, 1) == 1 && opened.size() == 1,
          "write requests are never redirected");
    forcedError = 3;
    Check(Read(rules, greeting) == 3 && opened.size() == 1,
          "file errors are preserved rather than masked by another recording");
    forcedError = 0;
    Check(Read(rules, "Data/Sound/Voice/Morrowind_ob.esm/TES4MaleНорд/morrodefau_greeting_023a00_00018aaa_1.fuz", &value) == 0 && value == 40,
          "Data prefix and ASCII casing do not break Russian voice paths");
    return failures ? 1 : 0;
}
