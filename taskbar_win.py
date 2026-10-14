"""Windows taskbar identity and relaunch properties, using only ctypes.

Call set_process_app_id before creating Tk windows. Pass the native outer
top-level HWND to configure_window, and clear_window before destroying it.

Microsoft references:
https://learn.microsoft.com/windows/win32/api/shellapi/nf-shellapi-shgetpropertystoreforwindow
https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-shgetpropertystorefromparsingname
https://learn.microsoft.com/windows/win32/properties/props-system-appusermodel-relaunchiconresource
"""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
from uuid import UUID


class TaskbarIntegrationError(RuntimeError):
    """A Windows API failed while configuring taskbar identity."""


class WindowIcons:
    """Own native small/large icons and explicitly apply them to each HWND."""

    def __init__(self, icon_path):
        if sys.platform != "win32":
            raise TaskbarIntegrationError("Native window icons require Windows.")
        icon = Path(icon_path).resolve(strict=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                       wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user.LoadImageW.restype = wintypes.HANDLE
        self.user.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.user.GetSystemMetrics.restype = ctypes.c_int
        self.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                         wintypes.WPARAM, wintypes.LPARAM]
        self.user.SendMessageW.restype = wintypes.LPARAM
        self.user.IsWindow.argtypes = [wintypes.HWND]
        self.user.IsWindow.restype = wintypes.BOOL
        self.user.DestroyIcon.argtypes = [wintypes.HICON]
        self.user.DestroyIcon.restype = wintypes.BOOL
        self.handles = []
        try:
            for width_metric, height_metric in ((49, 50), (11, 12)):
                width = self.user.GetSystemMetrics(width_metric)
                height = self.user.GetSystemMetrics(height_metric)
                handle = self.user.LoadImageW(None, str(icon), 1, width, height, 0x10)
                if not handle:
                    raise TaskbarIntegrationError(f"LoadImageW failed: {ctypes.WinError()}")
                self.handles.append(handle)
        except Exception:
            self.close()
            raise

    def apply(self, hwnd):
        if len(self.handles) != 2 or not self.user.IsWindow(hwnd):
            raise TaskbarIntegrationError("Cannot apply icons to an invalid or closed window.")
        for size, handle in enumerate(self.handles):
            self.user.SendMessageW(hwnd, 0x80, size, handle)  # WM_SETICON
            if self.user.SendMessageW(hwnd, 0x7F, size, 0) != handle:  # WM_GETICON
                raise TaskbarIntegrationError("Windows did not retain the assigned window icon.")

    def close(self):
        # Call only after all windows that reference these icons are destroyed.
        for handle in self.handles:
            self.user.DestroyIcon(handle)
        self.handles.clear()


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value):
        return cls.from_buffer_copy(UUID(value).bytes_le)


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]


class _Blob(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("pBlobData", ctypes.c_void_p)]


class _PropValue(ctypes.Union):
    # BLOB makes the union 16 bytes on Win64 and 8 bytes on Win32. DOUBLE
    # preserves native alignment. Only VT_EMPTY and VT_LPWSTR are used here.
    _fields_ = [("pwszVal", ctypes.c_void_p), ("blob", _Blob), ("dblVal", ctypes.c_double)]


class PROPVARIANT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [
        ("vt", ctypes.c_uint16),
        ("wReserved1", ctypes.c_uint16),
        ("wReserved2", ctypes.c_uint16),
        ("wReserved3", ctypes.c_uint16),
        ("value", _PropValue),
    ]


_FMTID = GUID.from_string("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3")
_IID_IPROPERTY_STORE = GUID.from_string("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")
_KEYS = {pid: PROPERTYKEY(_FMTID, pid) for pid in (2, 3, 4, 5)}
_HRESULT = ctypes.c_int32
_VT_EMPTY = 0
_VT_LPWSTR = 31
_GPS_READWRITE = 2
_RPC_E_CHANGED_MODE = 0x80010106
_native_instance = None


def _check_hresult(result, operation):
    if result < 0:
        code = result & 0xFFFFFFFF
        description = ctypes.FormatError(code).strip() if sys.platform == "win32" else ""
        detail = f": {description}" if description else ""
        raise TaskbarIntegrationError(f"{operation} failed (HRESULT 0x{code:08X}){detail}")


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{label} must be a nonempty string without embedded NULs.")
    return value


def _app_id(value):
    value = _text(value, "AppUserModelID")
    if len(value) > 128 or any(character.isspace() for character in value):
        raise ValueError("AppUserModelID must be at most 128 characters without spaces.")
    return value


class _Native:
    def __init__(self):
        if sys.platform != "win32":
            raise TaskbarIntegrationError("Taskbar integration requires Windows.")
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.ole = ctypes.WinDLL("ole32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)

        self.shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
        self.shell.SetCurrentProcessExplicitAppUserModelID.restype = _HRESULT
        self.shell.SHGetPropertyStoreForWindow.argtypes = [
            wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
        ]
        self.shell.SHGetPropertyStoreForWindow.restype = _HRESULT
        self.shell.SHGetPropertyStoreFromParsingName.argtypes = [
            ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
        ]
        self.shell.SHGetPropertyStoreFromParsingName.restype = _HRESULT
        self.ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.ole.CoInitializeEx.restype = _HRESULT
        self.ole.CoUninitialize.argtypes = []
        self.ole.CoUninitialize.restype = None
        self.ole.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]
        self.ole.CoTaskMemAlloc.restype = ctypes.c_void_p
        self.ole.PropVariantClear.argtypes = [ctypes.POINTER(PROPVARIANT)]
        self.ole.PropVariantClear.restype = _HRESULT
        self.user.IsWindow.argtypes = [wintypes.HWND]
        self.user.IsWindow.restype = wintypes.BOOL


def _native():
    global _native_instance
    if _native_instance is None:
        _native_instance = _Native()
    return _native_instance


@contextmanager
def _com_apartment():
    native = _native()
    result = native.ole.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    already_other_apartment = (result & 0xFFFFFFFF) == _RPC_E_CHANGED_MODE
    if not already_other_apartment:
        _check_hresult(result, "CoInitializeEx")
    try:
        yield native
    finally:
        # S_FALSE still increments COM's initialization count. CHANGED_MODE does not.
        if not already_other_apartment:
            native.ole.CoUninitialize()


class _PropertyStore:
    def __init__(self, pointer, native):
        if not pointer.value:
            raise TaskbarIntegrationError("Windows returned a null IPropertyStore.")
        self.pointer = pointer
        self.native = native
        self.vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

    def _method(self, index, result_type, *arguments):
        return ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *arguments)(self.vtable[index])

    def set_string(self, pid, text):
        value = PROPVARIANT()
        try:
            if text is not None:
                buffer = ctypes.create_unicode_buffer(text)
                allocation = self.native.ole.CoTaskMemAlloc(ctypes.sizeof(buffer))
                if not allocation:
                    raise TaskbarIntegrationError("CoTaskMemAlloc could not allocate a property string.")
                ctypes.memmove(allocation, buffer, ctypes.sizeof(buffer))
                value.vt = _VT_LPWSTR
                value.pwszVal = allocation
            setter = self._method(6, _HRESULT, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))
            _check_hresult(setter(self.pointer, ctypes.byref(_KEYS[pid]), ctypes.byref(value)),
                           f"IPropertyStore.SetValue(pid={pid})")
        finally:
            _check_hresult(self.native.ole.PropVariantClear(ctypes.byref(value)), "PropVariantClear")

    def get_string(self, pid):
        """Internal readback for integration checks."""
        value = PROPVARIANT()
        try:
            getter = self._method(5, _HRESULT, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))
            _check_hresult(getter(self.pointer, ctypes.byref(_KEYS[pid]), ctypes.byref(value)),
                           f"IPropertyStore.GetValue(pid={pid})")
            if value.vt == _VT_EMPTY:
                return None
            if value.vt != _VT_LPWSTR:
                raise TaskbarIntegrationError(f"Property pid={pid} has unexpected type VT_{value.vt}.")
            return ctypes.wstring_at(value.pwszVal) if value.pwszVal else ""
        finally:
            _check_hresult(self.native.ole.PropVariantClear(ctypes.byref(value)), "PropVariantClear")

    def commit(self):
        _check_hresult(self._method(7, _HRESULT)(self.pointer), "IPropertyStore.Commit")

    def release(self):
        if self.pointer.value:
            self._method(2, ctypes.c_uint32)(self.pointer)
            self.pointer = ctypes.c_void_p()


@contextmanager
def _window_store(hwnd):
    if not isinstance(hwnd, int) or isinstance(hwnd, bool) or hwnd <= 0:
        raise ValueError("HWND must be a positive integer native window handle.")
    with _com_apartment() as native:
        if not native.user.IsWindow(hwnd):
            raise TaskbarIntegrationError(f"HWND 0x{hwnd:X} is not a valid Windows window.")
        pointer = ctypes.c_void_p()
        _check_hresult(native.shell.SHGetPropertyStoreForWindow(
            hwnd, ctypes.byref(_IID_IPROPERTY_STORE), ctypes.byref(pointer)),
            "SHGetPropertyStoreForWindow")
        store = _PropertyStore(pointer, native)
        try:
            yield store
        finally:
            store.release()


@contextmanager
def _shortcut_store(shortcut_path, writable=False):
    shortcut = Path(shortcut_path).resolve(strict=True)
    if not shortcut.is_file() or shortcut.suffix.lower() != ".lnk":
        raise ValueError("Shortcut path must name an existing .lnk file.")
    with _com_apartment() as native:
        pointer = ctypes.c_void_p()
        _check_hresult(native.shell.SHGetPropertyStoreFromParsingName(
            str(shortcut), None, _GPS_READWRITE if writable else 0,
            ctypes.byref(_IID_IPROPERTY_STORE), ctypes.byref(pointer)),
            "SHGetPropertyStoreFromParsingName")
        store = _PropertyStore(pointer, native)
        try:
            yield store
        finally:
            store.release()


def set_process_app_id(app_id: str) -> None:
    """Set explicit process identity before creating any UI; raise on failure."""
    app_id = _app_id(app_id)
    _check_hresult(_native().shell.SetCurrentProcessExplicitAppUserModelID(app_id),
                   "SetCurrentProcessExplicitAppUserModelID")


def configure_window(hwnd: int, app_id: str, icon_path, relaunch_command: str,
                     display_name: str) -> None:
    """Set taskbar identity, icon resource, and a complete relaunch command.

    hwnd must be Tk's native outer top-level window, not its inner client HWND.
    Build relaunch_command with subprocess.list2cmdline([pythonw, script_path])
    so paths with spaces are quoted correctly. icon_path must be an existing ICO
    or an executable with an embedded icon (for durable packaged-app pinning).
    This metadata complements Tk iconphoto/iconbitmap; it does not set WM_SETICON.
    """
    app_id = _app_id(app_id)
    relaunch_command = _text(relaunch_command, "Relaunch command")
    display_name = _text(display_name, "Display name")
    icon = Path(icon_path).resolve(strict=True)
    if not icon.is_file() or icon.suffix.lower() not in (".ico", ".exe"):
        raise ValueError("Icon path must name an existing .ico or .exe file.")
    with _window_store(hwnd) as store:
        # Install the complete relaunch metadata before publishing the identity.
        for pid, value in ((2, relaunch_command), (3, f"{icon},0"),
                           (4, display_name), (5, app_id)):
            store.set_string(pid, value)
        # Window properties take effect immediately; no Commit is needed.


def clear_window(hwnd: int) -> None:
    """Release all four properties before the native window is destroyed."""
    with _window_store(hwnd) as store:
        for pid in (5, 2, 3, 4):
            store.set_string(pid, None)


def configure_shortcut(shortcut_path, app_id: str) -> None:
    """Persist matching taskbar identity on an existing desktop/start-menu link.

    Preserve its target, arguments, icon, and all unrelated link properties.
    This configures identity only; it does not pin the shortcut automatically.
    """
    app_id = _app_id(app_id)
    with _shortcut_store(shortcut_path, writable=True) as store:
        store.set_string(5, app_id)
        store.commit()
