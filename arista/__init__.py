#!/usr/bin/env python3

"""
    Arista Transcoder Library
    =========================
    A set of tools to transcode files for various devices and presets using
    gstreamer.

    Usage
    -----

        >>> import arista
        >>> arista.init()
        >>> arista.presets.get()
        {'name': Device(), ...}

    License
    -------
    Copyright 2008 - 2011 Daniel G. Taylor <dan@programmer-art.org>

    This file is part of Arista.

    Arista is free software: you can redistribute it and/or modify
    it under the terms of the GNU Lesser General Public License as
    published by the Free Software Foundation, either version 2.1 of
    the License, or (at your option) any later version.

    Arista is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Lesser General Public License for more details.

    You should have received a copy of the GNU Lesser General Public
    License along with Arista.  If not, see
    <http://www.gnu.org/licenses/>.
"""

import gettext

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

_ = gettext.gettext

def init():
    """
        Initialize the arista module. You MUST call this method after
        importing.
    """
    from . import discoverer
    from . import dvd
    from . import inputs
    from . import presets
    from . import queue
    from . import transcoder
    from . import utils

    # Ref: https://bugzilla.gnome.org/show_bug.cgi?id=655084#c1
    Gst.init()

__version__ = _("0.9.8")
__author__ = _("Daniel G. Taylor <dan@programmer-art.org>")
