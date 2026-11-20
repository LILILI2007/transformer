"""Windows Generic Credential for the authorized tests; no plaintext files."""
import ctypes
from ctypes import wintypes
import sys

TARGET='ModLingo/DeepSeek/20261017-tests'

class Credential(ctypes.Structure):
    _fields_=[('Flags',wintypes.DWORD),('Type',wintypes.DWORD),('TargetName',wintypes.LPWSTR),
              ('Comment',wintypes.LPWSTR),('LastWritten',wintypes.FILETIME),('CredentialBlobSize',wintypes.DWORD),
              ('CredentialBlob',ctypes.POINTER(ctypes.c_ubyte)),('Persist',wintypes.DWORD),
              ('AttributeCount',wintypes.DWORD),('Attributes',ctypes.c_void_p),('TargetAlias',wintypes.LPWSTR),('UserName',wintypes.LPWSTR)]

def library():
    if sys.platform!='win32':raise RuntimeError('本次凭据仅支持 Windows Credential Manager')
    api=ctypes.WinDLL('advapi32',use_last_error=True)
    api.CredWriteW.argtypes=[ctypes.POINTER(Credential),wintypes.DWORD];api.CredWriteW.restype=wintypes.BOOL
    api.CredReadW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ctypes.POINTER(Credential))];api.CredReadW.restype=wintypes.BOOL
    api.CredDeleteW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD];api.CredDeleteW.restype=wintypes.BOOL
    api.CredFree.argtypes=[ctypes.c_void_p]
    return api

def store(secret):
    if not secret.startswith('sk-') or any(c.isspace() for c in secret):raise ValueError('密钥格式无效')
    data=secret.encode('utf-16-le');blob=(ctypes.c_ubyte*len(data)).from_buffer_copy(data)
    record=Credential();record.Type=1;record.TargetName=TARGET;record.Persist=2
    record.UserName='authorized-test';record.CredentialBlobSize=len(data);record.CredentialBlob=blob
    if not library().CredWriteW(ctypes.byref(record),0):raise ctypes.WinError(ctypes.get_last_error())

def load():
    api=library();pointer=ctypes.POINTER(Credential)()
    if not api.CredReadW(TARGET,1,0,ctypes.byref(pointer)):raise RuntimeError('专用测试凭据不存在；未发送 API 请求')
    try:return ctypes.string_at(pointer.contents.CredentialBlob,pointer.contents.CredentialBlobSize).decode('utf-16-le')
    finally:api.CredFree(pointer)

def clear():
    api=library()
    if not api.CredDeleteW(TARGET,1,0) and ctypes.get_last_error()!=1168:raise ctypes.WinError(ctypes.get_last_error())
