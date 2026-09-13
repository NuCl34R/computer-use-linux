"""Expose only the private accessibility bus to apps in an isolated session."""
import sys
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
name = dbus.service.BusName("org.a11y.Bus", bus, do_not_queue=True)


class BusAddress(dbus.service.Object):
    @dbus.service.method("org.a11y.Bus", out_signature="s")
    def GetAddress(self):
        return sys.argv[1]


class Status(dbus.service.Object):
    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ss", out_signature="v")
    def Get(self, interface, property):
        if interface != "org.a11y.Status" or property not in ("IsEnabled", "ScreenReaderEnabled"):
            raise dbus.exceptions.DBusException("Unknown accessibility property")
        return dbus.Boolean(True)

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return {"IsEnabled": dbus.Boolean(True), "ScreenReaderEnabled": dbus.Boolean(True)} if interface == "org.a11y.Status" else {}


address = BusAddress(bus, "/org/a11y/bus")
status = Status(bus, "/org/a11y/status")
print("ready", flush=True)
GLib.MainLoop().run()
