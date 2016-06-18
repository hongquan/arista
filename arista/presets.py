#!/usr/bin/env python3

"""
    Arista Presets
    ==============
    Objects for handling devices, presets, etc.

    Example Use
    -----------
    Presets are automatically loaded when the module is initialized.

        >>> import arista.presets
        >>> arista.presets.get()
        { "name": Device, ... }

    If you have other paths to load, use:

        >>> arista.presets.load("file")
        >>> arista.presets.load_directory("path")

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

import os
import json
import gettext
import shutil
import logging
import subprocess
import tarfile
import urllib.request
from collections import OrderedDict

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')
from gi.repository import GObject
from gi.repository import Gst
from gi.repository import GstPbutils

from . import utils
from .utils import Fraction

_ = gettext.gettext
_presets = {}
_log = logging.getLogger("arista.presets")


class Author:
    """
        An author object that stores a name and an email.
    """
    def __init__(self, name = "", email = ""):
        """
            @type name: str
            @param name: The author's full name
            @type email: str
            @param email: The email address of the author
        """
        self.name = name
        self.email = email

    def __str__(self):
        return (self.name or self.email) and '{} <{}>'.format(self.name, self.email) or ''

    def __repr__(self):
        return '<Author {}>'.format(self)


class Device:
    """
        A device holds information about a product and several presets for that
        product. This includes the make, model, version, etc.
    """
    def __init__(self, make = "Generic", model = "", description = "",
                 author = None, version = "", presets = None, icon = "",
                 default = ""):
        """
            @type make: str
            @param make: The make of the product, e.g. Apple
            @type model: str
            @param model: The model of the product, e.g. iPod
            @type description: str
            @param description: A user-friendly description of these presets
            @type author: Author
            @param author: The author of these presets
            @type version: str
            @param version: The version of these presets (not the product)
            @type presets: dict
            @param presets: A dictionary of presets where the keys are the
                            preset names
            @type icon: str
            @param icon: A URI to an icon. Only file:// and stock:// are
                         allowed, where stock refers to a GTK stock icon
            @type default: str
            @param default: The default preset name to use (if blank then the
                            first available preset is used)
        """
        self.make = make
        self.model = model
        self.description = description

        if author is not None:
            self.author = author
        else:
            self.author = Author()

        self.version = version
        self.presets = presets and presets or {}
        self.icon = icon
        self.default = default

        self.filename = None

    def __repr__(self):
        return '<Device: {} {}>'.format(self.make, self.model)

    def __str__(self):
        return '{} {}'.format(self.make, self.model)

    @property
    def name(self):
        """
            Get a friendly name for this device.

            @rtype: str
            @return: Either the make and model or just the model of the device
                     for generic devices
        """
        if self.make == "Generic":
            return self.model
        else:
            return "%s %s" % (self.make, self.model)

    @property
    def short_name(self):
        """
            Return the short name of this device preset.
        """
        return ".".join(os.path.basename(self.filename).split(".")[:-1])

    @property
    def default_preset(self):
        """
            Get the default preset for this device. If no default has been
            defined, the first preset that was loaded is returned. If no
            presets have been defined an exception is raised.

            @rtype: Preset
            @return: The default preset for this device
            @raise ValueError: No presets have been defined for this device
        """
        if self.default in self.presets:
            preset = self.presets[self.default]
        elif len(self.presets):
            preset = self.presets.values()[0]
        else:
            raise ValueError(_("No presets have been defined for " \
                                 "%(name)s") % { "name": self.name })

        return preset

    @property
    def json(self):
        data = {
            "make": self.make,
            "model": self.model,
            "description": self.description,
            "author": {
                "name": self.author.name,
                "email": self.author.email,
            },
            "version": self.version,
            "icon": self.icon,
            "default": self.default,
            "presets": [],
        }

        for name, preset in self.presets.items():
            rates = []
            for x in preset.acodec.rate[0], preset.acodec.rate[1], preset.vcodec.rate[0], preset.vcodec.rate[1]:
                if isinstance(x, Fraction):
                    rates.append(str(x))
                else:
                    rates.append("%s" % x)

            data["presets"].append({
                "name": preset.name,
                "description": preset.description,
                "author": {
                    "name": preset.author.name,
                    "email": preset.author.email,
                },
                "container": preset.container,
                "extension": preset.extension,
                "icon": preset.icon,
                "version": preset.version,
                "acodec": {
                    "name": preset.acodec.name,
                    "container": preset.acodec.container,
                    "rate": [rates[0], rates[1]],
                    "passes": preset.acodec.passes,
                    "width": preset.acodec.width,
                    "depth": preset.acodec.depth,
                    "channels": preset.acodec.channels,
                },
                "vcodec": {
                    "name": preset.vcodec.name,
                    "container": preset.vcodec.container,
                    "rate": [rates[2], rates[3]],
                    "passes": preset.vcodec.passes,
                    "width": preset.vcodec.width,
                    "height": preset.vcodec.height,
                    "transform": preset.vcodec.transform,
                },
            })

        return json.dumps(data, indent=4)

    def save(self):
        """
            Save this device and its presets to a file. The device.filename must
            be set to a valid path or an error will be thrown.
        """
        open(self.filename, "w").write(self.json)

    def export(self, filename):
        """
            Export this device and all presets to a file. Creates a bzipped
            tarball of the JSON and all associated images that can be easily
            imported later.
        """
        # Make sure all changes are saved
        self.save()

        # Gather image files
        images = set()
        for name, preset in self.presets.items():
            if preset.icon:
                images.add(preset.icon[7:])

        files = " ".join([os.path.basename(self.filename)] + list(images))

        cwd = os.getcwd()
        os.chdir(os.path.dirname(self.filename))
        subprocess.call("tar -cjf %s %s" % (filename, files), shell=True)
        os.chdir(cwd)

    @staticmethod
    def from_json(data):
        parsed = json.loads(data)

        device = Device(**{
            "make": parsed.get("make", "Generic"),
            "model": parsed.get("model", ""),
            "description": parsed.get("description", ""),
            "author": Author(
                name = parsed.get("author", {}).get("name", ""),
                email = parsed.get("author", {}).get("email", ""),
            ),
            "version": parsed.get("version", ""),
            "icon": parsed.get("icon", ""),
            "default": parsed.get("default", ""),
        })

        for preset in parsed.get("presets", []):
            acodec = preset.get("acodec", {})
            vcodec = preset.get("vcodec", {})
            device.presets[preset.get("name", "")] = Preset(**{
                "name": preset.get("name", ""),
                "description": preset.get("description", device.description),
                "author": Author(
                    name = preset.get("author", {}).get("name", device.author.name),
                    email = preset.get("author", {}).get("email", device.author.email),
                ),
                "container": preset.get("container", ""),
                "extension": preset.get("extension", ""),
                "version": preset.get("version", device.version),
                "icon": preset.get("icon", device.icon),
                "acodec": AudioCodec(**{
                    "name": acodec.get("name", ""),
                    "container": acodec.get("container", ""),
                    "rate": [int(x) for x in acodec.get("rate", [])],
                    "passes": acodec.get("passes", []),
                    "width": acodec.get("width", []),
                    "depth": acodec.get("depth", []),
                    "channels": acodec.get("channels", []),
                }),
                "vcodec": VideoCodec(**{
                    "name": vcodec.get("name", ""),
                    "container": vcodec.get("container", ""),
                    "rate": [Fraction(x) for x in vcodec.get("rate", [])],
                    "passes": vcodec.get("passes", []),
                    "width": vcodec.get("width", []),
                    "height": vcodec.get("height", []),
                    "transform": vcodec.get("transform", ""),
                }),
                "device": device,
            })

        return device


class Preset:
    """
        A preset representing audio and video encoding options for a particular
        device.
    """
    def __init__(self, name = "", container = "", extension = "",
                 acodec = None, vcodec = None, device = None, icon = None,
                 version = None, description = None, author = None):
        """
            @type name: str
            @param name: The name of the preset, e.g. "High Quality"
            @type container: str
            @param container: The container element name, e.g. ffmux_mp4
            @type extension: str
            @param extension: The filename extension to use, e.g. mp4
            @type acodec: AudioCodec
            @param acodec: The audio encoding settings
            @type vcodec: VideoCodec
            @param vcodec: The video encoding settings
            @type device: Device
            @param device: A link back to the device this preset belongs to
        """
        self.name = name
        self.description = description
        self.author = author
        self.container = container
        self.extension = extension
        self.acodec = acodec
        self.vcodec = vcodec
        self.device = device
        self.version = version
        self.icon = icon

    def __repr__(self):
        return '<Preset {} {}>'.format(self.name, self.container)

    def __str__(self):
        return '{} {}'.format(self.name, self.container)

    @property
    def pass_count(self):
        """
            @rtype: int
            @return: The number of passes in this preset
        """
        return max(len(self.vcodec.passes), len(self.acodec.passes))

    @property
    def slug(self):
        """
            @rtype: str
            @return: A slug based on the preset name safe to use as a filename
                     or in links
        """
        slug = ".".join(os.path.basename(self.device.filename).split(".")[:-1]) + "-" + self.name.lower()

        return slug.replace(" ", "_").replace("'", "").replace("/", "")

    def check_elements(self, callback, *args):
        """
            Check the elements used in this preset. If they don't exist then
            let GStreamer offer to install them.

            @type callback: callable(preset, success, *args)
            @param callback: A method to call when the elements are all
                             available or installation failed
            @rtype: bool
            @return: True if required elements are available, False otherwise
        """
        elements = [
            # Elements defined in external files
            self.container,
            self.acodec.name,
            self.vcodec.name,
            # Elements used internally
            "decodebin2",
            "videobox",
            "ffmpegcolorspace",
            "videoscale",
            "videorate",
            "ffdeinterlace",
            "audioconvert",
            "audiorate",
            "audioresample",
            "tee",
            "queue",
        ]

        missing = []
        missingdesc = ""
        for element in elements:
            if not Gst.ElementFactory.find(element):
                missing.append(GstPbutils.missing_element_installer_detail_new(element))
                if missingdesc:
                    missingdesc += ", %s" % element
                else:
                    missingdesc += element

        if missing:
            _log.info("Attempting to install elements: %s" % missingdesc)
            if GstPbutils.install_plugins_supported():
                def install_done(result, null):
                    if result == GstPbutils.INSTALL_PLUGINS_INSTALL_IN_PROGRESS:
                        # Ignore start of installer message
                        pass
                    elif result == GstPbutils.INSTALL_PLUGINS_SUCCESS:
                        callback(self, True, *args)
                    else:
                        _log.error("Unable to install required elements!")
                        callback(self, False, *args)

                context = GstPbutils.InstallPluginsContext()
                GstPbutils.install_plugins_async(missing, context,
                                                  install_done, "")
            else:
                _log.error("Installing elements not supported!")
                GObject.idle_add(callback, self, False, *args)
        else:
            GObject.idle_add(callback, self, True, *args)


class Codec:
    """
        Settings for encoding audio or video. This object defines options
        common to both audio and video encoding.
    """
    def __init__(self, name=None, container=None, passes=None):
        """
            @type name: str
            @param name: The name of the encoding GStreamer element, e.g. faac
            @type container: str
            @param container: A container to fall back to if only audio xor
                              video is present, e.g. for plain mp3 audio you
                              may not want to wrap it in an avi or mp4; if not
                              set it defaults to the preset container
        """
        self.name = name and name or ""
        self.container = container and container or ""
        self.passes = passes and passes or []

        self.rate = (Fraction(), Fraction())

    def __repr__(self):
        return '<Codec {} {}>'.format(self.name, self.container)

    def __str__(self):
        return '{} {}'.format(self.name, self.container)


class AudioCodec(Codec):
    """
        Settings for encoding audio.
    """
    def __init__(self, name=None, container=None, rate=None, passes=None, width=None, depth=None, channels=None):
        super().__init__(name=name, container=container, passes=passes)
        # The value of these attributes can be of type:
        # range(): when encoder returns data as range
        # tuple(): when encode returns data as array (list) or single
        self.rate = rate and rate or (8000, 96000)   # Sample rate
        self.width = width and width or (8, 24)   # Not exist in GStreamer 1.0
        self.depth = depth and depth or (8, 24)   # Not mentioned in encoder in GStreamer 1.0
        self.channels = channels and channels or range(1, 6)


class VideoCodec(Codec):
    """
        Settings for encoding video.
    """
    def __init__(self, name=None, container=None, rate=None, passes=None, width=None, height=None, transform=None):
        Codec.__init__(self, name=name, container=container, passes=passes)
        self.rate = rate and rate or (Fraction(1), Fraction(60))
        self.width = width and width or (2, 1920)
        self.height = height and height or (2, 1080)
        self.transform = transform


def load(filename):
    """
        Load a filename into a new Device.

        @type filename: str
        @param filename: The file to load
        @rtype: Device
        @return: A new device instance loaded from the file
    """
    device = Device.from_json(open(filename).read())

    device.filename = filename

    _log.debug(_("Loaded device %(device)s (%(presets)d presets)") % {
        "device": device.name,
        "presets": len(device.presets),
    })

    return device


def load_directory(directory):
    """
        Load an entire directory of device presets.

        @type directory: str
        @param directory: The path to load
        @rtype: dict
        @return: A dictionary of all the loaded devices
    """
    for filename in os.listdir(directory):
        if filename.endswith("json"):
            try:
                _presets[filename[:-5]] = load(os.path.join(directory, filename))
            except OSError as e:
                _log.warning("Problem loading %s! %s" % (filename, str(e)))
    return _presets


def get():
    """
        Get all loaded device presets.

        @rtype: dict
        @return: A dictionary of Device objects where the keys are the short
                 name for the device
    """
    return _presets


def version_info():
    """
        Generate a string of version information. Each line contains
        "name, version" for a particular preset file, where name is the key
        found in arista.presets.get().

        This is used for checking for updates.
    """
    info = ""

    for name, device in _presets.items():
        info += "%s, %s\n" % (name, device.version)

    return info

def extract(stream):
    """
        Extract a preset file into the user's local presets directory.

        @type stream: a file-like object
        @param stream: The opened bzip2-compressed tar file of the preset
        @rtype: list
        @return: The installed device preset shortnames ["name1", "name2", ...]
    """
    local_path = os.path.expanduser(os.path.join("~", ".arista", "presets"))

    if not os.path.exists(local_path):
        os.makedirs(local_path)

    tar = tarfile.open(mode="r|bz2", fileobj=stream)
    _log.debug(_("Extracting %(filename)s") % {
        "filename": hasattr(stream, "name") and stream.name or "data stream",
    })
    tar.extractall(path=local_path)

    return [x[:-5] for x in tar.getnames() if x.endswith(".json")]


def fetch(location, name):
    """
        Attempt to fetch and install a preset. Presets are always installed
        to ~/.arista/presets/.

        @type location: str
        @param location: The location of the preset
        @type name: str
        @param name: The name of the preset to fetch, without any extension
        @rtype: list
        @return: The installed device preset shortnames ["name1", "name2", ...]
    """
    if not location.endswith("/"):
        location = location + "/"

    path = location + name + ".tar.bz2"
    _log.debug(_("Fetching %(location)s") % {
        "location": path,
    })

    updated = []

    try:
        f = urllib.request.urlopen(path)
        updated += extract(f)
    except OSError as e:
        _log.warning(_("There was an error fetching and installing " \
                       "%(location)s: %(error)s") % {
            "location": path,
            "error": str(e),
        })

    return updated


def reset(overwrite=False, ignore_initial=False):
    # Automatically load presets
    global _presets

    _presets = {}

    load_path = utils.get_write_path("presets")
    if ignore_initial or not os.path.exists(os.path.join(load_path, ".initial_complete")):
        # Do initial population of presets from system install / cwd
        if not os.path.exists(load_path):
            os.makedirs(load_path)

        # Write file to say we have done this
        open(os.path.join(load_path, ".initial_complete"), "w").close()

        # Copy actual files
        search_paths = utils.get_search_paths()
        if overwrite:
            # Reverse search paths because things will get overwritten
            search_paths = reversed(search_paths)

        for path in search_paths:
            full = os.path.join(path, "presets")
            if full != load_path and os.path.exists(full):
                for f in os.listdir(full):
                    # Do not overwrite existing files
                    if overwrite or not os.path.exists(os.path.join(load_path, f)):
                        shutil.copy2(os.path.join(full, f), load_path)

    load_directory(load_path)


def parse_pass(pas):
    pairs = pas.split()
    d = OrderedDict()
    for p in pairs:
        k, v = p.split('=', 1)
        d[k] = v
    return d


def make_pass_from_dict(d):
    out = []
    for k, v in d.items():
        out.append('{}={}'.format(k, v))
    return ' '.join(out)


def remove_param_from_passes(passes, key):
    '''
    Remove key=value from codec passess
    '''
    out = []
    for pas in passes:
        d = parse_pass(pas)
        if key in d:
            del d[key]
        out.append(make_pass_from_dict(d))
    return out


reset()
