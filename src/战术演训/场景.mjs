import * as THREE from 'three';
import {createModelLoader,disposeModelLoader} from '/compressed-loader.mjs';
import {mergeGeometries} from 'three/addons/utils/BufferGeometryUtils.js';
import {CLASSIC} from './地图.mjs';
import {rotationEnvelope} from './标签布局.mjs';
import {AttachedLabelLayout,UNIT_SYMBOLS,nearbySlots,attachmentLine} from './载具名牌.mjs';
import {CameraScaleTransition} from './镜头过渡.mjs';
const color={select:0xd4ee80,target:0xf89169,muted:0x8a9d72};
// 固定太阳位置；投影沿背光方向落在当前平地，方向不随镜头或车身转动。
const SUN={x:-70,y:110,z:40};
const SHADOW={tank:{width:2.65,length:5.25,height:1.65,opacity:.34},heli:{width:3.5,length:6,height:0,opacity:.25},plane:{width:7,length:5,height:0,opacity:.2}};
function shadowGeometry(count){
  // 两圈八边形：17顶点、24三角形，原圆盘为26顶点、24三角形。
  const outline=[[-.62,-1],[.62,-1],[1,-.62],[1,.62],[.62,1],[-.62,1],[-1,.62],[-1,-.62]];
  const positions=[0,0,0],colors=[1,1,1,1],indices=[];
  for(const [radius,alpha] of [[.68,.88],[1,0]])for(const [x,y] of outline){positions.push(x*radius,y*radius,0);colors.push(1,1,1,alpha);}
  for(let i=0;i<8;i++){const a=1+i,b=1+(i+1)%8,c=a+8,d=b+8;indices.push(0,a,b,a,c,d,a,d,b);}
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,4));geometry.setIndex(indices);
  geometry.setAttribute('shadowOpacity',new THREE.InstancedBufferAttribute(new Float32Array(count),1));return geometry;
}
// 依据官方片段的机身占比观察标定网页候选，不代表原作内部参数；正交缩放由span控制。
export const CAMERA_TUNING={tilt:48*Math.PI/180,defaultWidth:220,minWidth:70,panSpeed:.38,panStart:.055,panStop:.028,zoomInputPower:.85,zoomTime:.06,rotateTime:.028,focusTime:.075,altitudeFocusTime:.08};
export class Battlefield{
  constructor(host,labels){
    Object.assign(this,{host,labels});this.map=CLASSIC;this.units=new Map();this.targets=new Map();this.effects=[];this.picks=[];this.unitPicks=[];this.batches=[];this.rigs={};this.layerFilter='all';this.pathLabels=new Map();this.altitudeLinks=[];this.airFocus=new Map();this.labelLayout=new AttachedLabelLayout();this.labelStats={rows:[]};
    this.hoveredUnit=null;this.svgNS='http://www.w3.org/2000/svg';
    this.nameplateLines=document.createElementNS(this.svgNS,'svg');this.nameplateLines.classList.add('nameplate-lines');this.nameplateLines.setAttribute('aria-hidden','true');labels.prepend(this.nameplateLines);
    this.labelMeasure=document.createElement('canvas').getContext('2d');this.labelMeasure.font='600 12px "Microsoft YaHei",sans-serif';
    this.scene=new THREE.Scene();this.scene.background=new THREE.Color(0x45523b);
    this.renderer=new THREE.WebGLRenderer({antialias:false,powerPreference:'high-performance'});this.renderer.setPixelRatio(1);this.renderer.outputColorSpace=THREE.SRGBColorSpace;this.renderer.toneMapping=THREE.ACESFilmicToneMapping;this.renderer.toneMappingExposure=1.25;host.append(this.renderer.domElement);this.quality=640;
    this.camera=new THREE.OrthographicCamera(-100,100,65,-65,.1,5000);
    this.center=new THREE.Vector3(0,0,-4);this.angle=0;this.span=CAMERA_TUNING.defaultWidth*Math.max(1,host.clientHeight)/Math.max(1,host.clientWidth);this.targetSpan=this.span;this.targetAngle=0;this.panInput=new THREE.Vector2();this.panVelocity=new THREE.Vector2();this.centerTarget=null;this.zoomAnchor=null;this.fixedFocusHeight=false;this.compass=document.querySelector('.compass');this.northDirection=new THREE.Vector3();
    this.scaleTransition=new CameraScaleTransition(320);this.scaleTransition.cancel(this.span);
    this.scene.add(new THREE.HemisphereLight(0xe9efdc,0x74816c,1.6));const sun=this.sun=new THREE.DirectionalLight(0xffedd3,3.4);sun.position.set(SUN.x,SUN.y,SUN.z);this.scene.add(sun,sun.target);
    this.renderer.shadowMap.enabled=true;this.renderer.shadowMap.type=THREE.PCFShadowMap;
    sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);sun.shadow.autoUpdate=false;sun.shadow.camera.layers.set(1);
    sun.shadow.camera.near=1;sun.shadow.camera.far=1400;sun.shadow.bias=-.00008;sun.shadow.normalBias=.06;
    this.shadowMode='realtime';this.shadowStats={};this.shadowGroups=[];
    this.sunDirection=new THREE.Vector3(SUN.x,SUN.y,SUN.z).normalize();this.sunRight=new THREE.Vector3(SUN.z,0,-SUN.x).normalize();this.sunUp=new THREE.Vector3().crossVectors(this.sunDirection,this.sunRight);
    this.shadowSphere=new THREE.Sphere();this.viewFrustum=new THREE.Frustum();this.viewProjection=new THREE.Matrix4();
    this.raycaster=new THREE.Raycaster();this.groundPlane=new THREE.Plane(new THREE.Vector3(0,1,0),0);this.matrix=new THREE.Matrix4();this.unitMatrix=new THREE.Matrix4();this.dummy=new THREE.Object3D();this.temp=new THREE.Vector3();this.tint=new THREE.Color();
    this.lineMaterial=new THREE.LineBasicMaterial({color:color.select,transparent:true,opacity:.6});this.pathLine=new THREE.LineSegments(new THREE.BufferGeometry(),new THREE.LineBasicMaterial({vertexColors:true,transparent:true,opacity:.75,depthTest:false}));this.pathLine.renderOrder=5;this.scene.add(this.pathLine);
    this.altitudeLine=new THREE.LineSegments(new THREE.BufferGeometry(),new THREE.LineDashedMaterial({color:0x9ce5ff,transparent:true,opacity:.6,depthTest:false,dashSize:1.2,gapSize:.8}));this.altitudeLine.renderOrder=6;this.scene.add(this.altitudeLine);
    this.preview=new THREE.Mesh(new THREE.RingGeometry(2.1,2.4,32),new THREE.MeshBasicMaterial({color:color.select,side:THREE.DoubleSide,depthTest:false}));this.preview.rotation.x=-Math.PI/2;this.preview.visible=false;this.preview.renderOrder=5;this.scene.add(this.preview);
    this.runLine=new THREE.LineSegments(new THREE.BufferGeometry(),this.lineMaterial);this.runLine.visible=false;this.scene.add(this.runLine);
    this.commandPreview=new THREE.LineSegments(new THREE.BufferGeometry(),new THREE.LineBasicMaterial({vertexColors:true,depthTest:false,transparent:true,opacity:.85}));this.commandPreview.renderOrder=7;this.scene.add(this.commandPreview);this.commandMarkers=[];this.axisY=new THREE.Vector3(0,1,0);
    this.targetGeometry=new THREE.BoxGeometry(5,4.2,5);this.targetMaterial=new THREE.MeshStandardMaterial({color:0x82644c,roughness:1});this.stripeGeo=new THREE.BoxGeometry(5.06,.42,5.06);this.stripeMat=new THREE.MeshBasicMaterial({color:0xe9a27b});
    this.ringGeo=new THREE.RingGeometry(.94,1,32);this.ringMat=new THREE.MeshBasicMaterial({color:color.select,side:THREE.DoubleSide,depthWrite:false});
    this.shadowMat=new THREE.MeshBasicMaterial({color:0x18221c,vertexColors:true,transparent:true,depthWrite:false});
    this.shadowMat.onBeforeCompile=shader=>{shader.vertexShader='attribute float shadowOpacity;\n'+shader.vertexShader.replace('#include <color_vertex>','#include <color_vertex>\nvColor.a *= shadowOpacity;');};
    this.shadowMat.customProgramCacheKey=()=> 'range-contact-shadow-v1';
    this.effectBatch=new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1,1),new THREE.MeshBasicMaterial({color:0xffffff,transparent:true,opacity:.6,depthWrite:false}),96);this.effectBatch.count=0;this.effectBatch.frustumCulled=false;this.scene.add(this.effectBatch);
    this.ammo={};for(const [kind,geo,tint] of [['bullet',new THREE.CylinderGeometry(.11,.11,1,4),0xffffff],['missile',new THREE.ConeGeometry(.28,1.7,6),0xffbb69],['bomb',new THREE.CapsuleGeometry(.35,1.8,3,6),0x505c3d]]){const batch=new THREE.InstancedMesh(geo,new THREE.MeshBasicMaterial({color:tint}),4096);batch.count=0;batch.frustumCulled=false;this.scene.add(batch);this.ammo[kind]=batch;}
    this.resizeObserver=new ResizeObserver(()=>this.resize());this.resizeObserver.observe(host);this.resize();
  }
  terrain(map){
    if(this.terrainRoot){this.scene.remove(this.terrainRoot);if(!this.terrainRoot.userData.externalMap)this.terrainRoot.traverse(o=>{o.geometry?.dispose();if(o.material){o.material.map?.dispose();o.material.dispose();}});}
    if(map.root){this.terrainRoot=map.root;map.root.userData.externalMap=true;this.scene.add(map.root);return;}
    const root=this.terrainRoot=new THREE.Group();this.scene.add(root);const b=map.bounds;
    const canvas=document.createElement('canvas');canvas.width=canvas.height=128;const ctx=canvas.getContext('2d');ctx.fillStyle='#59664a';ctx.fillRect(0,0,128,128);for(let i=0;i<1000;i++){ctx.fillStyle=i%2?'#546044':'#5f6b4d';ctx.fillRect((i*37)%128,(i*61)%128,3,3);}
    const texture=new THREE.CanvasTexture(canvas);texture.wrapS=texture.wrapT=THREE.RepeatWrapping;texture.repeat.set(map.id==='city'?22:3,map.id==='city'?22:3);texture.magFilter=THREE.NearestFilter;
    const ground=new THREE.Mesh(new THREE.PlaneGeometry(b.maxX-b.minX+20,b.maxZ-b.minZ+20),new THREE.MeshStandardMaterial({map:texture,roughness:1,color:0xa8ad90}));ground.receiveShadow=true;ground.rotation.x=-Math.PI/2;ground.position.z=(b.maxZ+b.minZ)/2;root.add(ground);
    const vertices=[],cell=map.id==='city'?40:10;for(let x=b.minX;x<=b.maxX;x+=cell)vertices.push(x,.025,b.minZ,x,.025,b.maxZ);for(let z=b.minZ;z<=b.maxZ;z+=cell)vertices.push(b.minX,.025,z,b.maxX,.025,z);
    const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));root.add(new THREE.LineSegments(g,new THREE.LineBasicMaterial({color:0xc4ce9f,transparent:true,opacity:.12})));
    const boxes=new THREE.InstancedMesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshStandardMaterial({color:0x93987f,roughness:1}),map.obstacles.length);
    boxes.castShadow=boxes.receiveShadow=true;boxes.layers.enable(1);
    map.obstacles.forEach((o,i)=>{this.dummy.position.set(o.x,o.h/2,o.z);this.dummy.rotation.set(0,0,0);this.dummy.scale.set(o.w,o.h,o.d);this.dummy.updateMatrix();boxes.setMatrixAt(i,this.dummy.matrix);this.tint.setHex(o.building?[0x909479,0x777e6e,0x9b9679,0x7d897c][i%4]:0x77806b);boxes.setColorAt(i,this.tint);});boxes.computeBoundingSphere();root.add(boxes);
    if(map.id==='city'){
      const roads=new THREE.InstancedMesh(new THREE.PlaneGeometry(1,1),new THREE.MeshStandardMaterial({color:0x525b50,roughness:1}),9);let i=0;
      roads.receiveShadow=true;
      for(const x of [-180,-90,0,90,180]){this.dummy.position.set(x,.04,-175);this.dummy.rotation.set(-Math.PI/2,0,0);this.dummy.scale.set(20,360,1);this.dummy.updateMatrix();roads.setMatrixAt(i++,this.dummy.matrix);}
      for(const z of [-310,-220,-130,-40]){this.dummy.position.set(0,.045,z);this.dummy.scale.set(380,20,1);this.dummy.updateMatrix();roads.setMatrixAt(i++,this.dummy.matrix);}roads.computeBoundingSphere();root.add(roads);
    }
    this.dummy.scale.setScalar(1);this.dummy.rotation.set(0,0,0);
  }
  async load(manifest,progress){
    this.referenceUI=manifest.referenceUI??{};
    if(this.referenceUI.SelectionCircle){const texture=await new THREE.TextureLoader().loadAsync(this.referenceUI.SelectionCircle);texture.colorSpace=THREE.SRGBColorSpace;this.ringGeo.dispose();this.ringGeo=new THREE.PlaneGeometry(2,2);this.ringMat.map=texture;this.ringMat.transparent=true;this.ringMat.alphaTest=.04;this.ringMat.needsUpdate=true;}
    const loader=createModelLoader(this.renderer);await Promise.all(Object.entries(manifest.models).map(async([kind,row])=>{progress('加载'+({tank:'M1A2',heli:'AH-64D',plane:'A-10C'})[kind]+'…');const model=(this.streamingDownloads?await loader.parseAsync(await this.streamingDownloads.get(new URL(row.url,location.href).href,{bytes:row.bytes,sha256:row.runtime_sha256??row.sha256}),new URL('.',new URL(row.url,location.href)).href):await loader.loadAsync(row.url)).scene;this.rigs[kind]=this.buildRig(kind,model);}));
  }
  buildRig(kind,model){
    const nodes={};model.traverse(n=>{if(n.name.startsWith('原型'))nodes[n.name]=n;});model.updateMatrixWorld(true);
    const pivot=(point,parts)=>{const group=new THREE.Group();group.position.set(...point);model.add(group);model.updateMatrixWorld(true);for(const part of parts.filter(Boolean))group.attach(part);return group;};const motion={body:model};
    if(kind==='tank'){motion.turret=pivot([0,1.53,-.23],Object.entries(nodes).filter(([k])=>k.includes('炮塔附件')).map(([,v])=>v));motion.barrel=pivot([0,1.83,-1.5],[nodes['原型炮管']]);motion.turret.attach(motion.barrel);}
    if(kind==='heli'){motion.rotor=pivot([0,3.772,0],[nodes['原型主旋翼']]);motion.tail=pivot([-.64,3.02,8.98],[nodes['原型尾旋翼']]);motion.gun=pivot([0,1.097,-2.804],[nodes['原型机炮']]);}
    model.updateMatrixWorld(true);const grouped=new Map();
    model.traverse(n=>{
      if(!n.isMesh)return;let part='body';for(let a=n;a&&a!==model;a=a.parent){const name=Object.keys(motion).find(k=>motion[k]===a);if(name){part=name;break;}}
      const relative=new THREE.Matrix4().copy(motion[part].matrixWorld).invert().multiply(n.matrixWorld);
      const geo=(n.geometry.index?n.geometry.toNonIndexed():n.geometry.clone()).applyMatrix4(relative);const attrs=geo.attributes;
      if(!attrs.normal)geo.computeVertexNormals();if(!attrs.uv)geo.setAttribute('uv',new THREE.Float32BufferAttribute(new Float32Array(attrs.position.count*2),2));
      if(!attrs.uv1)geo.setAttribute('uv1',geo.attributes.uv.clone());for(const key of Object.keys(attrs))if(!['position','normal','uv','uv1'].includes(key))geo.deleteAttribute(key);
      // 烘焙镜像节点后同步三角形绕序，实例矩阵保持正尺度。
      if(relative.determinant()<0)for(const attribute of Object.values(geo.attributes)){const a=attribute.array,s=attribute.itemSize;for(let i=0;i<attribute.count;i+=3)for(let k=0;k<s;k++){const j=(i+1)*s+k,l=(i+2)*s+k,tmp=a[j];a[j]=a[l];a[l]=tmp;}}
      const materials=Array.isArray(n.material)?n.material:[n.material];
      if(materials.length===1){const key=part+'|'+materials[0].uuid;let row=grouped.get(key);if(!row)grouped.set(key,row={part,material:materials[0],geometries:[]});row.geometries.push(geo);}
      else throw new Error('模型存在未拆分多材质网格，需要明确处理');
    });
    const parts=[...grouped.values()].map(row=>{const geometry=mergeGeometries(row.geometries,false);for(const g of row.geometries)g.dispose();return{part:row.part,material:row.material,geometry};});
    const bounds=new THREE.Box3().setFromObject(model),sphere=bounds.getBoundingSphere(new THREE.Sphere());
    const labelBounds=new THREE.Box3();
    for(const p of parts){p.geometry.computeBoundingBox();let box=p.geometry.boundingBox.clone();if(kind==='heli'&&['rotor','tail'].includes(p.part)){const envelope=rotationEnvelope(box,p.part==='rotor'?'y':'x');box=new THREE.Box3(new THREE.Vector3().copy(envelope.min),new THREE.Vector3().copy(envelope.max));}labelBounds.union(box.applyMatrix4(motion[p.part].matrixWorld));}
    return{model,motion,parts,labelBounds,center:bounds.getCenter(new THREE.Vector3()),radius:sphere.radius+sphere.center.length()+.5};
  }
  setup(sim){
    this.sim=sim;
    this.airFocus.clear();this.altitudeLinks=[];
    this.labelLayout.clear();this.labelStats={rows:[]};this.hoveredUnit=null;this.nameplateLines.replaceChildren();
    if(this.map!==sim.map||!this.terrainRoot){this.map=sim.map;this.terrain(this.map);}
    for(const batch of this.batches){batch.removeFromParent();if(batch.userData.shadow)batch.geometry.dispose();batch.dispose();}this.batches=[];this.shadowGroups=[];this.picks=[];this.unitPicks=[];for(const v of this.units.values()){v.label?.remove();v.altitudeLabel?.remove();}this.units.clear();for(const label of this.pathLabels.values())label.remove();this.pathLabels.clear();
    for(const v of this.targets.values()){v.root.removeFromParent();v.label.remove();}this.targets.clear();this.effects=[];this.lastPathUpdate=undefined;
    for(const kind of ['tank','heli','plane']){
      const units=sim.units.filter(u=>u.kind===kind),rig=this.rigs[kind];if(!units.length)continue;
      if(!rig)throw new Error('缺少载具模型：'+kind);
      const add=(geo,mat)=>{const m=new THREE.InstancedMesh(geo,mat,units.length);m.frustumCulled=false;m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);this.scene.add(m);this.batches.push(m);return m;};
      const parts=rig.parts.map(p=>{const batch=add(p.geometry,p.material),caster=add(p.geometry,p.material);batch.receiveShadow=true;batch.userData.ids=[];this.unitPicks.push(batch);caster.layers.set(1);caster.castShadow=true;return{...p,batch,caster};}),rings=add(this.ringGeo,this.ringMat),shadows=add(shadowGeometry(units.length),this.shadowMat);shadows.userData.shadow=true;
      this.shadowGroups.push({kind,parts,count:0});
      units.forEach((u,index)=>{const label=document.createElement('button');label.type='button';label.className='unit-label';label.dataset.unitId=u.id;const altitudeLabel=document.createElement('div');altitudeLabel.className='altitude-marker';altitudeLabel.hidden=true;this.labels.append(label,altitudeLabel);const leader=document.createElementNS(this.svgNS,'path'),highlight=document.createElementNS(this.svgNS,'path');
        leader.classList.add('nameplate-leader');highlight.classList.add('nameplate-highlight');leader.dataset.owner=u.id;highlight.dataset.owner=u.id;this.nameplateLines.append(leader,highlight);
        this.units.set(u.id,{index,rig,parts,rings,shadows,label,altitudeLabel,leader,highlight,worldBounds:new THREE.Box3(),labelWorldBounds:new THREE.Box3(),nameplateWorldBounds:new THREE.Box3(),recoil:0,shadowIndex:-1});});
    }
    for(const t of sim.map.renderTargets===false?[]:sim.targets){const root=new THREE.Group();root.position.set(t.x,t.baseY??0,t.z);this.scene.add(root);const body=new THREE.Mesh(this.targetGeometry,this.targetMaterial);body.castShadow=body.receiveShadow=true;body.layers.enable(1);body.position.y=2.1;body.userData={type:'target',id:t.id};root.add(body);this.picks.push(body);const stripe=new THREE.Mesh(this.stripeGeo,this.stripeMat);stripe.position.y=3.3;root.add(stripe);const label=document.createElement('div');label.className='target-label';this.labels.append(label);this.targets.set(t.id,{root,body,stripe,label});}
    if(this.map.streaming)this.home(true);this.sync(sim,new Set(),null,0);this.updateCamera();
  }
  resize(){this.width=Math.max(1,this.host.clientWidth);this.height=Math.max(1,this.host.clientHeight);this.zoomAnchor=null;this.renderer.setSize(this.quality,Math.max(1,Math.round(this.quality*this.height/this.width)),false);this.updateCamera();}
  cameraGroundY(x,z){const coarse=this.map.streaming?this.map.coarseHeightAt?.(x,z):undefined;return Number.isFinite(coarse)?coarse:this.groundY(x,z);}
  updateCamera(){
    const aspect=this.width/this.height;this.camera.left=-this.span*aspect/2;this.camera.right=this.span*aspect/2;this.camera.top=this.span/2;this.camera.bottom=-this.span/2;this.camera.updateProjectionMatrix();
    const streaming=this.map.streaming,ground=streaming?this.cameraGroundY(this.center.x,this.center.z):undefined;if(!this.fixedFocusHeight&&Number.isFinite(ground))this.center.y=ground;const k=streaming?Math.min(Math.max(this.span/180,((this.map.safeAltitude??200)+40-this.center.y)/165),(this.map.camera?.max_height_m??20000)/165):Math.max(1,this.span/180);this.camera.far=streaming?30000:5000;this.camera.updateProjectionMatrix();
    const horizontal=165/Math.tan(CAMERA_TUNING.tilt)*k;
    this.groundPlane.constant=-this.center.y;this.camera.position.set(this.center.x+Math.sin(this.angle)*horizontal,this.center.y+165*k,this.center.z+Math.cos(this.angle)*horizontal);this.camera.lookAt(this.center);this.camera.updateMatrixWorld();
    if(this.compass){
      this.northDirection.set(0,0,-1).transformDirection(this.camera.matrixWorldInverse);
      this.compass.style.transform=`rotate(${Math.atan2(this.northDirection.x,this.northDirection.y)}rad)`;
    }
  }
  clampCenter(){const b=this.map.bounds;this.center.x=THREE.MathUtils.clamp(this.center.x,b.minX+7,b.maxX-7);this.center.z=THREE.MathUtils.clamp(this.center.z,b.minZ+7,b.maxZ-7);}
  getCameraTransitionState(){return this.scaleTransition.state();}
  transitionToSpan(value){const target=THREE.MathUtils.clamp(value,CAMERA_TUNING.minWidth*this.height/this.width,this.map.maxSpan);this.zoomAnchor=null;this.targetSpan=target;this.scaleTransition.start(this.span,target,performance.now());}
  interruptScaleTransition(){if(this.scaleTransition?.active){this.scaleTransition.cancel(this.span);this.targetSpan=this.span;}}
  pan(x,z){
    if(x||z)this.interruptScaleTransition();
    this.panInput.set(x,z);if(this.panInput.lengthSq()>1)this.panInput.normalize();
    if(this.panInput.lengthSq()){this.centerTarget=null;this.zoomAnchor=null;}
  }
  rotate(delta){if(delta)this.interruptScaleTransition();this.centerTarget=null;this.zoomAnchor=null;this.targetAngle+=delta;}
  dragPan(dx,dy){
    this.interruptScaleTransition();
    const scale=this.span/this.height,c=Math.cos(this.angle),s=Math.sin(this.angle),vertical=dy/Math.sin(CAMERA_TUNING.tilt);
    this.centerTarget=null;this.zoomAnchor=null;this.panVelocity.set(0,0);this.center.x+=(-dx*c-vertical*s)*scale;this.center.z+=(dx*s-vertical*c)*scale;this.clampCenter();this.updateCamera();
  }
  zoom(factor,screen={x:this.width/2,y:this.height/2}){
    this.interruptScaleTransition();
    const next=THREE.MathUtils.clamp(this.targetSpan*Math.pow(factor,CAMERA_TUNING.zoomInputPower),CAMERA_TUNING.minWidth*this.height/this.width,this.map.maxSpan);
    if(Math.abs(next-this.targetSpan)<1e-7)return;
    this.centerTarget=null;this.panVelocity.set(0,0);
    const world=this.groundPoint(screen.x,screen.y);
    this.zoomAnchor=world?{screen:{...screen},world}:null;this.targetSpan=next;
  }
  focusAt(point){
    this.interruptScaleTransition();
    this.zoomAnchor=null;this.panVelocity.set(0,0);
    this.fixedFocusHeight=Number.isFinite(point.y);const b=this.map.bounds,x=THREE.MathUtils.clamp(point.x,b.minX+7,b.maxX-7),z=THREE.MathUtils.clamp(point.z,b.minZ+7,b.maxZ-7),y=this.fixedFocusHeight?point.y:this.cameraGroundY(x,z);this.centerTarget=new THREE.Vector3(x,Number.isFinite(y)?y:this.center.y,z);
  }
  trackAt(point){this.fixedFocusHeight=true;this.centerTarget=null;this.panVelocity.set(0,0);this.center.set(point.x,point.y,point.z);this.clampCenter();this.updateCamera();}
  stopCamera(){
    this.interruptScaleTransition();
    this.panInput.set(0,0);this.panVelocity.set(0,0);this.centerTarget=null;this.zoomAnchor=null;
    this.targetSpan=this.span;this.targetAngle=this.angle;
  }
  home(immediate=false){
    this.stopCamera();this.fixedFocusHeight=false;this.targetSpan=Math.min(this.map.maxSpan,CAMERA_TUNING.defaultWidth*this.height/this.width);this.targetAngle=0;const y=this.cameraGroundY(this.map.center.x,this.map.center.z);this.centerTarget=new THREE.Vector3(this.map.center.x,Number.isFinite(y)?y:this.center.y,this.map.center.z);
    if(immediate){this.center.copy(this.centerTarget);this.centerTarget=null;this.span=this.targetSpan;this.angle=0;this.updateCamera();}
  }
  stepCamera(dt){
    if(dt<=0)return;dt=Math.min(dt,.1);
    const blend=time=>1-Math.exp(-dt/time);
    const previousSpan=this.span,previousAngle=this.angle,previousX=this.center.x,previousY=this.center.y,previousZ=this.center.z;
    this.span=this.scaleTransition.active?this.scaleTransition.sample(performance.now()):THREE.MathUtils.lerp(this.span,this.targetSpan,blend(CAMERA_TUNING.zoomTime));
    this.angle=THREE.MathUtils.lerp(this.angle,this.targetAngle,blend(CAMERA_TUNING.rotateTime));
    const rotating=Math.abs(this.targetAngle-this.angle)>.0001,zooming=Math.abs(this.targetSpan-this.span)>.0001;
    if(!rotating)this.angle=this.targetAngle;if(!zooming)this.span=this.targetSpan;
    if(this.centerTarget){
      this.center.lerp(this.centerTarget,blend(CAMERA_TUNING.focusTime));
      // 自动地面焦点的Y由当前地面采样驱动；只用XZ判定到达，避免粗略高度格边界阻止收敛。
      const distance=this.fixedFocusHeight?this.center.distanceToSquared(this.centerTarget):(this.center.x-this.centerTarget.x)**2+(this.center.z-this.centerTarget.z)**2;
      if(distance<.000001){this.center.copy(this.centerTarget);if(!this.fixedFocusHeight){const y=this.cameraGroundY(this.center.x,this.center.z);if(Number.isFinite(y))this.center.y=y;}this.centerTarget=null;}
    }
    const c=Math.cos(this.angle),s=Math.sin(this.angle),speed=this.span*this.width/this.height*CAMERA_TUNING.panSpeed;
    const desiredX=(this.panInput.x*c+this.panInput.y*s)*speed;
    const desiredZ=(-this.panInput.x*s+this.panInput.y*c)*speed;
    const tau=this.panInput.lengthSq()?CAMERA_TUNING.panStart:CAMERA_TUNING.panStop,decay=Math.exp(-dt/tau);
    // 一阶速度响应的解析积分，30/60/高刷新率下保持相同启停距离。
    this.center.x+=desiredX*dt+(this.panVelocity.x-desiredX)*tau*(1-decay);
    this.center.z+=desiredZ*dt+(this.panVelocity.y-desiredZ)*tau*(1-decay);
    this.panVelocity.set(desiredX+(this.panVelocity.x-desiredX)*decay,desiredZ+(this.panVelocity.y-desiredZ)*decay);
    if(this.panVelocity.lengthSq()<.00001)this.panVelocity.set(0,0);
    this.clampCenter();
    if((this.center.x===this.map.bounds.minX+7&&this.panVelocity.x<0)||(this.center.x===this.map.bounds.maxX-7&&this.panVelocity.x>0))this.panVelocity.x=0;
    if((this.center.z===this.map.bounds.minZ+7&&this.panVelocity.y<0)||(this.center.z===this.map.bounds.maxZ-7&&this.panVelocity.y>0))this.panVelocity.y=0;
    if(this.center.x===previousX&&this.center.y===previousY&&this.center.z===previousZ&&this.span===previousSpan&&this.angle===previousAngle)return;
    this.updateCamera();
    if(this.zoomAnchor){
      const after=this.groundPoint(this.zoomAnchor.screen.x,this.zoomAnchor.screen.y);
      if(after){this.center.x+=this.zoomAnchor.world.x-after.x;this.center.z+=this.zoomAnchor.world.z-after.z;this.clampCenter();this.updateCamera();}
      if(!zooming)this.zoomAnchor=null;
    }
  }
  project(point){const p=new THREE.Vector3(point.x,point.y??0,point.z).project(this.camera);return {x:(p.x+1)*this.width/2,y:(1-p.y)*this.height/2,visible:p.z>-1&&p.z<1&&Math.abs(p.x)<1.1&&Math.abs(p.y)<1.1};}
  ray(x,y){this.raycaster.setFromCamera(new THREE.Vector2(x/this.width*2-1,1-y/this.height*2),this.camera);return this.raycaster;}
  unitCenter(id,unit=null){
    const v=this.units.get(id),u=unit??this.sim?.units.find(u=>u.id===id);if(!v||!u)return{x:u?.x??0,y:u?.y??0,z:u?.z??0};
    const pose=new THREE.Object3D();pose.position.set(u.x,u.y,u.z);pose.rotation.set(u.bodyPitch??0,u.yaw,u.kind==='plane'?u.bank:u.kind==='tank'?(u.bodyRoll??0):u.kind==='heli'&&u.moving?Math.sin((this.sim?.time??0)*1.2)*.025:0,'YXZ');pose.updateMatrix();return v.rig.center.clone().applyMatrix4(pose.matrix);
  }
  getCameraState(){
    const viewWidth=this.span*this.width/this.height;
    return{candidate:true,tiltDegrees:CAMERA_TUNING.tilt*180/Math.PI,viewWidth,minWidth:CAMERA_TUNING.minWidth,defaultWidth:CAMERA_TUNING.defaultWidth,maxSpan:this.map.maxSpan,span:this.span,yaw:this.angle,center:{x:this.center.x,y:this.center.y,z:this.center.z},position:{x:this.camera.position.x,y:this.camera.position.y,z:this.camera.position.z},panSpeed:viewWidth*CAMERA_TUNING.panSpeed,zoomInputPower:CAMERA_TUNING.zoomInputPower,zoomTime:CAMERA_TUNING.zoomTime};
  }
  getAltitudeDisplay(id){
    const u=this.sim?.units.find(unit=>unit.id===id);if(!u||u.kind==='tank')return null;
    const state=u.altitudeState,support=this.supportAt(u.x,u.z),baseGround=Number.isFinite(state?.baseGround)?state.baseGround:support.status==='ready'&&Number.isFinite(support.height)?support.height:null;
    const runPoint={x:u.x,y:u.y,z:u.z},groundPoint=baseGround===null?null:{x:u.x,y:baseGround,z:u.z};
    return{unitId:id,status:state?.status??(groundPoint?'ready':'pending'),reason:state?.reason??(groundPoint?'':'脚下地面支撑尚未就绪'),runPoint,groundPoint,baseGround,height:groundPoint&&Number.isFinite(u.y)?u.y-baseGround:null,targetAltitude:Number.isFinite(state?.targetAltitude)?state.targetAltitude:null};
  }
  unitFocus(id,unit=null){
    const u=unit??this.sim?.units.find(unit=>unit.id===id),body=this.unitCenter(id,u),altitude=this.getAltitudeDisplay(id);
    if(!u||u.kind!=='heli'||!altitude?.groundPoint)return body;
    // 直升机优先关联中点；高障碍时限制偏移，固定翼保持机身聚焦。测量线保持完整。
    const foot=altitude.groundPoint,up=new THREE.Vector3(-Math.sin(this.angle)*Math.sin(CAMERA_TUNING.tilt),Math.cos(CAMERA_TUNING.tilt),-Math.cos(this.angle)*Math.sin(CAMERA_TUNING.tilt)),view=this.units.get(id),bounds=view?.labelWorldBounds?.clone().union(view.worldBounds)??view?.worldBounds;
    let bodyExtent=0;if(bounds&&!bounds.isEmpty())for(const x of [bounds.min.x,bounds.max.x])for(const y of [bounds.min.y,bounds.max.y])for(const z of [bounds.min.z,bounds.max.z])bodyExtent=Math.max(bodyExtent,Math.abs(up.dot(new THREE.Vector3(x-body.x,y-body.y,z-body.z))));
    const allowance=Math.max(0,this.span*.4-bodyExtent),gap=Math.abs(up.dot(new THREE.Vector3(body.x-foot.x,body.y-foot.y,body.z-foot.z))),weight=Math.min(.5,allowance/Math.max(gap,.0001));
    const point={x:body.x+(foot.x-body.x)*weight,y:body.y+(foot.y-body.y)*weight,z:body.z+(foot.z-body.z)*weight},time=this.sim?.time??0,previous=this.airFocus.get(id);
    if(previous&&Math.abs(previous.span-this.span)<.000001&&time>=previous.time&&time-previous.time<.5)point.y=THREE.MathUtils.lerp(previous.y,point.y,1-Math.exp(-(time-previous.time)/CAMERA_TUNING.altitudeFocusTime));
    const offset=up.dot(new THREE.Vector3(body.x-point.x,body.y-point.y,body.z-point.z));point.y+=(offset-THREE.MathUtils.clamp(offset,-allowance,allowance))/up.y;
    this.airFocus.set(id,{time,y:point.y,span:this.span});return point;
  }
  projectUnit(id){
    const v=this.units.get(id);if(!v)return{x:0,y:0,visible:false};const point=this.project(this.unitCenter(id));
    if(this.map.heightAt&&v.displayIndex<0)point.visible=false;
    if(!v.worldBounds.isEmpty()){const points=[];for(const x of [v.worldBounds.min.x,v.worldBounds.max.x])for(const y of [v.worldBounds.min.y,v.worldBounds.max.y])for(const z of [v.worldBounds.min.z,v.worldBounds.max.z])points.push(this.project({x,y,z}));point.bounds={left:Math.min(...points.map(p=>p.x)),right:Math.max(...points.map(p=>p.x)),top:Math.min(...points.map(p=>p.y)),bottom:Math.max(...points.map(p=>p.y))};}
    return point;
  }
  pickUnits(x,y,allowed=()=>true){
    // 使用真正显示的实例矩阵及裁剪后索引，不使用高度平移的旧代理盒。
    for(const batch of this.unitPicks){batch.updateWorldMatrix(true,false);batch.boundingSphere=null;}
    const hits=this.ray(x,y).intersectObjects(this.unitPicks.filter(p=>p.visible&&p.count>0),false),seen=new Set(),ids=[];
    for(const hit of hits){const id=hit.object.userData.ids[hit.instanceId],unit=this.sim?.units.find(u=>u.id===id);if(id&&!seen.has(id)&&unit&&allowed(unit)&&this.projectUnit(id).visible){seen.add(id);ids.push(id);}}
    return ids;
  }
  pick(x,y){const id=this.pickUnits(x,y)[0];if(id)return{type:'unit',id};return this.ray(x,y).intersectObjects(this.picks.filter(p=>p.visible),false)[0]?.object.userData??null;}
  groundPoint(x,y){const point=new THREE.Vector3(),ray=this.ray(x,y).ray;if(!ray.intersectPlane(this.groundPlane,point))return null;
    if(this.map.heightAt){for(let i=0;i<5;i++){const precise=this.map.heightAt(point.x,point.z),h=Number.isFinite(precise)?precise:this.map.coarseHeightAt?.(point.x,point.z);if(!Number.isFinite(h))break;const t=(h-ray.origin.y)/ray.direction.y;ray.at(t,point);} }return point;}
  ground(x,y){const point=this.groundPoint(x,y);if(!point)return null;const b=this.map.bounds,xz={x:point.x,z:point.z};if(xz.x<b.minX||xz.x>b.maxX||xz.z<b.minZ||xz.z>b.maxZ)return{...xz,state:'unsupported'};const support=this.supportAt(xz.x,xz.z);return{...xz,...(Number.isFinite(support.height)?{y:support.height}:{}),state:support.status};}

  supportAt(x,z){if(!this.map.heightAt)return{height:0,status:'ready'};const h=this.map.heightAt(x,z);if(Number.isFinite(h))return{height:h,status:'ready'};const coarse=this.map.coarseHeightAt?.(x,z);return{height:Number.isFinite(coarse)?coarse:undefined,status:'pending'};}
  groundY(x,z){return this.supportAt(x,z).height;}
  showPreview(point,mode,start=null){
    this.preview.visible=!!point&&!!mode;if(point){const p=mode==='bomb'&&start?{x:(point.x+start.x)/2,z:(point.z+start.z)/2}:point;this.preview.position.set(p.x,this.groundY(p.x,p.z)+.2,p.z);}
    this.preview.material.color.setHex(mode==='ground'||mode==='bomb'?color.target:color.select);
    this.runLine.visible=!!start&&!!point;if(start&&point){const dx=point.x-start.x,dz=point.z-start.z,n=Math.max(1,Math.hypot(dx,dz)),s=Math.min(16,n*.18),tip=new THREE.Vector3(point.x,.4,point.z);this.runLine.geometry.dispose();this.runLine.geometry=new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(start.x,.4,start.z),tip,tip,new THREE.Vector3(point.x-dx/n*s+dz/n*s*.5,.4,point.z-dz/n*s-dx/n*s*.5),tip,new THREE.Vector3(point.x-dx/n*s-dz/n*s*.5,.4,point.z-dz/n*s+dx/n*s*.5)]);}
  }
  showCommandPreview(rows,{append=false}={}){
    const vertices=[],colors=[],tint=new THREE.Color();
    for(const {unit,job,valid,status} of rows.slice(0,100)){
      const point=job.point;if(!point)continue;const support=this.supportAt(point.x,point.z),height=Number.isFinite(support.height)?support.height:point.y;
      // 未知支撑只显示文字“准备路径”，不绘制假造的零高度落点。
      if(!Number.isFinite(height))continue;
      const start=append?(unit.queue.at(-1)?.point??unit.job?.point??unit):unit,startHeight=Number.isFinite(start.y)?start.y:this.groundY(start.x,start.z),offset=vertices.length;
      const y=height+.35,r=unit.kind==='tank'?3:5,angle=job.facing??unit.yaw,s=Math.sin(angle),c=Math.cos(angle);
      if(Number.isFinite(startHeight))vertices.push(start.x,startHeight+.3,start.z,point.x,y,point.z);
      for(let i=0;i<20;i++){if(status==='pending'&&i%2)continue;const a=i/20*Math.PI*2,b=(i+1)/20*Math.PI*2;vertices.push(point.x+Math.sin(a)*r,y,point.z+Math.cos(a)*r,point.x+Math.sin(b)*r,y,point.z+Math.cos(b)*r);}
      const x=point.x-s*(r+3),z=point.z-c*(r+3);vertices.push(point.x,y,point.z,x,y,z,x,y,z,x+s*2+c*2,y,z+c*2-s*2,x,y,z,x+s*2-c*2,y,z+c*2+s*2);
      tint.setHex(status==='rejected'||!valid?0xff725e:status==='pending'?0xf3c66c:0x80d8ff);for(let i=offset;i<vertices.length;i+=3)colors.push(tint.r,tint.g,tint.b);
    }
    this.commandPreview.geometry.dispose();this.commandPreview.geometry=new THREE.BufferGeometry();this.commandPreview.geometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));this.commandPreview.geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));this.commandPreview.visible=vertices.length>0;
  }
  commandFeedback(point,type){
    if(!point)return;const marker=document.createElement('div');marker.className='command-marker';const attack=['attack','ground','strafe','bomb'].includes(type),url=this.referenceUI[attack?'TaskMarker_Attack':'TaskMarker_Task'];
    if(url){const image=document.createElement('img');image.src=url;image.alt='';marker.append(image);}marker.append(document.createTextNode(({move:'移动',attack:'攻击',ground:'地面攻击',reverse:'倒车',attackMove:'攻击移动',strafe:'扫射',bomb:'轰炸'})[type]??'指令'));
    this.labels.append(marker);this.commandMarkers.push({marker,point:{...point},until:performance.now()+950});if(this.commandMarkers.length>8)this.commandMarkers.shift().marker.remove();
  }
  event(event){
    if(event.type==='shot'){const v=this.units.get(event.unit);if(v)v.recoil=.35;const gun=['gun','planeGun'].includes(event.weapon);this.effect(event.position,gun?.07:.22,gun?.45:2.2,0xfff4bc);}
    if(event.type==='impact'){
      if(['gun','planeGun'].includes(event.weapon)){this.effect(event.position,.12,.6,0xffc677);this.effect(event.position,.55,event.surface==='building'?2:1.35,0x8b8371);}
      else this.effect(event.position,event.weapon==='bomb'?1.1:.6,event.weapon==='bomb'?17:4,0xffc16e);
    }
    if(event.type==='destroy')this.effect(event.position,1.1,7,0xffa75e);
  }
  effect(position,duration,size,tint){if(this.effects.length>=96)this.effects.shift();this.effects.push({position,life:duration,total:duration,size,tint});}
  prepareShadow(sim){
    const weight=this.shadowMode==='realtime'?1-THREE.MathUtils.smoothstep(this.span,220,420):0;
    this.sun.shadow.intensity=weight;this.shadowMat.opacity=1-weight;
    this.shadowMat.visible=weight<1;
    const active=weight>0,groups=this.shadowGroups;for(const g of groups)g.count=0;
    if(active){
      // 接收区域在太阳光空间拟合，保留画外投影者；纹素对齐减少移动时的抖动。
      let minR=Infinity,maxR=-Infinity,minU=Infinity,maxU=-Infinity;
      const include=(p,r=0)=>{const x=this.sunRight.dot(p),y=this.sunUp.dot(p);minR=Math.min(minR,x-r);maxR=Math.max(maxR,x+r);minU=Math.min(minU,y-r);maxU=Math.max(maxU,y+r);};
      for(const [x,y] of [[0,0],[this.width,0],[this.width,this.height],[0,this.height]]){const p=this.groundPoint(x,y);if(p)include(p,24);}
      this.viewProjection.multiplyMatrices(this.camera.projectionMatrix,this.camera.matrixWorldInverse);this.viewFrustum.setFromProjectionMatrix(this.viewProjection);
      for(const u of sim.units){if(u.kind==='tank')continue;const v=this.units.get(u.id);this.shadowSphere.center.set(u.x,u.y,u.z);this.shadowSphere.radius=v.rig.radius;if(this.viewFrustum.intersectsSphere(this.shadowSphere))include(this.shadowSphere.center,v.rig.radius+4);}
      const extent=Math.ceil(Math.max(128,maxR-minR,maxU-minU)/32)*32,texel=extent/2048;
      const cx=Math.round((minR+maxR)/2/texel)*texel,cy=Math.round((minU+maxU)/2/texel)*texel;
      this.sun.target.position.copy(this.sunRight).multiplyScalar(cx).addScaledVector(this.sunUp,cy).addScaledVector(this.sunDirection,this.sunDirection.dot(this.center));
      this.sun.position.copy(this.sun.target.position).addScaledVector(this.sunDirection,650);this.sun.updateMatrixWorld();this.sun.target.updateMatrixWorld();
      const camera=this.sun.shadow.camera;camera.left=camera.bottom=-extent/2;camera.right=camera.top=extent/2;camera.updateProjectionMatrix();this.sun.shadow.updateMatrices(this.sun,this.camera);
      this.shadowStats.extent=extent;this.shadowStats.texel=texel;
    }
    let casters=0,triangles=0;const frustum=this.sun.shadow.getFrustum();
    for(const u of sim.units){const v=this.units.get(u.id);v.shadowIndex=-1;if(!active)continue;
      this.shadowSphere.center.set(u.x,u.y,u.z);this.shadowSphere.radius=v.rig.radius;
      let receiverVisible=true;
      if(this.map.heightAt){const ground=this.groundY(u.x,u.z);if(Number.isFinite(ground)){const h=u.y+SHADOW[u.kind].height-ground;this.shadowSphere.center.set(u.x-SUN.x/SUN.y*h,ground,u.z-SUN.z/SUN.y*h);this.shadowSphere.radius=v.rig.radius*1.35;receiverVisible=this.viewFrustum.intersectsSphere(this.shadowSphere);}this.shadowSphere.center.set(u.x,u.y,u.z);this.shadowSphere.radius=v.rig.radius;}
      if(receiverVisible&&frustum.intersectsSphere(this.shadowSphere)){const group=groups.find(g=>g.kind===u.kind);v.shadowIndex=group.count++;casters++;}
    }
    for(const g of groups)for(const p of g.parts){p.caster.count=g.count;p.caster.visible=g.count>0;triangles+=g.count*(p.geometry.index?.count??p.geometry.attributes.position.count)/3;}
    // 始终保留阴影纹理与着色器；总览仅停止更新，不在缩放中反复分配。
    this.sun.shadow.needsUpdate=active||!this.sun.shadow.map;
    Object.assign(this.shadowStats,{mode:this.shadowMode,weight,casters,triangles,mapSize:2048,updating:this.sun.shadow.needsUpdate});
  }
  setLayerFilter(value){this.layerFilter=value;}
  getLabelState(id){return(this.labelStats?.rows??[]).filter(row=>row.unitId===id);}
  projectLabelBounds(id,fallback){
    const bounds=this.units.get(id)?.labelWorldBounds;if(!bounds||bounds.isEmpty())return fallback;
    const points=[];for(const x of [bounds.min.x,bounds.max.x])for(const y of [bounds.min.y,bounds.max.y])for(const z of [bounds.min.z,bounds.max.z])points.push(this.project({x,y,z}));
    return{left:Math.min(...points.map(p=>p.x)),right:Math.max(...points.map(p=>p.x)),top:Math.min(...points.map(p=>p.y)),bottom:Math.max(...points.map(p=>p.y))};
  }
  setHoveredUnit(id){this.hoveredUnit=id??null;}
  projectNameplateBounds(id,fallback){
    const box=this.units.get(id)?.nameplateWorldBounds;if(!box||box.isEmpty())return fallback;
    const points=[];for(const x of [box.min.x,box.max.x])for(const y of [box.min.y,box.max.y])for(const z of [box.min.z,box.max.z])points.push(this.project({x,y,z}));
    return{left:Math.min(...points.map(p=>p.x)),right:Math.max(...points.map(p=>p.x)),top:Math.min(...points.map(p=>p.y)),bottom:Math.max(...points.map(p=>p.y))};
  }
  syncLabels(sim,selected){
    const rows=sim.units.map(unit=>{const point=this.projectUnit(unit.id),bounds=this.projectNameplateBounds(unit.id,point.bounds);return{unit,view:this.units.get(unit.id),point,bounds};});
    const priority=u=>selected.has(u.id)?2:this.hoveredUnit===u.id?1:0;
    rows.sort((a,b)=>priority(b.unit)-priority(a.unit)||String(a.unit.id).localeCompare(String(b.unit.id)));
    const reserved=rows.filter(r=>r.point.visible&&r.bounds).map(r=>({...r.bounds}));
    const origin=this.labels.getBoundingClientRect(),fixedBoxes=[];
    for(const node of document.querySelectorAll('#toast,#mode-banner,#pause-overlay,.field-top,.field-bottom,.compass')){
      if(node.hidden)continue;const r=node.getBoundingClientRect();if(r.width&&r.height)fixedBoxes.push({left:r.left-origin.left-4,right:r.right-origin.left+4,top:r.top-origin.top+4,bottom:r.bottom-origin.top+4});
    }
    this.labelLayout.begin({width:this.width,height:this.height,blockers:[...reserved,...fixedBoxes],time:performance.now()/1000});
    this.nameplateLines.setAttribute('viewBox',`0 0 ${this.width} ${this.height}`);
    const altitudes=[];this.altitudeLinks=[];let labelCount=0;
    for(const {unit:u,view:v,point:p,bounds} of rows){
      const chosen=selected.has(u.id),hovered=this.hoveredUnit===u.id,expanded=chosen||hovered;
      const allowed=this.layerFilter==='all'||(this.layerFilter==='ground'?u.kind==='tank':u.kind!=='tank');
      const body=bounds??{left:p.x-8,right:p.x+8,top:p.y-8,bottom:p.y+8},anchor={x:(body.left+body.right)/2,y:(body.top+body.bottom)/2};
      const key='unit:'+u.id,info={unitId:u.id,kind:'unit',anchor},eligible=p.visible&&allowed&&u.alive!==false&&(u.hp===undefined||u.hp>0)&&labelCount<40;
      v.altitudeLabel.hidden=true;v.altitudeLabel.style.display='none';
      this.labelLayout.hide('height:'+u.id,{...info,kind:'height'},'merged-into-main');
      const altitude=u.kind==='tank'?null:this.getAltitudeDisplay(u.id),hasHeight=Number.isFinite(altitude?.height);
      const phase=({cruise:'巡航',climbing:'爬升',descending:'降高',holding:'悬停',pending:'高度准备',error:'高度异常'})[altitude?.status];
      const detail=altitude?`${phase??u.state} · ${hasHeight?'离地'+Math.round(altitude.height)+'米':'高度待确认'}`:u.state;
      const normalWidth=Math.ceil(Math.max(86,Math.min(180,this.labelMeasure.measureText(u.name).width+42,))),height=expanded?43:25;
      const width=expanded?Math.ceil(Math.max(normalWidth,Math.min(200,this.labelMeasure.measureText(detail).width+20))):normalWidth;
      const tiny=(body.right-body.left)<12&&!expanded;
      let compact=tiny,layout=eligible?this.labelLayout.place({key,...info,width:compact?27:width,height:compact?25:height,body,candidates:nearbySlots(body,compact?27:width,compact?25:height)}):this.labelLayout.hide(key,info,!p.visible?'out-of-view':!allowed?'layer-filter':'label-budget');
      if(eligible&&!layout.visible&&!compact){compact=true;layout=this.labelLayout.place({key,...info,width:27,height:25,body,candidates:nearbySlots(body,27,25)});}
      v.label.hidden=!layout.visible;v.label.style.display=layout.visible?'':'none';v.leader.style.display=layout.visible?'':'none';
      v.label.className='unit-label attached-label'+(chosen?' selected':'')+(hovered?' hovered':'')+(compact?' compact':'');
      v.label.dataset.layoutSlot=layout.slot??'';v.label.dataset.layoutReason=layout.reason;v.label.dataset.compact=String(compact);v.label.dataset.kind=u.kind;
      v.label.setAttribute('aria-label',[u.name,detail].filter(Boolean).join('，'));v.label.setAttribute('aria-pressed',String(chosen));
      v.label.title=[u.name,detail,altitude?.reason].filter(Boolean).join(' · ');
      const signature=JSON.stringify([u.name,detail,expanded,compact,u.kind]);
      if(v.label.dataset.text!==signature){
        const icon=document.createElementNS(this.svgNS,'svg');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');icon.classList.add('nameplate-symbol');
        const path=document.createElementNS(this.svgNS,'path');path.setAttribute('d',UNIT_SYMBOLS[u.kind]);icon.append(path);
        const title=document.createElement('span');title.className='nameplate-name';title.textContent=u.name;
        v.label.replaceChildren(icon,title);
        if(expanded&&!compact){const status=document.createElement('em');status.textContent=detail;v.label.append(status);}
        v.label.dataset.text=signature;
      }
      if(layout.visible){
        labelCount++;Object.assign(v.label.style,{left:layout.x+'px',top:layout.y+'px',width:layout.width+'px',height:layout.height+'px'});
        const box={left:layout.x,right:layout.x+layout.width,top:layout.y,bottom:layout.y+layout.height},line=attachmentLine(body,box);
        v.leader.setAttribute('d',`M${line.from.x},${line.from.y} L${line.to.x},${line.to.y}`);v.leader.classList.toggle('active',expanded);
      }
      const showHighlight=p.visible&&allowed&&expanded;v.highlight.style.display=showHighlight?'':'none';v.highlight.classList.toggle('selected',chosen);
      if(showHighlight){
        const l=body.left-3,r=body.right+3,t=body.top-3,b=body.bottom+3,k=6;
        v.highlight.setAttribute('d',`M${l+k},${t}H${l}V${t+k} M${r-k},${t}H${r}V${t+k} M${l},${b-k}V${b}H${l+k} M${r},${b-k}V${b}H${r-k}`);
      }
      if(chosen&&p.visible&&allowed&&altitude?.groundPoint&&hasHeight){
        const start=altitude.runPoint,foot=altitude.groundPoint,r=this.span/this.height*4;
        altitudes.push(start.x,start.y,start.z,foot.x,foot.y,foot.z,foot.x-r,foot.y,foot.z,foot.x+r,foot.y,foot.z,foot.x,foot.y,foot.z-r,foot.x,foot.y,foot.z+r);
        this.altitudeLinks.push({...altitude,runPoint:{...start},groundPoint:{...foot}});
      }
    }
    this.labelStats=this.labelLayout.finish();
    this.altitudeLine.geometry.dispose();this.altitudeLine.geometry=new THREE.BufferGeometry();this.altitudeLine.geometry.setAttribute('position',new THREE.Float32BufferAttribute(altitudes,3));this.altitudeLine.computeLineDistances();this.altitudeLine.visible=altitudes.length>0;
    this.visibleUnits=rows.filter(r=>r.point.visible).length;this.visibleLabels=labelCount;
    for(const label of this.pathLabels.values()){const p=this.project(label.point);label.hidden=!p.visible;label.style.left=p.x+'px';label.style.top=p.y+'px';}
    for(const item of this.commandMarkers){const h=Number.isFinite(item.point.y)?item.point.y:this.groundY(item.point.x,item.point.z);if(!Number.isFinite(h)){item.marker.hidden=true;continue;}const p=this.project({...item.point,y:h+2});item.marker.hidden=!p.visible;item.marker.style.left=p.x+'px';item.marker.style.top=p.y+'px';}
  }
  syncPaths(sim,selected){
    const units=sim.units.filter(u=>selected.has(u.id)).slice(0,40),signature=units.map(u=>u.id+':'+u.job?.id+':'+u.job?.status+':'+u.queue.length+':'+u.commandStatus?.status+':'+u.lastRejectedCommand?.commandId).join('|');
    if(this.lastPathUpdate!==undefined&&sim.time-this.lastPathUpdate<=.15&&!sim.paused&&signature===this.pathSignature)return;
    this.pathSignature=signature;this.lastPathUpdate=sim.time;
    const vertices=[],colors=[],active=new Set(),tint=new THREE.Color();
    const pointOnGround=p=>{const h=this.groundY(p.x,p.z);return Number.isFinite(h)?{x:p.x,y:h+.28,z:p.z}:null;};
    const segment=(a,b,hex)=>{if(!a||!b||![a.x,a.y,a.z,b.x,b.y,b.z].every(Number.isFinite))return;vertices.push(a.x,a.y,a.z,b.x,b.y,b.z);tint.setHex(hex);colors.push(tint.r,tint.g,tint.b,tint.r,tint.g,tint.b);};
    for(const u of units){
      let from=u.kind==='tank'?pointOnGround(u):this.unitCenter(u.id);
      for(const node of u.path){const p=pointOnGround(node);segment(from,p,0xd4ee80);from=p;}
      const jobs=[u.job,...u.queue].filter(Boolean);
      for(const [index,job] of jobs.entries()){
        if(!job.point)continue;const point=pointOnGround(job.point),status=job.status??'ready',rejected=status==='rejected',pending=status==='pending'||sim.commandRows?.get(job.id)?.preparationStatus==='pending',hex=rejected?0xff725e:pending?0xf3c66c:0x80d8ff;
        segment(from,point,hex);if(point){const r=u.kind==='tank'?3:5;for(let i=0;i<20;i++){if(pending&&i%2)continue;const a=i/20*Math.PI*2,b=(i+1)/20*Math.PI*2;segment({x:point.x+Math.sin(a)*r,y:point.y,z:point.z+Math.cos(a)*r},{x:point.x+Math.sin(b)*r,y:point.y,z:point.z+Math.cos(b)*r},hex);}
          if(Number.isFinite(job.facing)){const s=Math.sin(job.facing),c=Math.cos(job.facing),tip={x:point.x-s*(r+4),y:point.y,z:point.z-c*(r+4)};segment(point,tip,hex);segment(tip,{x:tip.x+s*2+c*2,y:tip.y,z:tip.z+c*2-s*2},hex);segment(tip,{x:tip.x+s*2-c*2,y:tip.y,z:tip.z+c*2+s*2},hex);}
          const key=u.id+':'+(job.id??index);active.add(key);let label=this.pathLabels.get(key);if(!label){label=document.createElement('div');label.className='altitude-marker command-step';this.pathLabels.set(key,label);this.labels.append(label);}label.point=point;label.textContent=`${index+1} ${u.kind==='plane'?'盘旋区':'目的地'}${pending?' · 准备路径':rejected?' · 不可达':''}`;label.style.color=rejected?'#ff725e':pending?'#f3c66c':'#b7e9ff';const p=this.project(point);label.style.left=p.x+'px';label.style.top=p.y+'px';label.hidden=!p.visible;
        }from=point;
      }
      const rejected=u.lastRejectedCommand;if(rejected?.point){const point=pointOnGround(rejected.point);if(point){const r=5;segment({x:point.x-r,y:point.y,z:point.z-r},{x:point.x+r,y:point.y,z:point.z+r},0xff725e);segment({x:point.x+r,y:point.y,z:point.z-r},{x:point.x-r,y:point.y,z:point.z+r},0xff725e);}}
    }
    for(const [key,label]of this.pathLabels)if(!active.has(key)){label.remove();this.pathLabels.delete(key);}
    this.pathLine.geometry.dispose();this.pathLine.geometry=new THREE.BufferGeometry();this.pathLine.geometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));this.pathLine.geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));
  }
  sync(sim,selected,focus,dt){
    this.sim=sim;
    for(const item of this.commandMarkers){const p=this.project({x:item.point.x,y:(item.point.y??this.groundY(item.point.x,item.point.z))+2,z:item.point.z});item.marker.style.left=p.x+'px';item.marker.style.top=p.y+'px';item.marker.hidden=!p.visible;if(performance.now()>item.until)item.marker.remove();}this.commandMarkers=this.commandMarkers.filter(item=>performance.now()<=item.until);
    this.map.update?.({center:this.center,span:this.map.streaming?this.span*this.width/this.height:this.span,height:this.camera.position.y-this.center.y,camera:this.camera});
    this.prepareShadow(sim);
    if(this.map.heightAt){
      this.viewProjection.multiplyMatrices(this.camera.projectionMatrix,this.camera.matrixWorldInverse);this.viewFrustum.setFromProjectionMatrix(this.viewProjection);
      for(const g of this.shadowGroups)g.displayCount=0;
      for(const u of sim.units){const v=this.units.get(u.id),g=this.shadowGroups.find(g=>g.kind===u.kind);this.shadowSphere.center.set(u.x,u.y,u.z);this.shadowSphere.radius=v.rig.radius;v.displayIndex=this.viewFrustum.intersectsSphere(this.shadowSphere)?g.displayCount++:-1;}
      for(const g of this.shadowGroups)for(const p of g.parts)p.batch.count=g.displayCount;
    }
    for(const batch of this.unitPicks)batch.userData.ids.length=0;
    for(const u of sim.units){
      const v=this.units.get(u.id),rig=v.rig,m=rig.motion;v.recoil=Math.max(0,v.recoil-dt*2);
      if(u.kind==='tank'){m.turret.rotation.y=u.turretYaw-u.yaw;m.barrel.rotation.x=u.pitch;m.barrel.position.z=-1.27+v.recoil;}
      if(u.kind==='heli'){m.rotor.rotation.y=sim.time*26;m.tail.rotation.x=sim.time*40;m.gun.rotation.x=u.pitch;m.gun.rotation.y=(u.gunYaw??u.yaw)-u.yaw;}
      rig.model.updateMatrixWorld(true);
      this.dummy.position.set(u.x,u.y,u.z);this.dummy.scale.setScalar(1);this.dummy.rotation.set(u.bodyPitch??0,u.yaw,u.kind==='plane'?u.bank:u.kind==='tank'?(u.bodyRoll??0):u.kind==='heli'&&u.moving?Math.sin(sim.time*1.2)*.025:0,'YXZ');this.dummy.updateMatrix();this.unitMatrix.copy(this.dummy.matrix);
      v.worldBounds.makeEmpty();v.nameplateWorldBounds.makeEmpty();
      for(const p of v.parts){this.matrix.multiplyMatrices(this.unitMatrix,m[p.part].matrixWorld);const i=this.map.heightAt?v.displayIndex:v.index;if(i>=0){p.batch.setMatrixAt(i,this.matrix);p.batch.userData.ids[i]=u.id;const partBounds=p.geometry.boundingBox.clone().applyMatrix4(this.matrix);v.worldBounds.union(partBounds);if(!['rotor','tail','barrel'].includes(p.part))v.nameplateWorldBounds.union(partBounds);}if(v.shadowIndex>=0)p.caster.setMatrixAt(v.shadowIndex,this.matrix);}
      v.labelWorldBounds.copy(rig.labelBounds).applyMatrix4(this.unitMatrix).union(v.worldBounds);
      const ground=this.groundY(u.x,u.z),hasGround=Number.isFinite(ground);
      this.dummy.rotation.set(-Math.PI/2,0,0);this.dummy.position.set(u.x,hasGround?ground+.1:u.y,u.z);const radius=u.kind==='tank'?4.65:u.kind==='heli'?8.25:11;this.dummy.scale.setScalar(selected.has(u.id)&&hasGround?radius:0);this.dummy.updateMatrix();v.rings.setMatrixAt(v.index,this.dummy.matrix);
      const shadow=SHADOW[u.kind],height=hasGround?Math.max(0,u.y-ground+shadow.height):0,spread=1+Math.min(height,100)*.002;
      const sx=u.x-SUN.x/SUN.y*height,sz=u.z-SUN.z/SUN.y*height,shadowGround=this.groundY(sx,sz);this.dummy.position.set(sx,Number.isFinite(shadowGround)?shadowGround+.065:u.y,sz);this.dummy.rotation.set(-Math.PI/2,0,u.yaw,'XYZ');this.dummy.scale.set(hasGround&&Number.isFinite(shadowGround)?shadow.width*spread:0,shadow.length*spread,1);this.dummy.updateMatrix();v.shadows.setMatrixAt(v.index,this.dummy.matrix);
      // 高度不变时不上传额外属性；每个实例独立淡化，保留同型号一个批次。
      if(v.shadowHeight!==height){const opacity=v.shadows.geometry.getAttribute('shadowOpacity');opacity.setX(v.index,shadow.opacity/(1+height*.022));opacity.needsUpdate=true;v.shadowHeight=height;}
    }
    this.syncLabels(sim,selected);for(const batch of this.batches)batch.instanceMatrix.needsUpdate=true;
    for(const t of sim.targets){const v=this.targets.get(t.id);if(!v)continue;v.body.visible=t.hp>0;v.stripe.visible=t.hp>0;const p=this.project({x:t.x,y:(t.baseY??0)+6,z:t.z});v.label.style.display=p.visible?'':'none';v.label.style.left=p.x+'px';v.label.style.top=p.y+'px';const html=t.name+(t.hp>0?'<div class="hp"><i style="width:'+t.hp/t.maxHp*100+'%"></i></div>':sim.recoverTargets?' · 恢复中':' ×');if(v.label.innerHTML!==html)v.label.innerHTML=html;}
    const counts={bullet:0,missile:0,bomb:0};this.visualAmmoDropped=0;
    for(const p of sim.projectiles){const kind=p.weapon==='bomb'?'bomb':p.guided?'missile':'bullet',batch=this.ammo[kind],i=counts[kind]++;if(i>=4096){this.visualAmmoDropped++;continue;}this.dummy.position.set(p.position.x,p.position.y,p.position.z);this.dummy.scale.setScalar(1);this.dummy.rotation.set(0,0,0);this.temp.set(p.velocity?.x??p.direction?.x??p.point.x-p.position.x,p.velocity?.y??p.direction?.y??p.point.y-p.position.y,p.velocity?.z??p.direction?.z??p.point.z-p.position.z).normalize();this.dummy.quaternion.setFromUnitVectors(this.axisY,this.temp);
      if(kind==='bullet'){this.dummy.scale.y=p.weapon==='planeGun'?8:p.weapon==='gun'?3:1.5;batch.setColorAt(i,this.tint.setHex(p.weapon==='gun'?0x78ed23:p.weapon==='planeGun'?0xff6829:0xffecc0));}
      this.dummy.updateMatrix();batch.setMatrixAt(i,this.dummy.matrix);}
    for(const [k,b] of Object.entries(this.ammo)){b.count=Math.min(4096,counts[k]);b.instanceMatrix.needsUpdate=true;if(b.instanceColor)b.instanceColor.needsUpdate=true;}
    this.effects=this.effects.filter(f=>(f.life-=dt)>0);this.effectBatch.count=this.effects.length;this.effects.forEach((f,i)=>{const a=1-f.life/f.total;this.dummy.position.set(f.position.x,Math.max((this.groundY(f.position.x,f.position.z)??f.position.y)+.3,f.position.y),f.position.z);this.dummy.scale.setScalar((.2+a*f.size)*(1-a*.3));this.dummy.rotation.set(0,0,0);this.dummy.updateMatrix();this.effectBatch.setMatrixAt(i,this.dummy.matrix);this.effectBatch.setColorAt(i,this.tint.setHex(f.tint));});this.effectBatch.instanceMatrix.needsUpdate=true;if(this.effectBatch.instanceColor)this.effectBatch.instanceColor.needsUpdate=true;
    this.syncPaths(sim,selected);
    if(this.rangeRing)this.rangeRing.visible=false;
  }
  textureEstimate(){const textures=new Set();this.scene.traverse(o=>{for(const m of [o.material].flat().filter(Boolean))for(const t of Object.values(m))if(t?.isTexture)textures.add(t);});let bytes=2048*2048*4;for(const t of textures){const compressed=t.isCompressedTexture?t.mipmaps.reduce((n,m)=>n+(m.data?.byteLength??0),0):0;bytes+=compressed||((t.image?.width??0)*(t.image?.height??0)*4*(t.generateMipmaps?4/3:1));}return bytes;}
  render(){this.scene.updateMatrixWorld();this.renderer.render(this.scene,this.camera);}
  minimap(canvas,sim,selected){
    const c=canvas.getContext('2d'),w=canvas.width,h=canvas.height,b=this.map.bounds,sx=w/(b.maxX-b.minX),sz=h/(b.maxZ-b.minZ),map=p=>[(p.x-b.minX)*sx,(p.z-b.minZ)*sz];
    c.fillStyle='#293525';c.fillRect(0,0,w,h);c.fillStyle='#879174';for(const o of this.map.obstacles){const[x,y]=map(o);if(o.polygon){c.beginPath();o.polygon.forEach(([px,pz],i)=>{const q=map({x:px,z:pz});i?c.lineTo(...q):c.moveTo(...q);});c.closePath();c.fill();}else c.fillRect(x-o.w*sx/2,y-o.d*sz/2,Math.max(1,o.w*sx),Math.max(1,o.d*sz));}
    for(const t of sim.targets){const[x,y]=map(t);c.fillStyle=t.hp>0?'#fa9b78':'#6c775f';c.fillRect(x-1.5,y-1.5,3,3);}
    for(const u of sim.units){const[x,y]=map(u);c.fillStyle=selected.has(u.id)?'#e1f39b':u.kind==='plane'?'#d8b6f3':u.kind==='tank'?'#9dc990':'#8bb4bc';c.fillRect(x-1.5,y-1.5,3,3);}
    const corners=[[0,0],[this.width,0],[this.width,this.height],[0,this.height]].map(([x,y])=>this.groundPoint(x,y));c.strokeStyle='#d5e998aa';c.beginPath();corners.forEach((p,i)=>{if(p){const[x,y]=map(p);i?c.lineTo(x,y):c.moveTo(x,y);}});c.closePath();c.stroke();
  }
}
