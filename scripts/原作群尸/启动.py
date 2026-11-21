"""复用或隐藏启动原作群尸独立服务，不接管其他版本端口。"""
import argparse,json,os,subprocess,sys,threading,time
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/原作群尸/V001-01a1aa48'
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--start',action='store_true');parser.add_argument('--defense',action='store_true');args=parser.parse_args()
    port=8798 if args.defense else 8797;app='scavenger-native-defense' if args.defense else 'scavenger-native-crowd'
    def status():
        try:
            with urlopen(f'http://127.0.0.1:{port}/api/status',timeout=1) as response:return json.load(response)
        except OSError:return None
    current=status()
    if current and current.get('app')!=app:raise RuntimeError('端口属于其他应用，未接管')
    if not current:
        OUT.mkdir(parents=True,exist_ok=True);script=Path(__file__).with_name('防线服务.py' if args.defense else '服务.py');command=[sys.executable,'-X','utf8','-B',str(script)]
        if args.defense:command.append('--serve')
        with (OUT/f'启动-{port}.log').open('ab') as stream:subprocess.Popen(command,cwd=ROOT,stdout=stream,stderr=stream,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        deadline=time.time()+30;event=threading.Event()
        while time.time()<deadline and not status():event.wait(.15)
        if not status():raise RuntimeError('原作运行服务启动失败')
    url=f'http://127.0.0.1:{port}/'+('?mode=defense' if args.defense else '?family=native');print(url,flush=True)
    if not args.start:
        import webbrowser
        webbrowser.open(url)
if __name__=='__main__':main()
