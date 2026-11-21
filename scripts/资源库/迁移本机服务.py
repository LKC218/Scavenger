"""核对本项目旧服务后切换固定入口；历史端口改为跳转。没有文件删除操作。"""
import hashlib,json,subprocess,sys,threading,time
from urllib.request import urlopen,build_opener,HTTPRedirectHandler,Request
from urllib.error import HTTPError
from 统一资源 import ROOT,HOME,read,save,current
PORTS={8768:('scripts/预览载具靶场.py','api/status','scavenger-range'),8769:('scripts/预览丧尸群体测试.py','api/status','scavenger-horde'),8770:('scripts/资源优化/验证服务.py','catalog.json',None),13261:('scripts/预览载具靶场.py','api/status','scavenger-range'),23699:('scripts/资源优化/游戏版服务.py','api/status','scavenger-range'),47061:('scripts/资源优化/游戏版服务.py','api/status','scavenger-range'),44433:('scripts/资源优化/游戏版服务.py','catalog.json',None)}
def powershell(code):
 result=subprocess.run(['powershell','-NoProfile','-Command','[Console]::OutputEncoding=[Text.UTF8Encoding]::new();'+code],capture_output=True,check=True)
 return result.stdout.decode('utf-8-sig').strip()
def inventory():
 return json.loads(powershell("$rows=Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 | ForEach-Object { $proc=Get-CimInstance Win32_Process -Filter ('ProcessId='+$_.OwningProcess); [PSCustomObject]@{port=$_.LocalPort;pid=$_.OwningProcess;command=$proc.CommandLine;created=$proc.CreationDate.ToString('O')} }; @($rows) | ConvertTo-Json -Compress"))
def get(port,endpoint):return json.load(urlopen(f'http://127.0.0.1:{port}/{endpoint}',timeout=5))
def run():
 before=inventory();planned=[]
 for r in before:
  if r['port'] not in PORTS:continue
  script,endpoint,app=PORTS[r['port']]
  if '服务兼容入口.py' in (r['command'] or ''):continue
  if script.replace('/','\\').split('\\')[-1] not in (r['command'] or ''):raise ValueError('端口进程身份不匹配：'+str(r))
  data=get(r['port'],endpoint)
  if app and data.get('app')!=app:raise ValueError('应用身份不匹配')
  if not app and not data.get('models'):raise ValueError('历史目录身份不匹配')
  planned.append(r)
 save(HOME/'验收/服务迁移前.json',planned)
 # 核对完整命令和创建时间，再按PID停止；不结束浏览器和其他项目进程。
 for row in planned:
  latest=next((r for r in inventory() if r['port']==row['port']),None)
  if latest!=row:raise ValueError('进程在核对后发生变化，停止迁移')
  powershell(f"Stop-Process -Id {row['pid']} -ErrorAction Stop")
 specs={8767:['scripts/断箭/启动正式资料库.py','--serve'],8768:['scripts/预览载具靶场.py','--serve'],8769:['scripts/预览丧尸群体测试.py','--serve']}
 for port in PORTS:
  if port not in specs:specs[port]=['scripts/资源库/服务兼容入口.py','--port',str(port),'--target-port',str(8767 if port in [8770,44433] else 8768)]
 running={r['port']:r for r in inventory()};started=[]
 for port,args in specs.items():
  if port in running:continue
  args=[str(ROOT/args[0]),*args[1:]];log=HOME/f'验收/服务{port}.log';log.parent.mkdir(parents=True,exist_ok=True)
  with log.open('ab') as stream:proc=subprocess.Popen([sys.executable,'-X','utf8','-B',*args],cwd=ROOT,stdout=stream,stderr=stream,creationflags=subprocess.CREATE_NO_WINDOW)
  started.append({'port':port,'pid':proc.pid,'args':args})
 deadline=time.time()+30;event=threading.Event()
 while True:
  try:
   assert get(8768,'api/status')['app']=='scavenger-range';assert get(8769,'api/status')['app']=='scavenger-horde';catalog=get(8767,'api/catalog');assert catalog['asset_library_revision']==1;break
  except Exception:
   if time.time()>=deadline:raise
   event.wait(.2)
 manifest=get(8768,'manifest.json');models=[]
 for key,row in manifest['models'].items():
  assert row['asset_library']=='统一资源库';body=urlopen('http://127.0.0.1:8768'+row['url']).read();digest=hashlib.sha256(body).hexdigest();assert digest==row['runtime_sha256'] and len(body)==row['bytes'];models.append({'key':key,'bytes':len(body),'sha256':digest,'url':row['url']})
 class NoRedirect(HTTPRedirectHandler):
  def redirect_request(self,*args):return None
 redirects=[]
 for port in specs:
  if port in [8767,8768,8769]:continue
  try:build_opener(NoRedirect).open(Request(f'http://127.0.0.1:{port}/',method='HEAD'))
  except HTTPError as e:
   assert e.code==307;redirects.append({'port':port,'location':e.headers['Location']})
 result={'models':models,'model_bytes':sum(r['bytes'] for r in models),'audio':len(manifest['audio']),'library_revision':catalog['revision'],'started':started,'redirects':redirects,'deleted':0,'passed':True};save(HOME/'验收/本机服务迁移.json',result);print('固定入口已切换；8768模型合计',result['model_bytes'],'字节，旧预览端口仅跳转',flush=True)
if __name__=='__main__':run()
