// 显示和模拟分别持有租约；未知区域保持pending，不因镜头移动释放单位数据。
import {TriangleSupportTile} from './精细支撑.mjs';

const abortError=()=>new DOMException('区域请求已取消','AbortError');
export class RequestPool {
  constructor(limit=4){this.limit=limit;this.active=0;this.maximum=0;this.queue=[];}
  run(operation,signal){
    return new Promise((resolve,reject)=>{
      if(signal?.aborted){reject(abortError());return;}
      const item={operation,signal,resolve,reject};
      item.cancel=()=>{const at=this.queue.indexOf(item);if(at>=0){this.queue.splice(at,1);reject(abortError());}};
      signal?.addEventListener('abort',item.cancel,{once:true});this.queue.push(item);this.pump();
    });
  }
  pump(){
    while(this.active<this.limit&&this.queue.length){
      const item=this.queue.shift();item.signal?.removeEventListener('abort',item.cancel);
      if(item.signal?.aborted){item.reject(abortError());continue;}
      this.active++;this.maximum=Math.max(this.maximum,this.active);
      Promise.resolve().then(()=>item.operation(item.signal)).then(item.resolve,item.reject).finally(()=>{this.active--;this.pump();});
    }
  }
}

export class DownloadStore {
  constructor({limit=64*1048576,pool=new RequestPool(4),fetcher=(url,options)=>globalThis.fetch(url,options)}={}){
    Object.assign(this,{limit,pool,fetcher});this.cache=new Map();this.pending=new Map();this.bytes=0;this.clock=0;this.received=0;
  }
  async get(url,identity,signal){
    if(signal?.aborted)throw abortError();
    const key=url+'#'+(identity?.sha256??''),cached=this.cache.get(key);
    if(cached){cached.used=++this.clock;return cached.buffer;}
    let entry=this.pending.get(key);
    if(entry?.controller.signal.aborted){await entry.promise.catch(()=>{});return this.get(url,identity,signal);}
    if(!entry){
      entry={controller:new AbortController(),users:new Set(),done:false};this.pending.set(key,entry);
      const pending=entry,downloadSignal=entry.controller.signal;
      entry.promise=this.pool.run(async()=>{
      const response=await this.fetcher(url,{signal:downloadSignal});
      if(!response.ok)throw new Error(`区域资源HTTP ${response.status}: ${url}`);
      const buffer=await response.arrayBuffer();
      if(downloadSignal.aborted)throw abortError();
      if(identity?.bytes!==undefined&&buffer.byteLength!==identity.bytes)throw new Error(`区域资源长度不符: ${url}`);
      if(identity?.sha256){
        const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)),v=>v.toString(16).padStart(2,'0')).join('');
        if(digest!==identity.sha256)throw new Error(`区域资源摘要不符: ${url}`);
      }
      this.received+=buffer.byteLength;return buffer;
      },downloadSignal).then(buffer=>{
    // 只缓存有界下载字节；解析后驻留数据由各租约单独记账。
    if(buffer.byteLength<=this.limit){
      while(this.bytes+buffer.byteLength>this.limit&&this.cache.size){
        const [old,row]=[...this.cache].sort((a,b)=>a[1].used-b[1].used)[0];this.cache.delete(old);this.bytes-=row.buffer.byteLength;
      }
      if(!this.cache.has(key)){this.cache.set(key,{buffer,used:++this.clock});this.bytes+=buffer.byteLength;}
    }
        return buffer;
      }).finally(()=>{pending.done=true;if(this.pending.get(key)===pending)this.pending.delete(key);});
    }
    const token=Symbol();entry.users.add(token);let cancel;
    const aborted=new Promise((_,reject)=>{cancel=()=>reject(abortError());signal?.addEventListener('abort',cancel,{once:true});});
    try{return await Promise.race([entry.promise,aborted]);}
    finally{signal?.removeEventListener('abort',cancel);entry.users.delete(token);if(!entry.users.size&&!entry.done)entry.controller.abort();}
  }
  clear(){this.cache.clear();this.bytes=0;for(const row of this.pending.values())row.controller.abort();}
}

export class RegionData {
  constructor(index,base,downloads,{limit=64*1048576,loadTile}={}){
    Object.assign(this,{index,base,downloads,limit});this.rows=new Map(index.chunks.map(row=>[row.id,row]));
    this.entries=new Map();this.owners=new Map();this.generations=new Map();this.operations=new Map();this.liveTokens=new Set();this.clock=0;this.bytes=0;this.reserved=0;
    this.loadTile=loadTile??(async(row,signal)=>new TriangleSupportTile(await downloads.get(new URL(row.geometry,base).href,row.geometry_identity,signal)));
  }
  ids(bounds){
    if(![bounds.minX,bounds.maxX,bounds.minZ,bounds.maxZ].every(Number.isFinite)||bounds.minX>bounds.maxX||bounds.minZ>bounds.maxZ)throw new Error('区域准备边界无效');
    const b=this.index.bounds,size=this.index.chunk_m,[nx,nz]=this.index.grid;
    const minX=Math.max(0,Math.floor((bounds.minX-b.minX)/size)),maxX=Math.min(nx-1,Math.floor((bounds.maxX-b.minX)/size));
    const minZ=Math.max(0,Math.floor((bounds.minZ-b.minZ)/size)),maxZ=Math.min(nz-1,Math.floor((bounds.maxZ-b.minZ)/size));
    const ids=[];for(let z=minZ;z<=maxZ;z++)for(let x=minX;x<=maxX;x++){const id=`${String(x).padStart(2,'0')}-${String(z).padStart(2,'0')}`;if(!this.rows.get(id)?.empty)ids.push(id);}return ids;
  }
  trim(required=0){
    for(const [id,entry] of [...this.entries].sort((a,b)=>a[1].used-b[1].used)){
      if(this.bytes+this.reserved+required<=this.limit)break;
      if(entry.tokens.size||entry.promise)continue;
      this.entries.delete(id);this.bytes-=entry.tile.bytes;
    }
    return this.bytes+this.reserved+required<=this.limit;
  }
  expectedBytes(row){return row.geometry_identity.bytes+Math.max(row.collision_decoded_bytes??0,row.collision_identity?row.collision_identity.bytes+row.collision_count*1536+4096:0);}
  canPrepare(bounds,headroom=0){
    const ids=this.ids(bounds);
    if(ids.every(id=>{const entry=this.entries.get(id);return entry?.tile&&entry.tokens.size;}))return true;
    const extra=()=>ids.reduce((sum,id)=>sum+(this.entries.has(id)?0:this.expectedBytes(this.rows.get(id))),0);
    // 只清未引用缓存。二次核对涵盖第一次清理刚好移除了目标缓存的情况。
    return this.trim(extra()+headroom)&&this.trim(extra()+headroom);
  }
  cleanupOwner(owner){if(!this.owners.has(owner)&&!(this.operations.get(owner)?.size)){this.operations.delete(owner);this.generations.delete(owner);}}
  acquire(id,token){
    if(!this.liveTokens.has(token)||this.disposed)return Promise.reject(abortError());
    let entry=this.entries.get(id);
    if(entry?.controller.signal.aborted)return entry.promise.catch(()=>{}).then(()=>this.acquire(id,token));
    if(entry){entry.tokens.add(token);entry.used=++this.clock;return entry.promise??Promise.resolve(entry.tile);}
    const row=this.rows.get(id),expected=this.expectedBytes(row);
    if(!this.trim(expected))throw new Error('模拟数据预算不足，命令继续等待准备');
    const controller=new AbortController();entry={tokens:new Set([token]),used:++this.clock,controller};
    this.entries.set(id,entry);this.reserved+=expected;
    entry.promise=Promise.resolve().then(()=>this.loadTile(row,controller.signal)).then(tile=>{
      if(controller.signal.aborted||this.disposed)throw abortError();
      if(!Number.isFinite(tile.bytes)||tile.bytes>expected)throw new Error('精细区域解码大小超出登记预算');
      entry.tile=tile;this.bytes+=tile.bytes;return tile;
    }).catch(error=>{if(this.entries.get(id)===entry)this.entries.delete(id);throw error;}).finally(()=>{this.reserved-=expected;entry.promise=null;});
    return entry.promise;
  }
  releaseToken(token){
    this.liveTokens.delete(token);
    for(const entry of this.entries.values())if(entry.tokens.delete(token)&&!entry.tokens.size&&entry.promise)entry.controller.abort();
  }
  async prepare(owner,bounds,{signal}={}){
    if(this.disposed||signal?.aborted)throw abortError();
    const generation=(this.generations.get(owner)??0)+1;this.generations.set(owner,generation);
    const token=`${owner}:${generation}`,ids=this.ids(bounds),pending=this.operations.get(owner)??new Set();
    this.operations.set(owner,pending);pending.add(token);this.liveTokens.add(token);
    const cancel=()=>this.releaseToken(token);signal?.addEventListener('abort',cancel,{once:true});
    try{
      const jobs=[];
      // 同步预约失败时，已启动任务仍需等到settled，避免游离拒绝和重复执行器。
      let reservationError;
      for(const id of ids){try{jobs.push(this.acquire(id,token));}catch(error){reservationError=error;break;}}
      if(reservationError)this.releaseToken(token);
      const settled=await Promise.allSettled(jobs),failure=settled.find(row=>row.status==='rejected');
      if(reservationError)throw reservationError;if(failure)throw failure.reason;
      if(signal?.aborted||this.generations.get(owner)!==generation||this.disposed)throw abortError();
      const previous=this.owners.get(owner);this.owners.set(owner,token);if(previous)this.releaseToken(previous);
      return {state:'ready',ids};
    }catch(error){this.releaseToken(token);throw error;}
    finally{pending.delete(token);signal?.removeEventListener('abort',cancel);this.cleanupOwner(owner);}
  }
  release(owner){
    this.generations.set(owner,(this.generations.get(owner)??0)+1);
    const token=this.owners.get(owner);if(token)this.releaseToken(token);this.owners.delete(owner);
    for(const pending of this.operations.get(owner)??[])this.releaseToken(pending);
    this.cleanupOwner(owner);
  }
  query(x,z,hint){
    if(![x,z].every(Number.isFinite))throw new Error('区域查询坐标无效');
    const b=this.index.bounds;
    if(x<b.minX||x>b.maxX||z<b.minZ||z>b.maxZ)return {state:'unsupported',height:NaN};
    const xx=Math.min(this.index.grid[0]-1,Math.floor((x-b.minX)/this.index.chunk_m)),zz=Math.min(this.index.grid[1]-1,Math.floor((z-b.minZ)/this.index.chunk_m));
    const id=`${String(xx).padStart(2,'0')}-${String(zz).padStart(2,'0')}`,entry=this.entries.get(id);
    if(this.rows.get(id)?.empty)return {state:'unsupported',height:NaN};
    if(!entry?.tile)return {state:'pending',height:NaN,region:id};
    entry.used=++this.clock;return entry.tile.query(x,z,hint);
  }
  collisions(bounds){
    const ids=this.ids(bounds),shapes=new Map();
    for(const id of ids){const entry=this.entries.get(id);if(!entry?.tile)return {state:'pending',shapes:[],region:id};for(const shape of entry.tile.shapes??[])shapes.set(shape.id,shape);}
    return {state:'ready',shapes:[...shapes.values()]};
  }
  segment(a,b){
    const ids=this.ids({minX:Math.min(a.x,b.x),maxX:Math.max(a.x,b.x),minZ:Math.min(a.z,b.z),maxZ:Math.max(a.z,b.z)});let hit=null;
    for(const id of ids){const tile=this.entries.get(id)?.tile;if(!tile)return {state:'pending',region:id,hit:null};const t=tile.segment?.(a,b);if(t!==null&&t!==undefined&&(hit===null||t<hit))hit=t;}
    return {state:'ready',hit};
  }
  trimUnreferenced(){for(const [id,entry] of this.entries)if(!entry.tokens.size&&!entry.promise){this.entries.delete(id);this.bytes-=entry.tile.bytes;}}
  stats(){return {bytes:this.bytes,reserved:this.reserved,regions:this.entries.size,pending:[...this.entries.values()].filter(e=>e.promise).length,owners:this.owners.size,limit:this.limit};}
  dispose(){this.disposed=true;for(const entry of this.entries.values())entry.controller.abort();this.entries.clear();this.owners.clear();this.operations.clear();this.generations.clear();this.liveTokens.clear();this.bytes=0;}
}
