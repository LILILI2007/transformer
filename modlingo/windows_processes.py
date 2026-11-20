"""Fresh Windows process snapshot without launching tasklist on every write."""
import ctypes
from ctypes import wintypes


def process_names():
    class Entry(ctypes.Structure):
        _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),
                  ('th32ProcessID',wintypes.DWORD),('th32DefaultHeapID',ctypes.c_size_t),
                  ('th32ModuleID',wintypes.DWORD),('cntThreads',wintypes.DWORD),
                  ('th32ParentProcessID',wintypes.DWORD),('pcPriClassBase',wintypes.LONG),
                  ('dwFlags',wintypes.DWORD),('szExeFile',wintypes.WCHAR*260)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    snapshot=kernel.CreateToolhelp32Snapshot
    snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD];snapshot.restype=wintypes.HANDLE
    first=kernel.Process32FirstW;next_entry=kernel.Process32NextW
    for function in (first,next_entry):
        function.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)];function.restype=wintypes.BOOL
    close=kernel.CloseHandle;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
    handle=snapshot(2,0)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry=Entry();entry.dwSize=ctypes.sizeof(entry)
        if not first(handle,ctypes.byref(entry)):raise ctypes.WinError(ctypes.get_last_error())
        names=set()
        while True:
            names.add(entry.szExeFile.casefold())
            if not next_entry(handle,ctypes.byref(entry)):
                if ctypes.get_last_error()!=18:raise ctypes.WinError(ctypes.get_last_error())
                return names
    finally:close(handle)
