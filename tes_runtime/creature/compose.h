// Fragment loading and singlefile composition.
//
// Mirrors asset_convert/havok/animation_data.py compose_animationdata /
// compose_animationsetdata line for line; the Python side is the spec and
// tests/test_creature_anim.py proves the two agree.
// Schema: docs/reference/tes_runtime_fragments.md

#pragma once

#include <string>
#include <vector>

#include "json.h"

namespace tesruntime {

using Lines = std::vector<std::string>;

// Every *.json under `dir`, sorted by filename (case-insensitive), then one
// fragment per subfolder holding full singlefile copies, sorted by folder.
// Files that fail to parse or carry an unknown version are logged and skipped.
std::vector<Json> LoadFragments(const std::string& dir);

// Splits on '\n', dropping a trailing '\r' per line and a final empty line.
Lines SplitLines(const std::string& text);

// Joins with CRLF and a trailing CRLF (the vanilla files' own convention).
std::string JoinLines(const Lines& lines);

Lines ComposeAnimationData(const Lines& base, const std::vector<Json>& fragments);
Lines ComposeAnimationSetData(const Lines& base, const std::vector<Json>& fragments);

}  // namespace tesruntime
