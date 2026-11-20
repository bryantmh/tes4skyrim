// Scaleform helpers shared by the runtime's movie features: values read, written and released through the
// engine's own GFxValue interface, native functions put into a movie, and every open menu's movie view.
// Everything here touches a movie, so it runs at UI time (PostToUiTask) only.

#pragma once

#include "skse_abi.h"

namespace tesruntime {

// Resolves SetMember, ReleaseManaged, IsMenuOpen and the menu manager. False when SetMember is missing.
bool ResolveMovieCalls();

// Releases a value the movie handed out and empties it.
void ReleaseValue(GFxValue* value);

// `path` in the movie into `out`, which the caller releases. False when the movie has nothing there.
bool GetMovieVariable(void* view, const char* path, GFxValue* out);

void SetMovieVariable(void* view, const char* path, GFxValue* value);

// Whether `path` in the movie holds anything other than undefined.
bool MovieDefined(void* view, const char* path);

bool SetMovieMember(GFxValue* object, const char* name, GFxValue* value);

// Puts `handler` on `object` as the method `name`.
bool AddMovieFunction(void* view, GFxValue* object, const char* name, GFxFunctionHandler* handler);

// Calls the movie's method at `path` with no arguments.
bool InvokeMovie(void* view, const char* path);

// Whether the menu registered as `name` is open; callable off the UI thread.
bool MenuIsOpen(const char* name);

// Every open menu's movie view.
void ForEachOpenMovie(void (*visit)(void* view));

}  // namespace tesruntime
