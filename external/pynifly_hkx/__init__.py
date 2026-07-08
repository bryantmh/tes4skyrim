"""Vendored from PyNifly 27.4.0 (https://github.com/BadDogSkyrim/PyNifly).

anim_fo4.py / anim_skyrim.py: pure-Python Havok hk_2010/hk_2014 packfile
readers/writers (hkaSplineCompressedAnimation codec). GPL-3.0, see LICENSE.
Local modifications are marked with `# TESConversion:` comments — notably
allocation alignment fixes in the LE packfile writer (unaligned string/array
allocations crashed real Havok deserializers; vanilla files align to 8/16).
"""
