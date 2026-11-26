# ArchiveLoader

**Lets big converted games load all of their archives.**

## What it does for you

Skyrim loads only two archives per plugin: `<plugin>.bsa` and `<plugin> - Textures.bsa`. A large converted
game can ship more, named `<plugin> - Part 2.bsa`, `<plugin> - Part 3.bsa` and so on. ArchiveLoader loads them
right after the plugin's own two, so they take the same place in your load order, and its log
(`Documents\My Games\Skyrim Special Edition\SKSE\ArchiveLoader.log`) lists each one it loaded.

It works on Skyrim SE 1.5.97, every Anniversary Edition build, and Skyrim VR.

## Should I keep it enabled?

Yes, whenever you play a converted game that ships Part archives. Without it, those archives never load and
the game is missing meshes, textures and sounds. It changes no records and nothing in your save.
