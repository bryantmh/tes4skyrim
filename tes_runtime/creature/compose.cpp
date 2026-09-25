#include "compose.h"

#include <windows.h>

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <fstream>
#include <map>
#include <sstream>

#include "log.h"

namespace tesruntime {

namespace {

std::string Lower(std::string s) {
    for (auto& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

int ToInt(const std::string& s) { return std::atoi(s.c_str()); }

Lines JsonLines(const Json& arr) {
    Lines out;
    for (const auto& v : arr.items()) out.push_back(v.asString());
    return out;
}

// (names, body) of a singlefile: the leading count + name list, rest.
void SplitRegistry(const Lines& base, Lines& names, Lines& body) {
    const int n = base.empty() ? 0 : ToInt(base[0]);
    names.assign(base.begin() + 1, base.begin() + 1 + n);
    body.assign(base.begin() + 1 + n, base.end());
}

void AppendWrapped(Lines& out, const Lines& block) {
    out.push_back(std::to_string(block.size()));
    out.insert(out.end(), block.begin(), block.end());
}

struct Span { size_t start, mid, end; };

// {project lower: span} over the animationdata body (clip wrapper + motion
// wrapper when the has-clip-data flag is set).
std::map<std::string, Span> ProjectSpans(const Lines& names, const Lines& body) {
    std::map<std::string, Span> spans;
    size_t pos = 0;
    for (const auto& name : names) {
        if (pos >= body.size()) break;
        const size_t start = pos;
        const int count = ToInt(body[pos]);
        size_t flagAt = pos + 2;
        if (pos + 1 < body.size() && body[pos + 1] == "1" && pos + 2 < body.size())
            flagAt += 1 + static_cast<size_t>(ToInt(body[pos + 2]));
        const bool hasCache = flagAt < pos + 1 + count && flagAt < body.size()
                              && body[flagAt] == "1";
        pos += 1 + count;
        const size_t mid = pos;
        if (hasCache && pos < body.size()) pos += 1 + static_cast<size_t>(ToInt(body[pos]));
        spans[Lower(name)] = Span{start, mid, pos};
    }
    return spans;
}

void AppendAnimData(const Lines& names, Lines& body, const std::vector<const Json*>& appends) {
    auto spans = ProjectSpans(names, body);
    // Splice from the back so earlier spans stay valid.
    std::vector<const Json*> order(appends);
    std::stable_sort(order.begin(), order.end(), [&](const Json* a, const Json* b) {
        auto sa = spans.find(Lower((*a)["project"].asString()));
        auto sb = spans.find(Lower((*b)["project"].asString()));
        const size_t pa = sa == spans.end() ? 0 : sa->second.start;
        const size_t pb = sb == spans.end() ? 0 : sb->second.start;
        return pa > pb;
    });
    for (const Json* e : order) {
        auto it = spans.find(Lower((*e)["project"].asString()));
        if (it == spans.end()) continue;
        const Span sp = it->second;
        const int clipCount = ToInt(body[sp.start]);
        const size_t clipEnd = sp.start + 1 + clipCount;
        const Lines motion = JsonLines((*e)["motions"]);
        const Lines clips = JsonLines((*e)["clips"]);
        if (clipEnd < sp.end && !motion.empty()) {
            body[clipEnd] = std::to_string(ToInt(body[clipEnd]) + static_cast<int>(motion.size()));
            body.insert(body.begin() + sp.end, motion.begin(), motion.end());
        }
        body.insert(body.begin() + clipEnd, clips.begin(), clips.end());
        body[sp.start] = std::to_string(clipCount + static_cast<int>(clips.size()));
    }
}

// body[pos] as a count; 0 past the end, so a malformed copy cannot read out of range.
int CountAt(const Lines& body, size_t pos) { return pos < body.size() ? ToInt(body[pos]) : 0; }

size_t V3BlockEnd(const Lines& body, size_t pos) {
    pos += 1;                                                    // 'V3'
    pos += 1 + static_cast<size_t>(CountAt(body, pos));          // swap events
    pos += 1 + 3 * static_cast<size_t>(CountAt(body, pos));      // hand variables
    const int nAttacks = CountAt(body, pos);
    pos += 1;
    for (int i = 0; i < nAttacks && pos < body.size(); ++i) {
        pos += 2;                                                // event, mirrored
        pos += 1 + static_cast<size_t>(CountAt(body, pos));      // clip names
    }
    pos += 1 + 3 * static_cast<size_t>(CountAt(body, pos));      // crc triples
    return pos;
}

std::map<std::string, Span> SetDataSpans(const Lines& names, const Lines& body) {
    std::map<std::string, Span> spans;
    size_t pos = 0;
    for (const auto& name : names) {
        if (pos >= body.size()) break;
        const size_t start = pos;
        const int nSets = ToInt(body[pos]);
        pos += 1 + nSets;
        const size_t namesEnd = pos;
        for (int i = 0; i < nSets && pos < body.size(); ++i) pos = V3BlockEnd(body, pos);
        spans[Lower(name)] = Span{start, namesEnd, pos};
    }
    return spans;
}

void AppendAnimSetData(const Lines& names, Lines& body, const std::vector<const Json*>& appends) {
    auto spans = SetDataSpans(names, body);
    std::vector<const Json*> order(appends);
    std::stable_sort(order.begin(), order.end(), [&](const Json* a, const Json* b) {
        auto sa = spans.find(Lower((*a)["entry"].asString()));
        auto sb = spans.find(Lower((*b)["entry"].asString()));
        const size_t pa = sa == spans.end() ? 0 : sa->second.start;
        const size_t pb = sb == spans.end() ? 0 : sb->second.start;
        return pa > pb;
    });
    for (const Json* e : order) {
        auto it = spans.find(Lower((*e)["entry"].asString()));
        if (it == spans.end()) continue;
        const Span sp = it->second;
        const Lines block = JsonLines((*e)["block"]);
        body.insert(body.begin() + sp.end, block.begin(), block.end());
        body.insert(body.begin() + sp.mid, (*e)["set_file"].asString());
        body[sp.start] = std::to_string(ToInt(body[sp.start]) + 1);
    }
}

// Names matching `pattern` under `dir` (directories or files), sorted case-insensitively.
std::vector<std::string> ListDir(const std::string& dir, const char* pattern, bool dirs) {
    std::vector<std::string> names;
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA((dir + "\\" + pattern).c_str(), &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            const bool isDir = (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
            const std::string name = fd.cFileName;
            if (isDir == dirs && name != "." && name != "..") names.push_back(name);
        } while (FindNextFileA(h, &fd));
        FindClose(h);
    }
    std::sort(names.begin(), names.end(),
              [](const std::string& a, const std::string& b) { return Lower(a) < Lower(b); });
    return names;
}

bool ReadFile(const std::string& path, std::string& text) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return false;
    std::stringstream ss;
    ss << f.rdbuf();
    text = ss.str();
    return true;
}

Json LineArray(const Lines& lines, size_t from, size_t to) {
    Json arr = Json::Array();
    for (size_t i = from; i < to; ++i) arr.push(lines[i]);
    return arr;
}

bool ValidRegistry(const Lines& file) {
    const int n = file.empty() ? -1 : ToInt(file[0]);
    return n >= 0 && static_cast<size_t>(n) < file.size();
}

// `name`'s span, if it is ordered and inside the body.
const Span* Checked(const std::map<std::string, Span>& spans, const std::string& name, size_t size) {
    auto it = spans.find(Lower(name));
    if (it == spans.end()) return nullptr;
    const Span& s = it->second;
    return s.start < s.mid && s.mid <= s.end && s.end <= size ? &s : nullptr;
}

bool CopyAnimData(const Lines& file, Json& out) {
    if (!ValidRegistry(file)) return false;
    Lines names, body;
    SplitRegistry(file, names, body);
    const auto spans = ProjectSpans(names, body);
    for (const auto& name : names) {
        const Span* s = Checked(spans, name, body.size());
        if (!s) return false;
        Json e = Json::Object();
        e.set("project", name);
        e.set("clip_block", LineArray(body, s->start + 1, s->mid));
        if (s->end > s->mid) e.set("motion_block", LineArray(body, s->mid + 1, s->end));
        out.push(std::move(e));
    }
    return true;
}

bool CopyAnimSetData(const Lines& file, Json& out) {
    if (!ValidRegistry(file)) return false;
    Lines names, body;
    SplitRegistry(file, names, body);
    const auto spans = SetDataSpans(names, body);
    for (const auto& name : names) {
        const Span* s = Checked(spans, name, body.size());
        if (!s) return false;
        Json e = Json::Object();
        e.set("entry", name);
        e.set("block", LineArray(body, s->start, s->end));
        out.push(std::move(e));
    }
    return true;
}

// One folder of a mod's full singlefile copies as a fragment; null when it holds
// neither file or either is malformed.
// See: docs/reference/tes_runtime_fragments.md#singlefile-copies
Json LoadSinglefileCopy(const std::string& dir, const std::string& name) {
    Json animdata = Json::Array(), animsetdata = Json::Array();
    std::string text;
    bool any = false, ok = true;
    if (ReadFile(dir + "\\" + name + "\\animationdatasinglefile.txt", text)) {
        any = true;
        ok = CopyAnimData(SplitLines(text), animdata);
    }
    if (ok && ReadFile(dir + "\\" + name + "\\animationsetdatasinglefile.txt", text)) {
        any = true;
        ok = CopyAnimSetData(SplitLines(text), animsetdata);
    }
    if (!any) return Json();
    if (!ok) {
        Log("copy %s: malformed singlefile, skipped", name.c_str());
        return Json();
    }
    Log("copy %s: %zu animdata, %zu animsetdata projects", name.c_str(),
        animdata.size(), animsetdata.size());
    Json frag = Json::Object();
    frag.set("version", 1);
    frag.set("source", name);
    frag.set("animdata", std::move(animdata));
    frag.set("animsetdata", std::move(animsetdata));
    return frag;
}

}  // namespace

std::vector<Json> LoadFragments(const std::string& dir) {
    std::vector<Json> out;
    for (const auto& fn : ListDir(dir, "*.json", false)) {
        std::string text;
        ReadFile(dir + "\\" + fn, text);
        std::string err;
        Json j = Json::Parse(text, &err);
        if (!j.isObject()) {
            Log("fragment %s: unreadable (%s)", fn.c_str(), err.c_str());
            continue;
        }
        if (j["version"].asInt() != 1) {
            Log("fragment %s: unsupported version %d", fn.c_str(), j["version"].asInt());
            continue;
        }
        Log("fragment %s: %zu animdata, %zu animsetdata, %zu/%zu appends (source %s)",
            fn.c_str(), j["animdata"].size(), j["animsetdata"].size(),
            j["animdata_append"].size(), j["animsetdata_append"].size(),
            j["source"].asString().c_str());
        out.push_back(std::move(j));
    }
    for (const auto& sub : ListDir(dir, "*", true)) {
        Json copy = LoadSinglefileCopy(dir, sub);
        if (copy.isObject()) out.push_back(std::move(copy));
    }
    return out;
}

Lines SplitLines(const std::string& text) {
    Lines out;
    size_t start = 0;
    while (start <= text.size()) {
        size_t nl = text.find('\n', start);
        if (nl == std::string::npos) {
            if (start < text.size()) out.push_back(text.substr(start));
            break;
        }
        size_t end = nl;
        if (end > start && text[end - 1] == '\r') --end;
        out.push_back(text.substr(start, end - start));
        start = nl + 1;
    }
    return out;
}

std::string JoinLines(const Lines& lines) {
    std::string out;
    size_t total = 0;
    for (const auto& l : lines) total += l.size() + 2;
    out.reserve(total);
    for (const auto& l : lines) { out += l; out += "\r\n"; }
    return out;
}

Lines ComposeAnimationData(const Lines& base, const std::vector<Json>& fragments) {
    Lines names, body;
    SplitRegistry(base, names, body);
    std::map<std::string, bool> have;
    for (const auto& n : names) have[Lower(n)] = true;
    Lines newNames, newBody;
    std::vector<const Json*> appends;
    for (const auto& frag : fragments) {
        for (const auto& e : frag["animdata"].items()) {
            const std::string key = Lower(e["project"].asString());
            if (have.count(key)) continue;
            have[key] = true;
            newNames.push_back(e["project"].asString());
            AppendWrapped(newBody, JsonLines(e["clip_block"]));
            if (!e["motion_block"].isNull()) AppendWrapped(newBody, JsonLines(e["motion_block"]));
        }
        for (const auto& e : frag["animdata_append"].items()) appends.push_back(&e);
    }
    if (!appends.empty()) AppendAnimData(names, body, appends);
    Lines out;
    out.push_back(std::to_string(names.size() + newNames.size()));
    out.insert(out.end(), names.begin(), names.end());
    out.insert(out.end(), newNames.begin(), newNames.end());
    out.insert(out.end(), body.begin(), body.end());
    out.insert(out.end(), newBody.begin(), newBody.end());
    return out;
}

Lines ComposeAnimationSetData(const Lines& base, const std::vector<Json>& fragments) {
    Lines names, body;
    SplitRegistry(base, names, body);
    std::map<std::string, bool> have;
    for (const auto& n : names) have[Lower(n)] = true;
    Lines newNames, newBody;
    std::vector<const Json*> appends;
    for (const auto& frag : fragments) {
        for (const auto& e : frag["animsetdata"].items()) {
            const std::string key = Lower(e["entry"].asString());
            if (have.count(key)) continue;
            have[key] = true;
            newNames.push_back(e["entry"].asString());
            const Lines block = JsonLines(e["block"]);
            newBody.insert(newBody.end(), block.begin(), block.end());
        }
        for (const auto& e : frag["animsetdata_append"].items()) appends.push_back(&e);
    }
    if (!appends.empty()) AppendAnimSetData(names, body, appends);
    Lines out;
    out.push_back(std::to_string(names.size() + newNames.size()));
    out.insert(out.end(), names.begin(), names.end());
    out.insert(out.end(), newNames.begin(), newNames.end());
    out.insert(out.end(), body.begin(), body.end());
    out.insert(out.end(), newBody.begin(), newBody.end());
    return out;
}

}  // namespace tesruntime
