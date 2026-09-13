"""One D-Bus/GLib event pump for the persistent controller."""
import threading
import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

_lock = threading.Lock()
_loop = None
_thread = None
_closed = False


def session_bus():
    global _loop, _thread
    with _lock:
        if _closed:
            raise RuntimeError("This controller's GLib session ended; restart the server")
        if _loop is None:
            DBusGMainLoop(set_as_default=True)
            _loop = GLib.MainLoop()
            _thread = threading.Thread(target=_loop.run, daemon=True, name="cul-dbus")
            _thread.start()
    bus = dbus.SessionBus(private=True)
    # libdbus defaults to _exit(1) on a bus disconnect, even during explicit
    # connection.close(). Let the owner finish releasing its child processes.
    bus.set_exit_on_disconnect(False)
    return bus


def in_event_loop(function):
    if _loop is None or threading.current_thread() is _thread:
        return function()
    done, errors, values = threading.Event(), [], []
    def run():
        try:
            values.append(function())
        except Exception as error:
            errors.append(error)
        finally:
            done.set()
        return False
    GLib.idle_add(run)
    if not done.wait(7):
        raise TimeoutError("GLib operation did not finish within 7 seconds")
    if errors:
        raise errors[0]
    return values[0]


def close_bus(bus):
    in_event_loop(bus.close)


def shutdown(function=None):
    """Finish native cleanup and stop dispatch before owned buses disappear."""
    global _closed
    if _closed or _loop is None:
        return
    def finish():
        try:
            if function:
                function()
        finally:
            _loop.quit()
    try:
        in_event_loop(finish)
    finally:
        if threading.current_thread() is not _thread:
            _thread.join(3)
        _closed = True


def plain(value):
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, dbus.Boolean):
        return bool(value)
    if isinstance(value, (dbus.String, dbus.ObjectPath, dbus.Signature)):
        return str(value)
    if isinstance(value, dbus.Double):
        return float(value)
    if isinstance(value, (dbus.Int16, dbus.Int32, dbus.Int64, dbus.UInt16, dbus.UInt32, dbus.UInt64, dbus.Byte)):
        return int(value)
    return value
