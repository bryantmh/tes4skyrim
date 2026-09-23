#!/usr/bin/env python3
"""Search / extract pages from the UESP wiki XML dump in references/UESP.

The dump is ~1.1 GB and 463k pages, so this streams it with iterparse and never
holds more than one page in memory.

Usage:
    # list page titles matching a regex (case-insensitive)
    python tools/misc/uesp_lookup.py --find "Dialogue.*Hello|Greeting"

    # print one page's wikitext
    python tools/misc/uesp_lookup.py --page "Tes5Mod:Dialogue"

    # grep page BODIES for a pattern, printing matching lines with context
    python tools/misc/uesp_lookup.py --grep "fAIGreetingTimer" --title-filter "Tes5Mod|Skyrim"
"""
import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

DUMP = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    'references', 'UESP', 'uespwiki-2026-07-12-current.xml')
NS = '{http://www.mediawiki.org/xml/export-0.11/}'


def _reconfigure_stdout():
    """The wiki text is full of arrows, dashes and accented names, and the
    Windows console defaults to cp1252 — printing any of them raised
    UnicodeEncodeError and killed the search mid-result.  Force UTF-8 and
    replace anything the terminal still cannot render."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass


def pages(path=DUMP):
    """Yield (title, text) streaming, clearing elements as we go."""
    ns = None
    for event, elem in ET.iterparse(path, events=('start', 'end')):
        if ns is None and event == 'start':
            m = re.match(r'\{.*\}', elem.tag)
            ns = m.group(0) if m else ''
        if event == 'end' and elem.tag == f'{ns}page':
            title = elem.findtext(f'{ns}title') or ''
            text = ''
            rev = elem.find(f'{ns}revision')
            if rev is not None:
                text = rev.findtext(f'{ns}text') or ''
            yield title, text
            elem.clear()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dump', default=DUMP)
    ap.add_argument('--find', help='regex over page TITLES')
    ap.add_argument('--page', help='exact page title to print')
    ap.add_argument('--grep', help='regex over page BODIES')
    ap.add_argument('--title-filter', help='only grep pages whose title matches')
    ap.add_argument('--context', type=int, default=2)
    ap.add_argument('--limit', type=int, default=40)
    args = ap.parse_args()
    _reconfigure_stdout()

    if not os.path.exists(args.dump):
        sys.exit(f'dump not found: {args.dump}')

    find = re.compile(args.find, re.I) if args.find else None
    grep = re.compile(args.grep, re.I) if args.grep else None
    tfilt = re.compile(args.title_filter, re.I) if args.title_filter else None

    n = 0
    for title, text in pages(args.dump):
        if args.page:
            if title.lower() == args.page.lower():
                print(f'=== {title} ===')
                print(text)
                return
            continue
        if find and find.search(title):
            print(title)
            n += 1
        elif grep:
            if tfilt and not tfilt.search(title):
                continue
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if grep.search(line):
                    lo = max(0, i - args.context)
                    hi = min(len(lines), i + args.context + 1)
                    print(f'--- {title}:{i+1} ---')
                    for l in lines[lo:hi]:
                        print('   ', l[:200])
                    n += 1
                    break
        if n >= args.limit:
            break
    if args.page:
        sys.exit(f'page not found: {args.page}')


if __name__ == '__main__':
    main()
