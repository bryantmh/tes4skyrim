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

#: Tables small enough to load whole; the rest is per cell.
SHARED = ('base_model', 'door_fids', 'cells')

#: Schema marker; a mismatch rebuilds rather than serving stale shapes.
SCHEMA = 5


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
    con.execute('INSERT INTO meta VALUES (?, ?)', ('schema', SCHEMA))
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


def is_current(export):
    """True when a per-cell index exists at the current schema."""
    path = db_path(export)
    if not os.path.isfile(path):
        return False
    try:
        con = _connect(path)
        row = con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
        con.close()
        return bool(row) and row[0] == SCHEMA
    except sqlite3.DatabaseError:
        return False


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
        self._shared = None

    def _open_masters(self):
        """The master stores, opened on first need, nearest master first."""
        if self._masters is None:
            self._masters = [_Store(d) for d in self._master_dirs]
        return self._masters

    def _merged(self):
        """Base models, door bases and CELL records across the chain.

        Built on FIRST USE, not at open: merging 61,181 CELL records across
        four masters measured 23s, and a caller that only wants one cell's
        geometry never needs it.

        See: docs/commentary/tes5_import_navmesh.md#cellview-cell-index
        """
        if self._shared is None:
            base = dict(self._own.base_model)
            doors = dict(self._own.door_fids)
            cells = list(self._own.cells)
            seen = {(c.get('FormID') or '').upper() for c in cells}
            for store in self._open_masters():
                for k, v in store.base_model.items():
                    base.setdefault(k, v)
                for k, v in store.door_fids.items():
                    doors.setdefault(k, v)
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
        """`(refrs, pgrd, land)` for one cell, ours or a master's."""
        got = self._own.of_cell(fid)
        if got is not None:
            return got
        for store in self._open_masters():
            got = store.of_cell(fid)
            if got is not None:
                return got
        return [], None, None

    def iter_cells(self):
        """Yield `(fid, refrs, pgrd, land)` across the whole chain, ours first."""
        seen = set()
        for fid, refrs, pgrd, land in self._own.iter_cells():
            seen.add(fid)
            yield fid, refrs, pgrd, land
        for store in self._open_masters():
            for fid, refrs, pgrd, land in store.iter_cells():
                if fid not in seen:
                    seen.add(fid)
                    yield fid, refrs, pgrd, land

    def pathgrid_fids(self):
        """Cell FormIDs that have a pathgrid, across the chain.

        Answered from an indexed column, not by unpickling every cell: the
        scan measured 10.8s on a four-master chain.
        """
        out = set(self._own.pathgrid_fids())
        for store in self._open_masters():
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
        for store in (self._masters or ()):
            store.close()
