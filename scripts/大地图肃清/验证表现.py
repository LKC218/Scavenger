"""核对表现语法、真实炮口与本轮媒体身份；不运行浏览器或宣称性能通过。"""
import argparse, array, hashlib, json, math, subprocess, sys, wave
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
BATCH_HOME=ROOT/'local-data/大地图肃清/首批/V001-01a1ac6a'
BARREL_CHECK=r'''
import fs from 'node:fs';import {pathToFileURL} from 'node:url';
const root=process.cwd(),THREE=await import(pathToFileURL(root+'/local-data/tools/three/0.187.0/build/three.module.js')),{MeshoptDecoder}=await import(pathToFileURL(root+'/local-data/tools/three/0.187.0/examples/jsm/libs/meshopt_decoder.module.js')),{CONFIG}=await import(pathToFileURL(root+'/src/大地图肃清/配置.mjs')),{calculateMuzzle}=await import(pathToFileURL(root+'/src/大地图肃清/规则.mjs'));await MeshoptDecoder.ready;
const evidence=JSON.parse(fs.readFileSync(root+'/local-data/大地图肃清/首批/V001-01a1ac6a/视听/M1A2主炮拓扑核对.json','utf8')),b=fs.readFileSync(root+'/'+evidence.source.path),json=JSON.parse(b.subarray(20,20+b.readUInt32LE(12))),bin=b.subarray(28+b.readUInt32LE(12)),views=new Map();
function view(i){if(views.has(i))return views.get(i);const v=json.bufferViews[i],e=v.extensions?.EXT_meshopt_compression;let d;if(e){d=new Uint8Array(e.count*e.byteStride);MeshoptDecoder.decodeGltfBuffer(d,e.count,e.byteStride,bin.subarray(e.byteOffset,e.byteOffset+e.byteLength),e.mode,e.filter);}else d=bin.subarray(v.byteOffset,v.byteOffset+v.byteLength);views.set(i,d);return d;}
function accessor(i){const a=json.accessors[i],data=view(a.bufferView),dv=new DataView(data.buffer,data.byteOffset,data.byteLength),n={SCALAR:1,VEC2:2,VEC3:3,VEC4:4}[a.type],type={5120:[Int8Array,'getInt8',1],5121:[Uint8Array,'getUint8',1],5122:[Int16Array,'getInt16',2],5123:[Uint16Array,'getUint16',2],5125:[Uint32Array,'getUint32',4],5126:[Float32Array,'getFloat32',4]}[a.componentType],array=new type[0](a.count*n),stride=json.bufferViews[a.bufferView].byteStride??n*type[2];for(let k=0;k<a.count;k++)for(let c=0;c<n;c++)array[k*n+c]=dv[type[1]]((a.byteOffset??0)+k*stride+c*type[2],true);return new THREE.BufferAttribute(array,n,a.normalized??false);}
const primitive=json.meshes[4].primitives[0],geometry=new THREE.BufferGeometry();for(const[name,accessorId]of Object.entries(primitive.attributes))geometry.setAttribute(name==='POSITION'?'position':name==='NORMAL'?'normal':name==='TEXCOORD_0'?'uv':'uv1',accessor(accessorId));geometry.setIndex(accessor(primitive.indices));const indexBefore=geometry.index.array.slice();
const source=fs.readFileSync(root+'/src/大地图肃清/表现.mjs','utf8'),prefix=source.split('export async function createPresentation')[0].replace(/^import .*;\s*$/gm,''),api=new Function('THREE',prefix+';return{deriveMainBarrelGeometry,BARREL_TRIANGLE_IDS};')(THREE),split=api.deriveMainBarrelGeometry(geometry,evidence.source.sha256),selected=new Set(api.BARREL_TRIANGLE_IDS);
let partitionExact=true,mi=0,fi=0;for(let t=0;t<8500;t++)for(let c=0;c<3;c++){const actual=selected.has(t)?split.moving.index.array[mi++]:split.fixed.index.array[fi++];if(actual!==indexBefore[t*3+c])partitionExact=false;}
const attributesShared=Object.keys(geometry.attributes).every(k=>split.moving.attributes[k]===geometry.attributes[k]&&split.fixed.attributes[k]===geometry.attributes[k]),sourceIndexUnchanged=indexBefore.every((v,i)=>v===geometry.index.array[i]);
const m=CONFIG.weapons.main.muzzle,rootNode=new THREE.Group(),yaw=new THREE.Group(),pitch=new THREE.Group(),barrel=new THREE.Mesh(split.moving),fixed=new THREE.Mesh(split.fixed);rootNode.add(yaw);yaw.position.set(m.pivot.x,m.pivot.y,m.pivot.z);yaw.add(pitch,fixed);fixed.position.set(-m.pivot.x,-m.pivot.y,-m.pivot.z);pitch.position.set(m.pitchPivot.x-m.pivot.x,m.pitchPivot.y-m.pivot.y,m.pitchPivot.z-m.pivot.z);pitch.add(barrel);barrel.position.set(-m.pitchPivot.x,-m.pitchPivot.y,-m.pitchPivot.z);rootNode.updateMatrixWorld(true);
const p=new THREE.Vector3(),q=new THREE.Vector3();let zeroPoseError=0;for(const mesh of[fixed,barrel])for(const index of mesh.geometry.index.array){p.fromBufferAttribute(geometry.attributes.position,index);q.copy(p);mesh.localToWorld(q);zeroPoseError=Math.max(zeroPoseError,q.distanceTo(p));}
const unit={position:{x:7,y:3,z:-9},yaw:0,turretYaw:0,gunPitch:0,m2Yaw:0,m2Pitch:0},cases=[];for(const gunPitch of[CONFIG.weapons.main.minPitch,0,CONFIG.weapons.main.maxPitch])for(const turretYaw of[0,.7])for(const bodyYaw of[0,-.5]){unit.yaw=bodyYaw;unit.turretYaw=turretYaw;unit.gunPitch=gunPitch;rootNode.position.set(7,3,-9);rootNode.rotation.y=bodyYaw;yaw.rotation.y=turretYaw;pitch.rotation.x=gunPitch;rootNode.updateMatrixWorld(true);p.set(m.position.x,m.position.y,m.position.z);barrel.localToWorld(p);const expected=calculateMuzzle(unit,'main',CONFIG),error=Math.hypot(p.x-expected.x,p.y-expected.y,p.z-expected.z);cases.push({pitch:gunPitch,bodyYaw,turretYaw,muzzleErrorM:error});}
const passed=partitionExact&&attributesShared&&sourceIndexUnchanged&&zeroPoseError<1e-10&&cases.every(c=>c.muzzleErrorM<1e-10)&&split.fixed.index.count+split.moving.index.count===25500;process.stdout.write(JSON.stringify({passed,source:evidence.source,evidence:split.evidence,partitionExact,attributesShared,attributeNames:Object.keys(geometry.attributes),sourceIndexUnchanged,zeroPoseMaxErrorM:zeroPoseError,angleCases:cases,maxMuzzleErrorM:Math.max(...cases.map(c=>c.muzzleErrorM)),mantletMoved:false,rendered:false,browserValidated:false}));
'''
FEEDBACK_CHECK=r'''
import fs from 'node:fs';import{pathToFileURL}from'node:url';
const root=process.cwd(),THREE=await import(pathToFileURL(root+'/local-data/tools/three/0.187.0/build/three.module.js')),{createAudio}=await import(pathToFileURL(root+'/src/大地图肃清/声音.mjs'));
class Node{connect(){return this;}disconnect(){}}
class Param{constructor(){this.value=0;}setTargetAtTime(v){this.value=v;}}
class Context{constructor(){this.state='running';this.currentTime=0;this.destination=new Node();}createGain(){const n=new Node();n.gain=new Param();return n;}createStereoPanner(){const n=new Node();n.pan=new Param();return n;}createBufferSource(){const n=new Node();n.start=()=>{};n.stop=()=>n.onended?.();return n;}decodeAudioData(){return Promise.resolve({duration:6.4});}resume(){return Promise.resolve();}close(){return Promise.resolve();}}
globalThis.AudioContext=Context;globalThis.fetch=async()=>({ok:true,arrayBuffer:async()=>new ArrayBuffer(8)});const audio=createAudio({manifest:{audio:{zombieGroup:[{url:'/CPU替身',durationSeconds:6.4}]}},config:{audio:{volume:.25,groupVoices:8,battleVoices:16}},camera:{position:{x:0,z:0},matrixWorld:new THREE.Matrix4()}});await audio.setEnabled(true);
function frozen(v){if(v&&typeof v==='object'){Object.freeze(v);for(const x of Object.values(v))frozen(x);}return v;}
const visible=frozen({runId:'feedback',tick:1,time:0,units:[],heardGroups:[],zombies:Array.from({length:100},(_,i)=>({id:'z'+i,alive:true,visible:true,position:{x:10+(i%2)*40,y:0,z:8},behaviorState:i%2?'attack':'idle',animationState:i%2?'attack':'idle'}))}),before=JSON.stringify(visible);audio.update(visible,{x:0,y:0,z:0});const visibleOnly=audio.getState(),unchanged=JSON.stringify(visible)===before;
const mixed={...visible,tick:2,time:.21,units:[{id:'listener',kind:'tank',alive:true,moveState:'idle',position:{x:0,y:0,z:0}}],heardGroups:[{id:'hidden',listenerId:'listener',bearing:1.1,get count(){throw Error('不应读取隐藏数量');},get position(){throw Error('不应读取隐藏位置');}}]};audio.update(mixed,{x:0,y:0,z:0});const combined=audio.getState();
const crowded=frozen({...visible,tick:3,time:.42,zombies:Array.from({length:80},(_,i)=>({id:'many'+i,alive:true,visible:true,position:{x:(i%10-5)*32,y:0,z:(Math.floor(i/10)-4)*32},behaviorState:i%3?'walk':'attack'}))});audio.update(crowded,{x:0,y:0,z:0});const budget=audio.getState();audio.update({...crowded,tick:4,time:.63,zombies:[{alive:true,visible:false,get position(){throw Error('不可见实体未公开坐标');}}]},{x:0,y:0,z:0});const hiddenIgnored=audio.getState();await audio.setEnabled(false);const disabled=audio.getState();
const source=fs.readFileSync(root+'/src/大地图肃清/表现.mjs','utf8'),prefix=source.split('export async function createPresentation')[0].replace(/^import .*;\s*$/gm,''),state=new Function('THREE',prefix+';return baitVisualState;')(THREE),bait=Object.freeze({id:'bait',alive:true,hp:50,startsAt:5,endsAt:35}),timeline=[0,4.999,5,34.999,35,36].map(time=>({time,state:state(bait,time)})),expected=['waiting','waiting','active','active',null,null],inactive=state({...bait,hp:0},10)===null&&state({...bait,alive:false},10)===null;
const sharedStart=source.indexOf(' const baitGeometry='),sharedEnd=source.indexOf(' const frustum=',sharedStart),createStart=source.indexOf(' function createBait('),createEnd=source.indexOf(' function addEffect(',createStart),container=new THREE.Group(),map=new Map(),factory=new Function('THREE','group','baits',source.slice(sharedStart,sharedEnd)+source.slice(createStart,createEnd)+';return{createBait,baitGeometry,baitMaterials};')(THREE,container,map),a=factory.createBait({id:'a'}),b=factory.createBait({id:'b'}),sharedGeometry=a.base.geometry===b.base.geometry&&a.pulse.geometry===b.pulse.geometry,sharedMaterials=a.base.material===b.base.material&&a.pulse.material===b.pulse.material,controlledSource=a.root.userData.source==='网页受控样本';
const passed=visibleOnly.visibleGroupVoices>0&&visibleOnly.hiddenGroupVoices===0&&combined.visibleGroupVoices>0&&combined.hiddenGroupVoices>0&&combined.groupVoices<=8&&budget.groupVoices===8&&hiddenIgnored.groupVoices===0&&disabled.voices===0&&unchanged&&inactive&&timeline.every((row,i)=>row.state===expected[i])&&sharedGeometry&&sharedMaterials&&controlledSource;process.stdout.write(JSON.stringify({passed,method:'CPU Web Audio接口替身验证实际声音模块的声部调度；实际画面与听感仍待内置浏览器',visibleOnly,combined,budget,hiddenIgnored,disabled,readonlySnapshotUnchanged:unchanged,baitTimeline:timeline,baitInactiveHidden:inactive,baitGeometryShared:sharedGeometry,baitMaterialsShared:sharedMaterials,baitSourceExplicit:controlledSource}));
'''
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def output_paths(args,parser):
    if args.output:
        out=(ROOT/args.output).resolve()
    elif args.output_dir:
        out=(ROOT/args.output_dir).resolve()/'技术核对.json'
    else:
        revision=datetime.now(timezone.utc).strftime('r%Y%m%dT%H%M%S%fZ')+'-'+uuid4().hex[:6]
        out=BATCH_HOME/'验证回执'/revision/'技术核对.json'
    paths=[out,out.parent/'M1A2主炮运行派生CPU核对.json',out.parent/'G03群声与诱饵反馈CPU核对.json']
    if any(not p.resolve().is_relative_to(BATCH_HOME.resolve()) for p in paths):
        parser.error('三份技术回执必须全部位于本批 local-data/大地图肃清/首批/V001-01a1ac6a 内；请选择新的本批输出目录')
    if len(set(paths))!=len(paths):
        parser.error('--output文件名不能与两份CPU侧回执重名；请选择新的文件名或使用--output-dir')
    existing=[p.relative_to(ROOT).as_posix() for p in paths if p.exists() or p.is_symlink()]
    if existing:
        parser.error('拒绝覆盖已有回执：'+'、'.join(existing)+'；请用--output-dir选择新的输出目录，或省略输出参数生成独立UTC修订目录')
    return paths
def write_receipt(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    except FileExistsError:
        raise SystemExit('拒绝覆盖已有回执：'+path.relative_to(ROOT).as_posix()+'；请用--output-dir选择新的输出目录后重试')
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    outputs=parser.add_mutually_exclusive_group()
    outputs.add_argument('--output-dir',help='三份回执的统一新目录，限定本批目录内；默认创建验证回执下独立UTC修订目录')
    outputs.add_argument('--output',help='兼容指定技术核对文件；两份CPU侧回执随该文件父目录，拒绝覆盖已有目标')
    args=parser.parse_args();out,barrel_out,feedback_out=output_paths(args,parser);boundary=(BATCH_HOME/'视听').resolve()
    checks=[];sources=[]
    for name in ['表现.mjs','声音.mjs','原作合批适配.mjs']:
        p=ROOT/'src/大地图肃清'/name;result=subprocess.run(['node','--check',str(p)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8');checks.append({'name':'语法 '+name,'passed':result.returncode==0,'error':result.stderr});sources.append({'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p),'bytes':p.stat().st_size})
    origin=ROOT/'src/原作群尸/合批.mjs';checks.append({'name':'旧合批源保持原身份','passed':sha(origin)=='3283abd9753e644c944e66dac4246d69297a47526a42deb648bbcf2faa6e7f76'})
    script="import {CONFIG} from './src/大地图肃清/配置.mjs'; console.log(JSON.stringify(CONFIG.weapons));";config=json.loads(subprocess.run(['node','--input-type=module','-e',script],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True).stdout)
    batch=ROOT/'local-data/断箭/批次/肃清视听-V001-01a1ac6a';nodes=read(batch/'M1A2炮口与轴核对.json')['nodes']
    mapping={'main':('shell_spawn_0_0','turret_0','weapon_0_1_2'),'m2':('shell_spawn_0','turret_0_0','weapon_0')}
    for weapon,names in mapping.items():
        m=config[weapon]['muzzle'];error=max(abs(m[field][axis]-nodes[node]['position'][axis]) for field,node in zip(['position','pivot','pitchPivot'],names) for axis in 'xyz');checks.append({'name':weapon+'真实GLB炮口与轴','passed':bool(m['verified'] and error<1e-7),'maxErrorM':error})
    manifest=read(boundary/'素材清单.json');media=[]
    for category in ['audio','textures']:
        for role,values in manifest[category].items():
            for row in (values if isinstance(values,list) else [values]):
                p=(ROOT/row['path']).resolve();valid=p.is_relative_to(boundary) and p.exists() and sha(p)==row['sha256'] and p.stat().st_size==row['bytes'];item={'role':role,'path':row['path'],'bytes':row['bytes'],'sha256':row['sha256'],'passed':valid}
                if valid and p.suffix=='.wav':
                    with wave.open(str(p),'rb') as w:frames=w.getnframes();channels=w.getnchannels();rate=w.getframerate();samples=array.array('h',w.readframes(frames));peak=max(abs(x) for x in samples)/32768 if samples else 0
                    item.update(frames=frames,channels=channels,sampleRate=rate,peak=peak);item['passed']=item['passed'] and frames>0 and 0<peak<1 and all(math.isfinite(x) for x in [peak])
                media.append(item)
    checks.append({'name':'全部网页媒体的路径摘要与PCM幅度','passed':all(x['passed'] for x in media),'count':len(media)})
    for name in ['来源核对.json','毁伤补充/来源核对.json']:
        audit=read(batch/name);package=Path(audit['package']['path']);checks.append({'name':'只读原包保持摘要 '+name,'passed':sha(package)==audit['package']['sha256'],'build':audit['build']})
    baseline=read(ROOT/'local-data/断箭/批次/BA-26273833-002/输入清单.json');known={Path(x['path']).name:x for x in baseline['files']};current=read(batch/'来源核对.json');bank_results=[{'name':Path(x['path']).name,'passed':x['sha256']==known[Path(x['path']).name]['sha256']} for x in current['inputs'] if Path(x['path']).suffix=='.bank'];checks.append({'name':'实际FMOD三bank与原生事件索引输入身份一致','passed':len(bank_results)==3 and all(x['passed'] for x in bank_results),'banks':bank_results})
    if (boundary/'M1A2主炮拓扑核对.json').exists():
        result=subprocess.run(['node','--input-type=module','-e',BARREL_CHECK],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True);barrel= json.loads(result.stdout);write_receipt(barrel_out,barrel);checks.append({'name':'主炮完整分量面数与零姿态和极限角炮口CPU核对','passed':barrel['passed'],'zeroPoseErrorM':barrel['zeroPoseMaxErrorM'],'maxMuzzleErrorM':barrel['maxMuzzleErrorM']})
    result=subprocess.run(['node','--input-type=module','-e',FEEDBACK_CHECK],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True);feedback=json.loads(result.stdout);write_receipt(feedback_out,feedback);checks.append({'name':'可见群声与隐藏粗声像共用预算和诱饵生命周期','passed':feedback['passed']})
    passed=all(c['passed'] for c in checks);result={'version':'V001','technicalPassed':passed,'checks':checks,'sources':sources,'media':media,'acceptance':{'browser':'由主对话串行运行，此脚本未测试','performance':'未在此脚本测量P95/GPU/长帧','visualAndListening':'等待工程师确认'},'task':'肃清B表现视听-V001-01a1ac6a','taskClosed':False};write_receipt(out,result);print(json.dumps({'passed':passed,'checks':len(checks),'media':len(media),'receipt':out.relative_to(ROOT).as_posix(),'sideReceipts':[p.relative_to(ROOT).as_posix() for p in [barrel_out,feedback_out] if p.exists()]},ensure_ascii=False));return 0 if passed else 1
if __name__=='__main__':sys.exit(main())
