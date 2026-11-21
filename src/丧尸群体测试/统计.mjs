import {summarizeModelRows} from './角色配置.mjs';
const avg=a=>a.length?a.reduce((x,y)=>x+y,0)/a.length:null;
const percentile=(a,p)=>a.length?a[Math.min(a.length-1,Math.ceil(a.length*p)-1)]:null;

export class GpuTimer {
  constructor(renderer){this.gl=renderer.getContext();this.ext=this.gl.getExtension('EXT_disjoint_timer_query_webgl2');this.pending=[];this.last=null;}
  poll(){
    if(!this.ext)return;
    const gl=this.gl,disjoint=gl.getParameter(this.ext.GPU_DISJOINT_EXT);
    this.pending=this.pending.filter(({q,row})=>{
      if(disjoint){gl.deleteQuery(q);return false;}
      if(!gl.getQueryParameter(q,gl.QUERY_RESULT_AVAILABLE))return true;
      const ms=gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6;row.gpu=ms;this.last=ms;gl.deleteQuery(q);return false;
    });
  }
  begin(){
    this.poll();if(!this.ext||this.pending.length>=5)return;
    this.active=this.gl.createQuery();this.gl.beginQuery(this.ext.TIME_ELAPSED_EXT,this.active);
  }
  end(row){if(this.active){this.gl.endQuery(this.ext.TIME_ELAPSED_EXT);this.pending.push({q:this.active,row});this.active=null;}}
}

export function summarize(rows){
  const times=rows.map(r=>r.frame).sort((a,b)=>a-b),total=times.reduce((a,b)=>a+b,0);
  const cpu={};for(const key of ['simulation','animation','sync','submit'])cpu[key]=avg(rows.map(r=>r[key]));
  return {frames:rows.length,elapsedS:total/1000,fps:total?rows.length*1000/total:null,
    medianMs:percentile(times,.5),p95Ms:percentile(times,.95),p99Ms:percentile(times,.99),maxMs:times.at(-1)??null,
    over33Pct:rows.length?100*rows.filter(r=>r.frame>33.3).length/rows.length:0,
    over50Pct:rows.length?100*rows.filter(r=>r.frame>50).length/rows.length:0,
    cpuMs:cpu,gpuMs:avg(rows.filter(r=>r.gpu!==null).map(r=>r.gpu)),gpuSamples:rows.filter(r=>r.gpu!==null).length,
    minVisible:rows.length?Math.min(...rows.map(r=>r.visible)):0,
    minAnimated:rows.length?Math.min(...rows.map(r=>r.animated)):0,
    meanMoving:avg(rows.map(r=>r.moving)),meanCalls:avg(rows.map(r=>r.calls)),meanTriangles:avg(rows.map(r=>r.triangles)),
    simulationDroppedS:rows.reduce((s,r)=>s+r.dropped,0)};
}

export class Metrics {
  constructor(){this.recent=[];this.state='idle';this.result=null;this.rows=[];this.history=[];}
  start(config,environment,{warmup=10,duration=60}={}){
    this.config=structuredClone(config);this.environment=structuredClone(environment);this.warmup=warmup;this.duration=duration;
    this.startAt=performance.now();this.captureAt=this.startAt+warmup*1000;this.endAt=this.captureAt+duration*1000;
    this.startEpoch=Date.now()/1000;
    this.rows=[];this.interruptions=[];this.state='warmup';this.result=null;
  }
  interrupt(reason){if(this.busy)this.interruptions.push({at:performance.now(),reason});}
  get busy(){return ['warmup','recording'].includes(this.state);}
  add(row){
    this.recent.push(row);if(this.recent.length>300)this.recent.shift();
    const now=performance.now();
    if(this.state==='warmup'&&now>=this.captureAt){this.state='recording';this.firstFrame=true;}
    if(this.state==='recording'){
      if(this.firstFrame)this.firstFrame=false;else this.rows.push(row);
      if(now>=this.endAt){
        this.state='done';this.result={schema:2,createdAt:new Date().toISOString(),startEpoch:this.startEpoch,endEpoch:Date.now()/1000,config:this.config,environment:this.environment,
          warmupS:this.warmup,requestedDurationS:this.duration,valid:this.interruptions.length===0,interruptions:this.interruptions,
          longFrameThresholdMs:33.3,summary:summarize(this.rows),models:summarizeModelRows(this.rows,this.config.models??[]),rows:this.rows};
        this.history.push(this.result);if(this.history.length>1)this.history.shift();this.onDone?.(this.result);
      }
    }
  }
  cancel(){if(this.busy){this.interrupt('用户取消');this.state='idle';}}
  status(){return {state:this.state,remaining:Math.max(0,((this.state==='warmup'?this.captureAt:this.endAt)-performance.now())/1000),summary:this.result?.summary};}
  download(format='json'){
    if(!this.result)throw new Error('请先完成一次采样');
    const result=this.result;let text,type;
    if(format==='csv'){
      const cols=['t','frame','simulation','animation','sync','submit','gpu','calls','triangles','visible','animated','moving','neighbors','dropped'];
      const modelCols=(result.config.models??[]).flatMap(m=>['count','visible','animated','moving'].map(k=>m.id+':'+k));
      const meta=['# 配置 '+JSON.stringify(result.config),'# 环境 '+JSON.stringify(result.environment),'# 有效 '+result.valid];
      text='\uFEFF'+[...meta,[...cols,...modelCols].join(','),...result.rows.map(r=>[...cols.map(k=>r[k]??''),...(r.models??[]).flatMap(m=>[m.count,m.visible,m.animated,m.moving])].join(','))].join('\n');type='text/csv;charset=utf-8';
    }else{text=JSON.stringify(result,null,2);type='application/json';}
    const url=URL.createObjectURL(new Blob([text],{type})),a=document.createElement('a');a.href=url;a.download=`丧尸群体-${result.config.actorSet??'single'}-${result.config.count}-${result.config.mode}-${result.config.view.internal[0]}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
}
