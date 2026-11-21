/** 首批表现只读同一规则快照；几何、声音预算与玩法状态分离。 */
import * as THREE from 'three';
import {createModelLoader} from '/compressed-loader.mjs';
import {clone} from 'three/addons/utils/SkeletonUtils.js';
import {NativeCrowd} from './原作合批适配.mjs';
import {createAudio} from './声音.mjs';

const BARREL_SOURCE_SHA='2232103474c1d04f3c4c67854cfb6e7427f34e97bea40cacad001933fbcdeb94';
// 固定源810个完整三角形的序号；来自重合位置焊接后的5个独立连接分量，未空间切面。
const BARREL_TRIANGLE_IDS=Object.freeze([
 5982,5983,5984,5985,5986,5987,5988,5989,5990,5991,5992,5993,5994,5995,5996,5997,5998,5999,6000,6001,6002,6003,6004,6005,
 6006,6007,6008,6009,6010,6011,6012,6013,6014,6015,6016,6017,6018,6019,6020,6021,6022,6023,6024,6025,6026,6027,6028,6029,
 6030,6031,6032,6033,6034,6035,6036,6037,6038,6039,6040,6041,6042,6043,6044,6045,6046,6047,6048,6049,6050,6051,6052,6053,
 6054,6055,6056,6057,6058,6059,6060,6061,6062,6063,6064,6065,6066,6067,6068,6069,6070,6071,6072,6073,6074,6075,6076,6077,
 6078,6079,6080,6081,6082,6083,6084,6085,6086,6087,6088,6089,6090,6091,6092,6093,6094,6095,6096,6097,6098,6099,6100,6101,
 6102,6103,6104,6105,6106,6107,6108,6109,6110,6111,6112,6113,6114,6115,6116,6117,6118,6119,6120,6121,6122,6123,6124,6125,
 6126,6127,6128,6129,6130,6131,6132,6133,6134,6135,6136,6137,6138,6139,6140,6141,6142,6143,6144,6145,6146,6147,6148,6149,
 6150,6151,6152,6153,6154,6155,6156,6157,6158,6159,6160,6161,6162,6163,6164,6165,6166,6167,6168,6169,6170,6171,6172,6173,
 6174,6175,6176,6177,6178,6179,6180,6181,6182,6183,6184,6185,6186,6187,6188,6189,6190,6191,6192,6193,6194,6195,6196,6197,
 6198,6199,6200,6201,6202,6203,6204,6205,6206,6207,6208,6209,6210,6211,6212,6213,6214,6215,6216,6217,6218,6219,6220,6221,
 6222,6223,6224,6225,6226,6227,6228,6229,6230,6231,6232,6233,6234,6235,6236,6237,6238,6239,6240,6241,6242,6243,6244,6245,
 6246,6247,6248,6249,6250,6251,6252,6253,6254,6255,6256,6257,6258,6259,6260,6261,6262,6263,6264,6265,6266,6267,6268,6269,
 6270,6271,6272,6273,6274,6275,6276,6277,6278,6279,6280,6281,6282,6283,6284,6285,6286,6287,6288,6289,6290,6291,6292,6293,
 6294,6295,6296,6297,6298,6299,6300,6301,6302,6303,6304,6305,6306,6307,6308,6309,6310,6311,6312,6313,6314,6315,6316,6317,
 6318,6319,6320,6321,6322,6323,6324,6325,6326,6327,6328,6329,6330,6331,6332,6333,6334,6335,6336,6337,6338,6339,6340,6341,
 6342,6343,6344,6345,6346,6347,6348,6349,6350,6351,6352,6353,6354,6355,6356,6357,6358,6359,6360,6361,6362,6363,6364,6365,
 6366,6367,6368,6369,6370,6371,6372,6373,6374,6375,6376,6377,6378,6379,6380,6381,6382,6383,6384,6385,6386,6387,6388,6389,
 6390,6391,6392,6393,6394,6395,6396,6397,6398,6399,6400,6401,6402,6403,6404,6405,6406,6407,6408,6409,6410,6411,6412,6413,
 6414,6415,6416,6417,6418,6419,6420,6421,6422,6423,6424,6425,6426,6427,6428,6429,6430,6431,6432,6433,6434,6435,6436,6437,
 6438,6439,6440,6441,6442,6443,6444,6445,6446,6447,6448,6449,6450,6451,6452,6453,6454,6455,6456,6457,6458,6459,6460,6461,
 6462,6463,6464,6465,6466,6467,7653,7654,7655,7656,7657,7658,7659,7660,7661,7662,7663,7664,7665,7666,7667,7668,7669,7670,
 7671,7672,7673,7674,7675,7676,7677,7678,7679,7680,7681,7682,7683,7684,7685,7686,7687,7688,7689,7690,7691,7692,7693,7694,
 7859,7860,7861,7862,7863,7864,7865,7866,7867,7868,7869,7870,7871,7872,7873,7874,7875,7876,7877,7878,7879,7880,7881,7882,
 7883,7884,7885,7886,7887,7888,7889,7890,7891,7892,7893,7894,7895,7896,7897,7898,7899,7900,7901,7902,7903,7904,7905,7906,
 7907,7908,7909,7910,7911,7912,7913,7914,7915,7916,7917,7918,7919,7920,7921,7922,7923,7924,7925,7926,7927,7928,7929,7930,
 7931,7932,7933,7934,7935,7936,7937,7938,7939,7940,7941,7942,7943,7944,7945,7946,7947,7948,7949,7950,7951,7952,7953,7954,
 7955,7956,7957,7958,7959,7960,7961,7962,7963,7964,7965,7966,7967,7968,7969,7970,7971,7972,7973,7974,7975,7976,7977,7978,
 7979,7980,7981,7982,7983,7984,7985,7986,7987,7988,7989,7990,7991,7992,7993,7994,7995,7996,7997,7998,7999,8000,8001,8002,
 8003,8004,8005,8006,8007,8008,8009,8010,8011,8012,8013,8014,8015,8016,8017,8018,8019,8020,8021,8022,8023,8024,8025,8026,
 8027,8028,8029,8030,8031,8032,8033,8034,8035,8036,8037,8038,8039,8040,8041,8042,8043,8044,8045,8046,8047,8048,8049,8050,
 8051,8052,8053,8054,8055,8056,8057,8058,8059,8060,8061,8062,8063,8064,8065,8066,8067,8068,8069,8070,8071,8072,8073,8074,
 8075,8076,8077,8078,8079,8080,8081,8082,8083,8084,8085,8086,8087,8088,8089,8090,8091,8092,8093,8094,8095,8096,8097,8098,
 8099,8100,8101,8102,8103,8104,8105,8106,8107,8108,8109,8110,8111,8112,8113,8114,8115,8116,8117,8315,8316,8317,8318,8319,
 8320,8321,8322,8323,8324,8325,8326,8327,8328,8329,8330,8331,8332,8333,8334,8335,8336,8337
]);

function deriveMainBarrelGeometry(geometry,sourceSha){
 if(sourceSha!==BARREL_SOURCE_SHA||geometry.index?.count!==25500||geometry.attributes.position?.count!==11219)throw Error('M256炮管运行派生的冻结源身份不符');
 if(new Set(BARREL_TRIANGLE_IDS).size!==810)throw Error('M256炮管白名单身份不符');
 const selected=new Set(BARREL_TRIANGLE_IDS),moving=[],fixed=[],source=geometry.index.array;
 for(let triangle=0;triangle<8500;triangle++){const out=selected.has(triangle)?moving:fixed;out.push(source[triangle*3],source[triangle*3+1],source[triangle*3+2]);}
 if(moving.length!==2430||fixed.length!==23070)throw Error('M256炮塔分量面数不守恒');
 function make(indices){const result=new THREE.BufferGeometry();for(const[name,attribute]of Object.entries(geometry.attributes))result.setAttribute(name,attribute);result.setIndex(new THREE.BufferAttribute(new source.constructor(indices),1));const box=new THREE.Box3(),p=new THREE.Vector3();for(const index of indices){p.fromBufferAttribute(geometry.attributes.position,index);box.expandByPoint(p);}result.boundingBox=box;const center=box.getCenter(new THREE.Vector3());let radius=0;for(const index of indices){p.fromBufferAttribute(geometry.attributes.position,index);radius=Math.max(radius,p.distanceTo(center));}result.boundingSphere=new THREE.Sphere(center,radius);return result;}
 return{moving:make(moving),fixed:make(fixed),evidence:{sourceSha256:sourceSha,sourceTriangles:8500,movingTriangles:810,stationaryTriangles:7690,componentCount:5,newCutEdges:0}};
}
function baitVisualState(bait,time){if(!bait.alive||!(bait.hp>0)||!Number.isFinite(bait.startsAt)||!Number.isFinite(bait.endsAt)||time>=bait.endsAt)return null;return time<bait.startsAt?'waiting':'active';}

export async function createPresentation({renderer,scene,camera,manifest,world,config,onProgress=()=>{}}){
 const group=new THREE.Group();group.name='肃清V001表现';scene.add(group);const assets=new Map(),units=new Map(),effects=[],wrecks=[],pickables=[],effectTextures=new Map(),baits=new Map();let snapshot=null,runId=null,lastSeq=0,quality={},stats={},mainBarrel;
 const loader=createModelLoader(renderer),audio=createAudio({manifest,config,camera}),crowd=new NativeCrowd(scene,config,world),point=new THREE.Vector3();
 for(const[kind,row]of Object.entries(manifest.models)){onProgress('载入'+row.name);const asset=await loader.loadAsync(row.url);asset.scene.updateMatrixWorld(true);if(kind==='tank'){const turret=asset.scene.getObjectByName('turret'),meshes=[];turret?.traverse(o=>{if(o.isMesh)meshes.push(o);});if(meshes.length!==1)throw Error('M256炮塔源网格数量不符');const mesh=meshes[0];if(!mesh.matrixWorld.equals(new THREE.Matrix4()))throw Error('M256炮塔坐标系不符合已核冻结源');const split=deriveMainBarrelGeometry(mesh.geometry,row.sha256);mesh.geometry=split.fixed;mainBarrel={...split,material:mesh.material};}assets.set(kind,asset);}
 onProgress('烘焙原作共享骨骼姿态');await crowd.load(renderer,manifest.native);
 for(const[role,value]of Object.entries(manifest.assets??{})){const record=Array.isArray(value)?value[0]:value;if(!record?.url||!['muzzle','heatExplosion','vehicleExplosion'].includes(role))continue;const texture=await new THREE.TextureLoader().loadAsync(record.url);texture.colorSpace=THREE.SRGBColorSpace;effectTextures.set(role,{texture,record});}
 const sparkGeometry=new THREE.IcosahedronGeometry(1,0),projectileGeometry=new THREE.SphereGeometry(.1,4,3),projectileMaterial=new THREE.MeshBasicMaterial({color:0xffdd77}),projectiles=new Map();
 const baitGeometry={base:new THREE.BoxGeometry(1.2,.7,.8),mast:new THREE.CylinderGeometry(.08,.12,1.1,5),speaker:new THREE.ConeGeometry(.42,.5,8,1,true),ring:new THREE.RingGeometry(1.8,2,32)},baitMaterials={waiting:new THREE.MeshStandardMaterial({color:0xd1a55c,roughness:1,emissive:0x251b0c}),active:new THREE.MeshStandardMaterial({color:0x88bca4,roughness:1,emissive:0x10231b}),waitRing:new THREE.MeshBasicMaterial({color:0xdab46d,transparent:true,opacity:.6,depthWrite:false,side:THREE.DoubleSide}),activeRing:new THREE.MeshBasicMaterial({color:0x9aceb2,transparent:true,opacity:.4,depthWrite:false,side:THREE.DoubleSide})};
 const frustum=new THREE.Frustum(),projectionMatrix=new THREE.Matrix4(),sphere=new THREE.Sphere();
 function pivot(parent,position,name){const o=new THREE.Group();o.name=name;o.position.set(position.x,position.y,position.z);parent.add(o);return o;}
 function setupTank(root){
  root.updateMatrixWorld(true);const turret=root.getObjectByName('turret'),nativeM2=root.getObjectByName('turret_0_0'),main=config.weapons.main.muzzle,m2=config.weapons.m2.muzzle;
  const yaw=pivot(root,main.pivot,'网页主炮偏航轴');if(turret)yaw.attach(turret);
  const pitch=pivot(yaw,{x:main.pitchPivot.x-main.pivot.x,y:main.pitchPivot.y-main.pivot.y,z:main.pitchPivot.z-main.pivot.z},'网页M256实际俯仰轴'),barrel=new THREE.Mesh(mainBarrel.moving,mainBarrel.material);barrel.name='M256原作完整炮管分量';barrel.position.set(-main.pitchPivot.x,-main.pitchPivot.y,-main.pitchPivot.z);pitch.add(barrel);
  if(nativeM2)yaw.attach(nativeM2);const m2Pitch=root.getObjectByName('weapon_0');
  return{yaw,pitch,nativeM2,m2Pitch,m2Base:nativeM2?.rotation.clone(),pitchBase:m2Pitch?.rotation.clone()};
 }
 function createUnit(unit){const asset=assets.get(unit.kind);if(!asset)throw Error('未登记载具类型：'+unit.kind);const root=clone(asset.scene);root.name=unit.id;group.add(root);const parts=unit.kind==='tank'?setupTank(root):null;root.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;o.userData.unitId=unit.id;}});root.updateMatrixWorld(true);const bounds=new THREE.Box3().setFromObject(root),center=bounds.getCenter(new THREE.Vector3()),radius=Math.max(bounds.getSize(new THREE.Vector3()).x,bounds.getSize(new THREE.Vector3()).z)/2;const row={root,center,radius,parts};units.set(unit.id,row);return row;}
 function removeEffect(e){e.mesh.removeFromParent();e.mesh.material.map?.dispose();e.mesh.material.dispose();}
 function clear(){for(const u of units.values())u.root.removeFromParent();units.clear();pickables.length=0;for(const b of baits.values())b.root.removeFromParent();baits.clear();for(const e of effects)removeEffect(e);effects.length=0;for(const w of wrecks){w.root.removeFromParent();for(const m of w.materials)m.dispose();}wrecks.length=0;for(const p of projectiles.values())p.removeFromParent();projectiles.clear();crowd.reset();audio.reset();lastSeq=0;}
 function createBait(bait){const root=new THREE.Group();root.name='网页受控样本-声源装置-'+bait.id;const base=new THREE.Mesh(baitGeometry.base,baitMaterials.waiting),mast=new THREE.Mesh(baitGeometry.mast,baitMaterials.waiting),speaker=new THREE.Mesh(baitGeometry.speaker,baitMaterials.waiting),ring=new THREE.Mesh(baitGeometry.ring,baitMaterials.waitRing),pulse=new THREE.Mesh(baitGeometry.ring,baitMaterials.activeRing);base.position.y=.35;mast.position.y=1.15;speaker.position.y=1.7;speaker.rotation.x=-Math.PI/2;ring.rotation.x=pulse.rotation.x=-Math.PI/2;ring.position.y=.1;pulse.position.y=.11;root.add(base,mast,speaker,ring,pulse);root.userData={source:'网页受控样本',baitId:bait.id};group.add(root);const row={root,base,mast,speaker,ring,pulse};baits.set(bait.id,row);return row;}
 function addEffect(event){if(!event.position||effects.length>=config.render.effectLimit)return;const blood=event.type==='zombieDied'||event.result?.kind==='zombie',fire=event.type==='weaponFired',size=blood?.8:fire?(event.weaponId==='m2'?1:3):(event.type==='unitDestroyed'?12:event.ammoId==='heat'?10:2.4),role=fire?'muzzle':event.type==='unitDestroyed'?'vehicleExplosion':'heatExplosion',native=!blood&&effectTextures.get(role);let mesh,columns=1,rows=1;
  if(native){const map=native.texture.clone();columns=native.record.columns??1;rows=native.record.rows??1;map.repeat.set(1/columns,1/rows);map.offset.set(0,(rows-1)/rows);const material=new THREE.SpriteMaterial({map,color:fire?0xffcb77:0xffffff,transparent:true,depthWrite:false,opacity:.9,blending:THREE.AdditiveBlending});mesh=new THREE.Sprite(material);}else{const material=new THREE.MeshBasicMaterial({color:blood?0x6b1418:0xffb64f,transparent:true,depthWrite:false,opacity:.9});mesh=new THREE.Mesh(sparkGeometry,material);}
  mesh.position.set(event.position.x,event.position.y+(blood?.7:fire?0:1.5),event.position.z);group.add(mesh);effects.push({mesh,start:snapshot?.time??event.time??0,life:fire?.09:blood?.5:1.1,size,columns,rows});}
 function addWreck(event){const u=units.get(event.targetId);if(!u||wrecks.length>=config.render.wreckLimit)return;const root=u.root.clone(true),materials=[];root.visible=true;root.traverse(o=>{if(o.isMesh){o.material=o.material.clone();o.material.color?.multiplyScalar(.22);o.material.transparent=true;materials.push(o.material);o.castShadow=false;}});group.add(root);wrecks.push({root,materials,start:event.time??snapshot.time});}
 function update(snapshotValue,dt,interactionState={}){
  snapshot=snapshotValue;if(runId!==snapshot.runId){clear();runId=snapshot.runId;}
  const alpha=Math.max(0,Math.min(1,interactionState.alpha??1)),focus=interactionState.focus??{x:camera.position.x,y:0,z:camera.position.z},width=interactionState.viewWidth??(camera.isOrthographicCamera?(camera.right-camera.left)/camera.zoom:360),drawingWidth=renderer.getDrawingBufferSize(new THREE.Vector2()).x,pixelsPerMeter=drawingWidth/Math.max(1,width);
  pickables.length=0;const active=new Set();for(const unit of snapshot.units){active.add(unit.id);if(!unit.alive){units.get(unit.id)?.root.visible&&(units.get(unit.id).root.visible=false);continue;}const row=units.get(unit.id)??createUnit(unit),root=row.root,p=unit.position,previous=unit.previousPosition??p;root.visible=true;root.position.set(previous.x+(p.x-previous.x)*alpha,previous.y+(p.y-previous.y)*alpha,previous.z+(p.z-previous.z)*alpha);root.rotation.set(0,unit.yaw,0);
   if(row.parts){const p=row.parts;p.yaw.rotation.y=unit.turretYaw??0;p.pitch.rotation.x=unit.gunPitch??0;if(p.nativeM2)p.nativeM2.rotation.y=(p.m2Base?.y??0)+(unit.m2Yaw??0)-(unit.turretYaw??0);if(p.m2Pitch)p.m2Pitch.rotation.x=(p.pitchBase?.x??0)+(unit.m2Pitch??0);}
   root.updateMatrixWorld(true);const center=row.center.clone().applyMatrix4(root.matrixWorld);pickables.push({id:unit.id,kind:unit.kind,domain:unit.domain,object:root,center:{x:center.x,y:center.y,z:center.z},radius:row.radius});
  }
  for(const[id,row]of units)if(!active.has(id)){row.root.removeFromParent();units.delete(id);}
  const aliveBaits=new Set();let waitingBaits=0,activeBaits=0;for(const bait of snapshot.baits??[]){const state=baitVisualState(bait,snapshot.time);if(!state)continue;aliveBaits.add(bait.id);if(state==='waiting')waitingBaits++;else activeBaits++;const row=baits.get(bait.id)??createBait(bait);row.root.position.set(bait.position.x,bait.position.y,bait.position.z);row.root.userData.state=state;row.root.userData.startsAt=bait.startsAt;row.root.userData.endsAt=bait.endsAt;row.root.userData.hp=bait.hp;const material=baitMaterials[state];row.base.material=row.mast.material=row.speaker.material=material;row.ring.material=state==='waiting'?baitMaterials.waitRing:baitMaterials.activeRing;row.pulse.visible=state==='active';if(state==='active')row.pulse.scale.setScalar(1+((snapshot.time-bait.startsAt)%2)/2*3.5);}
  for(const[id,row]of baits)if(!aliveBaits.has(id)){row.root.removeFromParent();baits.delete(id);}
  camera.updateMatrixWorld();projectionMatrix.multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse);frustum.setFromProjectionMatrix(projectionMatrix,camera.coordinateSystem);const isOnScreen=(p,radius)=>{sphere.center.set(p.x,p.y+.93,p.z);sphere.radius=radius;return frustum.intersectsSphere(sphere);};
  crowd.update(snapshot,{focus,pixelsPerMeter,isOnScreen,alpha,billboardQuaternion:camera.quaternion,cameraYaw:Math.atan2(camera.position.x-focus.x,camera.position.z-focus.z),...quality});
  const ids=new Set();for(const p of snapshot.projectiles??[]){ids.add(p.id);let mesh=projectiles.get(p.id);if(!mesh){mesh=new THREE.Mesh(projectileGeometry,projectileMaterial);group.add(mesh);projectiles.set(p.id,mesh);}mesh.position.set(p.position.x,p.position.y,p.position.z);}for(const[id,mesh]of projectiles)if(!ids.has(id)){mesh.removeFromParent();projectiles.delete(id);}
  for(let i=effects.length-1;i>=0;i--){const e=effects[i],age=snapshot.time-e.start;if(age>e.life){removeEffect(e);effects.splice(i,1);continue;}const a=Math.max(0,age/e.life);e.mesh.scale.setScalar(e.size*(.4+a));e.mesh.material.opacity=.9*(1-a);if(e.mesh.material.map&&e.columns*e.rows>1){const frame=Math.min(e.columns*e.rows-1,Math.floor(a*e.columns*e.rows));e.mesh.material.map.offset.set((frame%e.columns)/e.columns,(e.rows-1-Math.floor(frame/e.columns))/e.rows);}}
  for(let i=wrecks.length-1;i>=0;i--){const w=wrecks[i],age=snapshot.time-w.start;if(age>config.render.wreckLife){w.root.removeFromParent();w.materials.forEach(m=>m.dispose());wrecks.splice(i,1);continue;}for(const m of w.materials)m.opacity=Math.min(1,(config.render.wreckLife-age)/config.render.wreckFade);}
  audio.update(snapshot,focus);stats={...crowd.stats,units:pickables.length,projectiles:projectiles.size,effects:effects.length,wrecks:wrecks.length,baitVisuals:baits.size,activeBaitVisuals:activeBaits,baits:{waiting:waitingBaits,active:activeBaits,total:baits.size,source:'网页受控样本'},audio:audio.getState(),source:'原作GLB与网页受控表现',mainPitchVisual:'原作810三角形完整炮管按真实轴俯仰，防盾保持',mainBarrel:mainBarrel.evidence};
 }
 return{update,handleEvents(events){const fresh=events.filter(e=>e.runId===runId&&e.seq>lastSeq).sort((a,b)=>a.seq-b.seq);for(const e of fresh){lastSeq=e.seq;if(['weaponFired','impact','zombieDied','unitDestroyed'].includes(e.type))addEffect(e);if(e.type==='unitDestroyed')addWreck(e);}audio.handleEvents(fresh);},getPickables(){return pickables;},projectUnit(id){const row=pickables.find(r=>r.id===id);if(!row)return{x:0,y:0,visible:false};point.set(row.center.x,row.center.y,row.center.z).project(camera);const rect=renderer.domElement.getBoundingClientRect();return{x:(point.x+1)*rect.width/2,y:(1-point.y)*rect.height/2,visible:point.z>=-1&&point.z<=1&&Math.abs(point.x)<=1&&Math.abs(point.y)<=1};},getStats(){return stats;},setQuality(options){quality={...quality,...options};},setAudioEnabled(value){return audio.setEnabled(value);},setAudioVolume(value){audio.setVolume(value);},getAudioState(){return audio.getState();},dispose(){clear();crowd.dispose();audio.dispose();sparkGeometry.dispose();projectileGeometry.dispose();projectileMaterial.dispose();for(const g of Object.values(baitGeometry))g.dispose();for(const m of Object.values(baitMaterials))m.dispose();for(const t of effectTextures.values())t.texture.dispose();group.removeFromParent();}};
}
