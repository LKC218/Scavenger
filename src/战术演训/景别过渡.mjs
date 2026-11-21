/** 地图重层的可取消短淡变。总览提供连续覆盖，状态机不接管镜头、输入或模拟。 */
export const MAP_TRANSITION=Object.freeze({fadeOutMs:120,fadeInMs:120,historyLimit:64});
const clamp=n=>Math.max(0,Math.min(1,n)),ease=t=>t*t*(3-2*t);

export class MapLevelTransition{
 constructor({now=()=>performance.now(),requestFrame=fn=>requestAnimationFrame(fn),cancelFrame=id=>cancelAnimationFrame(id),onChange=()=>{},fadeOutMs=120,fadeInMs=120,reducedMotion=globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches??false}={}){
  Object.assign(this,{now,requestFrame,cancelFrame,onChange,fadeOutMs:reducedMotion?0:fadeOutMs,fadeInMs:reducedMotion?0:fadeInMs});this.uniform={value:0};this.generation=0;this.displayedGeneration=0;this.targetLevel='overview';this.displayedLevel='overview';this.displayedMode='overview';this.phase='idle';this.pending=false;this.disposed=false;this.commits=0;this.progress=1;this.reason='';this.history=[];this.peaks={chunks:0,geometry:0,textures:0,cpu:0,simulation:0};this.timings={outMs:0,waitMs:0,inMs:0,totalMs:0};
 }
 current(generation){return !this.disposed&&generation===this.generation;}
 publish(event){if(event){this.history.push({event,generation:this.generation,at:this.now(),coverage:this.uniform.value,displayedGeneration:this.displayedGeneration,timings:{...this.timings}});if(this.history.length>MAP_TRANSITION.historyLimit)this.history.shift();}this.onChange(this);}
 cancelAnimation(){const run=this.run;if(!run)return;this.run=null;this.cancelFrame(run.frame);run.signal?.removeEventListener('abort',run.abort);this.timings[run.field]=run.base+Math.max(0,this.now()-run.start);this.publish('cancel-'+run.phase);run.resolve(false);}
 request(generation,level,mode){if(this.disposed)return;this.cancelAnimation();this.generation=generation;this.targetLevel=level;this.targetMode=mode;this.requestedAt=this.now();this.waitStartedAt=this.requestedAt;this.phase=this.uniform.value>0?'preparing':'waiting';this.pending=true;this.progress=0;this.reason='';this.timings={outMs:0,waitMs:0,inMs:0,totalMs:0};this.publish('request');}
 animate(generation,to,duration,phase,signal){
  if(!this.current(generation)||signal?.aborted)return Promise.resolve(false);this.cancelAnimation();const from=this.uniform.value,start=this.now();this.phase=phase;this.progress=0;this.publish(phase);
  return new Promise(resolve=>{
   const field=phase==='out'?'outMs':'inMs',run={generation,resolve,signal,frame:null,start,phase,field,base:this.timings[field]};this.run=run;
   const finish=()=>{if(this.run!==run)return;this.run=null;signal?.removeEventListener('abort',run.abort);this.timings[field]=run.base+Math.max(0,this.now()-start);this.progress=1;this.publish(phase+'-complete');resolve(true);};
   run.abort=()=>{if(this.run!==run)return;this.cancelAnimation();};signal?.addEventListener('abort',run.abort,{once:true});
   const tick=()=>{if(this.run!==run||!this.current(generation)||signal?.aborted){if(this.run===run)this.cancelAnimation();return;}const elapsed=Math.max(0,this.now()-start),t=duration<=0?1:clamp(elapsed/duration);this.uniform.value=from+(to-from)*ease(t);this.progress=t;this.timings[field]=run.base+elapsed;this.publish();if(t>=1){this.uniform.value=to;finish();}else run.frame=this.requestFrame(tick);};
   tick();
  });
 }
 async fadeOut(generation,signal){if(!this.current(generation))return false;const completed=await this.animate(generation,0,this.fadeOutMs*this.uniform.value,'out',signal);if(completed&&this.current(generation)){this.phase='waiting';this.waitStartedAt=this.now();this.publish('waiting');}return completed&&this.current(generation);}
 retired(generation){if(!this.current(generation))return false;this.displayedLevel='overview';this.displayedMode='overview';this.displayedGeneration=0;this.uniform.value=0;this.phase='waiting';this.waitStartedAt=this.now();this.publish('retired');return true;}
 async committed(generation,level,mode,signal,{instant=false}={}){
  if(!this.current(generation)||signal?.aborted)return false;this.timings.waitMs=Math.max(0,this.now()-(this.waitStartedAt??this.requestedAt));this.displayedGeneration=generation;this.displayedLevel=level;this.displayedMode=mode;this.commits++;this.pending=false;this.publish('commit');
  const completed=await this.animate(generation,1,instant?0:this.fadeInMs*(1-this.uniform.value),'in',signal);
  if(completed&&this.current(generation)){this.phase='steady';this.timings.totalMs=Math.max(0,this.now()-this.requestedAt);this.publish('steady');}return completed&&this.current(generation);
 }
 baseOnly(generation,reason=''){if(!this.current(generation))return;this.cancelAnimation();this.uniform.value=0;this.displayedGeneration=0;this.displayedLevel=this.displayedMode='overview';this.pending=false;this.phase='steady';this.progress=1;this.reason=reason;this.timings.waitMs=Math.max(0,this.now()-this.waitStartedAt);this.timings.totalMs=Math.max(0,this.now()-this.requestedAt);this.publish('base-only');}
 resources(used){for(const key of Object.keys(this.peaks))this.peaks[key]=Math.max(this.peaks[key],Number(used[key])||0);}
 snapshot({baseVisible=false,detailVisible=false,baseReady=false}={}){
  const detail=detailVisible?this.uniform.value:0,base=baseReady&&baseVisible?1:0;
  return{active:!this.disposed&&!['idle','steady'].includes(this.phase),pending:this.pending,phase:this.phase,generation:this.generation,targetLevel:this.targetLevel,displayedLevel:this.displayedLevel,displayedGeneration:this.displayedGeneration,progress:this.progress,detailCoverage:detail,coverage:this.disposed?0:Math.max(base,detail),layers:[{id:'overview',level:'overview',generation:0,opacity:1,visible:baseVisible,coverage:base},{id:'detail',level:this.displayedLevel,generation:this.displayedGeneration,opacity:this.uniform.value,visible:detailVisible,coverage:detail}],timings:{...this.timings,totalMs:this.phase==='steady'?this.timings.totalMs:Math.max(0,this.now()-(this.requestedAt??this.now())),waitMs:['preparing','waiting'].includes(this.phase)?Math.max(0,this.now()-(this.waitStartedAt??this.now())):this.timings.waitMs},peaks:{...this.peaks},commits:this.commits,reason:this.reason};
 }
 dispose(){if(this.disposed)return;this.cancelAnimation();this.disposed=true;this.phase='disposed';this.pending=false;this.uniform.value=0;this.publish('dispose');}
}

// 固定屏幕像素8×8排序阈值。没有时间种子，不会逐帧随机闪烁。
export const COVERAGE_FRAGMENT=`uniform float g01MapCoverage;
float g01Bayer2(vec2 p){return 2.0*p.x+3.0*p.y-4.0*p.x*p.y;}
float g01Threshold(vec2 xy){vec2 p=mod(floor(xy),8.0);return (16.0*g01Bayer2(mod(p,2.0))+4.0*g01Bayer2(mod(floor(p/2.0),2.0))+g01Bayer2(floor(p/4.0))+0.5)/64.0;}
`;

/** 保留原材质、树图集和深度写入，仅给地图重层增加覆盖丢弃。纹理及几何不克隆。 */
export function createMapCoverageBinding(uniform){
 const bound=new WeakSet();let materials=0;
 const material=m=>{
  if(!m||bound.has(m))return;bound.add(m);materials++;const compile=m.onBeforeCompile,key=m.customProgramCacheKey?.call(m)??'';
  m.onBeforeCompile=function(shader,...args){compile?.call(this,shader,...args);shader.uniforms.g01MapCoverage=uniform;shader.fragmentShader=COVERAGE_FRAGMENT+shader.fragmentShader.replace(/void\s+main\s*\(\s*\)\s*\{/,match=>match+'\nif(g01MapCoverage<1.0&&g01Threshold(gl_FragCoord.xy)>=g01MapCoverage)discard;\n');};
  m.customProgramCacheKey=()=>key+'|g01-map-coverage-v1';m.needsUpdate=true;
  const disposed=()=>{materials--;m.removeEventListener?.('dispose',disposed);};m.addEventListener?.('dispose',disposed);
 };
 return{bindMaterial:material,bind(root){root?.traverse(object=>{if(object.isMesh)for(const m of [object.material].flat())material(m);});return root;},get materialCount(){return materials;}};
}
