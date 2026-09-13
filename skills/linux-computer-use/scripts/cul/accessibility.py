"""Bounded AT-SPI snapshots and semantic actions, independent of the DE."""
import secrets
import time
import functools
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi
from .dbusutil import in_event_loop


def event_thread(function):
    @functools.wraps(function)
    def call(*args, **kwargs):
        return in_event_loop(lambda: function(*args, **kwargs))
    return call


class Accessibility:
    @event_thread
    def close(self):
        self.elements.clear()
        Atspi.exit()

    @event_thread
    def __init__(self):
        Atspi.set_timeout(350, 800)
        Atspi.init()
        self.elements = {}
        self.snapshot_id = None
        self.created = 0

    @event_thread
    def apps(self):
        desktop = Atspi.get_desktop(0)
        apps = []
        if desktop:
            for index in range(min(200, desktop.get_child_count())):
                try:
                    app = desktop.get_child_at_index(index)
                    apps.append({"name": app.get_name(), "pid": app.get_process_id()})
                except Exception:
                    continue
        return apps

    @event_thread
    def snapshot(self, pid=None, max_nodes=350, max_depth=16, budget_ms=2000):
        if not 1 <= max_nodes <= 1000 or not 1 <= max_depth <= 30 or not 100 <= budget_ms <= 5000:
            raise ValueError("Invalid accessibility traversal limits")
        started = time.monotonic()
        deadline = started + budget_ms / 1000
        self.snapshot_id = secrets.token_hex(8)
        self.created = started
        self.elements = {}
        nodes, errors = [], []
        desktop = Atspi.get_desktop(0)
        queue = [(desktop, 0, None)] if desktop else []
        truncated = False
        while queue:
            if len(nodes) >= max_nodes or time.monotonic() >= deadline:
                truncated = True
                break
            obj, depth, parent = queue.pop()
            if obj is None:
                continue
            try:
                role = obj.get_role_name()
                app_pid = obj.get_process_id()
                if depth == 1 and pid is not None and app_pid != pid:
                    continue
                states = obj.get_state_set()
                if states.contains(Atspi.StateType.DEFUNCT):
                    continue
                name = obj.get_name() or ""
                token = f"{self.snapshot_id}:{len(nodes)}"
                node = {"token": token, "parent": parent, "depth": depth, "role": role,
                        "name": name[:500], "pid": app_pid}
                flags = []
                for label, flag in (("focused", Atspi.StateType.FOCUSED), ("editable", Atspi.StateType.EDITABLE),
                                    ("enabled", Atspi.StateType.ENABLED), ("showing", Atspi.StateType.SHOWING),
                                    ("checked", Atspi.StateType.CHECKED), ("selected", Atspi.StateType.SELECTED)):
                    if states.contains(flag):
                        flags.append(label)
                node["states"] = flags
                interfaces = set(obj.get_interfaces() or [])
                if "Action" in interfaces:
                    action = obj.get_action_iface()
                    node["actions"] = [action.get_action_name(i) for i in range(min(action.get_n_actions(), 12))]
                if "Component" in interfaces:
                    rect = obj.get_component_iface().get_extents(Atspi.CoordType.SCREEN)
                    if rect.width > 0 and rect.height > 0 and rect.x > -100000 and rect.y > -100000:
                        node["bounds"] = {"x": rect.x, "y": rect.y, "width": rect.width, "height": rect.height}
                        node["bounds_source"] = "atspi (compositor may report window-relative coordinates on Wayland)"
                if "Text" in interfaces and "password" not in role.lower():
                    text = obj.get_text_iface()
                    count = text.get_character_count()
                    # Accessible.get_text() and Text.get_text(start,end) collide
                    # in PyGObject's inherited namespace; call the interface.
                    node["text"] = Atspi.Text.get_text(text, 0, min(count, 1200))
                    if count > 1200:
                        node["text_truncated"] = True
                self.elements[token] = (obj, role, name, app_pid)
                nodes.append(node)
                count = obj.get_child_count()
                if depth < max_depth:
                    for i in reversed(range(min(count, max_nodes))):
                        queue.append((obj.get_child_at_index(i), depth + 1, token))
                elif count:
                    truncated = True
            except Exception as error:
                errors.append(str(error)[:200])
        return {"snapshot_id": self.snapshot_id, "nodes": nodes, "truncated": truncated,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2), "errors": errors[:10]}

    @event_thread
    def resolve(self, token):
        if token not in self.elements or time.monotonic() - self.created > 60:
            raise ValueError("Expired element token; get a fresh state before acting")
        obj, role, name, pid = self.elements[token]
        if (obj.get_role_name(), obj.get_name() or "", obj.get_process_id()) != (role, name, pid):
            raise ValueError("Element changed since observation; get a fresh state")
        if obj.get_state_set().contains(Atspi.StateType.DEFUNCT):
            raise ValueError("Element no longer exists; get a fresh state")
        return obj

    @event_thread
    def perform_action(self, token, action=None):
        obj = self.resolve(token)
        iface = obj.get_action_iface()
        if iface is None:
            raise ValueError("Element exposes no semantic action")
        names = [iface.get_action_name(i) for i in range(iface.get_n_actions())]
        if not names:
            raise ValueError("Element exposes no semantic action")
        index = 0 if action is None else next((i for i, name in enumerate(names) if name.lower() == action.lower()), -1)
        if index < 0:
            raise ValueError(f"Available actions: {names}")
        if not iface.do_action(index):
            raise RuntimeError("Application declined the accessibility action")
        self.elements.clear()
        return {"action": names[index]}

    @event_thread
    def set_value(self, token, value):
        obj = self.resolve(token)
        if isinstance(value, str):
            iface = obj.get_editable_text_iface()
            if iface is None or not iface.set_text_contents(value):
                raise RuntimeError("Application declined editable text; use focused keyboard input")
        else:
            iface = obj.get_value_iface()
            if iface is None or not iface.set_current_value(float(value)):
                raise RuntimeError("Application declined the requested value")
        self.elements.clear()
        return {"set": True}

    @event_thread
    def focus(self, token):
        obj = self.resolve(token)
        component = obj.get_component_iface()
        if component is None or not component.grab_focus():
            raise RuntimeError("Application declined focus")
        return {"focused": True}
