"""Windows Restart Manager只读查询，不关闭应用或结束进程。"""
import ctypes
from ctypes import wintypes
import os


def require_unoccupied(paths):
    if not paths:
        return
    if os.name != 'nt':
        raise OSError('当前自动清理仅支持Windows占用检查，其他系统保持保护')
    class Unique(ctypes.Structure):
        _fields_ = [('pid', wintypes.DWORD), ('started', wintypes.FILETIME)]
    class Info(ctypes.Structure):
        _fields_ = [('process', Unique), ('app', wintypes.WCHAR*256), ('service', wintypes.WCHAR*64),
                    ('kind', ctypes.c_int), ('status', wintypes.ULONG), ('session', wintypes.DWORD), ('restartable', wintypes.BOOL)]
    rm = ctypes.WinDLL('Rstrtmgr')
    rm.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR]
    rm.RmRegisterResources.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR), wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p]
    rm.RmGetList.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT), ctypes.POINTER(Info), ctypes.POINTER(wintypes.DWORD)]
    rm.RmEndSession.argtypes = [wintypes.DWORD]
    session = wintypes.DWORD()
    if rm.RmStartSession(ctypes.byref(session), 0, ctypes.create_unicode_buffer(33)):
        raise OSError('无法启动文件占用检查')
    try:
        for start in range(0, len(paths), 256):
            chunk = [str(p) for p in paths[start:start+256]]
            names = (wintypes.LPCWSTR*len(chunk))(*chunk)
            code = rm.RmRegisterResources(session, len(chunk), names, 0, None, 0, None)
            if code:
                raise OSError('无法登记文件占用检查：'+str(code))
        needed, count, reasons = wintypes.UINT(), wintypes.UINT(), wintypes.DWORD()
        code = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
        if code:
            raise OSError('存在文件占用或占用查询异常，保持保护：'+str(code))
    finally:
        rm.RmEndSession(session)
