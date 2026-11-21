/** 大跨度镜头以绝对单调时间作对数插值；输入可随时接管。 */
export class CameraScaleTransition {
  constructor(duration=320){this.duration=duration;this.active=false;this.from=1;this.to=1;this.started=0;this.progress=1;}
  start(from,to,now){
    if(![from,to,now].every(Number.isFinite)||from<=0||to<=0)throw new Error('镜头过渡参数无效');
    this.from=from;this.to=to;this.started=now;this.progress=0;this.active=from!==to&&this.duration>0;return from;
  }
  sample(now){
    if(!this.active)return this.to;
    const t=Math.max(0,Math.min(1,(now-this.started)/this.duration)),ease=t*t*(3-2*t);
    this.progress=t;this.active=t<1;return t===0?this.from:t===1?this.to:Math.exp(Math.log(this.from)+(Math.log(this.to)-Math.log(this.from))*ease);
  }
  cancel(value){this.active=false;this.from=this.to=value;this.progress=1;return value;}
  state(){return{active:this.active,from:this.from,to:this.to,progress:this.progress,durationMs:this.duration};}
}
