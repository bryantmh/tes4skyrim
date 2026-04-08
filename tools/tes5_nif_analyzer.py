#!/usr/bin/env python3
"""Dump Skyrim (TES5) NIF files to a human-readable text representation.

Usage:
    python tools/tes5_nif_analyzer.py <nif_or_dir> [--outdir references/skyrim_meshes] [--max N]

Identical format to tes4_nif_analyzer.py so diffs between Oblivion source,
converted output, and Skyrim reference meshes are easy to compare.
"""

# The analyzer code is identical for both versions — PyFFI handles the format
# differences automatically.  We re-export from the TES4 analyzer.

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tools.tes4_nif_analyzer import analyze_nif, main

if __name__ == '__main__':
    main()
