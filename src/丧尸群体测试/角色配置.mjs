// 资源身份与群体分配不依赖Three.js；文件真实性由服务端交付读取器核验。
export function modelDescriptors(manifest){
  const rows=manifest.models??[{
    id:'model:zombie-runtime',source_mesh_index:0,bones:manifest.bones,
    materials:1,triangles:800,url:'/asset.glb',asset:manifest.asset,
    clips:Object.entries(manifest.checks??{}).map(([name,c])=>({name,duration_s:c.duration_s}))
  }];
  if(!Array.isArray(rows)||(rows.length!==1&&rows.length!==13))throw new Error('角色清单必须为单款或完整十三款');
  if(rows.length===13&&!manifest.delivery)throw new Error('十三款清单缺少冻结交付身份');
  const ids=new Set(),sources=new Set();
  for(const row of rows){
    if(typeof row.id!=='string'||ids.has(row.id))throw new Error('角色稳定ID缺失或重复');
    if(!Number.isInteger(row.source_mesh_index)||sources.has(row.source_mesh_index))throw new Error('源网格身份缺失或重复');
    if(!Number.isInteger(row.bones)||row.bones<1)throw new Error('角色骨骼数无效');
    if(!Number.isInteger(row.materials)||row.materials<1)throw new Error('角色材质数无效');
    if(typeof row.url!=='string'||!row.url.startsWith('/')||row.url.startsWith('//'))throw new Error('角色地址必须为本机服务路径');
    for(const name of ['待机','移动']){
      const clip=row.clips?.find(c=>c.name===name);
      if(!clip||!Number.isFinite(clip.duration_s)||clip.duration_s<=0)throw new Error('角色缺少有效待机或移动片段');
    }
    if(!row.asset||!Number.isInteger(row.asset.bytes)||row.asset.bytes<=0||!/^[a-f0-9]{64}$/.test(row.asset.sha256))throw new Error('角色缺少文件摘要或大小');
    ids.add(row.id);sources.add(row.source_mesh_index);
  }
  if(!ids.has('model:zombie-runtime'))throw new Error('编号0稳定ID缺失');
  if(rows.length===13&&Array.from({length:13},(_,i)=>i).some(i=>!sources.has(i)))throw new Error('十三款源网格编号不完整');
  return rows;
}

export function assignModels(count,models,seed,kind='mixed'){
  if(!Number.isInteger(count)||count<1||count>5000)throw new Error('角色数量超出1至5000范围');
  if(!models.length||!['single','mixed'].includes(kind))throw new Error('群体资源模式无效');
  const base=models.findIndex(m=>m.id==='model:zombie-runtime');
  if(base<0)throw new Error('单款对照资源缺失');
  if(kind==='single')return Array(count).fill(base);
  const indices=Array.from({length:count},(_,i)=>i%models.length);
  // 先均匀分配再洗牌；模型随机流不消耗群体导航的随机状态。
  let state=Number(seed)>>>0;
  for(let i=count-1;i>0;i--){
    state=(Math.imul(state,1664525)+1013904223)>>>0;
    const j=Math.floor(state/4294967296*(i+1));[indices[i],indices[j]]=[indices[j],indices[i]];
  }
  return indices;
}

export function modelDistribution(models,assignments){
  const rows=models.map(m=>({id:m.id,source_mesh_index:m.source_mesh_index,bones:m.bones,asset:m.asset,count:0}));
  for(const index of assignments){if(!rows[index])throw new Error('分配引用未知角色');rows[index].count++;}
  return rows;
}

export function summarizeModelRows(rows,distribution){
  // 配置分母固定；缺帧、重复款或计数变化均标明证据不完整。
  return distribution.map(({id,count})=>{
    let complete=rows.length>0,minVisible=count,minAnimated=count,moving=0;
    for(const row of rows){
      const matches=(row.models??[]).filter(m=>m.id===id),m=matches[0];
      if(matches.length!==1||m.count!==count||!['visible','animated','moving'].every(k=>Number.isInteger(m[k])&&m[k]>=0&&m[k]<=count)){
        complete=false;continue;
      }
      minVisible=Math.min(minVisible,m.visible);minAnimated=Math.min(minAnimated,m.animated);moving+=m.moving;
    }
    return {id,count,complete,frames:rows.length,minVisible:complete?minVisible:null,minAnimated:complete?minAnimated:null,meanMoving:complete?moving/rows.length:null};
  });
}
