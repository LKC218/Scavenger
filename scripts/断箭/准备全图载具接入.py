"""冻结当前靶场源码，在独立候选副本接入流式地图；不覆盖其他任务的共享源码。"""

import argparse
import json
from pathlib import Path
import shutil

from 地图执行记录 import ROOT, atomic_json, fingerprint


def quiet_defaults(output):
    """工程师要求降低网页音量；主监听增益同时控制机械声和瞬态声音。"""
    changes={
        '声音.mjs': [
            ('this.runtime.setVolume(1);this.boostDb=6;', 'this.runtime.setVolume(.25);this.boostDb=6;'),
            ('input.disconnect();input.connect(drive);drive.connect(compressor);compressor.connect(makeup);makeup.connect(limiter);limiter.connect(context.destination);limiter.connect(analyser);',
             'const mixInput=context.createGain();input.disconnect();mixInput.connect(drive);drive.connect(compressor);compressor.connect(makeup);makeup.connect(limiter);limiter.connect(input);input.connect(context.destination);input.connect(analyser);'),
            ('return{compressor,drive,makeup,limiter,analyser,setBoost};', 'return{mixInput,compressor,drive,makeup,limiter,analyser,setBoost};'),
            ('Object.assign(this,createMix(this.listener.context,this.listener.getInput()));this.wave=',
             'Object.assign(this,createMix(this.listener.context,this.listener.getInput()));this.listener.getInput=()=>this.mixInput;this.wave='),
            ('this.listener.gain.disconnect(this.drive);', 'this.mixInput.disconnect();this.listener.gain.disconnect();'),
        ],
        'index.html': [('<output id="volume-value">100%</output>', '<output id="volume-value">25%</output>'),
                       ('value="100" aria-label="总音量"', 'value="25" aria-label="总音量"')],
        '入口.mjs': [('enhancementDb:a.boostDb,audioRms:', 'enhancementDb:a.boostDb,masterVolume:a.volume,audioRms:')],
    }
    for name,pairs in changes.items():
        path=output/name;text=path.read_text('utf-8')
        for old,new in pairs:
            if text.count(old)!=1:
                raise ValueError('降低音量修订锚点变化：'+name)
            text=text.replace(old,new,1)
        path.write_text(text,'utf-8')


def revise_volume(directory,source_revision,revision):
    source=directory/'载具候选源码'/source_revision
    output=directory/'载具候选源码'/revision
    output.mkdir(parents=True,exist_ok=False)
    records={}
    for file in source.iterdir():
        if file.suffix not in ['.mjs','.css','.html']:continue
        shutil.copy2(file,output/file.name)
        records[file.name]={'source':file.relative_to(ROOT).as_posix(),'baseline':fingerprint(file)}
    quiet_defaults(output)
    for name,row in records.items():
        row['candidate']=fingerprint(output/name);row['changed']=row['baseline']['sha256']!=row['candidate']['sha256']
    atomic_json(output/'源码基线及候选.json',{'schema':'airbase-isolated-game-source-v1','source_revision':source_revision,
        'files':records,'source_program':fingerprint(Path(__file__)),'policy':'已冻结本轮候选的25%默认总音量修订；不重新复制其他任务的共享源码'})
    print({'revision':revision,'default_master_volume':.25,'changed':[name for name,row in records.items() if row['changed']]})


def run(directory, revision):
    output = directory / '载具候选源码' / revision
    output.mkdir(parents=True, exist_ok=False)
    source = ROOT / 'src/载具靶场'
    records = {}
    for file in source.iterdir():
        if file.suffix not in ['.mjs', '.css', '.html']:
            continue
        shutil.copy2(file, output / file.name)
        records[file.name] = {'source': file.relative_to(ROOT).as_posix(), 'baseline': fingerprint(file)}

    def edit(name, old, new):
        file = output / name
        text = file.read_text('utf-8')
        if text.count(old) != 1:
            raise ValueError(f'候选接入锚点变化，拒绝覆盖：{name}/{old[:45]}')
        file.write_text(text.replace(old, new, 1), 'utf-8')

    edit('寻路.mjs', 'export function segmentBox(a,b,box,pad=0){', "export function segmentBox(a,b,box,pad=0){if(typeof box.segment==='function')return box.segment(a,b,pad);")
    edit('寻路.mjs', 'n=new Navigator(map);', 'n=map.createNavigator?.()??new Navigator(map);')
    edit('模拟.mjs', '  reset(count){', '  reset(count){\n    this.map?.resetSimulation?.();')
    edit('模拟.mjs', '  freePoint(point){', '  freePoint(point){if(this.nav.freePoint)return this.nav.freePoint(point);')
    edit('模拟.mjs', 'groundHeight(x,z){return this.map.heightAt?this.map.heightAt(x,z):0;}', 'groundHeight(x,z,referenceY){return this.map.groundForUnit?this.map.groundForUnit(x,z,referenceY):this.map.heightAt?this.map.heightAt(x,z):0;}')
    edit('模拟.mjs', 'const h=this.groundHeight(u.x,u.z);', 'const h=this.groundHeight(u.x,u.z,u.y);')
    edit('模拟.mjs', 'sample=(dx,dz)=>this.groundHeight(u.x+dx,u.z+dz);', 'sample=(dx,dz)=>this.groundHeight(u.x+dx,u.z+dz,u.y);')
    edit('模拟.mjs', "}else if(u.kind==='heli')u.y=Math.max(this.map.safeAltitude-35,this.groundHeight(u.x,u.z)+35);", "}else if(u.kind==='heli'){const h=this.groundHeight(u.x,u.z);if(Number.isFinite(h))u.y=Math.max(this.map.helicopterAltitude??this.map.safeAltitude-35,h+35);}")
    edit('模拟.mjs', "kind==='heli'?this.map.safeAltitude-35+(i%3)*8", "kind==='heli'?(this.map.helicopterAltitude??this.map.safeAltitude-35)+(i%3)*8")
    edit('模拟.mjs', "if(kind==='plane')orbitHere(u,", "if(this.map.streaming)u.flightBounds=this.map.bounds;if(kind==='plane')orbitHere(u,")
    edit('模拟.mjs', 'job.point=this.nav.clamp({x:job.point.x+x*Math.cos(angle)+z*Math.sin(angle)', 'job.point=this.nav.clamp({...job.point,x:job.point.x+x*Math.cos(angle)+z*Math.sin(angle)')
    edit('模拟.mjs', '||this.nav.walkable(job.point.x,job.point.z,this.nav.radius);', "||(this.nav.destinationState?this.nav.destinationState(job.point)!=='unreachable':this.nav.walkable(job.point.x,job.point.z,this.nav.radius));")
    edit('模拟.mjs', '  finish(u){', '  finish(u){this.nav.cancel?.(u);')
    edit('模拟.mjs', 'if(set.has(u.id)){u.job=null;', 'if(set.has(u.id)){this.nav.cancel?.(u);u.job=null;')
    edit('模拟.mjs', "if(!u.path.length){u.state='无可达路径';return false;}", "if(!u.path.length){u.state=this.nav.pending?.(u)?'等待区域路线':'无可达路径';return false;}")
    edit('模拟.mjs', 'next=u.path[0];if(!next)return true;', 'next=u.path[0];if(!next)return this.nav.partialRoutes?this.nav.pathEnded(u,point):true;')
    edit('模拟.mjs', 'this.nav.walkable(nx,nz,this.map.heightAt?5.3:2.6)', 'this.nav.walkable(nx,nz,this.map.heightAt?5.3:2.6,{from:u})')
    edit('模拟.mjs', 'if(u.blockedTime>1){u.path=[];', 'if(u.blockedTime>1){this.nav.cancel?.(u);u.path=[];')
    edit('模拟.mjs', 'if(this.paused)return;this.time+=dt;', 'if(this.paused)return;this.map.syncSimulation?.(this.units,this.projectiles,this.targets);this.time+=dt;')
    edit('模拟.mjs', '  updateUnit(u,dt){', "  updateUnit(u,dt){\n    if(u.kind!=='plane'){const state=this.map.commandState?.(u);if(state&&state!=='ready'){u.moving=false;u.state=state==='pending'?'等待目标区域':'目的地不可达 · 请重新选位';return;}}")
    edit('模拟.mjs', 'const spawn=this.freePoint({x,z:', 'const spawn=this.freePoint({x:x+(this.map.spawnOffset?.x??0),z:(this.map.spawnOffset?.z??0)+(')
    edit('模拟.mjs', "kind==='heli'?170:0});u.x=spawn.x;", "kind==='heli'?170:0)});u.x=spawn.x;")
    edit('模拟.mjs', "?75+Math.floor(i/columns)*spacing:", "?(75+Math.floor(i/columns)*spacing):")
    edit('模拟.mjs', 'const p=this.freePoint({x,z});return this.makeTarget', 'const p=this.freePoint({x:x+(this.map.spawnOffset?.x??0),z:z+(this.map.spawnOffset?.z??0)});return this.makeTarget')
    edit('模拟.mjs', 'for(let z=-this.map.size/2+25;z<this.map.size/2-25;z+=28)for(let x=-this.map.size/2+25;x<this.map.size/2-25;x+=28)', 'const bounds=this.map.streaming?{minX:(this.map.spawnOffset?.x??0)-260,maxX:(this.map.spawnOffset?.x??0)+260,minZ:(this.map.spawnOffset?.z??0)-260,maxZ:(this.map.spawnOffset?.z??0)+260}:{minX:-this.map.size/2,maxX:this.map.size/2,minZ:-this.map.size/2,maxZ:this.map.size/2};for(let z=bounds.minZ+25;z<bounds.maxZ-25;z+=28)for(let x=bounds.minX+25;x<bounds.maxX-25;x+=28)')
    edit('模拟.mjs', "u.kind==='plane'?{x:0,z:0}", "u.kind==='plane'?{x:this.map.spawnOffset?.x??0,z:this.map.spawnOffset?.z??0}")
    edit('模拟.mjs', '      let earliest=2,target=null,surface=null;', '      if(this.nav.segmentReady&&!this.nav.segmentReady(a,b)){p.age-=dt;if(p.velocity)p.velocity.y+=FLIGHT.gravity*dt;alive.push(p);continue;}\n      let earliest=2,target=null,surface=null;')
    edit('飞行.mjs', "u.flight={home:{x:clamp(p.x,-430,430),z:clamp(p.z,-430,430)},plan:null};", "const b=u.flightBounds;u.flight={home:{x:clamp(p.x,b?b.minX+130:-430,b?b.maxX-130:430),z:clamp(p.z,b?b.minZ+130:-430,b?b.maxZ-130:430)},plan:null};")
    edit('飞行.mjs', "if(Math.abs(u.x)>limit||Math.abs(u.z)>limit)", "const b=sim.map.streaming?sim.map.bounds:null;if(b?(u.x<b.minX-1300||u.x>b.maxX+1300||u.z<b.minZ-1300||u.z>b.maxZ+1300):(Math.abs(u.x)>limit||Math.abs(u.z)>limit))")
    edit('飞行.mjs', "u.flight.home={x:clamp(u.x,-400,400),z:clamp(u.z,-400,400)};", "u.flight.home={x:clamp(u.x,b?b.minX+200:-400,b?b.maxX-200:400),z:clamp(u.z,b?b.minZ+200:-400,b?b.maxZ-200:400)};")
    edit('飞行.mjs', "  if(!u.job){orbit(u,dt);return;}", "  const preparation=sim.map.commandState?.(u);if(u.job&&preparation&&preparation!=='ready'){orbit(u,dt);u.state=preparation==='pending'?'等待目标区域 · 盘旋':'目标不可达 · 盘旋待命';return;}\n  if(!u.job){orbit(u,dt);return;}")

    edit('场景.mjs', 'const k=Math.max(1,this.span/180);this.camera.far=5000;', "const streaming=this.map.streaming,ground=streaming?this.map.coarseHeightAt(this.center.x,this.center.z):undefined;if(Number.isFinite(ground))this.center.y=ground;const k=streaming?Math.min(Math.max(this.span/180,((this.map.safeAltitude??200)+40-this.center.y)/165),this.map.camera.max_height_m/165):Math.max(1,this.span/180);this.camera.far=streaming?30000:5000;")
    edit('场景.mjs', 'this.camera.position.set(this.center.x+Math.sin(this.angle)*140*k,165*k,this.center.z+Math.cos(this.angle)*140*k);', 'this.groundPlane.constant=-this.center.y;this.camera.position.set(this.center.x+Math.sin(this.angle)*140*k,this.center.y+165*k,this.center.z+Math.cos(this.angle)*140*k);')
    edit('场景.mjs', 'const h=this.map.heightAt(point.x,point.z);if(!Number.isFinite(h))break;', 'const precise=this.map.heightAt(point.x,point.z),h=Number.isFinite(precise)?precise:this.map.coarseHeightAt?.(point.x,point.z);if(!Number.isFinite(h))break;')
    edit('场景.mjs', 'return Number.isFinite(h)?{...xz,y:h}:null;', "if(Number.isFinite(h))return{...xz,y:h,state:'ready'};const coarse=this.map.coarseHeightAt?.(xz.x,xz.z);return Number.isFinite(coarse)?{...xz,y:coarse,state:'pending'}:null;")
    edit('场景.mjs', 'return Number.isFinite(h)?h:0;', 'return Number.isFinite(h)?h:(this.map.coarseHeightAt?.(x,z)??0);')
    edit('场景.mjs', 'this.map.update?.(this);', 'this.map.update?.({center:this.center,span:this.map.streaming?this.span*this.width/this.height:this.span,height:this.camera.position.y-this.center.y,camera:this.camera});')
    edit('场景.mjs', 'this.centerTarget=new THREE.Vector3(THREE.MathUtils.clamp(point.x,b.minX+7,b.maxX-7),0,', 'this.centerTarget=new THREE.Vector3(THREE.MathUtils.clamp(point.x,b.minX+7,b.maxX-7),this.map.coarseHeightAt?.(point.x,point.z)??0,')
    edit('场景.mjs', 'new THREE.Vector3(this.map.center.x,0,this.map.center.z);', 'new THREE.Vector3(this.map.center.x,this.map.coarseHeightAt?.(this.map.center.x,this.map.center.z)??0,this.map.center.z);')
    edit('场景.mjs', 'this.sync(sim,new Set(),null,0);this.updateCamera();', 'if(this.map.streaming)this.home(true);this.sync(sim,new Set(),null,0);this.updateCamera();')
    edit('场景.mjs', 'const model=(await loader.loadAsync(row.url)).scene;', "const model=(this.streamingDownloads?await loader.parseAsync(await this.streamingDownloads.get(new URL(row.url,location.href).href,{bytes:row.bytes,sha256:row.runtime_sha256??row.sha256}),new URL('.',new URL(row.url,location.href)).href):await loader.loadAsync(row.url)).scene;")
    edit('输入.mjs', "const height=mid('y')+2.3;", "const ground=this.sim.map.streaming?(this.sim.map.coarseHeightAt(mid('x'),mid('z'))??0):0,height=mid('y')+2.3-ground;")

    edit('声音.mjs', 'baseUrl:location.href,catalog:', "baseUrl:location.href,loadBuffer:view.map.streaming?async url=>{const asset=Object.values(manifest.audio).find(a=>new URL(a.url,location.href).href===url),buffer=await view.map.downloads.get(url,asset?{bytes:asset.bytes,sha256:asset.runtime_sha256??asset.sha256}:undefined);return this.listener.context.decodeAudioData(buffer.slice(0));}:undefined,catalog:")

    edit('入口.mjs', 'window.addEventListener(\'pagehide\',()=>mixer.dispose());', "window.addEventListener('pagehide',()=>{mixer.dispose();externalMap?.dispose?.();});")
    edit('入口.mjs', "$('.field-top').append(debug);}", "$('.field-top').append(debug);if(externalMap.streaming){const status=document.createElement('span');status.id='map-stream-status';status.setAttribute('aria-live','polite');status.style.fontSize='11px';const retry=document.createElement('button');retry.id='map-retry';retry.textContent='重试地图细节';retry.hidden=true;retry.style.pointerEvents='auto';retry.addEventListener('pointerdown',event=>event.stopPropagation());retry.addEventListener('click',event=>{event.stopPropagation();externalMap.retry();});$('.field-top').append(status,retry);}}")
    edit('入口.mjs', "    view.minimap($('#minimap'),sim,commander.selected);", "    if(externalMap?.streaming){const stats=externalMap.stats;$('#map-stream-status').textContent=stats.pending?'正在加载地图细节…':stats.lastError?'细节暂不可用，已保留总览':stats.sceneLevel==='far'?'远景':stats.sceneLevel==='middle'?'中景':'近景';$('#map-retry').hidden=!!stats.pending||!stats.lastError;}\n    view.minimap($('#minimap'),sim,commander.selected);")
    edit('入口.mjs', "await view.load(manifest,message=>$('#load-status').textContent=message);", "if(externalMap?.streaming)view.streamingDownloads=externalMap.downloads;const unitPreparation=externalMap?.streaming?externalMap.waitForSimulationReady(sim.units,sim.projectiles):Promise.resolve();await Promise.all([view.load(manifest,message=>$('#load-status').textContent=message),unitPreparation]);")
    edit('入口.mjs', 'window.range={defense,externalMap,sim,view,', "if(externalMap?.streaming){$('#load-status').textContent='准备首屏声音、单位数据与着色器…';await externalMap.waitForView();await externalMap.waitForSimulationReady(sim.units,sim.projectiles);performance.mark('airbase:unit-data-ready');await Promise.all(Object.values(manifest.audio).map(asset=>mixer.runtime.buffer(asset)));await mixer.prepareHeliShot();await mixer.prepareAirAudio();performance.mark('airbase:audio-ready');await view.renderer.compileAsync(view.scene,view.camera);view.render();performance.mark('airbase:render-ready');}\n  window.range={defense,externalMap,sim,view,")
    benchmark_api = """ready:true};
  window.range.configureBenchmark=async options=>{
    if(!externalMap?.streaming)throw new Error('只用于全图候选核验');abortBench('核验配置改变');sim.paused=true;mixer.stop();
    const center=options.center??{x:0,z:85};externalMap.spawnOffset={x:center.x,z:center.z-85};externalMap.center={...center};
    await externalMap.prepareRegion('benchmark',{minX:center.x-560,maxX:center.x+560,minZ:center.z-560,maxZ:center.z+560});
    sim.options.map=externalMap;sim.options.city=true;sim.options.total=options.count;sim.options.composition=options.composition??{tank:Math.round(options.count*.8),heli:Math.round(options.count*.16),plane:Math.round(options.count*.04)};
    sim.reset();sim.configureLoad(options.mode??'combat');sim.paused=true;
    commander.selected.clear();commander.focus=null;commander.clearTransient();page=0;rebuildRoster();view.setup(sim);view.stopCamera();view.center.set(center.x,externalMap.coarseHeightAt(center.x,center.z),center.z);view.span=view.targetSpan=options.span??58;view.angle=view.targetAngle=0;view.updateCamera();
    if(options.allVisible)for(let attempt=0;attempt<24;attempt++){const fits=sim.units.every(u=>{const r=view.rigs[u.kind].radius;return [[-r,-r],[r,r],[-r,r],[r,-r]].every(([dx,dz])=>{const p=view.project({x:u.x+dx,y:u.y+r,z:u.z+dz});return p.x>20&&p.y>20&&p.x<view.width-20&&p.y<view.height-20;});});if(fits)break;view.span=view.targetSpan=Math.min(externalMap.maxSpan,view.span*1.2);view.updateCamera();}
    view.sync(sim,commander.selected,null,0);await externalMap.waitForView();await externalMap.waitForSimulationReady(sim.units,sim.projectiles);await sim.nav.patchFor({x:center.x,z:center.z,y:externalMap.heightAt(center.x,center.z),startup:true});
    view.center.set(center.x,externalMap.coarseHeightAt(center.x,center.z),center.z);view.span=view.targetSpan=options.allVisible?view.span:(options.span??58);view.updateCamera();view.sync(sim,commander.selected,null,0);await externalMap.waitForView();await view.renderer.compileAsync(view.scene,view.camera);view.render();
    if(![...$('#unit-count').options].some(o=>o.value===String(options.count)))$('#unit-count').add(new Option(String(options.count),String(options.count)));$('#unit-count').value=String(options.count);$('#load-mode').value=options.mode??'combat';rebuildRoster();sim.paused=false;accumulator=0;last=performance.now();frames=[];cpuFrames=[];mixer.resetStats();updateUI();return perf();
  };"""
    edit('入口.mjs', 'ready:true};', benchmark_api)
    edit('统计.mjs', 'duration:elapsed,frames:f.length,p50:', 'duration:elapsed,frames:f.length,averageFPS:1000*r.frames.length/r.frames.reduce((a,b)=>a+b,0),frame_times_ms:r.frames,cpu_times_ms:r.cpu,p50:')
    edit('统计.mjs', 'n=>n>33.34', 'n=>n>33.3')
    quiet_defaults(output)
    for name, record in records.items():
        record['candidate'] = fingerprint(output / name)
        record['changed'] = record['baseline']['sha256'] != record['candidate']['sha256']
    atomic_json(output / '源码基线及候选.json', {'schema': 'airbase-isolated-game-source-v1', 'files': records,
        'source_program': fingerprint(Path(__file__)), 'policy': '共享源码只读复制，所有适配只写本轮候选；验收后重新核对共享源码再合入', 'status': '候选源码已生成，尚未浏览器验证'})
    print(json.dumps({'files': len(records), 'changed': [n for n, r in records.items() if r['changed']]}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--adjust-volume-from', help='仅从本轮已冻结候选复制并降低默认总音量，不重读共享靶场源码')
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):
        raise ValueError('无效候选修订')
    if args.adjust_volume_from:
        if not args.adjust_volume_from or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.adjust_volume_from):
            raise ValueError('无效来源修订')
        revise_volume(directory,args.adjust_volume_from,args.revision)
    else:
        run(directory, args.revision)
