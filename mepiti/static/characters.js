'use strict';
// 게임 내 장비창과 같은 고정 배치(5열 6행). 빈 칸은 게임에서도 비어 있는 자리다.
// 슬롯 이름은 넥슨 API의 item_equipment_slot 값과 정확히 같아야 한다.
const EQUIPMENT_LAYOUT=[
  ['반지4',null,'모자',null,'엠블렘'],
  ['반지3','펜던트','얼굴장식',null,'뱃지'],
  ['반지2','펜던트2','눈장식','귀고리','훈장'],
  ['반지1','무기','상의','어깨장식','보조무기'],
  ['포켓 아이템','벨트','하의','장갑','망토'],
  ['칭호',null,'신발','안드로이드','기계 심장']
];
let selectedCharacterName = '', profileRequest = 0;
const profileCache = new Map();
function nexonImage(url,alt,cls){
  if(!url)return el('span','image-unavailable','이미지 없음');
  try{const u=new URL(url);if(u.protocol!=='https:'||u.hostname!=='open.api.nexon.com'||!u.pathname.startsWith('/static/maplestory/')||u.username)throw new Error();}
  catch{return el('span','image-unavailable','이미지 없음');}
  const img=el('img',cls);img.src=url;img.alt=alt;img.decoding='async';img.onerror=()=>img.replaceWith(el('span','image-unavailable','불러오기 실패'));return img;
}
function showProfileEmpty(message){
  const box=$('#character-profile');box.replaceChildren();const empty=el('div','profile-placeholder');empty.append(el('h2','','캐릭터 미선택'),el('p','',message||'아래 목록에서 캐릭터를 선택하세요.'));
  box.append(empty);
}
function autoSelectCharacter(){
  if(selectedCharacterName)return;
  const main=managedCharacters.find(c=>c.main);
  const first=main?.name||accountCatalog?.characters[0]?.name;
  if(first)showCharacterProfile(first);else showProfileEmpty('표시할 캐릭터가 없습니다. 이름으로 직접 등록하거나 키의 계정을 확인하세요.');
}
function renderCharacterRoster(){
  const list=$('#account-characters');list.replaceChildren();list.className='roster-grid';if(!accountCatalog)return;
  const query=$('#account-search').value.trim().toLowerCase();
  const characters=accountCatalog.characters.filter(c=>[c.name,c.world,c.job].some(v=>String(v||'').toLowerCase().includes(query)));
  if(!characters.length){list.append(el('p','hint',accountCatalog.characters.length?'검색 결과 없음':'반환된 캐릭터 없음'));return;}
  characters.forEach(c=>{
    const button=el('button','roster-card'+(selectedCharacterName===c.name?' selected':''));button.type='button';button.setAttribute('aria-pressed',String(selectedCharacterName===c.name));button.setAttribute('aria-label',`${c.name} 캐릭터 보기, ${c.world}, ${c.job}, 레벨 ${c.level}`);
    const art=el('div','roster-art');const cached=profileCache.get(c.name)?.data;
    if(cached?.image)art.append(nexonImage(cached.image,c.name+' 외형','roster-avatar'));else art.append(el('span','roster-job',c.job||'모험가'));
    const info=el('div','roster-info');info.append(el('strong','',c.name),el('small','',`${c.world||''} · Lv. ${fmt(c.level)}`));if(managedNames.has(c.name))info.append(el('span','roster-managed','관리 중'));button.append(art,info,el('span','roster-arrow','↗'));
    button.onclick=()=>{showCharacterProfile(c.name);$('#character-profile').scrollIntoView({behavior:'smooth',block:'start'});};list.append(button);
  });
}
async function showCharacterProfile(name,force=false){
  selectedCharacterName=name;const request=++profileRequest;renderCharacterRoster();
  const box=$('#character-profile');const cached=profileCache.get(name);
  if(!force&&cached&&Date.now()-cached.at<5*60*1000){renderCharacterProfile(cached.data);return;}
  box.replaceChildren();const loading=el('div','profile-placeholder loading');loading.append(el('h2','',name),el('p','','외형 · 능력치 · 장비 조회 중'));box.append(loading);box.setAttribute('aria-busy','true');
  try{const data=await api('characters/profile',{name});if(request!==profileRequest)return;profileCache.set(name,{data,at:Date.now()});renderCharacterProfile(data);renderCharacterRoster();}
  catch(e){if(request!==profileRequest)return;box.replaceChildren();const failed=el('div','profile-placeholder');failed.append(el('h2','',name+' 조회 실패'),el('p','',e.message));const retry=el('button','primary','다시 조회');retry.onclick=()=>showCharacterProfile(name,true);failed.append(retry);box.append(failed);}
  finally{if(request===profileRequest)box.removeAttribute('aria-busy');}
}
function statValue(data,name){return data.stats?.find(s=>s.name===name)?.value;}
function numericText(value){if(value===null||value===undefined||value==='')return '미조회';const n=Number(String(value).replaceAll(',',''));return Number.isFinite(n)?fmt(n):String(value);}
function metric(label,value,suffix=''){const box=el('div','profile-metric');box.append(el('span','',label),el('strong','',value==null?'미조회':numericText(value)+suffix));return box;}
function renderCharacterProfile(data){
  const root=$('#character-profile');root.replaceChildren();
  const managed=managedCharacters.find(c=>c.name===data.name);
  const hero=el('section','character-hero');const stage=el('div','character-stage');
  const identity=el('div','stage-identity');identity.append(el('h2','',data.name),el('p','',`${data.world||'월드 미확인'} · ${data.job||'직업 미확인'}`));stage.append(identity,nexonImage(data.image,data.name+' 실제 캐릭터 외형','hero-avatar'));
  const level=el('div','stage-level');level.append(el('small','','LEVEL'),el('strong','',fmt(data.level)));stage.append(level);
  const body=el('div','profile-summary');const top=el('div','profile-topline');top.append(el('span','profile-live','NEXON OPEN API'),el('span','badge',managed?.main?'대표':'선택'));body.append(top);
  const power=el('div','combat-power');power.append(el('span','','전투력'),el('strong','',numericText(data.combat_power)));body.append(power);
  const basic=el('div','profile-basic');basic.append(el('span','',data.job||'직업 미확인'),el('span','',data.guild?'길드 · '+data.guild:'소속 길드 없음'));body.append(basic);
  const stats=el('div','profile-stats');[['HP','HP',''],['보스 데미지','보스 몬스터 데미지','%'],['방어율 무시','방어율 무시','%'],['크리티컬 확률','크리티컬 확률','%']].forEach(([label,key,unit])=>{const value=statValue(data,key);stats.append(metric(label,value,value!=null&&!String(value).includes('%')?unit:''));});body.append(stats);
  const exp=Number(data.exp_rate);const expWrap=el('div','experience');expWrap.append(el('div','experience-label',`현재 레벨 경험치 · ${data.exp_rate!=null&&Number.isFinite(exp)?numericText(data.exp_rate)+'%':'미조회'}`));if(data.exp_rate!=null&&Number.isFinite(exp)){const bar=el('progress','experience-bar');bar.max=100;bar.value=Math.min(100,Math.max(0,exp));bar.setAttribute('aria-label','현재 레벨 경험치');expWrap.append(bar);}body.append(expWrap);
  const actions=el('div','profile-actions');const refresh=el('button','secondary','다시 조회');refresh.onclick=()=>showCharacterProfile(data.name,true);actions.append(refresh);
  const save=el('button','primary',managed?'스냅샷 저장':'관리 목록에 추가');save.onclick=()=>task(save,async()=>{const current=managedCharacters.find(c=>c.name===data.name);const record=current||await api('characters',{name:data.name,budget:0});await api('characters/refresh',{id:record.id});await loadCharacters();if(selectedCharacterName===data.name)renderCharacterProfile(data);toast('스냅샷을 저장했습니다.');});actions.append(save);body.append(actions);
  if(managed)body.append(el('p','profile-goal','목표 · '+(managed.goal||'미설정')+' · 예산 '+fmt(managed.budget)+' 메소'));
  hero.append(stage,body);root.append(hero);
  if(data.warnings?.length){const warnings=el('div','notice');data.warnings.forEach(w=>warnings.append(el('p','',w)));root.append(warnings);}
  const lower=el('div','profile-lower');const inventory=el('section','equipment-panel');const heading=el('div','equipment-heading');heading.append(el('h2','','착용 장비'),el('span','badge',data.equipment_status==='available'?`${data.equipment.length}칸 · 프리셋 ${data.equipment_preset??'—'}`:'조회 불가'));inventory.append(heading,el('p','hint','게임 장비창과 같은 자리. 칸 선택 시 상세 표시.'));
  const equipmentGrid=el('div','equipment-grid');const itemDetail=el('section','item-detail');itemDetail.setAttribute('aria-live','polite');
  if(data.equipment_status!=='available'){inventory.append(el('p','empty-state','장비 조회 실패'));itemDetail.append(el('p','hint','다시 조회하면 장비도 재조회합니다.'));}
  else if(!data.equipment.length){inventory.append(el('p','empty-state','착용 장비 없음'));itemDetail.append(el('p','hint','표시할 장비 없음'));}
  else{const extras=el('div','equipment-extra');const firstItem=renderEquipmentBoard(equipmentGrid,extras,itemDetail,data);inventory.append(equipmentGrid);if(extras.childElementCount)inventory.append(el('p','hint','배치표에 자리가 없는 장비'),extras);renderEquipmentDetail(itemDetail,firstItem||data.equipment[0]);}
  lower.append(inventory,itemDetail);root.append(lower);
  const allStats=el('details','panel all-stats');allStats.append(el('summary','','전체 능력치'));const statGrid=el('div','all-stats-grid');(data.stats||[]).forEach(s=>{const row=el('div');row.append(el('span','',s.name),el('strong','',numericText(s.value)));statGrid.append(row);});if(!data.stats?.length)statGrid.append(el('p','hint','능력치 조회 실패'));allStats.append(statGrid);root.append(allStats);
  const provenance=el('div','profile-provenance');provenance.append(el('span','',`조회 ${data.retrieved_at} · 기본 ${data.api_date||'미제공'} · 장비 ${data.equipment_api_date||'미제공'}`),sourceLink('https://openapi.nexon.com/ko/game/maplestory/?id=14','넥슨 Open API ↗'));root.append(provenance);
}
function equipmentButton(item,detail,buttons){
  const add=item.add_grade||{};const hasAdd=!!(add.grade||add.tier||add.boss_sum);
  const button=el('button','equipment-slot '+gradeClass(item.potential_grade)+(hasAdd?' has-add':''));button.type='button';
  const star=item.starforce,up=item.scroll_upgrade;
  button.title=`${item.slot||item.part||'장비'} · ${item.name}`;
  button.setAttribute('aria-pressed','false');
  button.setAttribute('aria-label',`${item.slot||item.part||'장비'}: ${item.name}, 스타포스 ${star==null?'미확인':star+'성'}, 주문서 강화 ${up==null?'미확인':up+'회'}, 추가옵션 ${add.label||'없음'}`);
  const marks=el('span','slot-marks');
  if(star)marks.append(el('span','slot-star','★'+star));
  if(up)marks.append(el('span','slot-upgrade','+'+up));
  if(marks.childElementCount)button.append(marks);
  button.append(nexonImage(item.icon,item.name,'item-icon'));
  if(add.tier)button.append(el('span','slot-add slot-tier',add.tier+(add.boss_sum?` 보뎀${add.boss_sum}`:(add.all_stat?` 올${add.all_stat}`:''))));
  else if(add.grade)button.append(el('span','slot-add',fmt(add.grade)));
  button.onclick=()=>{buttons.forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));});renderEquipmentDetail(detail,item);};
  return button;
}
// 장비창 자리마다 해당 슬롯의 장비를 놓는다. 자리가 비면 슬롯 이름만 흐리게 남긴다.
function renderEquipmentBoard(board,extras,detail,data){
  const bySlot=new Map();data.equipment.forEach(item=>{if(item.slot&&!bySlot.has(item.slot))bySlot.set(item.slot,item);});
  const overall=bySlot.get('한벌옷');const placed=new Set();const buttons=[];let first=null;
  EQUIPMENT_LAYOUT.forEach(row=>row.forEach(slot=>{
    if(!slot){board.append(el('div','equipment-cell blank'));return;}
    if(slot==='하의'&&overall){const cell=el('div','equipment-cell locked');cell.append(el('span','slot-empty','한벌옷 착용'));board.append(cell);return;}
    const item=(slot==='상의'&&overall)?overall:bySlot.get(slot);
    if(!item){const cell=el('div','equipment-cell empty');cell.append(el('span','slot-empty',slot));board.append(cell);return;}
    placed.add(item.slot);const button=equipmentButton(item,detail,buttons);buttons.push(button);board.append(button);
    if(!first){first=item;button.classList.add('selected');button.setAttribute('aria-pressed','true');}
  }));
  // 배치표에 없는 슬롯도 숨기지 않고 따로 보여 준다.
  data.equipment.filter(i=>!placed.has(i.slot)).forEach(item=>{const button=equipmentButton(item,detail,buttons);buttons.push(button);extras.append(button);});
  return first;
}
function gradeClass(grade){return {'레전드리':'grade-legendary','유니크':'grade-unique','에픽':'grade-epic','레어':'grade-rare'}[grade]||'grade-normal';}
function renderEquipmentDetail(box,item){
  box.replaceChildren();const head=el('div','item-detail-head');head.append(nexonImage(item.icon,item.name,'item-detail-icon'));const names=el('div');names.append(el('span','item-category',item.slot||item.part||'장비'),el('h3','',item.name),el('span','item-starforce',(item.starforce==null?'—':`★${item.starforce}`)+(item.scroll_upgrade?` · 주문서 +${item.scroll_upgrade}`:'')));head.append(names);box.append(head);
  const labels={str:'STR',dex:'DEX',int:'INT',luk:'LUK',max_hp:'HP',max_mp:'MP',attack_power:'공격력',magic_power:'마력',armor:'방어력',boss_damage:'보스 데미지 (%)',ignore_monster_armor:'방어율 무시 (%)',all_stat:'올스탯 (%)',damage:'데미지 (%)'};
  const options=el('div','item-options');Object.entries(item.options||{}).filter(([,v])=>Number(v)!==0).forEach(([k,v])=>{const row=el('div');row.append(el('span','',labels[k]||k),el('strong','',numericText(v)));options.append(row);});box.append(options);
  if(item.description){const info=el('div','item-info');item.description.split('\n').filter(line=>line.trim()).forEach(line=>info.append(el('p','',line.trim())));box.append(info);}
  const grade=item.add_grade;
  if(grade){
    const add=el('div','item-add');add.append(el('h4','','추가옵션'+(grade.label?' · '+grade.label:'')));
    const addRows=el('div','item-options');Object.entries(item.add_options||{}).forEach(([k,v])=>{const row=el('div');row.append(el('span','',labels[k]||k),el('strong','',numericText(v)));addRows.append(row);});
    if(addRows.childElementCount)add.append(addRows);else add.append(el('p','hint','추가옵션 없음'));
    if(grade.tier)add.append(el('p','hint',`무기 공/마 추옵 ${fmt(grade.power)} / 기본 ${grade.power_name} 대비 ${Math.round((grade.ratio||0)*1000)/10}% → ${grade.tier}. 단계 경계는 아케인셰이드 기준 약식이며 게임이 주는 등급이 아닙니다.`));
    else if(grade.grade)add.append(el('p','hint',`급 = ${grade.note||'—'} → 주스탯 ${fmt(grade.grade)} 상당. 올스탯 1%=주스탯 10, 공/마 1=주스탯 4로 잡은 커뮤니티 약식 기준입니다.`));
    box.append(add);}
  [['잠재능력',item.potential_grade,item.potential],['에디셔널 잠재능력',item.additional_grade,item.additional_potential]].filter(([,grade,lines])=>grade||lines?.length).forEach(([label,grade,lines])=>{const section=el('div','potential '+gradeClass(grade));section.append(el('h4','',label+(grade?' · '+grade:'')));if(lines?.length)lines.forEach(line=>section.append(el('p','',line)));else section.append(el('p','hint','제공된 옵션 없음'));box.append(section);});
}
