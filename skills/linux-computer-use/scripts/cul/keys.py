"""Key names shared by input backends. Text uses UTF-8 clipboard transfers."""
import ctypes
import ctypes.util

ALIASES = {"ctrl": "Control_L", "control": "Control_L", "alt": "Alt_L",
           "shift": "Shift_L", "super": "Super_L", "meta": "Super_L", "win": "Super_L",
           "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
           "space": "space", "tab": "Tab", "backspace": "BackSpace", "delete": "Delete",
           "up": "Up", "down": "Down", "left": "Left", "right": "Right",
           "home": "Home", "end": "End", "pageup": "Page_Up", "pagedown": "Page_Down",
           "insert": "Insert", "plus": "plus", "minus": "minus"}
CODES = {"Escape": 1, "BackSpace": 14, "Tab": 15, "Return": 28, "Control_L": 29,
         "Shift_L": 42, "Alt_L": 56, "space": 57, "Super_L": 125,
         "Home": 102, "Up": 103, "Page_Up": 104, "Left": 105, "Right": 106,
         "End": 107, "Down": 108, "Page_Down": 109, "Insert": 110, "Delete": 111,
         "minus": 12, "equal": 13, "bracketleft": 26, "bracketright": 27,
         "semicolon": 39, "apostrophe": 40, "grave": 41, "backslash": 43,
         "comma": 51, "period": 52, "slash": 53}
for row, start in (("1234567890", 2), ("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
    CODES.update({char: start + i for i, char in enumerate(row)})
CODES.update({"F" + str(i): 58 + i for i in range(1, 11)})
CODES.update({"F11": 87, "F12": 88})


def names(chord):
    if not isinstance(chord, str) or not chord or len(chord) > 128:
        raise ValueError("A key chord is required (for example Ctrl+Shift+S)")
    parts = chord.split("+")
    if any(not p for p in parts) or len(parts) > 8:
        raise ValueError("Invalid chord; spell the plus key as 'plus'")
    return [ALIASES.get(p.lower(), p.upper() if p.lower().startswith('f') and p[1:].isdigit()
            else p.lower() if len(parts) > 1 and len(p) == 1 and p.isascii() else p) for p in parts]


def keycodes(chord):
    result = []
    for name in names(chord):
        if name == "plus":
            result.extend([42, 13])
        elif len(name) == 1 and name.isupper():
            result.extend([42, CODES[name.lower()]])
        elif name in CODES:
            result.append(CODES[name])
        else:
            raise ValueError(f"Unknown key {name!r}; use type_text for literal text")
    return list(dict.fromkeys(result))


def keysym(name):
    lib = ctypes.CDLL(ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0")
    lib.xkb_keysym_from_name.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.xkb_keysym_from_name.restype = ctypes.c_uint32
    value = lib.xkb_keysym_from_name(name.encode(), 0)
    if not value:
        if len(name) == 1:
            return ord(name) if ord(name) <= 255 else 0x01000000 | ord(name)
        raise ValueError(f"Unknown key {name!r}")
    return value
