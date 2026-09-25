#include "json.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace tesruntime {

const std::string Json::kEmpty;

// ------------------------------------------------------------- serialize ----

namespace {

void EscapeTo(std::string& out, const std::string& s) {
    out.push_back('"');
    for (unsigned char c : s) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            case '\b': out += "\\b";  break;
            case '\f': out += "\\f";  break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out.push_back(static_cast<char>(c));
                }
        }
    }
    out.push_back('"');
}

void NumberTo(std::string& out, double d) {
    if (!std::isfinite(d)) { out += "null"; return; }
    // Emit integers without a trailing ".0" so form IDs and counts round-trip
    // cleanly through Python's json (which would otherwise make them floats).
    if (d == static_cast<double>(static_cast<long long>(d)) &&
        std::fabs(d) < 9.0e15) {
        char buf[32];
        std::snprintf(buf, sizeof(buf), "%lld", static_cast<long long>(d));
        out += buf;
        return;
    }
    char buf[40];
    std::snprintf(buf, sizeof(buf), "%.17g", d);
    out += buf;
}

void DumpTo(std::string& out, const Json& j) {
    switch (j.type()) {
        case Json::Type::Null:   out += "null"; break;
        case Json::Type::Bool:   out += j.asBool() ? "true" : "false"; break;
        case Json::Type::Number: NumberTo(out, j.asNumber()); break;
        case Json::Type::String: EscapeTo(out, j.asString()); break;
        case Json::Type::Array: {
            out.push_back('[');
            bool first = true;
            for (const auto& v : j.items()) {
                if (!first) out.push_back(',');
                first = false;
                DumpTo(out, v);
            }
            out.push_back(']');
            break;
        }
        case Json::Type::Object: {
            out.push_back('{');
            bool first = true;
            for (const auto& kv : j.fields()) {
                if (!first) out.push_back(',');
                first = false;
                EscapeTo(out, kv.first);
                out.push_back(':');
                DumpTo(out, kv.second);
            }
            out.push_back('}');
            break;
        }
    }
}

}  // namespace

std::string Json::dump() const {
    std::string out;
    out.reserve(256);
    DumpTo(out, *this);
    return out;
}

// ----------------------------------------------------------------- parse ----

namespace {

struct P {
    const std::string& s;
    size_t i = 0;
    std::string err;

    void ws() { while (i < s.size() && (s[i]==' '||s[i]=='\t'||s[i]=='\n'||s[i]=='\r')) ++i; }
    bool eof() const { return i >= s.size(); }
    char peek() const { return i < s.size() ? s[i] : '\0'; }

    bool lit(const char* w) {
        size_t n = std::strlen(w);
        if (s.compare(i, n, w) == 0) { i += n; return true; }
        return false;
    }

    bool str(std::string& out) {
        if (peek() != '"') { err = "expected string"; return false; }
        ++i;
        while (i < s.size()) {
            char c = s[i++];
            if (c == '"') return true;
            if (c != '\\') { out.push_back(c); continue; }
            if (i >= s.size()) break;
            char e = s[i++];
            switch (e) {
                case '"':  out.push_back('"');  break;
                case '\\': out.push_back('\\'); break;
                case '/':  out.push_back('/');  break;
                case 'n':  out.push_back('\n'); break;
                case 'r':  out.push_back('\r'); break;
                case 't':  out.push_back('\t'); break;
                case 'b':  out.push_back('\b'); break;
                case 'f':  out.push_back('\f'); break;
                case 'u': {
                    if (i + 4 > s.size()) { err = "bad \\u"; return false; }
                    unsigned cp = std::strtoul(s.substr(i, 4).c_str(), nullptr, 16);
                    i += 4;
                    // Minimal UTF-8 encode; surrogate pairs are not expected in
                    // this protocol (paths and editor IDs are ASCII).
                    if (cp < 0x80) out.push_back(static_cast<char>(cp));
                    else if (cp < 0x800) {
                        out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
                        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
                    } else {
                        out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
                        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
                        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
                    }
                    break;
                }
                default: err = "bad escape"; return false;
            }
        }
        err = "unterminated string";
        return false;
    }

    bool value(Json& out) {
        ws();
        if (eof()) { err = "unexpected end"; return false; }
        char c = peek();
        if (c == '{') {
            ++i;
            out = Json::Object();
            ws();
            if (peek() == '}') { ++i; return true; }
            for (;;) {
                ws();
                std::string k;
                if (!str(k)) return false;
                ws();
                if (peek() != ':') { err = "expected ':'"; return false; }
                ++i;
                Json v;
                if (!value(v)) return false;
                out.set(k, std::move(v));
                ws();
                if (peek() == ',') { ++i; continue; }
                if (peek() == '}') { ++i; return true; }
                err = "expected ',' or '}'";
                return false;
            }
        }
        if (c == '[') {
            ++i;
            out = Json::Array();
            ws();
            if (peek() == ']') { ++i; return true; }
            for (;;) {
                Json v;
                if (!value(v)) return false;
                out.push(std::move(v));
                ws();
                if (peek() == ',') { ++i; continue; }
                if (peek() == ']') { ++i; return true; }
                err = "expected ',' or ']'";
                return false;
            }
        }
        if (c == '"') {
            std::string v;
            if (!str(v)) return false;
            out = Json(std::move(v));
            return true;
        }
        if (lit("true"))  { out = Json(true);  return true; }
        if (lit("false")) { out = Json(false); return true; }
        if (lit("null"))  { out = Json();      return true; }

        char* end = nullptr;
        double d = std::strtod(s.c_str() + i, &end);
        if (end == s.c_str() + i) { err = "bad value"; return false; }
        i = static_cast<size_t>(end - s.c_str());
        out = Json(d);
        return true;
    }
};

}  // namespace

Json Json::Parse(const std::string& text, std::string* err) {
    P p{text};
    Json out;
    if (!p.value(out)) {
        if (err) *err = p.err.empty() ? "parse error" : p.err;
        return Json();
    }
    return out;
}

}  // namespace tesruntime
