// Tiny JSON value + parser/serializer.
//
// Self-contained on purpose: the plugin must build with nothing but MSVC and
// the Windows SDK, so a vendored third-party JSON library would be one more
// thing to keep in sync for no benefit. The protocol is small and machine-
// generated on both ends, so this only needs to be correct, not fast.

#pragma once

#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace tesruntime {

class Json {
public:
    enum class Type { Null, Bool, Number, String, Array, Object };

    Json() : type_(Type::Null) {}
    Json(bool b) : type_(Type::Bool), bool_(b) {}
    Json(double d) : type_(Type::Number), num_(d) {}
    Json(int i) : type_(Type::Number), num_(static_cast<double>(i)) {}
    Json(std::uint32_t i) : type_(Type::Number), num_(static_cast<double>(i)) {}
    Json(std::uint64_t i) : type_(Type::Number), num_(static_cast<double>(i)) {}
    Json(const char* s) : type_(Type::String), str_(s ? s : "") {}
    Json(std::string s) : type_(Type::String), str_(std::move(s)) {}

    static Json Array() { Json j; j.type_ = Type::Array; return j; }
    static Json Object() { Json j; j.type_ = Type::Object; return j; }

    Type type() const { return type_; }
    bool isNull() const { return type_ == Type::Null; }
    bool isObject() const { return type_ == Type::Object; }
    bool isArray() const { return type_ == Type::Array; }
    bool isString() const { return type_ == Type::String; }
    bool isNumber() const { return type_ == Type::Number; }
    bool isBool() const { return type_ == Type::Bool; }

    bool        asBool(bool d = false) const { return type_ == Type::Bool ? bool_ : d; }
    double      asNumber(double d = 0) const { return type_ == Type::Number ? num_ : d; }
    int         asInt(int d = 0) const { return type_ == Type::Number ? static_cast<int>(num_) : d; }
    std::uint32_t asU32(std::uint32_t d = 0) const {
        return type_ == Type::Number ? static_cast<std::uint32_t>(num_) : d;
    }
    const std::string& asString(const std::string& d = kEmpty) const {
        return type_ == Type::String ? str_ : d;
    }

    // Object access. Missing key -> null Json (never throws).
    const Json& operator[](const std::string& key) const {
        static const Json null;
        auto it = obj_.find(key);
        return it == obj_.end() ? null : it->second;
    }
    bool has(const std::string& key) const { return obj_.count(key) != 0; }
    void set(const std::string& key, Json v) { type_ = Type::Object; obj_[key] = std::move(v); }

    // Array access.
    void push(Json v) { type_ = Type::Array; arr_.push_back(std::move(v)); }
    size_t size() const { return type_ == Type::Array ? arr_.size() : obj_.size(); }
    const Json& at(size_t i) const {
        static const Json null;
        return i < arr_.size() ? arr_[i] : null;
    }
    const std::vector<Json>& items() const { return arr_; }
    const std::map<std::string, Json>& fields() const { return obj_; }

    std::string dump() const;
    static Json Parse(const std::string& text, std::string* err = nullptr);

private:
    static const std::string kEmpty;

    Type        type_;
    bool        bool_ = false;
    double      num_ = 0;
    std::string str_;
    std::vector<Json> arr_;
    std::map<std::string, Json> obj_;
};

}  // namespace tesruntime
