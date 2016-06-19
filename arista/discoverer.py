# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4

# discoverer.py
# (c) 2005 Edward Hervey <edward at fluendo dot com>
# Discovers multimedia information on files

# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

"""
Class and functions for getting multimedia information about files

Modified to support dvd://device@title:chapter:audio style URIs using dvdreadsrc.
Modified to support v4l://device style URIs using v4lsrc.
Modified to support v4l2://device style URIs using v4l2src.

Modified to use uridecodebin instead of decodebin
"""

import re
import gettext
import logging

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')
from gi.repository import Gst
from gi.repository import GstPbutils

_ = gettext.gettext
_log = logging.getLogger("arista.discoverer")

Discoverer = GstPbutils.Discoverer
#TODO: Implement the support of dvd:, v4l2: URIs

def get_mimetype(info):
    container = info.get_stream_info()
    return container.get_caps().get_structure(0).get_name()


def get_tags_dict(info):
    d = {}
    def retrieve(tlist, name):
        value = tlist.get_value_index(name, 0)
        if isinstance(value, Gst.DateTime):
            value = value.to_iso8601_string()
        elif isinstance(value, Gst.Sample):
            value = value.get_info().to_string()
        d[name] = value
    tags = info.get_tags()
    tags.foreach(retrieve)
    return d


def is_video(info):
    try:
        info.get_video_streams()[0]
    except IndexError:
        return False
    return True


def is_audio(info):
    try:
        info.get_audio_streams()[0]
    except IndexError:
        return False
    return True


def get_range_value(gstruct, fieldname):
    '''
    Get value of GstIntRange type in a Gst.Structure
    '''
    # The string can be
    # video/x-raw, framerate=(fraction)[ 0/1, 2147483647/1 ],
    # width=(int)[ 16, 2147483647 ], height=(int)[ 16, 2147483647 ],
    # format=(string){ I420, YV12, Y42B, Y444, NV12 }
    # If fieldname is 'width', we return range(6, 2147483647)
    pattern = r'{}=\([a-z1-9]+\)\[ *(\d+) *, *(\d+) *\]'.format(fieldname)
    m = re.search(pattern, gstruct.to_string())
    if not m:
        return
    return range(int(m.group(1)), int(m.group(2)) + 1)


def get_list_value(gstruct, fieldname, coerce=int):
    '''
    Get value as list in a Gst.Structure
    '''
    # The string can be
    # audio/x-raw, rate=(int){ 96000, 88200, 64000, 48000, 44100 },
    # channels=(int)[ 1, 6 ], layout=(string)interleaved, format=(string)F32LE
    # If fieldname is 'rate', we return (6000, 88200, 64000, 48000, 44100)
    pattern = r'{}=\([a-z1-9]+\){{ *(\d+ *(, *\d+)*) *}}'.format(fieldname)
    string = gstruct.to_string()
    m = re.search(pattern, string)
    if m:
        return tuple(coerce(i.strip()) for i in m.group(1).split(','))
    pattern = r'{}=\([a-z1-9]+\)(\d+)'.format(fieldname)
    m = re.search(pattern, string)
    if m:
        return (coerce(m.group(1)),)


def get_video_dimension(info):
    '''
    Get tuple of width & height of video from GstPbutils.DiscovererInfo
    '''
    try:
        v = info.get_video_streams()[0]
    except IndexError:
        return
    return (v.get_width(), v.get_height())
