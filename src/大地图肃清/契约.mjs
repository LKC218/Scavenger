/** 三线共用的数据约定，只有主对话维护。对象状态由规则持有，消费者只读。 */
export const CONTRACT_VERSION = 1;
export const COMMANDS = Object.freeze(['move','attack','attackGround','selectAmmo','enableWeapon','guard','altitude','bait','stop']);
export const KINDS = Object.freeze({ tank: 'model:US_M1A2', heli: 'model:US_AH64D', plane: 'model:US_A10' });
export const NATIVE_MODELS = Object.freeze(['model:ba-walker-lod2','model:ba-runner-lod2']);
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const distanceXZ = (a, b) => Math.hypot(a.x-b.x, a.z-b.z);
export const wrapAngle = a => Math.atan2(Math.sin(a), Math.cos(a));
export const yawTo = (a, b) => Math.atan2(-(b.x-a.x), -(b.z-a.z));
export const turnToward = (a,b,amount) => a + clamp(wrapAngle(b-a),-amount,amount);
export function validCommand(command, runId) {
  if (!command || command.runId !== runId || !Number.isSafeInteger(command.commandId) || !COMMANDS.includes(command.type)) return false;
  if (command.point && !['x','z'].every(k=>Number.isFinite(command.point[k]))) return false;
  return Array.isArray(command.unitIds) && command.unitIds.every(id=>typeof id==='string');
}
/*
规则类：export class Simulation { constructor({config,world}); reset({preset,seed,count});
 dispatch(command); step(dt); snapshot(); drainEvents(); }
snapshot：{runId,tick,time,preset,units,zombies,projectiles,baits,noises,heardGroups,lastSeen,counts,diagnostics}
单位：{id,kind,modelId,domain,position:{x,y,z},previousPosition:{x,y,z},groundY,yaw,hp,maxHp,alive,moveState,
       guardEnabled,altitudeMode,turretYaw,gunPitch,m2Yaw,m2Pitch,weapons:{main,m2}}
武器：{enabled,ammoId,ammo:{ap/heat/ball:剩余总数},magazine,readyAt,targetId,aimAt,reason}
尸群个体：{id,modelId,homeZoneId,position,previousPosition,yaw,phase,behaviorState,hp,maxHp,alive,deathAt,visible}
事件：{runId,seq,tick,time,type,sourceId,targetId,position,weaponId,ammoId,result}
事件type：weaponFired,impact,zombieDied,unitDestroyed,baitStarted,commandRejected,reset。
意图：move/attackGround point；attack targetId；selectAmmo weaponId/ammoId；
 enableWeapon weaponId/enabled；guard enabled；altitude mode(cruise/work)；bait point；stop。
初始化必须没有自动选定武器弹种。CLI受控测试可显式校准muzzle或注入样本，但运行不能假炮口。

A：export function createInteraction({canvas,camera,world,config,getSnapshot,dispatch,presentation,ui})
  -> {update(dt),getState(),setSelection(ids),focusSelection(),setView({focus,viewWidth,yaw}),dispose()}
 A.getState -> {selectedIds,filter,followId,hoverId,commandMode,focus:{x,y,z},viewWidth,yaw}
 setView仅供可复现检验，执行合法钳制并退出跟随；正常默认宽度仍为360米。
 A.ui为#ui根DOM。A可以创建自己的面板DOM；根HTML提供#stage/#viewport/#ui/#status。

B：export async function createPresentation({renderer,scene,camera,manifest,world,config,onProgress})
  -> {update(snapshot,dt,interactionState含alpha显示插值系数),handleEvents(events),getPickables(),projectUnit(id),
      getStats(),setQuality(options),dispose()}
 B.getPickables -> [{id,kind,domain,object:Object3D,center:{x,y,z},radius:米}]
 B.projectUnit -> {x,y,visible}，使用canvas CSS坐标；取当前机身中心，不取地面投影。
 B.update读取同一C快照，禁止反写。B的声音模块API由B内部维护。
manifest -> {app,version,models:{tank,heli,plane},native:{models:[原作清单]},audio,assets,sourceIdentity}
模型条目 -> {id,name,url,sha256,bytes}；Three /vendor/，共享加载器 /compressed-loader.mjs。
*/
