/** 丧尸防线独立运行配置；数值来自已确认研究建议，不读取研究目录。 */
export const DEFENSE_WEAPONS={
  tank:{name:'120毫米范围弹',range:90,reload:3.2,aim:.45,speed:85,damage:180,blast:7,inner:2,edge:.25,audio:'tank',shootOnMove:true,lowerPitch:-10,upperPitch:20},
  gun:{name:'30毫米机炮',range:100,reload:.15,aim:.25,speed:105,damage:24,blast:2,inner:.35,edge:.2,burst:5,rest:.8,audio:'heli',shootOnMove:true,lowerPitch:-70,upperPitch:12,sector:100},
  missile:{name:'自主追踪导弹',range:160,reload:6,aim:.65,speed:32,damage:290,blast:5,inner:1,edge:.2,audio:'missile',shootOnMove:true},
  planeGun:{name:'A-10C机炮',range:240,reload:.1,aim:0,speed:360,damage:28,blast:1.8,inner:.2,edge:.2,audio:'plane'},
  bomb:{name:'普通炸弹',range:320,reload:6,aim:0,speed:52,damage:400,blast:22,inner:6,edge:.1,audio:'bomb'}
};
export const DEFENSE_MAP={id:'defense',name:'丧尸防线',bounds:{minX:-600,maxX:600,minZ:-600,maxZ:600},
  cell:8,center:{x:0,z:5},defaultSpan:180,maxSpan:1450,targets:[],renderTargets:false,
  obstacles:[{x:0,z:-8,w:24,d:10,h:8,building:true},{x:88,z:-18,w:18,d:24,h:10,building:true},
    {x:-91,z:-25,w:20,d:17,h:7,building:true},{x:39,z:-2,w:12,d:17,h:6,building:true},
    {x:-105,z:37,w:12,d:22,h:9,building:true},{x:108,z:41,w:18,d:15,h:11,building:true}]};
export const COMBAT={minX:-150,maxX:150,minZ:-125,maxZ:100,cell:2,line:80};
export const DEFENSE_DEFAULTS={zombies:100,integrity:30,interval:10,speed:1.6,seed:261104,
  waves:[100,150,200],modelMode:'mixed',composition:{tank:1,heli:1,plane:1},movement:{tank:{speed:8,reverse:4,acceleration:3,braking:5},heli:{speed:18,acceleration:6,braking:8}}};
