"""Cellview: a whole-cell 3D preview and editor in the browser.

Serves the page and the geometry/edit JSON it runs on.  The mesh editor opens
any cell of any export; the transplant corpus is a secondary panel over the
seven Bruma-fitted cells, shown only with `--transplant`.

    python tools/cellview/server.py            # any cell of any export
    python tools/cellview/server.py --port 8765
    python tools/cellview/server.py --pins my_navmesh_pins --transplant

The first line printed is `cellview: <url>`, with the real port when `--port 0`
asks for a free one -- the GUI's Navmesh menu reads it.

Geometry is baked to JSON once per cell and cached in memory: gathering a
cell's collision takes seconds, and the page re-fetches on every cell switch.

See: docs/commentary/tes5_import_navmesh.md#transplant-editor
"""

import argparse
import json
import mimetypes
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote_plus

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.navmesh_options import set_navmesh_pins_dir
from tes5_import.base.navmesh_pins import save_dir
from tools.cellview import corpus, plugins, progress
from tools.cellview.bake import (
    mesh_bake, mesh_node_pin, mesh_pin, mesh_save, mesh_to_esm, mesh_unpin, pinned_cells, plugin_cells,
    seams_for,
)

#: Files the page is built from; nothing outside this folder is served.
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

#: Export whose cells the editor opens when the page names none.
DEFAULT_PLUGIN = 'Oblivion.esm'

#: What this run of the server offers the page; set once from the command line.
FEATURES = {'transplant': False}

#: Page preferences; server-side because the GUI's port, and so browser storage, changes each launch.
PREFS_FILE = os.path.join(plugins.ROOT, plugins.EXPORT_ROOT, 'cellview_prefs.json')


def features():
    """What the page may show and start on: panels, pin folder, saved preferences."""
    return {'transplant': FEATURES['transplant'], 'pins_dir': save_dir(),
            'prefs': prefs()}


def prefs():
    """Saved page preferences (`plugin`, `layers`), or {} when none are recorded."""
    try:
        with open(PREFS_FILE, encoding='utf-8') as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def save_prefs(update):
    """Merge `update` into the saved preferences; a failed write is ignored."""
    merged = dict(prefs(), **{k: v for k, v in update.items() if v})
    try:
        with open(PREFS_FILE, 'w', encoding='utf-8') as fh:
            json.dump(merged, fh, indent=1, sort_keys=True)
    except OSError:
        pass
    return {'ok': True}


def collision_job(params):
    """Build a plugin's collision cache (and its masters'), publishing progress."""
    job = params.get('job', '')
    progress.start(job, 'collision')
    error = ''
    try:
        error = plugins.build_collision(params.get('plugin', DEFAULT_PLUGIN))
        return {'error': error} if error else {'ok': True}
    finally:
        progress.finish(job, error)


def query_params(query):
    """A query string as a dict, values URL-decoded."""
    out = {}
    for part in query.split('&'):
        k, _, v = part.partition('=')
        if k:
            out[k] = unquote_plus(v)
    return out


def index_plugin(params):
    """Build one export's index on demand; the page shows the wait."""
    plugin = params.get('plugin', DEFAULT_PLUGIN)
    job = params.get('job', '')
    why = plugins.preconditions(plugin)
    if why:
        return {'error': why}
    progress.start(job, 'index')
    try:
        progress.step(job, 0)
        plugins.ensure_index(plugin)
        progress.step(job, 2, 1.0)
    finally:
        progress.finish(job)
    return {'ok': True, 'plugin': plugin}


def lattice_of(params):
    """True when the page asks for the prototype lattice generator."""
    return params.get('lattice', '0') == '1'


def pinned_of(params):
    """True unless the page shows the RAW generator, without committed edits."""
    return params.get('pinned', '1') != '0'


def mesh_job(params):
    """Bake one cell, publishing progress under the page's job id."""
    job = params.get('job', '')
    progress.start(job, 'mesh')
    error = ''
    save_prefs({'plugin': params.get('plugin', '')})
    try:
        out = mesh_bake(params.get('plugin', DEFAULT_PLUGIN),
                        params.get('cell', ''), job=job,
                        pinned=pinned_of(params),
                        lattice=lattice_of(params))
        error = out.get('error', '')
        return out
    finally:
        progress.finish(job, error)


def seams_job(params):
    """Compute one cell's seam links, publishing progress."""
    job = params.get('job', '')
    progress.start(job, 'seams')
    try:
        return seams_for(params.get('plugin', DEFAULT_PLUGIN),
                         params.get('cell', ''), job=job, lattice=lattice_of(params))
    finally:
        progress.finish(job)



def esm_patch_job(params, payload):
    """Patch the edited mesh into the built ESM, publishing progress.

    See: docs/commentary/tes5_import_navmesh.md#patching-a-navmesh-into-a-built-esm
    """
    job = params.get('job', '')
    progress.start(job, 'mesh')
    error = ''
    try:
        out = mesh_to_esm(params.get('plugin', DEFAULT_PLUGIN),
                          params.get('cell', ''), payload, lattice_of(params),
                          pinned_of(params))
        error = out.get('error', '')
        return out
    finally:
        progress.finish(job, error)



def pin_job(params, payload):
    """Pin the edited triangles to the committable pin file.

    See: docs/commentary/tes5_import_navmesh.md#pinned-navmesh-floor
    """
    job = params.get('job', '')
    progress.start(job, 'mesh')
    error = ''
    try:
        out = mesh_pin(params.get('plugin', DEFAULT_PLUGIN),
                       params.get('cell', ''), payload, lattice_of(params),
                       pinned_of(params))
        error = out.get('error', '')
        return out
    finally:
        progress.finish(job, error)


def _routes():
    """`(GET, POST)` endpoint tables, each `{path: fn(params[, payload])}`."""
    get = {
        '/features': lambda q: features(),
        '/cells': lambda q: corpus.cells() if FEATURES['transplant'] else [],
        '/plugins': lambda q: plugins.candidates(),
        '/plugin_cells': lambda q: plugin_cells(
            q.get('plugin', DEFAULT_PLUGIN), q.get('q', '')),
        '/pinned_cells': lambda q: pinned_cells(q.get('plugin', DEFAULT_PLUGIN)),
        '/mesh': mesh_job,
        '/progress': lambda q: progress.poll(q.get('job', '')),
        '/seams': seams_job,
        '/geometry': lambda q: corpus.bake(q.get('cell', '')),
        '/score': lambda q: corpus.score(q.get('cell', '')),
    }
    post = {
        '/index': lambda q, p: index_plugin(q),
        '/collision': lambda q, p: collision_job(q),
        '/save': lambda q, p: corpus.apply_edits(q.get('cell', ''), p),
        '/score': lambda q, p: corpus.score(q.get('cell', ''), p),
        '/mesh_save': lambda q, p: mesh_save(q.get('plugin', DEFAULT_PLUGIN),
                                             q.get('cell', ''), p, lattice_of(q),
                                             pinned_of(q)),
        '/esm_patch': lambda q, p: esm_patch_job(q, p),
        '/pin_save': lambda q, p: pin_job(q, p),
        '/prefs': lambda q, p: save_prefs(p),
        '/pin_remove': lambda q, p: mesh_unpin(q.get('plugin', DEFAULT_PLUGIN),
                                               q.get('cell', ''), p),
        '/node_pin': lambda q, p: mesh_node_pin(q.get('plugin', DEFAULT_PLUGIN),
                                                q.get('cell', ''), p),
    }
    return get, post


_GET_ROUTES, _POST_ROUTES = _routes()


class Handler(BaseHTTPRequestHandler):
    """Serves the editor page and the geometry/edit JSON endpoints.

    Served THREADED: a bake holds the handler for tens of seconds, and a
    single-threaded server could not answer the `/progress` poll that is
    meant to be tracking it.
    """

    def log_message(self, fmt, *args):
        """Quiet: one line per request would bury the startup banner."""

    def _send(self, body, ctype='application/json'):
        """Write one response with the right headers."""
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, name):
        """Serve one file from `static/`, or 404.

        `basename` is what keeps a crafted path from escaping the folder.
        """
        path = os.path.join(STATIC, os.path.basename(name))
        if not os.path.isfile(path):
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        with open(path, 'rb') as fh:
            self._send(fh.read(), ctype)

    def do_GET(self):
        """Route the page, its assets, cell lists, geometry and the score."""
        path, _, query = self.path.partition('?')
        if path in ('/', '/index.html'):
            self._static('index.html')
            return
        route = _GET_ROUTES.get(path)
        if route is None:
            self._static(path.lstrip('/'))
            return
        self._send(route(query_params(query)))

    def do_POST(self):
        """Build an index, or score and commit edits."""
        path, _, query = self.path.partition('?')
        route = _POST_ROUTES.get(path)
        if route is None:
            self.send_error(404)
            return
        n = int(self.headers.get('Content-Length') or 0)
        payload = json.loads(self.rfile.read(n) or b'{}')
        self._send(route(query_params(query), payload))


def main():
    """CLI: serve the editor until interrupted."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', type=int, default=8765,
                    help='0 picks a free port; the banner prints the one used')
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--pins', metavar='DIR',
                    help='save pins here, read over the shipped navmesh_pins/')
    ap.add_argument('--transplant', action='store_true',
                    help='show the Bruma transplant corpus panel')
    a = ap.parse_args()
    set_navmesh_pins_dir(a.pins)
    FEATURES['transplant'] = a.transplant
    server = ThreadingHTTPServer(('127.0.0.1', a.port), Handler)
    url = 'http://127.0.0.1:%d/' % server.server_address[1]
    print('cellview: %s' % url, flush=True)
    ready = [p['name'] for p in plugins.candidates() if p['collision']]
    print('  exports: %s\n  pins saved to: %s'
          % (', '.join(ready) or '(none)', save_dir()), flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    server.serve_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main())
