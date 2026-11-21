import {matches,modelSounds} from './search.mjs';

const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const QUALITY={
  passed:{label:'模型通过',tone:'ok'},
  partial:{label:'待修参考',tone:'warn'},
  pending:{label:'待验收',tone:'info'},
  ready:{label:'技术通过',tone:'ok'},
  review:{label:'待复核',tone:'review'}
};
const CATEGORY={air:'空中载具',ground:'地面载具',other:'角色与其他模型',scene:'场景',building:'建筑',decor:'道具',tree:'树木灌木',grass:'草丛'};
const CATEGORY_SHORT={air:'空中',ground:'地面',other:'角色',scene:'场景',building:'建筑',decor:'道具',tree:'植被',grass:'草丛'};
const MODEL_FILTERS=['all','air','ground','other','scene','building','decor','tree','grass'];
const CATEGORY_EN={air:'Air',ground:'Ground',other:'Character'};
const ROLE={MoveEvent:'发动机与运动',Shot:'射击与发射',Impact:'命中与爆炸',MissileInterception:'拦截',DeathEvent:'载具毁伤'};
const ICON={
  folder:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M3 7h6l2 2h10v10H3Z"/><path d="M9 14h6"/></svg>',
  play:'<path d="M8 5v14l11-7Z"/>',
  pause:'<path d="M6 4h4v16H6zM14 4h4v16h-4z"/>'
};
const state={
  catalog:null,
  modelId:null,
  soundId:null,
  currentTab:'vehicles',
  category:'all',
  query:'',
  modelLimit:30,
  audioLimit:40,
  drawerMode:null,
  drawerLimit:50,
  contextTab:'audio',
  audioCategory:'all',
  audioAssociation:'all',
  audioQuality:'all',
  audioFocusModel:null,
  activeAudio:null,
  playGeneration:0,
  viewer:null,
  viewerGeneration:0,
  specIndex:0,
  loop:false,
  volume:.35
};
const player=$('player');
player.volume=state.volume;
let toastTimer=0;
let lastFocused=null;

const modelEntries=()=>[...(state.catalog?.models||[]),...(state.catalog?.scenes||[])];
const modelCategory=model=>['scene','building','decor','tree','grass'].includes(model.asset_type)?model.asset_type:model.category;
const modelById=id=>modelEntries().find(model=>model.id===id);
const soundById=id=>state.catalog?.audio.find(sound=>sound.id===id);
const readableName=value=>String(value??'').replace(/_+/g,' ').replace(/\s+/g,' ').trim();
const formatBytes=value=>{
  const number=Number(value);
  if(!Number.isFinite(number)||number<0)return '—';
  return number>=1e6?`${(number/1e6).toFixed(2)} MB`:`${(number/1e3).toFixed(1)} KB`;
};
const formatTris=value=>Math.round(Number(value)||0).toLocaleString('zh-CN');
const formatSeconds=value=>`${(Number(value)||0).toFixed(1)} 秒`;
const formatClock=value=>{
  const total=Math.max(0,Math.floor(Number(value)||0));
  return `${Math.floor(total/60)}:${String(total%60).padStart(2,'0')}`;
};
const quality=status=>QUALITY[status]||{label:status||'未标注',tone:'info'};
const dotTone=status=>quality(status).tone;
const modelNumber=id=>{
  const index=modelEntries().findIndex(model=>model.id===id);
  return index<0?'000':String(index+1).padStart(3,'0');
};
const nameHtml=value=>{
  const text=readableName(value);
  const split=text.indexOf(' ');
  return split>0?`<span class="name-main">${esc(text.slice(0,split))}</span> <span>${esc(text.slice(split+1))}</span>`:esc(text);
};

function setSync(text,error=false){
  $('sync-text').textContent=text;
  $('sync-text').closest('.sync').classList.toggle('error',error);
}
function toast(message,error=false){
  clearTimeout(toastTimer);
  const node=$('toast');
  node.textContent=message;
  node.classList.toggle('error',error);
  node.hidden=false;
  toastTimer=setTimeout(()=>{node.hidden=true;},3600);
}
function setStatus(node,status,fallback='未标注'){
  const info=status?quality(status):{label:fallback,tone:'info'};
  node.className='status '+info.tone;
  node.innerHTML=`<i aria-hidden="true"></i><span>${esc(info.label)}</span>`;
}
function setViewTag(text,mode=''){
  $('view-tag-text').textContent=text;
  const tag=$('view-tag-text').parentElement;
  tag.classList.remove('loading','ready','error');
  if(mode)tag.classList.add(mode);
}
function setViewerState(text,error=false){
  const node=$('viewer-state');
  node.textContent=text;
  node.classList.toggle('error',error);
}

function navigate(push=true){
  if(!state.catalog)return;
  const params=new URLSearchParams({view:state.currentTab});
  if(state.currentTab==='vehicles'&&state.modelId)params.set('model',state.modelId);
  if(state.currentTab==='audio'&&state.soundId)params.set('sound',state.soundId);
  if(state.currentTab==='audio'&&state.audioFocusModel)params.set('related',state.audioFocusModel);
  const url='#'+params.toString();
  if(push)history.pushState(null,'',url);
  else history.replaceState(null,'',url);
}

function getVariants(model){
  const rows=Array.isArray(model?.runtime_variants)?model.runtime_variants.filter(row=>row?.url):[];
  if(rows.length)return rows;
  if(!model?.model_url)return [];
  return [{resolution:model.geometry?.texture_max||'原始',level:1,url:model.model_url,bytes:model.bytes||0,triangles:model.geometry?.triangles||0}];
}
function variantName(variant){
  if(!variant)return '当前文件';
  if(variant.resolution===256&&variant.level===1)return '256 · 默认';
  if(variant.resolution===256&&variant.level===2)return '256 · 远景';
  if(variant.resolution===512)return '512 · 细节档';
  return `${variant.resolution??'原始'} · L${variant.level??1}`;
}
function activeVariant(){
  const model=modelById(state.modelId);
  if(!model)return null;
  const variants=getVariants(model);
  return variants[Math.min(state.specIndex,variants.length-1)]||null;
}
function activeModel(){
  const model=modelById(state.modelId);
  if(!model)return null;
  const variant=activeVariant();
  return {...model,activeVariant:variant,model_url:variant?.url||model.model_url,bytes:variant?.bytes??model.bytes,triangles:variant?.triangles??model.geometry?.triangles};
}

function filteredModels(){
  if(!state.catalog)return [];
  return modelEntries().filter(model=>(state.category==='all'||modelCategory(model)===state.category)&&matches(model,state.query));
}
function modelRowHtml(model){
  const tone=dotTone(model.quality_status);
  return `<button type="button" class="model" data-model="${esc(model.id)}" role="option" aria-selected="${model.id===state.modelId}" aria-current="${model.id===state.modelId}"><span class="idx">${modelNumber(model.id)}</span><img src="${esc(model.preview_url)}" alt="" width="48" height="40" loading="lazy" decoding="async"><span class="model-copy"><strong>${esc(model.name)}</strong><small><em>${esc(model.source_id)}</em> · ${model.audio_ids.length} 声音</small></span><i class="dot ${tone==='ok'?'':tone}" aria-hidden="true"></i></button>`;
}
function renderModelFilters(){
  const models=modelEntries();
  const count=key=>key==='all'?models.length:models.filter(model=>modelCategory(model)===key).length;
  $('model-filters').innerHTML=MODEL_FILTERS.filter(key=>['all','air','ground','other'].includes(key)||count(key)).map(key=>`<button type="button" data-category="${key}" aria-pressed="${state.category===key}">${key==='all'?'全部':CATEGORY_SHORT[key]}<b>${count(key)}</b></button>`).join('');
  $('model-count').textContent=models.length;
}
function renderModels(scrollToSelected=false){
  const rows=filteredModels();
  const selectedIndex=rows.findIndex(model=>model.id===state.modelId);
  if(scrollToSelected&&selectedIndex>=state.modelLimit)state.modelLimit=Math.ceil((selectedIndex+1)/30)*30;
  const list=$('model-list');
  const previous=list.scrollTop;
  $('model-list-label').textContent=`当前编目 / ${state.category==='all'?'全部':CATEGORY_SHORT[state.category]}${state.query?' · 搜索':''}`;
  $('model-range').textContent=`${Math.min(state.modelLimit,rows.length)} / ${rows.length}`;
  $('more-models').hidden=rows.length<=state.modelLimit;
  list.innerHTML=rows.slice(0,state.modelLimit).map(modelRowHtml).join('')||'<div class="empty"><strong>没有找到模型</strong>可尝试型号关键词，或清空分类筛选。<button type="button" data-reset-model>清空筛选</button></div>';
  list.scrollTop=previous;
  if(scrollToSelected&&state.modelId){
    const selected=[...list.querySelectorAll('[data-model]')].find(node=>node.dataset.model===state.modelId);
    selected?.scrollIntoView({block:'nearest'});
  }
}

function renderSpecs(){
  const model=modelById(state.modelId);
  if(!model)return;
  if(model.type==='scene'){
    $('spec-row').innerHTML=`<div class="detail-section"><strong>场景引用装配</strong><p>${model.asset_ids.length}种独立资产 · 点击后读取场景</p></div>`;
    updateSpecDependent();return;
  }
  const variants=getVariants(model);
  state.specIndex=Math.min(state.specIndex,Math.max(0,variants.length-1));
  $('spec-row').innerHTML=variants.map((variant,index)=>{
    const triangles=variant.triangles??model.geometry?.triangles??0;
    return `<button type="button" class="spec" data-spec="${index}" aria-pressed="${index===state.specIndex}" title="${esc(variant.resolution===512?'贴图最大边长512像素；手动选择的细节档':variantName(variant))}"><span class="spec-top"><strong>${esc(variantName(variant))}</strong><em>L${esc(variant.level??1)}</em></span><span class="spec-data"><b>${formatBytes(variant.bytes)}</b><span>${formatTris(triangles)} tris</span></span></button>`;
  }).join('');
  updateSpecDependent();
}
function updateSpecDependent(){
  const model=activeModel();
  if(!model)return;
  const variant=model.activeVariant;
  const texture=variant?.resolution??model.geometry?.texture_max??'—';
  $('caption-title').textContent=`${model.name} / ${variantName(variant)}`;
  $('axis-spec').textContent=`${texture} / L${variant?.level??1}`;
  $('axis-tris').textContent=formatTris(model.triangles);
  $('axis-tex').textContent=typeof texture==='number'?`${texture}px`:'—';
  if(model.type==='scene'){
    $('caption-title').textContent=model.name;
    $('axis-spec').textContent='引用装配';$('axis-tris').textContent='—';$('axis-tex').textContent='按资产';
  }
  const download=$('download-model');
  const fileUrl=model.scene_url||model.model_url;
  const downloadIcon=download.querySelector('svg');
  download.replaceChildren(...(downloadIcon?[downloadIcon]:[]),document.createTextNode(model.type==='scene'?'下载场景清单':'下载 GLB'));
  if(fileUrl){download.href=fileUrl;download.setAttribute('download','');download.removeAttribute('aria-disabled');}
  else{download.href='#';download.removeAttribute('download');download.setAttribute('aria-disabled','true');}
  $('reveal-model').disabled=!fileUrl;
  $('reveal-blend').disabled=!model.blend_url;
  $('reveal-blend').title=model.blend_url?'在资源管理器中定位可编辑Blender工程':'当前资产没有可编辑工程';
  $('mobile-model-index').textContent=`${modelNumber(model.id)} / ${String(modelEntries().length).padStart(3,'0')}`;
  $('mobile-model-name').textContent=model.name;
}
function resetViewer(message='预览图已就绪，点击加载三维模型。'){
  state.viewerGeneration++;
  state.viewer?.clear();
  $('canvas').hidden=true;
  $('model-preview').hidden=false;
  $('preview').classList.remove('is-3d','is-loading');
  $('load-3d').disabled=false;
  $('load-3d-label').textContent='加载三维模型';
  $('load-progress').hidden=true;
  $('load-progress').value=0;
  setViewTag('预览图 · 点击加载');
  setViewerState(message);
}
function showModel(id,push=true){
  const model=modelById(id);
  if(!model)return;
  if(state.category!=='all'&&modelCategory(model)!==state.category){state.category='all';renderModelFilters();}
  const changed=state.modelId!==id;
  if(changed)stopAudio(true);
  state.modelId=id;
  state.specIndex=0;
  const index=modelNumber(model.id);
  $('model-category').textContent=`Asset Inspection / ${CATEGORY_EN[model.category]||'Asset'} / ${index}`;
  $('model-name').innerHTML=nameHtml(model.name);
  $('model-subtitle').innerHTML=`<b>${esc(model.source_id)}</b><i class="sep"></i><span>${esc(model.variant)}</span><i class="sep"></i><span>${esc(variantName(getVariants(model)[0]))}</span>`;
  setStatus($('model-quality'),model.quality_status);
  $('model-preview').src=model.preview_url;
  $('model-preview').alt=`${model.name} 模型预览`;
  $('ambient-image').src=model.preview_url;
  resetViewer(model.type==='scene'?'场景预览已就绪，点击进入引用装配。':'预览图已就绪，点击加载三维模型。');
  if(model.type==='scene')$('load-3d-label').textContent='进入场景预览';
  renderSpecs();
  renderModels(true);
  renderContext();
  if(push)navigate(true);
  else navigate(false);
}
function selectSpec(index){
  const model=modelById(state.modelId);
  const variants=model?getVariants(model):[];
  if(index<0||index>=variants.length||index===state.specIndex)return;
  state.specIndex=index;
  renderSpecs();
  resetViewer('规格已切换，点击加载当前模型。');
}

async function loadModel(){
  const model=activeModel();
  if(!model)return;
  const generation=++state.viewerGeneration;
  $('load-3d').disabled=true;
  $('load-3d-label').textContent='正在加载';
  $('preview').classList.add('is-loading');
  $('load-progress').hidden=false;
  $('load-progress').value=0;
  setViewTag('读取三维模型 · 0%','loading');
  setViewerState('正在读取三维模型…');
  try{
    if(!state.viewer){
      const {ModelViewer}=await import('./viewer.mjs');
      if(generation!==state.viewerGeneration)return;
      state.viewer=new ModelViewer($('canvas'),()=>{});
    }
    state.viewer.onState=(text,ratio)=>{
        if(generation!==state.viewerGeneration)return;
        setViewerState(text);
        if(Number.isFinite(ratio)){
          $('load-progress').hidden=false;
          $('load-progress').value=Math.max(0,Math.min(1,ratio));
          setViewTag(ratio>=1?(model.type==='scene'?'实时场景 · 已就绪':'实时三维 · 已就绪'):`读取三维模型 · ${Math.round(ratio*100)}%`,ratio>=1?'ready':'loading');
        }
    };
    if(generation!==state.viewerGeneration)return;
    const success=await state.viewer.load(model);
    if(generation!==state.viewerGeneration)return;
    if(!success)return;
    $('canvas').hidden=false;
    $('model-preview').hidden=true;
    $('preview').classList.remove('is-loading');
    $('preview').classList.add('is-3d');
    $('load-progress').value=1;
    $('load-3d').disabled=false;
    $('load-3d-label').textContent='重新加载三维';
    setViewTag('实时三维 · 已就绪','ready');
    setViewerState(model.type==='scene'?'场景已就绪 · 拖动旋转，右键平移，滚轮缩放；可切全图总览。':'模型已就绪 · 拖动旋转，滚轮缩放；按 F 重置视角。');
  }catch(error){
    if(generation!==state.viewerGeneration)return;
    $('canvas').hidden=true;
    $('model-preview').hidden=false;
    $('preview').classList.remove('is-3d','is-loading');
    $('load-3d').disabled=false;
    $('load-3d-label').textContent='重试三维预览';
    $('load-progress').hidden=true;
    setViewTag('三维读取失败','error');
    setViewerState('三维模型暂时无法显示，可重试或继续看预览图。',true);
    toast('三维模型暂时无法显示，请确认本地资源服务与文件仍在原位。',true);
  }
}
function showPreviewImage(){
  if(!state.modelId)return;
  resetViewer('预览图已显示；点击按钮加载当前规格的三维模型。');
}

function groupSounds(modelId){
  if(!state.catalog||!modelId)return [];
  const groups=new Map();
  for(const sound of modelSounds(state.catalog,modelId)){
    const fields=[...new Set(sound.bindings.filter(binding=>binding.model_id===modelId).map(binding=>binding.field))];
    const label=fields.map(field=>ROLE[field]||field).join(' / ')||sound.category;
    if(!groups.has(label))groups.set(label,[]);
    groups.get(label).push(sound);
  }
  return [...groups].map(([label,rows])=>({label,rows}));
}
function soundRowHtml(sound){
  return `<div class="sound" data-sound-row="${esc(sound.id)}"><button type="button" class="play-button" data-play="${esc(sound.id)}" aria-label="播放 ${esc(readableName(sound.name))}"><span class="play-glyph">▶</span></button><div class="sound-copy"><strong title="${esc(sound.name)}">${esc(readableName(sound.name))}</strong><small><b>${esc(sound.category)}</b> · ${formatSeconds(sound.check?.duration_seconds)}</small></div><div class="sound-side"><b>${formatBytes(sound.bytes)}</b><button type="button" class="folder-btn" data-reveal-sound="${esc(sound.id)}" title="在资源管理器中定位该声音" aria-label="打开 ${esc(readableName(sound.name))} 的文件位置">${ICON.folder}</button></div></div>`;
}
function renderContext(){
  const model=modelById(state.modelId);
  if(!model||!state.catalog){
    $('context-panel').innerHTML='<div class="empty"><strong>没有可显示的资产</strong>请确认本机服务与索引状态。</div>';
    $('related-count').textContent='';
    $('context-foot').hidden=true;
    return;
  }
  const sounds=modelSounds(state.catalog,model.id);
  const headings={audio:['关联音频',sounds.length],params:['参数与规格',''],source:['来源与文件',''],relation:['关系与状态','']};
  const [heading,count]=headings[state.contextTab]||headings.audio;
  $('context-heading').textContent=heading;
  $('related-count').textContent=count;
  for(const button of $('context-tabs').querySelectorAll('[data-context-tab]'))button.setAttribute('aria-selected',button.dataset.contextTab===state.contextTab);
  if(model.type==='scene'){
    const scene=model.extensions?.scene||{},coverage=scene.coverage||{};
    $('context-foot').hidden=true;
    if(state.contextTab==='relation'){
      $('context-panel').innerHTML=`<section class="detail-section"><h3>独立资产 · ${model.asset_ids.length}</h3>${model.asset_ids.map(id=>{const asset=modelById(id);return asset?`<p><button type="button" class="textbtn" data-open-model="${esc(id)}">${esc(asset.name)}</button></p>`:'';}).join('')}</section>`;
    }else if(state.contextTab==='source'){
      $('context-panel').innerHTML=`<section class="detail-section"><h3>来源与范围</h3><dl><dt>场景ID</dt><dd>${esc(model.id)}</dd><dt>构建</dt><dd>${esc(model.source?.build)}</dd><dt>用途</dt><dd>${esc(model.source?.usage_scope)}</dd><dt>许可状态</dt><dd>${esc(model.source?.license_status)}</dd></dl><p>${esc(coverage.scope||'按冻结清单记录范围')}</p><a href="${esc(model.scene_url)}" download>下载场景清单</a><p><button type="button" class="textbtn" data-reveal-blend>定位总装配工程</button></p><p>链接工程需连同登记依赖收集后使用。</p></section>`;
    }else if(state.contextTab==='params'){
      $('context-panel').innerHTML=`<section class="detail-section"><h3>场景装配</h3><dl><dt>独立资产</dt><dd>${model.asset_ids.length}</dd><dt>摆放实例</dt><dd>${esc(coverage.placed_instances??'见分页清单')}</dd><dt>技术状态</dt><dd>${esc(model.technical_status)}</dd><dt>AI视觉</dt><dd>${esc(model.ai_visual_status)}</dd><dt>人工验收</dt><dd>${esc(model.human_acceptance)}</dd></dl><p>${esc(coverage.scope||'见冻结范围')}</p></section>`;
    }else{
      $('context-panel').innerHTML='<div class="empty"><strong>静态场景未关联声音</strong>独立资产与装配关系可在“关系与状态”查看。</div>';
    }
    $('context-mini').textContent='场景与独立资产';return;
  }
  if(state.contextTab==='audio'){
    const groups=groupSounds(model.id);
    $('context-panel').innerHTML=groups.map(group=>`<section class="group"><div class="group-title">${esc(group.label)} · ${group.rows.length}</div>${group.rows.map(soundRowHtml).join('')}</section>`).join('')||'<div class="empty"><strong>暂无已确认声音</strong>保留这个模型入口，后续有确证来源即可补充。<button type="button" data-show-all-audio>浏览全部音效</button></div>';
    $('context-mini').innerHTML='按用途分组<br>当前资产';
    $('context-foot').hidden=false;
    $('context-more').innerHTML=`查看全部 <b>${sounds.length}</b> 项`;
    $('context-more').disabled=sounds.length===0;
  }else if(state.contextTab==='params'){
    const geometry=model.geometry||{};
    const variants=getVariants(model);
    $('context-panel').innerHTML=`<section class="detail-section"><h3>几何与规格 <span>${esc(quality(model.quality_status).label)}</span></h3><div class="stat-pair"><div><b>${formatTris(geometry.triangles||model.triangles)}</b><span>当前三角形</span></div><div><b>${formatTris(geometry.source_triangles||0)}</b><span>源三角形</span></div></div><dl><dt>贴图上限</dt><dd>${geometry.texture_max?`${geometry.texture_max}px`:'—'}</dd><dt>资源档</dt><dd>${esc(geometry.profile||'—')}</dd><dt>模型变体</dt><dd>${esc(model.variant)}</dd><dt>可选规格</dt><dd>${variants.map(variantName).map(esc).join(' / ')}</dd></dl></section><section class="detail-section"><h3>验收边界</h3>${(model.limitations||['未记录额外限制。']).map(item=>`<p>${esc(item)}</p>`).join('')}</section>`;
    $('context-mini').innerHTML='几何与规格<br>当前资产';
    $('context-foot').hidden=true;
  }else if(state.contextTab==='source'){
    const source=model.source||{};
    $('context-panel').innerHTML=`<section class="detail-section"><h3>来源批次</h3><dl><dt>资源ID</dt><dd>${esc(model.id)}</dd><dt>源身份</dt><dd>${esc(model.source_id)}</dd><dt>批次</dt><dd>${esc(source.batch||'—')}</dd><dt>构建</dt><dd>${esc(source.build||'—')}</dd><dt>摘要</dt><dd>${esc(model.sha256||'—')}</dd></dl></section><section class="detail-section"><h3>文件与位置</h3><div class="model-links"><a href="${esc(model.preview_url)}" download>下载预览</a><a href="${esc(activeVariant()?.url||model.model_url)}" download>下载当前 GLB</a>${model.blend_url?`<a href="${esc(model.blend_url)}" download>下载 Blender 工程</a>`:''}<button type="button" data-reveal-model>打开 GLB 位置</button>${model.blend_url?'<button type="button" data-reveal-blend>打开工程位置</button>':''}</div><p>“打开文件位置”只定位当前索引登记的本地文件，不向页面暴露完整路径。</p></section>`;
    $('context-mini').innerHTML='批次与摘要<br>当前资产';
    $('context-foot').hidden=true;
  }else{
    const linkedAudio=model.audio_ids.filter(id=>soundById(id)).length;
    $('context-panel').innerHTML=`<section class="detail-section"><h3>资源关系 <span>${esc(CATEGORY[model.category]||'其他')}</span></h3><div class="stat-pair"><div><b>${linkedAudio}</b><span>已确认声音</span></div><div><b>${model.reference_counts?.confirmed??0}</b><span>已确认引用</span></div></div><p>${esc((model.tags||[]).join(' · ')||'未标注标签')}</p></section><section class="detail-section"><h3>同组声音</h3>${groupSounds(model.id).map(group=>`<p><strong>${esc(group.label)}</strong>：${esc(group.rows.map(sound=>readableName(sound.name)).join('、'))}</p>`).join('')||'<p>暂未建立声音关系。</p>'}</section>`;
    $('context-mini').innerHTML='声音与引用<br>当前资产';
    $('context-foot').hidden=true;
  }
}

function filteredAudio(){
  if(!state.catalog)return [];
  const base=state.audioFocusModel?modelSounds(state.catalog,state.audioFocusModel):state.catalog.audio;
  return base.filter(sound=>matches(sound,state.query)&&(state.audioCategory==='all'||sound.category===state.audioCategory)&&(state.audioAssociation==='all'||(state.audioAssociation==='linked'?sound.model_ids.length>0:sound.model_ids.length===0))&&(state.audioQuality==='all'||sound.quality_status===state.audioQuality));
}
function renderAudioFilters(){
  const counts=new Map();
  for(const sound of state.catalog?.audio||[])counts.set(sound.category,(counts.get(sound.category)||0)+1);
  const categories=[...counts].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0],'zh-CN'));
  if(state.audioCategory!=='all'&&!counts.has(state.audioCategory))state.audioCategory='all';
  $('audio-category').innerHTML='<option value="all">全部用途</option>'+categories.map(([category,count])=>`<option value="${esc(category)}">${esc(category)}（${count}）</option>`).join('');
  $('audio-category').value=state.audioCategory;
  $('audio-association').value=state.audioAssociation;
  $('audio-quality').value=state.audioQuality;
  const focus=state.audioFocusModel?modelById(state.audioFocusModel):null;
  $('audio-focus-chip').hidden=!focus;
  if(focus)$('audio-focus-chip').textContent=`关联载具：${focus.name} ×`;
}
function soundIndexRowHtml(sound){
  const tone=dotTone(sound.quality_status);
  return `<div class="sound-index-row" data-sound-row="${esc(sound.id)}" role="listitem" aria-current="${sound.id===state.soundId}"><button type="button" class="mini-play" data-play="${esc(sound.id)}" aria-label="播放 ${esc(readableName(sound.name))}"><span class="play-glyph">▶</span></button><button type="button" class="sound-index-copy" data-sound="${esc(sound.id)}"><strong title="${esc(sound.name)}">${esc(readableName(sound.name))}</strong><small>${esc(sound.category)} · ${formatSeconds(sound.check?.duration_seconds)}</small></button><i class="dot ${tone==='ok'?'':tone}" aria-hidden="true"></i></div>`;
}
function markSoundSelection(){
  for(const row of document.querySelectorAll('[data-sound-row]'))row.setAttribute('aria-current',row.dataset.soundRow===state.soundId);
}
function renderAudioIndex(){
  const rows=filteredAudio();
  const focus=state.audioFocusModel?modelById(state.audioFocusModel):null;
  $('audio-count').textContent=state.catalog?.audio.length||0;
  $('audio-list-label').textContent=`当前编目 / ${focus?focus.name:'全部声音'}${state.query?' · 搜索':''}`;
  $('audio-list-range').textContent=`${Math.min(state.audioLimit,rows.length)} / ${rows.length}`;
  $('audio-more-range').textContent=`${Math.min(state.audioLimit,rows.length)} / ${rows.length}`;
  $('more-audio').hidden=rows.length<=state.audioLimit;
  $('audio-list').innerHTML=rows.slice(0,state.audioLimit).map(soundIndexRowHtml).join('')||'<div class="empty"><strong>没有匹配声音</strong>可尝试口径、用途或缩短搜索词。<button type="button" data-reset-audio>清空筛选</button></div>';
  markSoundSelection();
  if(!rows.some(sound=>sound.id===state.soundId)){
    const fallback=rows[0]?.id||null;
    if(fallback)showSound(fallback,false);
    else{
      state.soundId=null;
      renderSoundEmpty();
      navigate(false);
    }
  }
}
function waveSvg(sound){
  const text=String(sound?.id||'sound');
  let seed=2166136261;
  for(let index=0;index<text.length;index++){seed^=text.charCodeAt(index);seed=Math.imul(seed,16777619);}
  const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
  const total=72;
  const bars=[];
  for(let index=0;index<total;index++){
    const envelope=.34+.66*Math.sin(Math.PI*(index+.5)/total);
    const height=8+random()*92*envelope;
    const x=index*(1000/total)+2;
    const y=60-height/2;
    bars.push(`<rect class="wave-bar${height>56?' hot':''}" x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="7" height="${height.toFixed(2)}" rx="1"/>`);
  }
  return `<svg viewBox="0 0 1000 120" preserveAspectRatio="none" focusable="false"><line class="wave-line" x1="0" y1="60" x2="1000" y2="60"/>${bars.join('')}<line class="wave-playhead" id="wave-playhead" x1="0" y1="0" x2="0" y2="120" vector-effect="non-scaling-stroke"/></svg>`;
}
function updateWavePlayhead(){
  const line=$('waveform').querySelector('.wave-playhead');
  if(!line)return;
  const sound=soundById(state.activeAudio);
  let ratio=0;
  if(sound&&state.activeAudio===state.soundId){
    const duration=Number.isFinite(player.duration)&&player.duration>0?player.duration:Number(sound.check?.duration_seconds)||0;
    if(duration>0)ratio=Math.max(0,Math.min(1,player.currentTime/duration));
  }
  const x=(ratio*1000).toFixed(2);
  line.setAttribute('x1',x);
  line.setAttribute('x2',x);
}
function metricCard(value,label){
  return `<div class="metric-card"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
}
function showSound(id,push=true){
  const sound=id?soundById(id):null;
  if(!sound){
    state.soundId=null;
    renderSoundEmpty();
    if(push)navigate(true);
    return;
  }
  state.soundId=sound.id;
  const check=sound.check||{};
  const index=state.catalog?.audio.findIndex(row=>row.id===sound.id)??-1;
  $('audio-eyebrow').textContent=`Sound Inspection / ${sound.category||'Audio'} / ${index<0?'000':String(index+1).padStart(3,'0')}`;
  $('sound-name').innerHTML=nameHtml(sound.name);
  $('sound-subtitle').innerHTML=`<b>${esc(sound.category)}</b><i class="sep"></i><span>${esc(sound.format||'AUDIO')}</span><i class="sep"></i><span>${formatSeconds(check.duration_seconds)}</span><i class="sep"></i><span>${formatBytes(sound.bytes)}</span>`;
  setStatus($('sound-quality'),sound.quality_status);
  $('waveform').innerHTML=waveSvg(sound);
  $('sound-metrics').innerHTML=metricCard(formatSeconds(check.duration_seconds),'片段时长')+metricCard(sound.format||'AUDIO','声音格式')+metricCard(check.sample_rate?`${(check.sample_rate/1000).toFixed(check.sample_rate%1000?1:0)} kHz`:'—','采样率')+metricCard(check.channels?`${check.channels} 声道`:'—','声道');
  $('sound-issue').textContent=sound.issue||'有限时长事件渲染；循环点未知，逐条听审仍待完成。';
  const download=$('download-sound');
  download.href=sound.url;
  download.setAttribute('download','');
  $('reveal-sound').disabled=!sound.url;
  $('mobile-audio-index').textContent=`${index<0?'000':String(index+1).padStart(3,'0')} / ${String(state.catalog?.audio.length||0).padStart(3,'0')}`;
  $('mobile-audio-name').textContent=readableName(sound.name);
  renderSoundDetail(sound);
  markSoundSelection();
  updateWavePlayhead();
  renderPlayState();
  if(push)navigate(true);
}
function renderSoundEmpty(){
  state.soundId=null;
  $('audio-eyebrow').textContent='Sound Inspection / No selection';
  $('sound-name').textContent='没有匹配声音';
  $('sound-subtitle').textContent='调整用途、关联、状态或搜索词后重试。';
  setStatus($('sound-quality'),null,'无选择');
  $('waveform').innerHTML='<div class="wave-empty">当前筛选没有可显示的声音。</div>';
  $('sound-metrics').innerHTML='';
  $('sound-issue').textContent='声音索引仍可浏览；清空筛选即可恢复全部条目。';
  $('download-sound').href='#';
  $('download-sound').removeAttribute('download');
  $('reveal-sound').disabled=true;
  $('audio-main-play').disabled=true;
  $('sound-detail').innerHTML='<div class="empty"><strong>没有可显示的声音</strong>清空筛选后选择左侧声音条目。<button type="button" data-reset-audio>清空筛选</button></div>';
  $('mobile-audio-index').textContent=`000 / ${String(state.catalog?.audio.length||0).padStart(3,'0')}`;
  $('mobile-audio-name').textContent='没有匹配声音';
  markSoundSelection();
  renderPlayState();
}
function renderSoundDetail(sound){
  const check=sound.check||{};
  const source=sound.source||{};
  const models=(sound.model_ids||[]).map(modelById).filter(Boolean);
  $('sound-detail').innerHTML=`<section class="detail-section"><h3>关联模型 <span>${models.length}</span></h3><div class="model-links">${models.map(model=>`<button type="button" data-open-model="${esc(model.id)}">${esc(model.name)}</button>`).join('')||'<p>尚未与当前模型库建立确证关系。</p>'}</div></section><section class="detail-section"><h3>技术检查</h3><dl><dt>解码</dt><dd>${esc(check.decode_status||'—')}</dd><dt>循环</dt><dd>${esc(check.loop_status||'—')}</dd><dt>听审</dt><dd>${esc(check.listening_status||'—')}</dd><dt>峰值</dt><dd>${esc(check.peak??'—')}</dd><dt>RMS</dt><dd>${esc(check.rms??'—')}</dd><dt>削波样本</dt><dd>${esc(check.clip_samples??'—')}</dd></dl></section><section class="detail-section"><h3>事件与来源</h3><dl><dt>资源ID</dt><dd>${esc(sound.id)}</dd><dt>事件GUID</dt><dd>${esc(sound.event_guid||'—')}</dd><dt>声音库</dt><dd>${esc((sound.banks||[]).join(' · ')||'—')}</dd><dt>来源</dt><dd>${esc(source.batch||'—')} · ${esc(source.kind||source.operation||'—')}</dd></dl><code>${esc(sound.event_path||'未记录事件路径')}</code></section><section class="detail-section"><h3>渲染参数</h3><pre>${esc(JSON.stringify(sound.render_profile||{},null,2))}</pre></section>`;
}

function currentQueue(){
  if(state.currentTab==='audio')return filteredAudio();
  if(state.modelId){
    const rows=state.catalog?modelSounds(state.catalog,state.modelId):[];
    if(rows.length)return rows;
  }
  return state.catalog?.audio||[];
}
function setNowPlaying(){
  const sound=soundById(state.activeAudio);
  if(!sound){
    $('now-title').textContent='还没有播放声音';
    $('now-subtitle').textContent='从声音行或播放器选择一段试听';
    $('time-current').textContent='0:00';
    $('time-total').textContent='0:00';
    $('progress-range').value=0;
    $('progress-range').disabled=true;
    return;
  }
  $('now-title').textContent=readableName(sound.name);
  $('now-subtitle').textContent=`${sound.category} · ${sound.format||'AUDIO'} · ${formatSeconds(sound.check?.duration_seconds)}`;
}
async function playSound(id){
  const sound=id?soundById(id):null;
  if(!sound)return;
  if(state.currentTab==='audio'&&state.soundId!==id)showSound(id,false);
  if(state.activeAudio===id){
    if(player.paused){
      if(player.ended)player.currentTime=0;
      try{await player.play();}catch{toast('播放失败，请检查本地声音文件。',true);}
    }else player.pause();
    renderPlayState();
    return;
  }
  const generation=++state.playGeneration;
  player.pause();
  player.loop=state.loop;
  player.src=sound.url;
  player.load();
  state.activeAudio=id;
  setNowPlaying();
  $('play-state').textContent=`正在准备播放 ${readableName(sound.name)}`;
  renderPlayState();
  try{
    await player.play();
    if(generation!==state.playGeneration)return;
    $('play-state').textContent=`正在播放 ${readableName(sound.name)}`;
    if(state.currentTab==='audio')navigate(true);
    renderPlayState();
  }catch(error){
    if(generation!==state.playGeneration)return;
    $('play-state').textContent='播放失败';
    $('now-subtitle').textContent='播放失败，请检查本地声音文件或重试。';
    toast('声音播放失败，请确认本地资源服务与文件仍在原位。',true);
    renderPlayState();
  }
}
function stopAudio(clear=false){
  state.playGeneration++;
  player.pause();
  if(clear){
    player.removeAttribute('src');
    player.load();
    state.activeAudio=null;
    setNowPlaying();
  }else{
    try{player.currentTime=0;}catch{}
  }
  $('play-state').textContent='已停止播放';
  renderPlayState();
}
function togglePlay(){
  if(state.activeAudio)playSound(state.activeAudio);
  else if(state.soundId)playSound(state.soundId);
  else{
    const first=currentQueue()[0];
    if(first)playSound(first.id);
    else toast('当前范围没有可播放声音。',true);
  }
}
function stepSound(delta){
  const rows=currentQueue();
  if(!rows.length)return;
  let index=rows.findIndex(sound=>sound.id===state.activeAudio);
  if(index<0)index=delta>0?-1:0;
  const next=rows[(index+delta+rows.length)%rows.length];
  if(next)playSound(next.id);
}
function toggleLoop(){
  state.loop=!state.loop;
  player.loop=state.loop;
  $('loop-control').setAttribute('aria-pressed',state.loop);
  renderPlayState();
}
function renderPlayState(){
  const active=soundById(state.activeAudio);
  const playing=Boolean(active&&!player.paused&&!player.ended);
  for(const button of document.querySelectorAll('button[data-play]')){
    const isActive=button.dataset.play===state.activeAudio;
    const glyph=button.querySelector('.play-glyph');
    if(glyph)glyph.textContent=isActive&&playing?'Ⅱ':'▶';
    button.setAttribute('aria-label',`${isActive&&playing?'暂停':'播放'} ${readableName(soundById(button.dataset.play)?.name)}`);
    button.closest('.sound,.sound-index-row')?.classList.toggle('active',isActive&&playing);
  }
  const selected=soundById(state.soundId);
  $('audio-main-play').disabled=!selected;
  $('audio-main-play-label').textContent=!selected?'选择声音':(state.activeAudio===state.soundId&&playing?'暂停声音':(state.activeAudio===state.soundId&&player.currentTime>0?'继续播放':'播放声音'));
  const mainIcon=$('audio-main-play').querySelector('svg');
  if(mainIcon)mainIcon.innerHTML=state.activeAudio===state.soundId&&playing?ICON.pause:ICON.play;
  $('toggle-play-label').textContent=playing?'Ⅱ':'▶';
  $('toggle-play').setAttribute('aria-label',playing?'暂停当前声音':'播放或继续当前声音');
  $('loop-control').setAttribute('aria-pressed',state.loop);
  $('wave-state').textContent=active&&state.activeAudio===state.soundId?(playing?'正在播放':(player.currentTime>0?'已暂停':'尚未播放')):(active?'正在播放另一段声音':'尚未播放');
  updateWavePlayhead();
}
function updateProgress(){
  const sound=soundById(state.activeAudio);
  const duration=Number.isFinite(player.duration)&&player.duration>0?player.duration:Number(sound?.check?.duration_seconds)||0;
  const ratio=duration>0?Math.max(0,Math.min(1,player.currentTime/duration)):0;
  $('progress-range').disabled=!duration;
  $('progress-range').value=Math.round(ratio*1000);
  $('time-current').textContent=formatClock(player.currentTime);
  $('time-total').textContent=formatClock(duration);
  updateWavePlayhead();
}

function revealFile(id,url,button){
  if(!id||!url)return;
  button.setAttribute('aria-busy','true');
  fetch('/api/reveal',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id,url})
  }).then(async response=>{
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error||'无法打开文件位置');
    toast(`已在资源管理器中定位 ${data.file||'当前文件'}`);
  }).catch(error=>{
    toast(`无法打开文件位置：${error.message||'请确认本机服务与文件仍在原位'}`,true);
  }).finally(()=>button.setAttribute('aria-busy','false'));
}
function revealCurrent(kind){
  const model=activeModel();
  if(!model)return;
  if(kind==='blend')revealFile(model.id,model.blend_url,$('reveal-blend'));
  else revealFile(model.id,model.scene_url||model.model_url,$('reveal-model'));
}

function switchTab(name,push=true){
  const changed=state.currentTab!==name;
  if(changed){
    state.query='';
    $('global-search').value='';
    state.modelLimit=30;
    state.audioLimit=40;
  }
  state.currentTab=name;
  $('vehicles-view').hidden=name!=='vehicles';
  $('audio-view').hidden=name!=='audio';
  $('vehicles-tab').setAttribute('aria-selected',name==='vehicles');
  $('audio-tab').setAttribute('aria-selected',name==='audio');
  $('global-search').placeholder=name==='audio'?'搜索声音名称、用途、事件路径…':'搜索型号、角色、别名或来源…';
  document.title=name==='audio'?'前线资产库 · 音效检验台':'前线资产库 · 战术资产检验台';
  closeDrawer();
  if(name==='audio'){
    renderAudioFilters();
    renderAudioIndex();
    if(state.soundId)showSound(state.soundId,false);
    else if(!filteredAudio().length)renderSoundEmpty();
  }else{
    renderModels();
    if(!state.modelId&&state.catalog?.models.length)showModel(state.catalog.models[0].id,false);
    else renderContext();
  }
  if(push)navigate(true);
}
function restore(){
  if(!state.catalog)return;
  const params=new URLSearchParams(location.hash.slice(1));
  const view=params.get('view')==='audio'?'audio':'vehicles';
  const related=params.get('related');
  state.audioFocusModel=modelById(related)?.id||null;
  switchTab(view,false);
  if(view==='audio'){
    const id=params.get('sound');
    showSound(soundById(id)?.id||state.soundId||filteredAudio()[0]?.id||null,false);
    renderAudioIndex();
  }else{
    const id=params.get('model');
    showModel(modelById(id)?.id||state.modelId||filteredModels()[0]?.id||state.catalog.models[0]?.id,false);
  }
}

function openDrawer(mode){
  if(!state.catalog)return;
  state.drawerMode=mode;
  state.drawerLimit=mode==='audio'?40:30;
  $('drawer-title').textContent=mode==='audio'?'切换声音':'切换模型';
  $('drawer-eyebrow').textContent=mode==='audio'?'Sound Index':'Asset Index';
  $('drawer-search').placeholder=mode==='audio'?'搜索声音名称、用途或事件路径…':'搜索型号、角色或源身份…';
  $('drawer-search').value=state.query;
  renderDrawerFilters();
  renderDrawerList();
  lastFocused=document.activeElement;
  $('drawer-backdrop').hidden=false;
  $('asset-drawer').hidden=false;
  document.body.classList.add('drawer-open');
  $('drawer-search').focus();
}
function closeDrawer(){
  if($('asset-drawer').hidden)return;
  $('drawer-backdrop').hidden=true;
  $('asset-drawer').hidden=true;
  document.body.classList.remove('drawer-open');
  state.drawerMode=null;
  if(lastFocused instanceof HTMLElement&&document.contains(lastFocused))lastFocused.focus();
  lastFocused=null;
}
function renderDrawerFilters(){
  if(!state.drawerMode)return;
  if(state.drawerMode==='vehicles'){
    const models=modelEntries();
    const count=key=>key==='all'?models.length:models.filter(model=>modelCategory(model)===key).length;
    $('drawer-filters').innerHTML=MODEL_FILTERS.filter(key=>['all','air','ground','other'].includes(key)||count(key)).map(key=>`<button type="button" data-drawer-category="${key}" aria-pressed="${state.category===key}">${key==='all'?'全部':CATEGORY_SHORT[key]} ${count(key)}</button>`).join('');
  }else{
    const categories=[...new Set(state.catalog.audio.map(sound=>sound.category))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
    $('drawer-filters').innerHTML=`<div class="drawer-selects"><label>用途<select id="drawer-audio-category"><option value="all">全部用途</option>${categories.map(category=>`<option value="${esc(category)}">${esc(category)}</option>`).join('')}</select></label><label>关联<select id="drawer-audio-association"><option value="all">全部声音</option><option value="linked">已关联</option><option value="unassigned">未关联</option></select></label><label>状态<select id="drawer-audio-quality"><option value="all">全部状态</option><option value="ready">技术通过</option><option value="review">待复核</option></select></label></div>`;
    $('drawer-audio-category').value=state.audioCategory;
    $('drawer-audio-association').value=state.audioAssociation;
    $('drawer-audio-quality').value=state.audioQuality;
  }
}
function renderDrawerList(){
  if(!state.drawerMode)return;
  if(state.drawerMode==='vehicles'){
    const rows=filteredModels();
    $('drawer-list').innerHTML=rows.slice(0,state.drawerLimit).map(modelRowHtml).join('')||'<div class="empty"><strong>没有匹配模型</strong>尝试缩短搜索词。</div>';
    $('drawer-count').textContent=`${Math.min(state.drawerLimit,rows.length)} / ${rows.length}`;
    $('drawer-more').hidden=rows.length<=state.drawerLimit;
  }else{
    const rows=filteredAudio();
    $('drawer-list').innerHTML=rows.slice(0,state.drawerLimit).map(soundIndexRowHtml).join('')||'<div class="empty"><strong>没有匹配声音</strong>尝试缩短搜索词。</div>';
    $('drawer-count').textContent=`${Math.min(state.drawerLimit,rows.length)} / ${rows.length}`;
    $('drawer-more').hidden=rows.length<=state.drawerLimit;
  }
}
function resetModelFilters(){
  state.query='';
  state.category='all';
  state.modelLimit=30;
  $('global-search').value='';
  renderModelFilters();
  renderModels();
}
function resetAudioFilters(){
  state.query='';
  state.audioCategory='all';
  state.audioAssociation='all';
  state.audioQuality='all';
  state.audioFocusModel=null;
  state.audioLimit=40;
  $('global-search').value='';
  renderAudioFilters();
  renderAudioIndex();
}
function focusModelAudio(){
  if(!state.modelId)return;
  state.audioFocusModel=state.modelId;
  state.audioCategory='all';
  state.audioAssociation='all';
  state.audioQuality='all';
  state.soundId=null;
  switchTab('audio',false);
  const first=filteredAudio()[0];
  showSound(first?.id||null,false);
  navigate(true);
}

async function refresh(){
  const keep=state.catalog?{query:state.query,category:state.category,audioCategory:state.audioCategory,audioAssociation:state.audioAssociation,audioQuality:state.audioQuality,currentTab:state.currentTab,modelId:state.modelId,soundId:state.soundId,audioFocusModel:state.audioFocusModel}:null;
  $('refresh').disabled=true;
  setSync('正在同步索引');
  try{
    const response=await fetch('/api/catalog',{cache:'no-store'});
    if(!response.ok)throw new Error('读取失败');
    state.catalog=await response.json();
    $('global-notice').textContent=`${state.catalog.summary.models}个模型入口 · ${state.catalog.summary.audio}项声音 · ${state.catalog.summary.unassigned_audio}项声音尚未关联模型`;
    renderModelFilters();
    renderAudioFilters();
    if(keep){
      state.query=keep.query;
      $('global-search').value=keep.query;
      state.category=keep.category;
      state.audioCategory=keep.audioCategory;
      state.audioAssociation=keep.audioAssociation;
      state.audioQuality=keep.audioQuality;
      state.audioFocusModel=modelById(keep.audioFocusModel)?.id||null;
      state.modelId=modelById(keep.modelId)?.id||null;
      state.soundId=soundById(keep.soundId)?.id||null;
      renderAudioFilters();
      if(state.currentTab==='audio'){
        renderAudioIndex();
        showSound(state.soundId||filteredAudio()[0]?.id||null,false);
      }else{
        if(!state.modelId)state.modelId=filteredModels()[0]?.id||state.catalog.models[0]?.id||null;
        renderModels();
        if(state.modelId)showModel(state.modelId,false);
      }
    }else restore();
    setSync(`${state.catalog.status==='candidate'?'候选 · 基于':''}索引 V${state.catalog.revision} · 已同步`);
    $('global-notice').textContent=`${state.catalog.summary.models}个模型入口 · ${state.catalog.summary.audio}项声音 · ${state.catalog.summary.unassigned_audio}项声音尚未关联模型`;
  }catch(error){
    setSync('索引读取失败',true);
    toast('前线资产库暂时无法读取，请确认本地服务已启动后刷新。',true);
    if(!state.catalog){
      $('model-list').innerHTML='<div class="empty"><strong>索引读取失败</strong>确认8767服务已启动，再点击右上角刷新。</div>';
      $('audio-list').innerHTML='<div class="empty"><strong>索引读取失败</strong>确认8767服务已启动，再点击右上角刷新。</div>';
      $('model-count').textContent='0';
      $('audio-count').textContent='0';
    }
  }finally{$('refresh').disabled=false;}
}

function moveModel(delta){
  const rows=filteredModels();
  if(!rows.length)return;
  let index=rows.findIndex(model=>model.id===state.modelId);
  if(index<0)index=0;
  const next=rows[(index+delta+rows.length)%rows.length];
  if(next)showModel(next.id,false);
}

$('vehicles-tab').addEventListener('click',()=>switchTab('vehicles'));
$('audio-tab').addEventListener('click',()=>switchTab('audio'));
$('refresh').addEventListener('click',refresh);
$('global-search').addEventListener('input',event=>{
  state.query=event.target.value;
  state.modelLimit=30;
  state.audioLimit=40;
  if(state.currentTab==='audio')renderAudioIndex();
  else renderModels();
  if(state.drawerMode)renderDrawerList();
});
$('model-filters').addEventListener('click',event=>{
  const button=event.target.closest('[data-category]');
  if(!button)return;
  state.category=button.dataset.category;
  state.modelLimit=30;
  renderModelFilters();
  renderModels();
  if(state.drawerMode==='vehicles'){renderDrawerFilters();renderDrawerList();}
});
$('spec-row').addEventListener('click',event=>{
  const button=event.target.closest('[data-spec]');
  if(button)selectSpec(Number(button.dataset.spec));
});
$('context-tabs').addEventListener('click',event=>{
  const button=event.target.closest('[data-context-tab]');
  if(!button)return;
  state.contextTab=button.dataset.contextTab;
  renderContext();
  $('context-panel').scrollTop=0;
});
$('context-more').addEventListener('click',focusModelAudio);
$('audio-focus-chip').addEventListener('click',()=>{
  state.audioFocusModel=null;
  renderAudioFilters();
  renderAudioIndex();
  navigate(false);
});
$('more-models').addEventListener('click',()=>{state.modelLimit+=30;renderModels();});
$('more-audio').addEventListener('click',()=>{state.audioLimit+=40;renderAudioIndex();});
for(const id of ['audio-category','audio-association','audio-quality']){
  $(id).addEventListener('change',()=>{
    state.audioCategory=$('audio-category').value;
    state.audioAssociation=$('audio-association').value;
    state.audioQuality=$('audio-quality').value;
    state.audioLimit=40;
    renderAudioIndex();
    if(state.drawerMode==='audio')renderDrawerList();
  });
}
$('load-3d').addEventListener('click',loadModel);
$('show-image').addEventListener('click',showPreviewImage);
$('reset-view').addEventListener('click',()=>state.viewer?.reset());
$('fullscreen').addEventListener('click',()=>{
  if(document.fullscreenElement){document.exitFullscreen?.();return;}
  $('preview').requestFullscreen?.().catch(()=>toast('当前窗口不支持全屏，可放大浏览器窗口查看。',true));
});
$('reveal-model').addEventListener('click',()=>revealCurrent('model'));
$('reveal-blend').addEventListener('click',()=>revealCurrent('blend'));
$('reveal-sound').addEventListener('click',()=>{
  const sound=soundById(state.soundId);
  if(sound)revealFile(sound.id,sound.url,$('reveal-sound'));
});
$('audio-main-play').addEventListener('click',()=>{if(state.soundId)playSound(state.soundId);});
$('toggle-play').addEventListener('click',togglePlay);
$('prev-sound').addEventListener('click',()=>stepSound(-1));
$('next-sound').addEventListener('click',()=>stepSound(1));
$('loop-control').addEventListener('click',toggleLoop);
$('stop-audio').addEventListener('click',()=>stopAudio(true));
$('progress-range').addEventListener('input',event=>{
  const sound=soundById(state.activeAudio);
  if(!sound)return;
  const duration=Number.isFinite(player.duration)&&player.duration>0?player.duration:Number(sound.check?.duration_seconds)||0;
  if(duration>0){player.currentTime=(Number(event.target.value)/1000)*duration;updateProgress();}
});
$('volume').addEventListener('input',event=>{
  state.volume=Number(event.target.value)/100;
  player.volume=state.volume;
});
for(const id of ['drawer-close'])$(id).addEventListener('click',closeDrawer);
$('drawer-backdrop').addEventListener('click',closeDrawer);
$('open-model-drawer').addEventListener('click',()=>openDrawer('vehicles'));
$('open-audio-drawer').addEventListener('click',()=>openDrawer('audio'));
$('drawer-search').addEventListener('input',event=>{
  state.query=event.target.value;
  $('global-search').value=state.query;
  state.modelLimit=30;
  state.audioLimit=40;
  state.drawerLimit=50;
  if(state.drawerMode==='audio')renderAudioIndex();
  else renderModels();
  renderDrawerList();
});
$('drawer-more').addEventListener('click',()=>{state.drawerLimit+=50;renderDrawerList();});
$('drawer-filters').addEventListener('click',event=>{
  const button=event.target.closest('[data-drawer-category]');
  if(!button)return;
  state.category=button.dataset.drawerCategory;
  state.modelLimit=30;
  renderModelFilters();
  renderModels();
  renderDrawerFilters();
  renderDrawerList();
});
$('drawer-filters').addEventListener('change',event=>{
  if(state.drawerMode!=='audio')return;
  state.audioCategory=$('drawer-audio-category')?.value||'all';
  state.audioAssociation=$('drawer-audio-association')?.value||'all';
  state.audioQuality=$('drawer-audio-quality')?.value||'all';
  $('audio-category').value=state.audioCategory;
  $('audio-association').value=state.audioAssociation;
  $('audio-quality').value=state.audioQuality;
  state.audioLimit=40;
  renderAudioIndex();
  renderDrawerList();
});

document.addEventListener('click',event=>{
  const button=event.target.closest('button,a');
  if(!button||!state.catalog)return;
  if(button.dataset.model){
    switchTab('vehicles',false);
    showModel(button.dataset.model,true);
    closeDrawer();
    if(innerWidth<960)$('workspace').scrollIntoView({block:'start'});
    return;
  }
  if(button.dataset.sound){
    switchTab('audio',false);
    showSound(button.dataset.sound,true);
    closeDrawer();
    if(innerWidth<960)$('audio-workspace').scrollIntoView({block:'start'});
    return;
  }
  if(button.dataset.play){playSound(button.dataset.play);return;}
  if(button.dataset.revealSound){
    const sound=soundById(button.dataset.revealSound);
    if(sound)revealFile(sound.id,sound.url,button);
    return;
  }
  if(button.dataset.openModel){
    switchTab('vehicles',false);
    showModel(button.dataset.openModel,true);
    closeDrawer();
    return;
  }
  if(button.hasAttribute('data-reveal-model')){revealCurrent('model');return;}
  if(button.hasAttribute('data-reveal-blend')){revealCurrent('blend');return;}
  if(button.hasAttribute('data-show-all-audio')){focusModelAudio();return;}
  if(button.hasAttribute('data-reset-model')){resetModelFilters();return;}
  if(button.hasAttribute('data-reset-audio')){resetAudioFilters();return;}
  if(button.dataset.openDrawer){openDrawer(button.dataset.openDrawer);return;}
});

player.addEventListener('loadedmetadata',updateProgress);
player.addEventListener('timeupdate',updateProgress);
player.addEventListener('play',renderPlayState);
player.addEventListener('pause',renderPlayState);
player.addEventListener('ended',()=>{updateProgress();renderPlayState();$('play-state').textContent='当前声音已播放完毕';});
player.addEventListener('error',()=>{
  if(state.activeAudio){
    $('now-subtitle').textContent='音频加载失败，可再次点击播放重试。';
    toast('音频加载失败，请确认本地文件仍在原位。',true);
  }
});
document.addEventListener('visibilitychange',()=>{if(document.hidden)player.pause();});
window.addEventListener('pagehide',()=>{stopAudio(true);state.viewerGeneration++;state.viewer?.dispose();state.viewer=null;});
window.addEventListener('popstate',restore);

document.addEventListener('keydown',event=>{
  if(!$('asset-drawer').hidden){
    if(event.key==='Escape'){event.preventDefault();closeDrawer();return;}
    if(event.key==='Tab'){
      const focusable=[...$('asset-drawer').querySelectorAll('button,input,select,a[href]')].filter(node=>!node.disabled&&node.offsetParent!==null);
      if(!focusable.length)return;
      const first=focusable[0],last=focusable[focusable.length-1];
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
    }
    return;
  }
  const target=event.target;
  const typing=target.closest('input,select,textarea');
  if(event.key==='/'&&!typing){
    event.preventDefault();
    $('global-search').focus();
    $('global-search').select();
    return;
  }
  if(event.key==='Escape'){
    if(document.fullscreenElement)return;
    if(state.activeAudio){stopAudio(true);toast('已停止并清除当前声音');}
    return;
  }
  if(typing||target.closest('button,a,summary'))return;
  const key=event.key.toLowerCase();
  if(state.currentTab==='audio'){
    if(event.code==='Space'){event.preventDefault();togglePlay();}
    else if(key==='l'){event.preventDefault();toggleLoop();}
    return;
  }
  if(key==='arrowdown'||key==='j'){event.preventDefault();moveModel(1);}
  else if(key==='arrowup'||key==='k'){event.preventDefault();moveModel(-1);}
  else if(event.key==='Enter'){event.preventDefault();loadModel();}
  else if(['1','2','3'].includes(event.key)){event.preventDefault();selectSpec(Number(event.key)-1);}
  else if(event.code==='Space'){event.preventDefault();togglePlay();}
  else if(key==='l'){event.preventDefault();toggleLoop();}
  else if(key==='f'){
    event.preventDefault();
    if(document.fullscreenElement)document.exitFullscreen?.();
    else state.viewer?.reset();
  }
});

await refresh();
