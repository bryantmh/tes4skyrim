"""Checkout leases for the test instances: one session holds an instance until it checks it back in.

    python -m tools.live.test_instance checkout --who "<task>"   # prints the instance and its token
    python -m tools.live.test_instance --instance N --token T <subcommand> ...
    python -m tools.live.test_instance --instance N --token T checkin
    python -m tools.live.test_instance checkouts                 # who holds what

See: docs/commentary/headless_testing.md#checking-out-an-instance
"""
import datetime
import json
import os
import secrets
import sys
import time

#: The test instances a session may check out.
SLOTS = (1, 2, 3)
#: A lease unused this long, on an instance whose game is not running, may be taken by the next checkout.
STALE_SECONDS = 2 * 3600
#: Subcommands that need no lease: they only read the leases or the instance's status.
OPEN_COMMANDS = ('checkout', 'checkouts', 'status')


def lease_dir(first_instance: str) -> str:
    """The folder holding one `<slot>.json` lease per checked-out instance."""
    return os.path.join(os.path.dirname(first_instance), 'Skyrim Test checkouts')


def _path(first_instance: str, slot: int) -> str:
    """The lease file of instance `slot`."""
    return os.path.join(lease_dir(first_instance), '%d.json' % slot)


def read(first_instance: str, slot: int) -> dict:
    """Instance `slot`'s lease, or {} when nobody holds it."""
    try:
        with open(_path(first_instance, slot), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write(path: str, lease: dict, exclusive: bool) -> bool:
    """Writes `lease` to `path`; with `exclusive`, only when no lease exists yet. Whether it was written."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        fd = os.open(path, flags)
    except FileExistsError:
        return False
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(lease, f, indent=1)
    return True


def is_stale(lease: dict, running: bool) -> bool:
    """Whether `lease` is unused for STALE_SECONDS with its game not running."""
    return not running and time.time() - lease.get('last_used', 0) > STALE_SECONDS


def checkout(first_instance: str, who: str, running) -> int:
    """Leases the first free (or stale) instance to `who` and prints its slot and token; 0 on success.

    `running(slot)` says whether that instance's game is running.
    """
    os.makedirs(lease_dir(first_instance), exist_ok=True)
    now = time.time()
    lease = {'token': secrets.token_hex(4), 'who': who, 'since': now, 'last_used': now}
    for slot in SLOTS:
        path = _path(first_instance, slot)
        held = read(first_instance, slot)
        if held and is_stale(held, running(slot)):
            print('instance %d: reclaimed the stale lease of "%s"' % (slot, held.get('who')))
            os.remove(path)
        if _write(path, dict(lease, slot=slot), exclusive=True):
            print('checked out instance %d  token %s' % (slot, lease['token']))
            print('use: python -m tools.live.test_instance --instance %d --token %s <subcommand>'
                  % (slot, lease['token']))
            return 0
    print_leases(first_instance)
    print('every test instance is checked out; wait for one or coordinate with its holder')
    return 1


def _stamp(seconds: float) -> str:
    """`seconds` since the epoch as local `YYYY-MM-DD HH:MM`."""
    return datetime.datetime.fromtimestamp(seconds).strftime('%Y-%m-%d %H:%M')


def print_leases(first_instance: str) -> None:
    """Prints who holds each instance and when it was last used."""
    for slot in SLOTS:
        lease = read(first_instance, slot)
        print('instance %d: %s' % (slot, 'free' if not lease else 'held by "%s" since %s, last used %s' % (
            lease.get('who'), _stamp(lease.get('since', 0)), _stamp(lease.get('last_used', 0)))))


def require(first_instance: str, slot: int, token: str) -> None:
    """Exits unless `token` holds instance `slot`; refreshes the lease's last use when it does."""
    lease = read(first_instance, slot)
    if not lease:
        sys.exit('instance %d is not checked out: run `python -m tools.live.test_instance checkout --who "<task>"` '
                 'and pass its --instance and --token' % slot)
    if lease.get('token') != token:
        sys.exit('instance %d is checked out by "%s"; pass your own lease\'s --instance and --token, '
                 'or `checkout` a free one' % (slot, lease.get('who')))
    lease['last_used'] = time.time()
    _write(_path(first_instance, slot), lease, exclusive=False)


def checkin(first_instance: str, slot: int) -> None:
    """Releases instance `slot`'s lease (the caller has already passed `require`)."""
    os.remove(_path(first_instance, slot))
    print('checked in instance %d' % slot)
