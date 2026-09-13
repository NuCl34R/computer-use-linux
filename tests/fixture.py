"""Real GTK UI used by E2E tests. State is written only by GUI callbacks."""
import json
import os
import sys
import gi
# Debian/Ubuntu split this GI bridge from python3-cairo. Fail before reporting readiness.
gi.require_foreign("cairo")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

destination = sys.argv[1]
state = {"text": "", "clicks": 0, "scroll": 0, "drag": None, "pointer_down": False, "keys": [], "ready": False}


def record(**values):
    state.update(values)
    tmp = destination + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, destination)


win = Gtk.Window(title="Linux Computer Use — E2E")
win.set_default_size(900, 680)
win.connect("destroy", Gtk.main_quit)
win.connect("key-press-event", lambda w, e: record(keys=(state["keys"] + [Gdk.keyval_name(e.keyval)])[-20:]))
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
box.set_border_width(24)
win.add(box)
title = Gtk.Label(label="Linux Computer Use · Native GTK test")
title.set_markup('<span size="xx-large" weight="bold">Linux Computer Use</span>')
box.pack_start(title, False, False, 0)
box.pack_start(Gtk.Label(label="Real input • Unicode • Clicks • Scroll • Drag"), False, False, 0)
entry = Gtk.Entry()
entry.set_placeholder_text("Type the Unicode test here")
entry.get_accessible().set_name("Unicode test entry")
entry.connect("changed", lambda w: record(text=w.get_text()))
box.pack_start(entry, False, False, 0)
button = Gtk.Button(label="Verify click")
button.set_name("verify")
css = Gtk.CssProvider()
css.load_from_data(b"button#verify { background-image:none; background-color:#315fa8; color:white; }")
Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
button.get_accessible().set_name("Verify click")


def clicked(w):
    record(clicks=state["clicks"] + 1)
    w.set_label(f"Click verified: {state['clicks']}")


button.connect("clicked", clicked)
box.pack_start(button, False, False, 0)
canvas = Gtk.DrawingArea()
canvas.set_size_request(600, 160)
canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
start = []


def draw(w, cr):
    cr.set_source_rgb(.08, .13, .20)
    cr.paint()
    cr.set_source_rgb(.2, .8, .65)
    cr.rectangle(40, 45, 80, 70)
    cr.fill()
    cr.set_source_rgb(.9, .65, .2)
    cr.rectangle(380, 45, 120, 70)
    cr.fill()
    cr.set_source_rgb(1, 1, 1)
    cr.set_font_size(20)
    cr.move_to(145, 88)
    cr.show_text("Drag green to yellow")


canvas.connect("draw", draw)
canvas.connect("button-press-event", lambda w, e: (start.append([e.x, e.y]), record(pointer_down=True), False)[-1])
canvas.connect("button-release-event", lambda w, e: record(pointer_down=False, drag={"start": start[-1], "end": [e.x, e.y]}) if start else None)
box.pack_start(canvas, False, False, 0)
scroll = Gtk.ScrolledWindow()
scroll.set_min_content_height(200)
items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
for i in range(60):
    items.pack_start(Gtk.Label(label=f"Scrollable row {i + 1:02d}"), False, False, 0)
scroll.add(items)
scroll.get_vadjustment().connect("value-changed", lambda a: record(scroll=a.get_value()))
box.pack_start(scroll, True, True, 0)
win.show_all()
entry.grab_focus()
GLib.timeout_add(200, lambda: (record(ready=True), False)[1])
Gtk.main()
