#!/usr/bin/env python3

"""
    Arista Transcoder
    =================
    A class to transcode files given a preset.

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

import os
import os.path
import time
import gettext
import logging

# Default to 2 CPUs as most seem to be dual-core these days
CPU_COUNT = 2
try:
    import multiprocessing
    try:
        CPU_COUNT = multiprocessing.cpu_count()
    except NotImplementedError:
        pass
except ImportError:
    pass

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')
from gi.repository import GObject
from gi.repository import GLib
from gi.repository import Gst
from gi.repository import GstPbutils

from . import discoverer
from .discoverer import is_audio, is_video, get_range_value, \
    get_video_dimension, get_array_value
from .presets import remove_param_from_passes
from .utils import expand_capacity

_ = gettext.gettext
_log = logging.getLogger("arista.transcoder")


# =============================================================================
# Custom exceptions
# =============================================================================

class TranscoderException(Exception):
    """
        A generic transcoder exception to be thrown when something goes wrong.
    """
    pass

class TranscoderStatusException(TranscoderException):
    """
        An exception to be thrown when there is an error retrieving the current
        status of an transcoder.
    """
    pass

class PipelineException(TranscoderException):
    """
        An exception to be thrown when the transcoder fails to construct a
        working pipeline for whatever reason.
    """
    pass

# =============================================================================
# Transcoder Options
# =============================================================================

class TranscoderOptions(object):
    """
        Options pertaining to the input/output location, presets,
        subtitles, etc.
    """
    def __init__(self, uri = None, preset = None, output_uri = None, ssa = False,
                 subfile = None, subfile_charset = None, font = "Sans Bold 16",
                 deinterlace = None, crop = None, title = None, chapter = None,
                 audio = None):
        """
            @type uri: str
            @param uri: The URI to the input file, device, or stream
            @type preset: Preset
            @param preset: The preset to convert to
            @type output_uri: str
            @param output_uri: The URI to the output file, device, or stream
            @type subfile: str
            @param subfile: The location of the subtitle file
            @type subfile_charset: str
            @param subfile_charset: Subtitle file character encoding, e.g.
                                    'utf-8' or 'latin-1'
            @type font: str
            @param font: Pango font description
            @type deinterlace: bool
            @param deinterlace: Force deinterlacing of the input data
            @type crop: int tuple
            @param crop: How much should be cropped on each side
                                    (top, right, bottom, left)
            @type title: int
            @param title: DVD title index
            @type chatper: int
            @param chapter: DVD chapter index
            @type audio: int
            @param audio: DVD audio stream index
        """
        self.reset(uri, preset, output_uri, ssa,subfile, subfile_charset, font,
                   deinterlace, crop, title, chapter, audio)

    def reset(self, uri = None, preset = None, output_uri = None, ssa = False,
              subfile = None, subfile_charset = None, font = "Sans Bold 16",
              deinterlace = None, crop = None, title = None, chapter = None,
              audio = None):
        """
            Reset the input options to nothing.
        """
        self.uri = uri
        self.preset = preset
        self.output_uri = output_uri
        self.ssa = ssa
        self.subfile = subfile
        self.subfile_charset = subfile_charset
        self.font = font
        self.deinterlace = deinterlace
        self.crop = crop
        self.title = title
        self.chapter = chapter
        self.audio = audio

# =============================================================================
# The Transcoder
# =============================================================================

class Transcoder(GObject.GObject):
    """
        The transcoder - converts media between formats.
    """
    __gsignals__ = {
        "discovered": (GObject.SignalFlags.RUN_LAST, None,
                      (GObject.TYPE_PYOBJECT,      # info
                       GObject.TYPE_PYOBJECT)),    # is_media
        "pass-setup": (GObject.SignalFlags.RUN_LAST, None, tuple()),
        "pass-complete": (GObject.SignalFlags.RUN_LAST, None, tuple()),
        "message": (GObject.SignalFlags.RUN_LAST, None,
                   (GObject.TYPE_PYOBJECT,         # bus
                    GObject.TYPE_PYOBJECT)),       # message
        "complete": (GObject.SignalFlags.RUN_LAST, None, tuple()),
        "error": (GObject.SignalFlags.RUN_LAST, None,
                 (GObject.TYPE_PYOBJECT,)),        # error
    }

    def __init__(self, options):
        """
            @type options: TranscoderOptions
            @param options: The options, like input uri, subtitles, preset,
                            output uri, etc.
        """
        super().__init__()
        self.options = options

        self.pipe = None

        self.enc_pass = 0

        self._percent_cached = 0
        self._percent_cached_time = 0

        if options.uri.startswith("dvd://") and len(options.uri.split("@")) < 2:
            options.uri += "@%(title)s:%(chapter)s:%(audio)s" % {
                "title": options.title or "a",
                "chapter": options.chapter or "a",
                "audio": options.audio or "a",
            }

        if options.uri.startswith("dvd://") and not options.title:
            # This is a DVD and no title is yet selected... find the best
            # candidate by searching for the longest title!
            parts = options.uri.split("@")
            options.uri = parts[0] + "@0:a:a"
            self.dvd_infos = []

            def _got_info(info, is_media):
                self.dvd_infos.append([discoverer, info])
                parts = self.options.uri.split("@")
                fname = parts[0]
                title = int(parts[1].split(":")[0])
                if title >= 8:
                    # We've checked 8 titles, let's give up and pick the
                    # most likely to be the main feature.
                    longest = 0
                    self.info = None
                    for disco, info in self.dvd_infos:
                        if info.length > longest:
                            self.discoverer = disco
                            self.info = info
                            longest = info.length

                    if not self.info:
                        self.emit("error", _("No valid DVD title found!"))
                        return

                    self.options.uri = self.info.filename

                    _log.debug(_("Longest title found is %(filename)s") % {
                        "filename": self.options.uri,
                    })

                    self.emit("discovered", self.info, is_video(self.info) or is_audio(self.info))

                    if is_video(self.info) or is_audio(self.info):
                        try:
                            self._setup_pass()
                        except PipelineException as e:
                            self.emit("error", str(e))
                            return

                        self.start()
                        return

                self.options.uri = fname + "@" + str(title + 1) + ":a:a"
                self.discoverer = discoverer.Discoverer(options.uri)
                self.discoverer.connect("discovered", _got_info)
                self.discoverer.discover()

            self.discoverer = discoverer.Discoverer(options.uri)
            self.discoverer.connect("discovered", _got_info)
            self.discoverer.discover()

        else:
            self.info = None
            self.discoverer = discoverer.Discoverer.new(Gst.SECOND*5)
            self.discoverer.connect("discovered", self.on_got_info)
            self.discoverer.start()
            self.discoverer.discover_uri_async(options.uri)

    @property
    def infile(self):
        """
            Provide access to the input uri for backwards compatibility after
            moving to TranscoderOptions for uri, subtitles, etc.

            @rtype: str
            @return: The input uri to process
        """
        return self.options.uri

    @property
    def preset(self):
        """
            Provide access to the output preset for backwards compatibility
            after moving to TranscoderOptions.

            @rtype: Preset
            @return: The output preset
        """
        return self.options.preset

    def _get_source(self):
        """
            Return a file or dvd source string usable with Gst.parse_launch.

            This method uses self.infile to generate its output.

            @rtype: string
            @return: Source to prepend to Gst-launch style strings.
        """
        if self.infile.startswith("dvd://"):
            parts = self.infile.split("@")
            device = parts[0][6:]
            rest = len(parts) > 1 and parts[1].split(":")

            title = 1
            if rest:
                try:
                    title = int(rest[0])
                except (TypeError, ValueError, IndexError):
                    title = 1
                try:
                    chapter = int(rest[1])
                except (TypeError, ValueError, IndexError):
                    chapter = None

            if self.options.deinterlace is None:
                self.options.deinterlace = True

            return "dvdreadsrc device=\"%s\" title=%d %s ! decodebin2 name=dmux" % (device, title, chapter and "chapter=" + str(chapter) or '')
        elif self.infile.startswith("v4l://") or self.infile.startswith("v4l2://"):
            filename = self.infile
        elif self.infile.startswith("file://"):
            filename = self.infile
        else:
            filename = "file://" + os.path.abspath(self.infile)

        return "uridecodebin uri=\"%s\" name=dmux" % filename

    def _setup_pass(self):
        """
            Setup the pipeline for an encoding pass. This configures the
            GStreamer elements and their setttings for a particular pass.
        """
        # Get limits and setup caps
        self.vcaps = Gst.Caps.new_empty_simple('video/x-raw')

        self.acaps = Gst.Caps.new_empty_simple('audio/x-raw')

        # =====================================================================
        # Setup video, audio/video, or audio transcode pipeline
        # =====================================================================

        # Figure out which mux element to use
        container = None
        if is_video(self.info) and is_audio(self.info):
            container = self.preset.container
        elif is_video(self.info):
            container = self.preset.vcodec.container and \
                        self.preset.vcodec.container or \
                        self.preset.container
        elif is_audio(self.info):
            container = self.preset.acodec.container and \
                        self.preset.acodec.container or \
                        self.preset.container

        mux_str = ""
        if container:
            mux_str = "%s name=mux ! queue !" % container

        # Decide whether or not we are using a muxer and link to it or just
        # the file sink if we aren't (for e.g. mp3 audio)
        if mux_str:
            premux = "mux."
        else:
            premux = "sink."

        src = self._get_source()

        cmd = "%s %s filesink name=sink " \
              "location=\"%s\"" % (src, mux_str, self.options.output_uri)

        if is_video(self.info) and self.preset.vcodec:
            # =================================================================
            # Update limits based on what the encoder really supports
            # =================================================================
            element = Gst.ElementFactory.make(self.preset.vcodec.name,
                                              "videoencoder")

            # TODO: Add rate limits based on encoder sink below
            cap = element.get_static_pad("sink").query_caps()
            struct = cap.get_structure(0)
            for field in ('width', 'height'):
                if struct.has_field(field):
                    range_data = get_range_value(struct, field)
                    vmin, vmax = range_data.start, range_data.stop - 1

                    cur = getattr(self.preset.vcodec, field)
                    if cur[0] < vmin:
                        cur = (vmin, cur[1])
                        setattr(self.preset.vcodec, field, cur)

                    if cur[1] > vmax:
                        cur = (cur[0], vmax)
                        setattr(self.preset.vcodec, field, cur)

            # =================================================================
            # Calculate video width/height, crop and add black bars if necessary
            # =================================================================
            vcrop = ""
            crop = [0, 0, 0, 0]
            if self.options.crop:
                crop = self.options.crop
                vcrop = "videocrop top=%i right=%i bottom=%i left=%i ! "  % \
                        (crop[0], crop[1], crop[2], crop[3])

            wmin, wmax = self.preset.vcodec.width
            hmin, hmax = self.preset.vcodec.height

            video_w, video_h = get_video_dimension(self.info)
            owidth = video_w - crop[1] - crop[3]
            oheight = video_h - crop[0] - crop[2]

            try:
                v_stream = self.info.get_video_streams()[0]
                owidth = int(owidth * v_stream.get_par_num() / v_stream.get_par_denom())
            except KeyError:
                # The videocaps we are looking for may not even exist, just ignore
                v_stream = None

            width, height = owidth, oheight

            # Scale width / height to fit requested min/max
            if owidth < wmin:
                width = wmin
                height = int((float(wmin) / owidth) * oheight)
            elif owidth > wmax:
                width = wmax
                height = int((float(wmax) / owidth) * oheight)

            if height < hmin:
                height = hmin
                width = int((float(hmin) / oheight) * owidth)
            elif height > hmax:
                height = hmax
                width = int((float(hmax) / oheight) * owidth)

            # Add any required padding
            # TODO: Remove the extra colorspace conversion when no longer
            #       needed, but currently xvidenc and possibly others will fail
            #       without it!
            vbox = ""
            if width < wmin and height < hmin:
                wpx = (wmin - width) / 2
                hpx = (hmin - height) / 2
                vbox = "videobox left=%i right=%i top=%i bottom=%i ! videoconvert ! " % \
                       (-wpx, -wpx, -hpx, -hpx)
            elif width < wmin:
                px = (wmin - width) / 2
                vbox = "videobox left=%i right=%i ! videoconvert ! " % \
                       (-px, -px)
            elif height < hmin:
                px = (hmin - height) / 2
                vbox = "videobox top=%i bottom=%i ! videoconvert ! " % \
                       (-px, -px)

            # FIXME Odd widths / heights seem to freeze Gstreamer
            if width % 2:
                width += 1
            if height % 2:
                height += 1

            self.vcaps.set_value('width', width)
            self.vcaps.set_value('height', height)

            # TODO: Set framerate and pixel-aspect-ratio
            # when gir-gstreamer supports

            # =================================================================
            # Setup the video encoder and options
            # =================================================================
            vencoder = "%s %s" % (self.preset.vcodec.name,
                                  self.preset.vcodec.passes[self.enc_pass] % {
                                    "threads": CPU_COUNT,
                                  })

            deint = ""
            if self.options.deinterlace:
                deint = " avdeinterlace ! "

            transform = ""
            if self.preset.vcodec.transform:
                transform = self.preset.vcodec.transform + " ! "

            sub = ""
            if self.options.subfile:
                charset = ""
                if self.options.subfile_charset:
                    charset = "subtitle-encoding=\"%s\"" % \
                                                self.options.subfile_charset

                # Render subtitles onto the video stream
                sub = "textoverlay font-desc=\"%(font)s\" name=txt ! " % {
                    "font": self.options.font,
                }
                cmd += " filesrc location=\"%(subfile)s\" ! subparse " \
                       "%(subfile_charset)s ! txt." % {
                    "subfile": self.options.subfile,
                    "subfile_charset": charset,
                }

            if self.options.ssa is True:
                # Render subtitles onto the video stream
                sub = "textoverlay font-desc=\"%(font)s\" name=txt ! " % {
                    "font": self.options.font,
                }
                cmd += " filesrc location=\"%(infile)s\" ! matroskademux name=demux ! ssaparse ! txt. " % {
                    "infile": self.infile,
                }

            vmux = premux
            if container in ("qtmux", "webmmux", "avmux_dvd", "matroskamux", "mp4mux"):
                if premux.startswith("mux"):
                    vmux += "video_%u"

            cmd += " dmux. ! queue ! videoconvert ! videorate !" \
                   "%s %s %s %s videoscale ! %s ! %s%s ! tee " \
                   "name=videotee ! queue ! %s" % \
                   (deint, vcrop, transform, sub, self.vcaps.to_string(), vbox,
                    vencoder, vmux)

        if is_audio(self.info) and self.preset.acodec and \
           self.enc_pass == len(self.preset.vcodec.passes) - 1:
            # =================================================================
            # Update limits based on what the encoder really supports
            # =================================================================
            element = Gst.ElementFactory.make(self.preset.acodec.name,
                                              'audioencoder')
            # When facc is missing, use avenc_aac and avmux_mp4
            # Ref: https://bugs.launchpad.net/ubuntu/+source/gst-plugins-bad1.0/+bug/1299376
            if element is None and self.preset.acodec.name == 'faac':
                self.preset.acodec.name = 'avenc_aac'
                self.preset.acodec.passes[0] += ' compliance=experimental'
                self.preset.acodec.container = 'mp4mux'
                self.preset.acodec.passes = \
                    remove_param_from_passes(self.preset.acodec.passes, 'profile')
                element = Gst.ElementFactory.make('avenc_aac', 'audioencoder')

            cap = element.get_static_pad("sink").query_caps()
            # Get maximum capable rates and channels which encoder can produce,
            # and make the preset's rates, channels fit in.
            # Note, the value returned from encoder can be a range, or array
            capable_rates = range(0, 0)
            capable_channels = range(0, 0)
            for i in range(cap.get_size()):
                struct = cap.get_structure(i)
                if struct.has_field('rate'):
                    new = get_range_value(struct, 'rate')
                    if not new:
                        new = get_array_value(struct, 'rate')
                    if new:
                        capable_rates = expand_capacity(capable_rates, new)
                        self.preset.acodec.rate = capable_rates
                if struct.has_field('channels'):
                    new = get_range_value(struct, 'channels')
                    if not new:
                        new = get_array_value(struct, 'channels')
                    if new:
                        capable_channels = expand_capacity(capable_channels, new)
                        self.preset.acodec.channels = capable_channels

            # =================================================================
            # Prepare audio capabilities
            # =================================================================
            a_stream = self.info.get_audio_streams()[0]
            self.acaps.set_value('channels', a_stream.get_channels())
            self.acaps.set_value('depth', a_stream.get_depth())
            self.acaps.set_value('rate', a_stream.get_sample_rate())

            # =================================================================
            # Add audio transcoding pipeline to command
            # =================================================================
            aencoder = self.preset.acodec.name + " " + \
                       self.preset.acodec.passes[ \
                            len(self.preset.vcodec.passes) - \
                            self.enc_pass - 1 \
                       ] % {
                            "threads": CPU_COUNT,
                       }

            amux = premux
            if container in ("qtmux", "webmmux", "avmux_dvd", "matroskamux", "mp4mux"):
                if premux.startswith("mux"):
                    amux += "audio_%u"

            cmd += " dmux. ! queue ! audioconvert ! " \
                   "audiorate tolerance=100000000 ! " \
                   "audioresample ! %s ! %s ! %s" % \
                   (self.acaps.to_string(), aencoder, amux)

        # =====================================================================
        # Build the pipeline and get ready!
        # =====================================================================
        self._build_pipeline(cmd)

        self.emit("pass-setup")

    def _build_pipeline(self, cmd):
        """
            Build a Gstreamer pipeline from a given gst-launch style string and
            connect a callback to it to receive messages.

            @type cmd: string
            @param cmd: A gst-launch string to construct a pipeline from.
        """
        _log.debug(cmd)

        try:
            self.pipe = Gst.parse_launch(cmd)
        except GLib.GError as e:
            raise PipelineException(_("Unable to construct pipeline! ") + \
                                    str(e))

        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message)

    def _on_message(self, bus, message):
        """
            Process pipe bus messages, e.g. start new passes and emit signals
            when passes and the entire encode are complete.

            @type bus: object
            @param bus: The session bus
            @type message: object
            @param message: The message that was sent on the bus
        """
        t = message.type
        if t == Gst.MessageType.EOS:
            self.state = Gst.State.NULL
            self.emit("pass-complete")
            if self.enc_pass < self.preset.pass_count - 1:
                self.enc_pass += 1
                self._setup_pass()
                self.start()
            else:
                self.emit("complete")
        elif t == Gst.MessageType.ERROR:
            print(message.parse_error())

        self.emit("message", bus, message)

    def start(self, reset_timer=True):
        """
            Start the pipeline!
        """
        self.state = Gst.State.PLAYING
        if reset_timer:
            self.start_time = time.time()

    def pause(self):
        """
            Pause the pipeline!
        """
        self.state = Gst.State.PAUSED

    def stop(self):
        """
            Stop the pipeline!
        """
        self.state = Gst.State.NULL

    def get_state(self):
        """
            Return the Gstreamer state of the pipeline.

            @rtype: int
            @return: The state of the current pipeline.
        """
        if self.pipe:
            return self.pipe.get_state(Gst.SECOND*2)[1]
        else:
            return None

    def set_state(self, state):
        """
            Set the Gstreamer state of the pipeline.

            @type state: int
            @param state: The state to set, e.g. Gst.State.PLAYING
        """
        if self.pipe:
            self.pipe.set_state(state)

    state = property(get_state, set_state)

    def get_status(self):
        """
            Get information about the status of the encoder, such as the
            percent completed and nicely formatted time remaining.

            Examples

             - 0.14, "00:15" => 14% complete, 15 seconds remaining
             - 0.0, "Unknown" => 0% complete, unknown time remaining

            Raises EncoderStatusException on errors.

            @rtype: tuple
            @return: A tuple of percent, time_rem
        """
        duration = self.info.get_duration()

        if not duration or duration < 0:
            return 0.0, _("Unknown")

        try:
            success, pos = self.pipe.query_position(Gst.Format.TIME)
            if not success:
                raise TranscoderStatusException(_("Can't query position!"))
        except AttributeError:
            raise TranscoderStatusException(_("No pipeline to query!"))

        percent = pos / duration
        if percent <= 0.0:
            return 0.0, _("Unknown")

        if self._percent_cached == percent and time.time() - self._percent_cached_time > 5:
            self.pipe.post_message(Gst.Message.new_eos(self.pipe))

        if self._percent_cached != percent:
            self._percent_cached = percent
            self._percent_cached_time = time.time()

        total = 1.0 / percent * (time.time() - self.start_time)
        rem = total - (time.time() - self.start_time)
        min = rem / 60
        sec = rem % 60

        try:
            time_rem = _("%(min)d:%(sec)02d") % {
                "min": min,
                "sec": sec,
            }
        except TypeError:
            raise TranscoderStatusException(_("Problem calculating time " \
                                              "remaining!"))

        return percent, time_rem

    status = property(get_status)

    def on_got_info(self, disc, info, error):
        self.info = info
        r = GstPbutils.DiscovererInfo.get_result(info)
        is_media = r == GstPbutils.DiscovererResult.OK
        self.emit("discovered", info, is_media)

        if is_video(info) or is_audio(info):
            try:
                self._setup_pass()
            except PipelineException as e:
                self.emit("error", str(e))
                return

            self.start()
