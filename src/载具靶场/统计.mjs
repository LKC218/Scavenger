/** 以单调时间戳采样，预热与正式测量分别计时。 */
const percentile=(a,n)=>a[Math.min(a.length-1,Math.floor(a.length*n))]??0;
export class Benchmark{
  constructor(){this.run=null;this.last=null;}
  start(now,seconds,meta){this.run={start:now,warmup:10,seconds,meta,frames:[],cpu:[],timeline:[],next:0};return this.run;}
  step(now,dt,cpu,state){
    const r=this.run;if(!r)return null;const elapsed=(now-r.start)/1000-r.warmup;
    if(elapsed<0)return '预热 '+Math.ceil(-elapsed)+' 秒';
    r.frames.push(dt);r.cpu.push(cpu);
    if(elapsed>=r.next){r.next=elapsed+1;r.timeline.push({seconds:elapsed,...state});}
    if(elapsed<r.seconds)return '采样 '+Math.min(r.seconds,Math.floor(elapsed))+' / '+r.seconds+' 秒';
    const f=[...r.frames].sort((a,b)=>a-b),c=[...r.cpu].sort((a,b)=>a-b);
    this.last={schema:1,date:new Date().toISOString(),...r.meta,duration:elapsed,frames:f.length,p50:percentile(f,.5),p95:percentile(f,.95),p99:percentile(f,.99),max:f.at(-1)??0,over20ms:f.filter(n=>n>20).length,over33ms:f.filter(n=>n>33.34).length,over50ms:f.filter(n=>n>50).length,cpuP95:percentile(c,.95),timeline:r.timeline};this.run=null;return '完成 · P95 '+this.last.p95.toFixed(2)+' 毫秒';
  }
  cancel(){this.run=null;}
}
