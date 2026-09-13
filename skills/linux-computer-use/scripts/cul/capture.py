"""Persistent PipeWire video; bounded on-demand encoding in native libraries."""
import base64
import hashlib
import threading
import time

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
gi.require_version("GstVideo", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gst, GstVideo, GdkPixbuf, GLib

Gst.init(None)


class PipeWireCapture:
    def __init__(self, node, fd=-1):
        self.condition = threading.Condition()
        self.sample = None
        self.sequence = 0
        self.received_at = 0
        self.closed = False
        self.pipeline = Gst.parse_launch(
            f"pipewiresrc fd={fd} path={int(node)} do-timestamp=true keepalive-time=33 min-buffers=3 max-buffers=8 "
            "! videoconvert ! video/x-raw,format=RGB "
            "! appsink name=frames emit-signals=true drop=true max-buffers=1 sync=false")
        self.sink = self.pipeline.get_by_name("frames")
        self.sink.connect("new-sample", self._receive)
        # Some pipewiresrc versions synchronously wait for a session manager
        # while changing state. Bound startup even if the host is misconfigured.
        self.startup = threading.Thread(target=lambda: self.pipeline.set_state(Gst.State.PLAYING),
                                        daemon=True, name="cul-pipewire-start")
        self.startup.start()

    def _receive(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.EOS
        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        buffer = sample.get_buffer()
        frame = (buffer.extract_dup(0, buffer.get_size()), info.width, info.height, info.stride[0])
        with self.condition:
            # Do not retain a PipeWire buffer between calls: on some producers
            # that starves their small buffer pool and freezes the last frame.
            # pipewiresrc keepalive repeats the previous pixels with a new
            # timestamp. Those repeats must not prove an action was repainted.
            if frame != self.sample:
                self.sample = frame
                self.sequence += 1
                self.received_at = time.monotonic()
                self.condition.notify_all()
        return Gst.FlowReturn.OK

    def check(self):
        if self.closed:
            raise RuntimeError("Capture is closed")
        message = self.pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if message:
            if message.type == Gst.MessageType.ERROR:
                error, detail = message.parse_error()
                raise RuntimeError(f"PipeWire capture: {error.message}; {detail}")
            raise RuntimeError("PipeWire stream ended")

    def snapshot(self, max_width=1600, max_height=1000, format="jpeg", quality=85, after=None):
        self.check()
        deadline = time.monotonic() + (5 if self.sample is None else .35)
        with self.condition:
            while self.sample is None or (after and self.received_at < after):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if self.sample is None:
                        self.check()
                        raise TimeoutError("No PipeWire frame received within 5 seconds")
                    break
                self.condition.wait(min(.1, remaining))
            sample, seq, received = self.sample, self.sequence, self.received_at
        data, width, height, stride = sample
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB,
                                               False, 8, width, height, stride)
        return encode(pixbuf, max_width, max_height, format, quality) | {
            "capture_width": width, "capture_height": height,
            "sequence": seq, "age_ms": round((time.monotonic() - received) * 1000, 2),
            "after_action_frame": after is None or received >= after,
        }

    def close(self):
        self.closed = True
        stop = threading.Thread(target=lambda: self.pipeline.set_state(Gst.State.NULL), daemon=True)
        stop.start()
        stop.join(3)
        self.sample = None


def encode(pixbuf, max_width=1600, max_height=1000, format="jpeg", quality=85):
    if format not in ("jpeg", "png"):
        raise ValueError("format must be jpeg or png")
    if not (64 <= max_width <= 4096 and 64 <= max_height <= 4096 and 1 <= quality <= 95):
        raise ValueError("Image dimensions must be 64..4096; JPEG quality 1..95")
    ratio = min(1, max_width / pixbuf.get_width(), max_height / pixbuf.get_height())
    if ratio < 1:
        pixbuf = pixbuf.scale_simple(max(1, round(pixbuf.get_width() * ratio)),
                                    max(1, round(pixbuf.get_height() * ratio)), GdkPixbuf.InterpType.BILINEAR)
    keys, values = (["quality"], [str(quality)]) if format == "jpeg" else (["compression"], ["3"])
    _, data = pixbuf.save_to_bufferv(format, keys, values)
    # A hard transport cap applies to both PNG and JPEG.
    while len(data) > 4 * 1024 * 1024:
        pixbuf = pixbuf.scale_simple(max(1, pixbuf.get_width() * 3 // 4),
                                    max(1, pixbuf.get_height() * 3 // 4), GdkPixbuf.InterpType.BILINEAR)
        _, data = pixbuf.save_to_bufferv(format, keys, values)
    return {"width": pixbuf.get_width(), "height": pixbuf.get_height(),
            "mimeType": "image/" + format, "data": base64.b64encode(data).decode(),
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
