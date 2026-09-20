"""Cellview: a whole-cell 3D preview and editor in the browser.

Serves the page and the geometry/edit JSON it runs on.  The mesh editor opens
any cell of any export; the transplant corpus is a secondary panel over the
seven Bruma-fitted cells.

    python tools/cellview/server.py            # any cell of any export
    python tools/cellview/server.py --port 8765

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
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote_plus

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.cellview import corpus, plugins
from tools.cellview.bake import mesh_bake, mesh_save, plugin_cells

#: Files the page is built from; nothing outside this folder is served.
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

#: Export whose cells the editor opens when the page names none.
DEFAULT_PLUGIN = 'Oblivion.esm'


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
    why = plugins.preconditions(plugin)
    if why:
        return {'error': why}
    plugins.ensure_index(plugin)
    return {'ok': True, 'plugin': plugin}


def _routes():
    """`(GET, POST)` endpoint tables, each `{path: fn(params[, payload])}`."""
    get = {
        '/cells': lambda q: corpus.cells(),
        '/plugins': lambda q: plugins.candidates(),
        '/plugin_cells': lambda q: plugin_cells(
            q.get('plugin', DEFAULT_PLUGIN), q.get('q', '')),
        '/mesh': lambda q: mesh_bake(q.get('plugin', DEFAULT_PLUGIN),
                                     q.get('cell', '')),
        '/geometry': lambda q: corpus.bake(q.get('cell', '')),
        '/score': lambda q: corpus.score(q.get('cell', '')),
    }
    post = {
        '/index': lambda q, p: index_plugin(q),
        '/save': lambda q, p: corpus.apply_edits(q.get('cell', ''), p),
        '/score': lambda q, p: corpus.score(q.get('cell', ''), p),
        '/mesh_save': lambda q, p: mesh_save(q.get('plugin', DEFAULT_PLUGIN),
                                             q.get('cell', ''), p),
    }
    return get, post


_GET_ROUTES, _POST_ROUTES = _routes()


class Handler(BaseHTTPRequestHandler):
    """Serves the editor page and the geometry/edit JSON endpoints."""

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
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true')
    a = ap.parse_args()
    ready = [p['name'] for p in plugins.candidates() if p['collision']]
    url = 'http://127.0.0.1:%d/' % a.port
    print('cellview: %s\n  exports: %s\n  corpus cells: %s'
          % (url, ', '.join(ready) or '(none)',
             ', '.join(corpus.cells()) or '(none fitted -- mesh editor only)'))
    if not a.no_browser:
        webbrowser.open(url)
    HTTPServer(('127.0.0.1', a.port), Handler).serve_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main())
