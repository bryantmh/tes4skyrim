// Headless store test: parses real exported DIAL/INFO text with no game.
//
// Built by `build.bat test` into store_test.exe. Takes an export directory
// (the one holding MWDI.txt and MWIN.txt) and reports what parsed, so the
// reader is checked against 23,693 real records rather than a fixture.

#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#include "store.h"

namespace mwruntime {
namespace {

int g_failures = 0;

void Check(bool ok, const char* what) {
    if (!ok) {
        std::printf("  FAIL %s\n", what);
        ++g_failures;
    }
}

std::string ReadFile(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return "";
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

void TestUnescape() {
    std::printf("unescape\n");
    Check(Unescape("a\\nb") == "a\nb", "\\n");
    Check(Unescape("a\\r\\nb") == "a\r\nb", "\\r\\n");
    Check(Unescape("a\\tb") == "a\tb", "\\t");
    Check(Unescape("a\\\\b") == "a\\b", "backslash");
    Check(Unescape("plain") == "plain", "passthrough");
    Check(Unescape("trailing\\") == "trailing\\", "lone trailing backslash");
    Check(Unescape("\\q") == "\\q", "unknown escape kept verbatim");
}

void TestParse() {
    std::printf("parse\n");
    const std::string text =
        "---RECORD_BEGIN---\nSignature=MWDI\nEditorID=t1\n---RECORD_END---\n"
        "\n"
        "---RECORD_BEGIN---\nSignature=MWIN\nEditorID=i1\n---RECORD_END---\n";
    const auto recs = ParseExport(text);
    Check(recs.size() == 2, "two records");
    Check(recs[0].at("Signature") == "MWDI", "first is MWDI");
    Check(recs[1].at("EditorID") == "i1", "second id");
}

struct Corpus {
    std::size_t topics = 0;
    std::size_t infos = 0;
    std::size_t conditions = 0;
    std::size_t scripts = 0;
    std::size_t floats = 0;
    std::size_t orphans = 0;
    std::size_t badOrdinal = 0;
    std::size_t emptyId = 0;
    std::size_t choices = 0;
    std::size_t multiline = 0;
};

// Re-parses the export the way LoadStore does, but from a caller-named
// directory so the test needs no game install.
Corpus Walk(const std::string& dir) {
    Corpus out;
    std::unordered_map<std::string, std::vector<int>> ordinals;
    std::unordered_map<std::string, bool> topics;

    for (const auto& rec : ParseExport(ReadFile(dir + "/MWDI.txt"))) {
        const auto it = rec.find("EditorID");
        if (it == rec.end()) continue;
        std::string id = Unescape(it->second);
        for (char& c : id) c = static_cast<char>(::tolower(c));
        topics[id] = true;
        ++out.topics;
    }

    for (const auto& rec : ParseExport(ReadFile(dir + "/MWIN.txt"))) {
        ++out.infos;
        const auto idIt = rec.find("EditorID");
        if (idIt == rec.end() || idIt->second.empty()) ++out.emptyId;
        const auto topicIt = rec.find("Topic");
        if (topicIt != rec.end()) {
            std::string key = Unescape(topicIt->second);
            for (char& c : key) c = static_cast<char>(::tolower(c));
            if (!topics.count(key)) ++out.orphans;
            const auto ordIt = rec.find("Ordinal");
            if (ordIt != rec.end()) {
                auto& seen = ordinals[key];
                if (std::atoi(ordIt->second.c_str()) !=
                    static_cast<int>(seen.size())) {
                    ++out.badOrdinal;
                }
                seen.push_back(0);
            }
        }
        if (rec.count("ResultScript")) ++out.scripts;
        const auto respIt = rec.find("Response");
        if (respIt != rec.end() &&
            Unescape(respIt->second).find('\n') != std::string::npos) {
            ++out.multiline;
        }
        const auto countIt = rec.find("ConditionCount");
        if (countIt == rec.end()) continue;
        const int n = std::atoi(countIt->second.c_str());
        out.conditions += static_cast<std::size_t>(n);
        for (int i = 0; i < n; ++i) {
            const std::string prefix = "Condition[" + std::to_string(i) + "].";
            const auto typeIt = rec.find(prefix + "ValueType");
            if (typeIt != rec.end() && typeIt->second == "Float") ++out.floats;
            const auto fnIt = rec.find(prefix + "FunctionIndex");
            if (fnIt != rec.end() && std::atoi(fnIt->second.c_str()) == 50) {
                ++out.choices;
            }
        }
    }
    return out;
}

}  // namespace
}  // namespace mwruntime

int main(int argc, char** argv) {
    using namespace mwruntime;
    TestUnescape();
    TestParse();

    if (argc > 2 && std::strcmp(argv[1], "--sidecar") == 0) {
        // The deployed layout: <root>/<plugin>/{DIAL,INFO}.txt, walked the way
        // LoadStore does rather than from a caller-named directory.
        std::printf("sidecar root %s\n", argv[2]);
        const StoreStats s = LoadStoreFrom(argv[2]);
        std::printf("  plugins   %zu\n", s.files);
        std::printf("  topics    %zu\n", s.topics);
        std::printf("  responses %zu\n", s.infos);
        std::printf("  scripts   %zu\n", s.scripts);
        Check(s.files > 0, "at least one plugin folder found");
        Check(s.topics > 0, "topics loaded");
        Check(s.infos > 0, "responses loaded");
        std::printf(g_failures ? "\nFAILED (%d)\n" : "\nOK\n", g_failures);
        return g_failures ? 1 : 0;
    }

    if (argc > 1) {
        std::printf("corpus %s\n", argv[1]);
        const Corpus c = Walk(argv[1]);
        std::printf("  topics          %zu\n", c.topics);
        std::printf("  responses       %zu\n", c.infos);
        std::printf("  conditions      %zu\n", c.conditions);
        std::printf("  result scripts  %zu\n", c.scripts);
        std::printf("  float values    %zu\n", c.floats);
        std::printf("  Choice cond.    %zu\n", c.choices);
        std::printf("  multiline resp. %zu\n", c.multiline);
        Check(c.topics > 0, "topics parsed");
        Check(c.infos > 0, "responses parsed");
        Check(c.orphans == 0, "every INFO's topic exists");
        Check(c.badOrdinal == 0, "ordinals are dense and in order");
        Check(c.emptyId == 0, "every INFO has an id");
        Check(c.conditions > 0, "conditions parsed");
        Check(c.scripts > 0, "result scripts parsed");
        // A rule-slicing bug reads as zero here while every other count is
        // right, which is exactly how the first export shipped broken.
        Check(c.choices > 0, "Choice conditions parsed");
    }

    std::printf(g_failures ? "\nFAILED (%d)\n" : "\nOK\n", g_failures);
    return g_failures ? 1 : 0;
}
