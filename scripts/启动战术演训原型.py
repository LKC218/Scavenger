"""按冻结入口配置启动或复用8784战术演训原型，不接管其他应用或修改历史源码。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
import uuid
import webbrowser

ROOT=Path(__file__).resolve().parents[1]
HOME=ROOT/'local-data/原型/战术演训原型'
CONFIG=HOME/'当前版本.json'
URL='http://127.0.0.1:8784/'


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def configuration():
    data=json.loads(CONFIG.read_text('utf-8'))
    if data['schema']!=1 or data['name']!='战术演训原型' or data['port']!=8784:raise ValueError('原型入口配置身份无效')
    if not re.fullmatch('[a-z0-9-]+',data['service_revision_prefix']):raise ValueError('服务修订前缀无效')
    directory=(ROOT/data['directory']).resolve();directory.relative_to(ROOT/'local-data/断箭/批次')
    source=(ROOT/data['source_override']).resolve();source.relative_to(directory.parents[1]/'载具候选源码')
    if digest(directory/'地图运行.json')!=data['map_sha256']:raise ValueError('地图清单摘要变化')
    if digest(ROOT/'scripts/断箭/预览地图.py')!=data['server_program_sha256']:raise ValueError('预览服务程序变化，须先更新入口验证')
    pin=json.loads((ROOT/'local-data/tools/three/当前版本.json').read_text('utf-8'))
    if data['vendor_prefix']!='/vendor/'+pin['metadata_sha256']+'/':raise ValueError('固定Three.js版本身份变化')
    for name,expected in data['source_files'].items():
        if Path(name).name!=name or digest(source/name)!=expected:raise ValueError('冻结原型源码变化：'+name)
    return data,directory,source


def status():
    try:
        with urlopen(URL+'api/status',timeout=1) as response:return json.load(response)
    except (OSError,URLError):return None


def verify_service(identity,config):
    if (identity.get('app')!='scavenger-airbase-map' or identity.get('port')!=8784 or
        identity.get('directory')!=config['directory'] or identity.get('map_sha256')!=config['map_sha256'] or
        not identity.get('revision','').startswith(config['service_revision_prefix']+'-') or
        identity.get('transport',{}).get('program_sha256')!=config['server_program_sha256'] or
        identity.get('transport',{}).get('vendor_prefix')!=config['vendor_prefix']):
        raise RuntimeError('8784已由其他应用或原型版本占用，未停止或接管；请核对服务身份')
    snapshot=ROOT/config['directory']/('浏览器源码-'+identity['revision'])/'服务身份.json'
    sources=json.loads(snapshot.read_text('utf-8'))['sources']
    for name in ['index.html','入口.mjs']:
        if sources[name]['source_sha256']!=config['source_files'][name] or not sources[name]['source'].startswith(config['source_override']+'/'):
            raise RuntimeError('运行服务未使用当前命名副本')
    with urlopen(URL,timeout=5) as response:html=response.read()
    expected=(ROOT/config['source_override']/'index.html').read_bytes().replace(b'/vendor/',config['vendor_prefix'].encode())
    if html!=expected or hashlib.sha256(html).hexdigest()!=sources['index.html']['http_sha256']:
        raise RuntimeError('实际页面与当前命名副本的版本化HTTP内容不同')
    return identity


def start():
    config,directory,source=configuration()
    current=status()
    if current:return verify_service(current,config)
    HOME.mkdir(parents=True,exist_ok=True);lock=HOME/'启动锁'
    deadline=time.time()+90;event=threading.Event()
    while True:
        try:
            with lock.open('x',encoding='utf-8') as stream:stream.write(str(os.getpid()))
            break
        except FileExistsError:
            current=status()
            if current:return verify_service(current,config)
            if time.time()>=deadline:raise RuntimeError('已有启动任务或遗留启动锁，保留现场；请先核对锁内进程')
            event.wait(.15)
    try:
        current=status()
        if current:return verify_service(current,config)
        revision=config['service_revision_prefix']+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6]
        log=HOME/('服务-'+revision+'.log')
        command=[sys.executable,'-X','utf8','-B',str(ROOT/'scripts/断箭/预览地图.py'),
            '--directory',str(directory),'--port','8784','--revision',revision,'--source-override',str(source)]
        print('正在启动战术演训原型…',flush=True)
        with log.open('xb') as stream:
            process=subprocess.Popen(command,cwd=ROOT,stdout=stream,stderr=stream,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        while time.time()<deadline:
            current=status()
            if current:
                identity=verify_service(current,config)
                (HOME/'服务进程.json').write_text(json.dumps({**identity,'config_sha256':digest(CONFIG),'log':log.relative_to(ROOT).as_posix()},ensure_ascii=False,indent=2),encoding='utf-8')
                return identity
            if process.poll() is not None:raise RuntimeError('原型服务未能启动，请查看：'+str(log))
            event.wait(.15)
        raise RuntimeError('原型启动超时，未停止进程；请查看服务日志后处理，避免重复启动')
    finally:
        if lock.exists() and lock.read_text('utf-8')==str(os.getpid()):lock.unlink()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--start',action='store_true',help='仅启动并核对身份，不打开浏览器')
    args=parser.parse_args();identity=start();print('战术演训原型 · Airbase全图 · 空地载具验证：'+URL,flush=True)
    if not args.start:webbrowser.open(URL)
