// Offline harness: compose two singlefiles from a base dir and a fragment
// dir, writing the result to an output dir, so the C++ composer can be
// diffed against the Python reference without launching the game.
//
//   compose_test.exe <base_dir> <fragment_dir> <out_dir>

#include <fstream>
#include <sstream>
#include <string>

#include "compose.h"

using namespace tesruntime;

namespace {

std::string ReadFile(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

void WriteFile(const std::string& path, const std::string& text) {
    std::ofstream f(path, std::ios::binary);
    f << text;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 4) return 2;
    const std::string base = argv[1], frags = argv[2], out = argv[3];
    const auto fragments = LoadFragments(frags);
    const Lines ad = SplitLines(ReadFile(base + "\\animationdatasinglefile.txt"));
    const Lines asd = SplitLines(ReadFile(base + "\\animationsetdatasinglefile.txt"));
    WriteFile(out + "\\animationdatasinglefile.txt",
              JoinLines(ComposeAnimationData(ad, fragments)));
    WriteFile(out + "\\animationsetdatasinglefile.txt",
              JoinLines(ComposeAnimationSetData(asd, fragments)));
    return 0;
}
