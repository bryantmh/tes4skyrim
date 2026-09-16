// What would this NPC actually say? The per-actor gate, headless.
//
//   session_test.exe <sidecar root> <actor id> [--race R] [--class C]
//                    [--faction F] [--rank N] [--cell NAME] [--disp N]
//                    [--topic T] [--female]
//
// Prints the greeting, the offered topic list and one topic's answer, so the
// filter is checked against a REAL actor before any menu exists to show it.

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#include "filter.h"
#include "session.h"
#include "store.h"

namespace mwruntime {
namespace {

// An actor described entirely by command-line flags.
class CliActor : public ActorView {
public:
    RefId id;
    RefId race = "Dark Elf";
    RefId clazz = "Commoner";
    RefId faction;
    int   rank = -1;
    bool  female = false;
    int   disposition = 50;
    std::string cell = "Balmora";

    RefId Id() const override { return id; }
    bool  IsNpc() const override { return true; }
    RefId Race() const override { return race; }
    RefId Class() const override { return clazz; }
    bool  IsFemale() const override { return female; }
    RefId PrimaryFaction() const override { return faction; }
    int   PrimaryFactionRank() const override { return rank; }
    int   PlayerFactionRank(const RefId&) const override { return -1; }
    bool  PlayerExpelled(const RefId&) const override { return false; }
    int   FactionReaction(const RefId&, const RefId&) const override {
        return 0;
    }
    int   Disposition() const override { return disposition; }
    RefId PlayerRace() const override { return "Dark Elf"; }
    RefId PlayerClass() const override { return "Warrior"; }
    bool  PlayerIsFemale() const override { return false; }
    int   PlayerLevel() const override { return 1; }
    int   PlayerHealthPercent() const override { return 100; }
    int   PlayerCrimeLevel() const override { return 0; }
    int   PlayerSkill(int) const override { return 100; }
    int   PlayerAttribute(int) const override { return 100; }
    std::string PlayerCellName() const override { return cell; }
    int  Health() const override { return 100; }
    int  Level() const override { return 1; }
    bool Detected() const override { return true; }
    bool Alarmed() const override { return false; }
    bool Attacked() const override { return false; }
    bool TalkedToPlayer() const override { return false; }
    int  DeadCount(const RefId&) const override { return 0; }
    int  ItemCount(const RefId&) const override { return 0; }
    int  JournalIndex(const RefId&) const override { return 0; }
    float LocalVariable(const std::string&, bool* found) const override {
        *found = false;
        return 0.0f;
    }
    float GlobalVariable(const std::string&, bool* found) const override {
        *found = false;
        return 0.0f;
    }
    int Choice() const override { return -1; }
};

void PrintWrapped(const std::string& text, int indent) {
    const int width = 76 - indent;
    int column = 0;
    std::printf("%*s", indent, "");
    for (std::size_t i = 0; i < text.size(); ++i) {
        const char c = text[i];
        if (c == '\r') continue;
        if (c == '\n' || (column > width && c == ' ')) {
            std::printf("\n%*s", indent, "");
            column = 0;
            continue;
        }
        std::putchar(c);
        ++column;
    }
    std::putchar('\n');
}

}  // namespace
}  // namespace mwruntime

int main(int argc, char** argv) {
    using namespace mwruntime;
    if (argc < 3) {
        std::printf("usage: session_test <sidecar root> <actor id> "
                    "[--race R] [--class C] [--faction F] [--rank N] "
                    "[--cell NAME] [--disp N] [--topic T] [--female]\n");
        return 2;
    }

    const StoreStats stats = LoadStoreFrom(argv[1]);
    std::printf("store: %zu plugin(s), %zu topics, %zu responses\n\n",
                stats.files, stats.topics, stats.infos);
    if (!stats.topics) {
        std::printf("no dialogue loaded -- is the sidecar staged?\n");
        return 1;
    }

    CliActor actor;
    actor.id = argv[2];
    std::string wanted;
    for (int i = 3; i < argc; ++i) {
        const bool more = i + 1 < argc;
        if (!std::strcmp(argv[i], "--female")) actor.female = true;
        else if (!std::strcmp(argv[i], "--race") && more) actor.race = argv[++i];
        else if (!std::strcmp(argv[i], "--class") && more) actor.clazz = argv[++i];
        else if (!std::strcmp(argv[i], "--faction") && more) actor.faction = argv[++i];
        else if (!std::strcmp(argv[i], "--rank") && more) actor.rank = std::atoi(argv[++i]);
        else if (!std::strcmp(argv[i], "--cell") && more) actor.cell = argv[++i];
        else if (!std::strcmp(argv[i], "--disp") && more) actor.disposition = std::atoi(argv[++i]);
        else if (!std::strcmp(argv[i], "--topic") && more) wanted = argv[++i];
    }

    std::printf("actor '%s'  race=%s class=%s faction=%s rank=%d "
                "cell=%s disp=%d\n\n", actor.id.c_str(), actor.race.c_str(),
                actor.clazz.c_str(),
                actor.faction.empty() ? "(none)" : actor.faction.c_str(),
                actor.rank, actor.cell.c_str(), actor.disposition);

    const Reply hello = Greet(actor);
    if (hello.text.empty()) {
        std::printf("GREETING  (none matched)\n\n");
    } else {
        std::printf("GREETING  [%s]\n", hello.topic.c_str());
        PrintWrapped(hello.text, 2);
        if (!hello.mentioned.empty()) {
            std::printf("  mentions:");
            for (const auto& t : hello.mentioned) std::printf(" '%s'", t.c_str());
            std::putchar('\n');
        }
        std::putchar('\n');
    }

    const std::vector<TopicEntry> topics = OfferedTopics(actor, {});
    std::printf("TOPICS (%zu)\n", topics.size());
    std::size_t shown = 0;
    for (const TopicEntry& row : topics) {
        if (shown++ >= 40) {
            std::printf("  ... and %zu more\n", topics.size() - 40);
            break;
        }
        std::printf("  %s\n", row.id.c_str());
    }
    std::putchar('\n');

    if (!wanted.empty()) {
        const Reply reply = Answer(wanted, actor, -1);
        if (reply.text.empty()) {
            std::printf("TOPIC '%s'  (no response passes)\n", wanted.c_str());
        } else {
            std::printf("TOPIC '%s'\n", reply.topic.c_str());
            PrintWrapped(reply.text, 2);
            if (!reply.resultScript.empty()) {
                std::printf("  result script:\n");
                PrintWrapped(reply.resultScript, 4);
            }
        }
    }
    return 0;
}
