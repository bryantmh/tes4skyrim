"""Per-cell navmesh index: open ONE cell without materializing the plugin.

Two problems with the monolithic `audit_index3.pkl`.  It holds the whole plugin
in one pickle -- 1.1 GB for TR_Mainland, 1.6M REFR dicts -- so a tool that wants
one cell pays to rebuild all of them.  And a child plugin merged its masters'
records into its OWN index, storing Morrowind_ob's 37,742 LAND records a second
time (measured: 379 MB -> 2400 MB, 1767 MB of it LAND).

So each plugin indexes only what it OWNS, one row per cell, and a lookup walks
the master chain (TR_Mainland -> Morrowind_ob -> Oblivion) until a plugin
answers.  The child's own record wins; nothing is duplicated.

See: docs/commentary/tes5_import_navmesh.md#cellview-cell-index
"""

import os
import pickle
import sqlite3
import threading

from core.plugin_masters import master_dir, masters_from_export_header

#: Tables small enough to load whole; the rest is per cell.
SHARED = ('base_model', 'door_fids', 'cells')

#: Schema marker; a mismatch rebuilds rather than serving stale shapes.
SCHEMA = 5

#: Version of the stored `base_model` table; a mismatch rewrites that row alone.
BASES = 3


def db_path(export):
    """Where `export`'s per-cell index lives."""
    return os.path.join(export, 'cell_index.sqlite')


def _connect(path):
    """A read-tuned connection to the index at `path`."""
    con = sqlite3.connect(path)
    con.execute('PRAGMA journal_mode=OFF')
    con.execute('PRAGMA synchronous=OFF')
    return con


def write(export, tables):
    """Store one plugin's OWN tables, one row per cell; returns the path.

    Written to a temp name and renamed, so an interrupted build never leaves
    a half-written index that later reads as complete.
    """
    base_model, refr, pgrd, land, door_fids, cells = tables
    path = db_path(export)
    tmp = path + '.tmp'
    for stale in (tmp, tmp + '-journal'):
        if os.path.exists(stale):
            os.remove(stale)
    con = _connect(tmp)
    con.execute('CREATE TABLE meta (k TEXT PRIMARY KEY, v INTEGER)')
    con.execute('CREATE TABLE cell (fid TEXT PRIMARY KEY, blob BLOB, '
                'has_pgrd INTEGER)')
    con.execute('CREATE TABLE shared (k TEXT PRIMARY KEY, blob BLOB)')
    con.executemany('INSERT INTO meta VALUES (?, ?)',
                    (('schema', SCHEMA), ('bases', BASES)))
    fids = set(refr) | set(pgrd) | set(land)
    con.executemany('INSERT INTO cell VALUES (?, ?, ?)', (
        (f, pickle.dumps((refr.get(f, []), pgrd.get(f), land.get(f)),
                         pickle.HIGHEST_PROTOCOL),
         1 if pgrd.get(f) is not None else 0) for f in fids))
    con.executemany('INSERT INTO shared VALUES (?, ?)', (
        (k, pickle.dumps(v, pickle.HIGHEST_PROTOCOL))
        for k, v in zip(SHARED, (base_model, door_fids, cells))))
    con.commit()
    con.close()
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)
    return path


def _meta(export, key):
    """One `meta` value of `export`'s index, or None when absent or unreadable."""
    path = db_path(export)
    if not os.path.isfile(path):
        return None
    try:
        con = _connect(path)
        row = con.execute('SELECT v FROM meta WHERE k=?', (key,)).fetchone()
        con.close()
    except sqlite3.DatabaseError:
        return None
    return row[0] if row else None


def is_current(export):
    """True when a per-cell index exists at the current schema."""
    return _meta(export, 'schema') == SCHEMA


def bases_current(export):
    """True when the stored `base_model` table is at the current `BASES` version."""
    return _meta(export, 'bases') == BASES


def write_base_model(export, base_model):
    """Replace only the stored `base_model` table, leaving every cell row as it is.

    See: docs/commentary/tes5_import_navmesh.md#frozen-corner-takes-the-vertex
    """
    con = _connect(db_path(export))
    con.execute('UPDATE shared SET blob=? WHERE k=?',
                (pickle.dumps(base_model, pickle.HIGHEST_PROTOCOL), 'base_model'))
    con.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', ('bases', BASES))
    con.commit()
    con.close()


class _Store(object):
    """One plugin's own index file.

    Holds ONE CONNECTION PER THREAD: sqlite3 forbids using a connection from
    the thread that did not create it, and the server is threaded so a cached
    index outlives the request that opened it.

    See: docs/commentary/tes5_import_navmesh.md#cellview-cell-index
    """

    def __init__(self, export):
        """Open `export`'s index and read its shared tables."""
        self.export = export
        self._local = threading.local()
        got = {k: pickle.loads(b)
               for k, b in self._con().execute('SELECT k, blob FROM shared')}
        self.base_model = got['base_model']
        self.door_fids = got['door_fids']
        self.cells = got['cells']

    def _con(self):
        """This thread's connection, opened on first use here."""
        con = getattr(self._local, 'con', None)
        if con is None:
            con = _connect(db_path(self.export))
            self._local.con = con
        return con

    def of_cell(self, fid):
        """`(refrs, pgrd, land)` for one cell, or None when not ours."""
        row = self._con().execute('SELECT blob FROM cell WHERE fid=?',
                                  (fid,)).fetchone()
        return pickle.loads(row[0]) if row else None

    def iter_cells(self):
        """Yield `(fid, refrs, pgrd, land)` for every cell we own."""
        for fid, blob in self._con().execute('SELECT fid, blob FROM cell'):
            refrs, pgrd, land = pickle.loads(blob)
            yield fid, refrs, pgrd, land

    def pathgrid_fids(self):
        """Our cell FormIDs that have a pathgrid, from the stored flag."""
        return {fid for (fid,) in
                self._con().execute('SELECT fid FROM cell WHERE has_pgrd=1')}

    def close(self):
        """Release THIS thread's connection; others close with their thread."""
        con = getattr(self._local, 'con', None)
        if con is not None:
            con.close()
            self._local.con = None


def _dir_key(path):
    """A directory path normalized for comparison."""
    return os.path.normcase(os.path.normpath(path))


def index_map(child, master):
    """`{index byte in master's export: index byte in child's}`.

    Each plugin numbers FormIDs by its OWN master list, so one record is
    010C084C in Morrowind_ob.esm and 000C084C in TR_Mainland.esm.  A byte the
    child cannot address (a master it does not declare) is absent.

    See: docs/commentary/tes5_import_navmesh.md#cellview-master-numbering
    """
    slots = {_dir_key(master_dir(child, n)): i
             for i, n in enumerate(masters_from_export_header(child))}
    names = masters_from_export_header(master)
    out = {}
    for i, d in enumerate([master_dir(child, n) for n in names] + [master]):
        if _dir_key(d) in slots:
            out[i] = slots[_dir_key(d)]
    return out


def shift_fid(fid, imap):
    """`fid` moved into the child's numbering by `index_map`, or None."""
    got = imap.get(fid >> 24)
    return None if got is None else (got << 24) | (fid & 0xFFFFFF)


def _rebased_refr(refr, imap):
    """A copy of `refr` whose base NAME uses the child's numbering ('' if unaddressable)."""
    try:
        new = shift_fid(int(refr.get('NAME') or '', 16), imap)
    except ValueError:
        return refr
    return dict(refr, NAME='%08X' % new if new is not None else '')


def _rebased_table(table, imap, out):
    """Merge a master's FormID-keyed table into `out`, keys rebased; `out` wins."""
    for k, v in table.items():
        new = shift_fid(k, imap)
        if new is not None:
            out.setdefault(new, v)


class CellIndex(object):
    """One plugin's index plus its masters', queried as a chain.

    The plugin's own answer wins; a miss falls through to each master in load
    order.  Masters are opened lazily, so a cell the plugin owns outright
    never touches them.

    See: docs/commentary/tes5_import_navmesh.md#cellview-cell-index
    """

    def __init__(self, export, master_dirs=()):
        """Open `export`, recording (not yet opening) its masters."""
        self._own = _Store(export)
        self._master_dirs = [d for d in master_dirs
                             if os.path.isdir(d) and is_current(d)]
        self._masters = None
        self._maps = [index_map(export, d) for d in self._master_dirs]
        self._shared = None

    def _open_masters(self):
        """`(store, index_map)` per master, opened on first need, nearest first."""
        if self._masters is None:
            self._masters = [_Store(d) for d in self._master_dirs]
        return list(zip(self._masters, self._maps))

    def _merged(self):
        """Base models, door bases and CELL records across the chain.

        Built on FIRST USE, not at open: merging 61,181 CELL records across
        four masters measured 23s, and a caller that only wants one cell's
        geometry never needs it.  Base and door ids are rebased into THIS
        plugin's numbering, the numbering its REFRs name them by.

        See: docs/commentary/tes5_import_navmesh.md#cellview-master-numbering
        """
        if self._shared is None:
            base = dict(self._own.base_model)
            doors = dict(self._own.door_fids)
            cells = list(self._own.cells)
            seen = {(c.get('FormID') or '').upper() for c in cells}
            for store, imap in self._open_masters():
                _rebased_table(store.base_model, imap, base)
                _rebased_table(store.door_fids, imap, doors)
                for rec in store.cells:
                    fid = (rec.get('FormID') or '').upper()
                    if fid not in seen:
                        seen.add(fid)
                        cells.append(rec)
            self._shared = (base, doors, cells)
        return self._shared

    @property
    def base_model(self):
        """Base FormID -> model key, this plugin's own winning."""
        return self._merged()[0]

    @property
    def door_fids(self):
        """DOOR base FormIDs across the chain."""
        return self._merged()[1]

    @property
    def cells(self):
        """Every CELL record in the chain, this plugin's own winning."""
        return self._merged()[2]

    def of_cell(self, fid):
        """`(refrs, pgrd, land)` for one cell, ours or a master's (bases rebased)."""
        got = self._own.of_cell(fid)
        if got is not None:
            return got
        for store, imap in self._open_masters():
            got = store.of_cell(fid)
            if got is not None:
                refrs, pgrd, land = got
                return [_rebased_refr(r, imap) for r in refrs], pgrd, land
        return [], None, None

    def iter_cells(self):
        """Yield `(fid, refrs, pgrd, land)` across the whole chain, ours first."""
        seen = set()
        for fid, refrs, pgrd, land in self._own.iter_cells():
            seen.add(fid)
            yield fid, refrs, pgrd, land
        for store, imap in self._open_masters():
            for fid, refrs, pgrd, land in store.iter_cells():
                if fid not in seen:
                    seen.add(fid)
                    yield (fid, [_rebased_refr(r, imap) for r in refrs],
                           pgrd, land)

    def pathgrid_fids(self):
        """Cell FormIDs that have a pathgrid, across the chain.

        Answered from an indexed column, not by unpickling every cell: the
        scan measured 10.8s on a four-master chain.
        """
        out = set(self._own.pathgrid_fids())
        for store, _imap in self._open_masters():
            out |= store.pathgrid_fids()
        return out

    def tables(self):
        """Every table, as the monolithic pickle held them.

        See: docs/commentary/tes5_import_navmesh.md#cellview-cell-index
        """
        refr, pgrd, land = {}, {}, {}
        for fid, r, p, ld in self.iter_cells():
            if r:
                refr[fid] = r
            if p is not None:
                pgrd[fid] = p
            if ld is not None:
                land[fid] = ld
        return (self.base_model, refr, pgrd, land, self.door_fids, self.cells)

    def close(self):
        """Release every connection in the chain."""
        self._own.close()
        for store in self._masters or ():
            store.close()
