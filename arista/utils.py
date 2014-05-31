#!/usr/bin/env python

"""
    Arista Utilities
    ================
    A set of utility methods to do various things inside of Arista.

    License
    -------
    Copyright 2009 - 2011 Daniel G. Taylor <dan@programmer-art.org>

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
import logging
import os
import re
import sys

_ = gettext.gettext

RE_ENDS_NUM = re.compile(r'^.*(?P<number>[0-9]+)$')

def get_search_paths():
    """
        Get a list of paths that are searched for installed resources.

        @rtype: list
        @return: A list of paths to search in the order they will be searched
    """
    return [
        # Current path, useful for development:
        os.getcwd(),
        # User home directory:
        os.path.expanduser(os.path.join("~", ".arista")),
        # User-installed:
        os.path.join(sys.prefix, "local", "share", "arista"),
        # System-installed:
        os.path.join(sys.prefix, "share", "arista"),
        # The following allows stuff like virtualenv to work!
        os.path.join(os.path.join(os.path.dirname(os.path.dirname(__file__)), "share", "arista")),
    ]

def get_path(*parts, **kwargs):
    """
        Get a path, searching first in the current directory, then the user's
        home directory, then sys.prefix, then sys.prefix + "local".

            >>> get_path("presets", "computer.json")
            '/usr/share/arista/presets/computer.json'
            >>> get_path("presets", "my_cool_preset.json")
            '/home/dan/.arista/presets/my_cool_preset.json'

        @type parts: str
        @param parts: The parts of the path to get that you would normally
                      send to os.path.join
        @type default: bool
        @param default: A default value to return rather than raising IOError
        @rtype: str
        @return: The full path to the relative path passed in
        @raise IOError: The path cannot be found in any location
    """
    path = os.path.join(*parts)

    for search in get_search_paths():
        full = os.path.join(search, path)
        if os.path.exists(full):
            return full
    else:
        if "default" in kwargs:
            return kwargs["default"]

        raise IOError(_("Can't find %(path)s in any known prefix!") % {
            "path": path,
        })

def get_write_path(*parts, **kwargs):
    """
        Get a path that can be written to. This uses the same logic as get_path
        above, but instead of checking for the existence of a path it checks
        to see if the current user has write accces.

            >>>> get_write_path("presets", "foo.json")
            '/home/dan/.arista/presets/foo.json'

        @type parts: str
        @param parts: The parts of the path to get that you would normally
                      send to os.path.join
        @type default: bool
        @param default: A default value to return rather than raising IOError
        @rtype: str
        @return: The full path to the relative path passed in
        @raise IOError: The path cannot be written to in any location
    """
    path = os.path.join(*parts)

    for search in get_search_paths()[1:]:
        full = os.path.join(search, path)

        # Find part of path that exists
        test = full
        while not os.path.exists(test):
            test = os.path.dirname(test)

        if os.access(test, os.W_OK):
            if not os.path.exists(os.path.dirname(full)):
                os.makedirs(os.path.dirname(full))

            return full
    else:
        if "default" in kwargs:
            return kwargs["default"]

        raise IOError(_("Can't find %(path)s that can be written to!") % {
            "path": path,
        })

def get_friendly_time(seconds):
   """
      Get a human-friendly time description.
   """
   hours = seconds / (60 * 60)
   seconds = seconds % (60 * 60)
   minutes = seconds / 60
   seconds = seconds % 60

   return "%(hours)02d:%(minutes)02d:%(seconds)02d" % {
      "hours": hours,
      "minutes": minutes,
      "seconds": seconds,
   }

def generate_output_path(filename, preset, to_be_created=[],
                         device_name=""):
    """
        Generate a new output filename from an input filename and preset.

        @type filename: str
        @param filename: The input file name
        @type preset: arista.presets.Preset
        @param preset: The preset being encoded
        @type to_be_created: list
        @param to_be_created: A list of filenames that will be created and
                              should not be overwritten, useful if you are
                              processing many items in a queue
        @type device_name: str
        @param device_name: Device name to appent to output filename, e.g.
                            myvideo-ipod.m4v
        @rtype: str
        @return: A new unique generated output path
    """
    name, ext = os.path.splitext(filename)

    # Is this a special URI? Let's just use the basename then!
    if name.startswith("dvd://") or name.startswith("v4l://") or name.startswith("v4l2://"):
        name = os.path.basename(name)

    if device_name:
        name += "-" + device_name
    default_out = name + "." + preset.extension

    while os.path.exists(default_out) or default_out in to_be_created:
        parts = default_out.split(".")
        name, ext = ".".join(parts[:-1]), parts[-1]

        result = RE_ENDS_NUM.search(name)
        if result:
            value = result.group("number")
            name = name[:-len(value)]
            number = int(value) + 1
        else:
            number = 1

        default_out = "%s%d.%s" % (name, number, ext)

    return default_out

