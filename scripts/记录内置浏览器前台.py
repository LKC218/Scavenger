"""只读记录内置浏览器宿主是否处于Windows前台，不激活、不最小化任何窗口。"""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'local-data/丧尸模型/性能测试/ZOM-PERF-261103-001/浏览器验证/IAB前台状态.jsonl'


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--seconds',type=float,default=1800)
    parser.add_argument('--start-at',type=float,default=0,help='可选的系统时间戳，用于接续观察');args=parser.parse_args()
    event=threading.Event()
    while time.time()<args.start_at:event.wait(min(30,args.start_at-time.time()))
    u=ctypes.windll.user32;k=ctypes.windll.kernel32
    u.GetForegroundWindow.restype=wintypes.HWND
    u.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];k.OpenProcess.restype=wintypes.HANDLE
    k.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.argtypes=[wintypes.HANDLE]
    def host_front():
        pid=wintypes.DWORD();u.GetWindowThreadProcessId(u.GetForegroundWindow(),ctypes.byref(pid))
        handle=k.OpenProcess(0x1000,False,pid.value)
        if not handle:return False
        try:
            buf=ctypes.create_unicode_buffer(2048);size=wintypes.DWORD(len(buf))
            return bool(k.QueryFullProcessImageNameW(handle,0,buf,ctypes.byref(size))) and Path(buf.value).name.casefold()=='chatgpt.exe'
        finally:k.CloseHandle(handle)
    OUT.parent.mkdir(parents=True,exist_ok=True);deadline=time.monotonic()+args.seconds
    with OUT.open('a',encoding='utf-8',buffering=1) as stream:
        while time.monotonic()<deadline:
            stream.write(json.dumps({'epoch':time.time(),'hostForeground':host_front()})+'\n')
            event.wait(min(.5,max(0,deadline-time.monotonic())))


if __name__=='__main__':main()
