// 名称、型号、别名和分类使用统一的宽松匹配，A-10与A10均可搜索。
export const normalize=value=>String(value??'').normalize('NFKC').replace(/[\p{P}\p{S}\s_]+/gu,'').toLocaleLowerCase();
export function matches(row,query){const terms=query.split(/\s+/).map(normalize).filter(Boolean);const text=normalize([row.id,row.name,...row.aliases,...row.tags,row.event_path||'',row.event_guid||'',...(row.banks||[])].join(' '));return terms.every(term=>text.includes(term));}
export function modelSounds(catalog,modelId){const model=catalog.models.find(model=>model.id===modelId);return model?catalog.audio.filter(audio=>model.audio_ids.includes(audio.id)):[];}
