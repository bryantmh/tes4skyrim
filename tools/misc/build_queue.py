"""Watch the machine's heavy-job queue: the running build with its runtime,
then every queued build in arrival order. Updates in place until Ctrl+C.

  python -m tools.misc.build_queue            # live view, refreshed every 2 s
  python -m tools.misc.build_queue --once     # print once and exit

See: docs/commentary/performance.md#one-heavy-job-at-a-time
"""

import argparse
import sys
import time

from rich.console import Console
from rich.live import Live
from rich.table import Table

from core.heavy_lock import snapshot


def _since(start) -> str:
    """How long ago epoch `start` was, as h:mm:ss."""
    seconds = int(time.time() - (start or time.time()))
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def render() -> Table:
    """The running build, then the queue, as one table."""
    running, queued = snapshot()
    table = Table(title=f"Heavy-job queue  {time.strftime('%H:%M:%S')}",
                  caption="Ctrl+C to stop", expand=True)
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Time", justify="right", no_wrap=True)
    table.add_column("PID", justify="right", no_wrap=True)
    table.add_column("Command")
    if running:
        table.add_row("", "[bold green]running", _since(running.get("started")),
                      str(running["pid"]), running.get("label", "?"))
    else:
        table.add_row("", "[dim]idle", "", "", "[dim]nothing running")
    for n, ticket in enumerate(queued, 1):
        replaced = ticket.get("replaced_by")
        state = f"[yellow]replaced by {replaced}" if replaced else "waiting"
        table.add_row(str(n), state, _since(ticket.get("queued")),
                      str(ticket["pid"]), ticket.get("label", "?"))
    return table


def main() -> int:
    """Print the queue once, or keep it updated in place every `--interval` seconds."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="print once and exit")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between refreshes")
    args = parser.parse_args()
    if args.once:
        Console().print(render())
        return 0
    try:
        with Live(render(), auto_refresh=False, screen=True) as live:
            while True:
                time.sleep(args.interval)
                live.update(render(), refresh=True)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
