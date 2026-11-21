import * as THREE from 'three';
import {OBSTACLES} from './群体.mjs';

export class View {
  constructor(host){
    this.host=host;this.width=640;this.scene=new THREE.Scene();this.scene.background=new THREE.Color('#394936');
    this.renderer=new THREE.WebGLRenderer({antialias:false,powerPreference:'high-performance'});
    this.renderer.setPixelRatio(1);this.renderer.outputColorSpace=THREE.SRGBColorSpace;
    this.renderer.toneMapping=THREE.ACESFilmicToneMapping;this.renderer.toneMappingExposure=1.25;
    host.append(this.renderer.domElement);this.resize();
    this.scene.add(new THREE.HemisphereLight(0xe9efdc,0x65754a,2.4));
    const sun=new THREE.DirectionalLight(0xffedd3,3.2);sun.position.set(-70,110,40);this.scene.add(sun);
    this.camera=new THREE.OrthographicCamera(-40,40,22.5,-22.5,.1,300);
    this.center=new THREE.Vector3();this.targetCenter=null;this.span=43;this.targetSpan=43;
    this.angle=0;this.targetAngle=0;this.vx=0;this.vz=0;this.keys=new Set();this.anchor=null;this.locked=false;
    this.ray=new THREE.Raycaster();this.plane=new THREE.Plane(new THREE.Vector3(0,1,0),0);
    this.frustum=new THREE.Frustum();this.proj=new THREE.Matrix4();this.sphere=new THREE.Sphere(new THREE.Vector3(),1.1);
    this.obstacles=new THREE.Group();this.scene.add(this.obstacles);
    const floor=new THREE.Mesh(new THREE.PlaneGeometry(58,44),new THREE.MeshStandardMaterial({color:0x637454,roughness:1}));
    floor.rotation.x=-Math.PI/2;this.scene.add(floor);
    const grid=new THREE.GridHelper(60,30,0x83916a,0x6a7958);grid.position.y=.006;grid.material.transparent=true;grid.material.opacity=.19;this.scene.add(grid);
    const wallMat=new THREE.MeshStandardMaterial({color:0x9b9e81,roughness:1});
    for(const o of OBSTACLES){
      const box=new THREE.Mesh(new THREE.BoxGeometry(o.w,2.5,o.d),wallMat);box.position.set(o.x,1.25,o.z);this.obstacles.add(box);
      const edge=new THREE.LineSegments(new THREE.EdgesGeometry(box.geometry),new THREE.LineBasicMaterial({color:0xc4c4a2,transparent:true,opacity:.35}));box.add(edge);
    }
    this.marker=new THREE.Mesh(new THREE.RingGeometry(.7,.86,40),new THREE.MeshBasicMaterial({color:0xd4ee80,side:THREE.DoubleSide,depthTest:false}));
    this.marker.rotation.x=-Math.PI/2;this.marker.position.set(17,.08,0);this.scene.add(this.marker);
    this.area=new THREE.Mesh(new THREE.RingGeometry(7.8,7.85,60),new THREE.MeshBasicMaterial({color:0xd4ee80,transparent:true,opacity:.24,side:THREE.DoubleSide}));
    this.area.rotation.x=-Math.PI/2;this.area.position.copy(this.marker.position);this.scene.add(this.area);
    const shadowGeo=new THREE.CircleGeometry(.34,8);shadowGeo.rotateX(-Math.PI/2);
    this.shadows=new THREE.InstancedMesh(shadowGeo,new THREE.MeshBasicMaterial({color:0x17251a,transparent:true,opacity:.25,depthWrite:false}),500);
    this.shadows.frustumCulled=false;this.shadows.instanceMatrix.setUsage(THREE.DynamicDrawUsage);this.scene.add(this.shadows);this.temp=new THREE.Object3D();
    this.bind();this.updateCamera(1);
  }
  resize(width=this.width){this.width=width;this.renderer.setSize(width,width*9/16,false);}
  point(clientX,clientY){
    const r=this.renderer.domElement.getBoundingClientRect();
    const p=new THREE.Vector2((clientX-r.left)/r.width*2-1,-(clientY-r.top)/r.height*2+1);
    this.ray.setFromCamera(p,this.camera);return this.ray.ray.intersectPlane(this.plane,new THREE.Vector3());
  }
  home(instant=false){
    this.targetCenter=new THREE.Vector3();this.targetSpan=43;this.targetAngle=0;this.anchor=null;
    if(instant){this.center.set(0,0,0);this.span=43;this.angle=0;this.vx=this.vz=0;this.updateCamera(0);}
  }
  focus(agents){
    if(this.locked)return;const c=new THREE.Vector3();for(const a of agents){c.x+=a.x;c.z+=a.z;}c.multiplyScalar(1/agents.length);this.targetCenter=c;
  }
  bind(){
    const canvas=this.renderer.domElement;
    window.addEventListener('keydown',e=>{
      if(/INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName)||e.ctrlKey||e.metaKey)return;
      if(['KeyW','KeyA','KeyS','KeyD'].includes(e.code)){this.keys.add(e.code);e.preventDefault();this.targetCenter=null;}
      if(e.code==='KeyF'&&!this.locked)this.home();
    });
    window.addEventListener('keyup',e=>this.keys.delete(e.code));
    window.addEventListener('blur',()=>{this.keys.clear();this.vx=this.vz=0;this.anchor=null;this.targetCenter=null;});
    canvas.addEventListener('wheel',e=>{
      e.preventDefault();if(this.locked)return;
      this.anchor={x:e.clientX,y:e.clientY,point:this.point(e.clientX,e.clientY)};
      this.targetSpan=THREE.MathUtils.clamp(this.targetSpan*Math.exp(e.deltaY*.0011),14,75);this.targetCenter=null;
    },{passive:false});
    let dragging=false,lastX=0;
    canvas.addEventListener('pointerdown',e=>{if(e.button===1){e.preventDefault();dragging=true;lastX=e.clientX;canvas.setPointerCapture(e.pointerId);}});
    canvas.addEventListener('pointermove',e=>{if(dragging&&!this.locked){this.targetAngle-=(e.clientX-lastX)*.004;this.anchor=null;this.targetCenter=null;}lastX=e.clientX;});
    canvas.addEventListener('pointerup',()=>dragging=false);
    canvas.addEventListener('contextmenu',e=>{e.preventDefault();if(!this.locked){const p=this.point(e.clientX,e.clientY);if(p)this.onTarget?.(p);}});
  }
  updateCamera(dt){
    const oldSpan=this.span;this.span+=(this.targetSpan-this.span)*(1-Math.exp(-dt/.05));
    this.angle+=(this.targetAngle-this.angle)*(1-Math.exp(-dt/.028));
    if(!this.locked){
      let ix=Number(this.keys.has('KeyD'))-Number(this.keys.has('KeyA')),iz=Number(this.keys.has('KeyS'))-Number(this.keys.has('KeyW'));
      const norm=Math.hypot(ix,iz);if(norm>0){ix/=norm;iz/=norm;this.anchor=null;}
      const tx=(ix*Math.cos(this.angle)+iz*Math.sin(this.angle))*this.span*.48;
      const tz=(-ix*Math.sin(this.angle)+iz*Math.cos(this.angle))*this.span*.48;
      const tau=norm?.045:.022,k=1-Math.exp(-dt/tau);
      this.center.x+=tx*dt+(this.vx-tx)*tau*k;this.center.z+=tz*dt+(this.vz-tz)*tau*k;
      this.vx+=(tx-this.vx)*k;this.vz+=(tz-this.vz)*k;
      if(this.targetCenter)this.center.lerp(this.targetCenter,1-Math.exp(-dt/.075));
    }
    this.center.x=THREE.MathUtils.clamp(this.center.x,-25,25);this.center.z=THREE.MathUtils.clamp(this.center.z,-20,20);
    this.camera.left=-this.span*16/18;this.camera.right=this.span*16/18;this.camera.top=this.span/2;this.camera.bottom=-this.span/2;
    this.camera.updateProjectionMatrix();
    const place=()=>{this.camera.position.set(this.center.x+Math.sin(this.angle)*60,70.8,this.center.z+Math.cos(this.angle)*60);this.camera.lookAt(this.center);this.camera.updateMatrixWorld();};place();
    if(this.anchor&&Math.abs(oldSpan-this.span)>.00001){
      const p=this.point(this.anchor.x,this.anchor.y);if(p){this.center.x+=this.anchor.point.x-p.x;this.center.z+=this.anchor.point.z-p.z;place();}
    }
    if(Math.abs(this.targetSpan-this.span)<.0001)this.anchor=null;
  }
  sync(crowd,actors){
    this.obstacles.visible=crowd.mode==='channel';this.marker.position.set(crowd.target.x,.08,crowd.target.z);this.area.position.copy(this.marker.position);
    if(crowd.count>this.shadows.instanceMatrix.count){
      const old=this.shadows;
      this.shadows=new THREE.InstancedMesh(old.geometry,old.material,crowd.count);
      this.shadows.frustumCulled=false;this.shadows.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      this.scene.remove(old);old.dispose();this.scene.add(this.shadows);
    }
    this.shadows.count=crowd.count;
    for(let i=0;i<crowd.count;i++){
      const a=crowd.agents[i];this.temp.position.set(a.x,.025,a.z);this.temp.scale.setScalar(a.scale);this.temp.updateMatrix();this.shadows.setMatrixAt(i,this.temp.matrix);
    }
    this.shadows.instanceMatrix.needsUpdate=true;
    this.proj.multiplyMatrices(this.camera.projectionMatrix,this.camera.matrixWorldInverse);this.frustum.setFromProjectionMatrix(this.proj);
    let visible=0;for(let i=0;i<crowd.count;i++){
      const a=crowd.agents[i],o=actors?.items[i],height=(o?.height??1.75)*a.scale;
      this.sphere.radius=height*.65;this.sphere.center.set(o?.root.position.x??a.x,height/2,o?.root.position.z??a.z);
      if(this.frustum.intersectsSphere(this.sphere)){visible++;if(o)actors.frameCounts[o.modelIndex].visible++;}
    }
    return visible;
  }
  render(){this.renderer.render(this.scene,this.camera);}
  config(){return {internal:[this.renderer.domElement.width,this.renderer.domElement.height],display:[this.renderer.domElement.clientWidth,this.renderer.domElement.clientHeight],dpr:1,projection:'orthographic',span:this.span,angle:this.angle,center:this.center.toArray(),realTimeShadows:false,postProcessing:false,antialias:false};}
  screen(x,z){const p=new THREE.Vector3(x,0,z).project(this.camera),r=this.renderer.domElement.getBoundingClientRect();return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2};}
}
