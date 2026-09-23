"""The menu bar finds which dropdown button the pointer is over.

Hover-switching reads this every tick while a dropdown is open, so an
off-by-one at a button's edge opens the wrong menu.
"""

from core.gui.menubar_behavior import button_at


class _Button:
    """A menubutton's on-screen box, as Tk reports it."""

    def __init__(self, x, y, w, h):
        self.box = (x, y, w, h)

    def winfo_rootx(self):
        """Left edge on screen."""
        return self.box[0]

    def winfo_rooty(self):
        """Top edge on screen."""
        return self.box[1]

    def winfo_width(self):
        """Width in pixels."""
        return self.box[2]

    def winfo_height(self):
        """Height in pixels."""
        return self.box[3]


def test_pointer_picks_the_button_under_it():
    """Edges are inclusive on the left and top, exclusive on the right."""
    settings, tools = _Button(0, 0, 60, 24), _Button(60, 0, 50, 24)
    bar = [(settings, None), (tools, None)]
    assert button_at(bar, 0, 0) is settings
    assert button_at(bar, 59, 23) is settings
    assert button_at(bar, 60, 10) is tools
    assert button_at(bar, 110, 10) is None
    assert button_at(bar, 30, 24) is None
