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
let selectedCharacterName = '', profileRequest = 0, shownPreset = null;
const profileCache = new Map();
function resetCharacterCache(){
  keyRequest++;keyChecking=false;
  catalogRequest++;accountLoading=false;$('#discover-characters').disabled=false;
  accountCatalog=null;profileCache.clear();selectedCharacterName='';shownPreset=null;profileRequest++;
  $('#character-profile').removeAttribute('aria-busy');showProfileEmpty('캐릭터 목록을 다시 불러와 주세요.');
}
function nexonImage(url,alt,cls){
  if(!url)return el('span','image-unavailable','이미지 없음');
  try{const u=new URL(url);if(u.protocol!=='https:'||u.hostname!=='open.api.nexon.com'||!u.pathname.startsWith('/static/maplestory/')||u.username)throw new Error();}
  catch{return el('span','image-unavailable','이미지 없음');}
  const img=el('img',cls);img.src=url;img.alt=alt;img.decoding='async';img.onerror=()=>img.replaceWith(el('span','image-unavailable','불러오기 실패'));return img;
}
// 넥슨 캐릭터 이미지는 캐릭터 주위에 투명 여백이 크다. 캐릭터가 있는 부분만 잘라 칸을 채운다.
// 넥슨 서버가 교차 출처 읽기를 막으면 잘라낼 수 없으므로, 캐릭터 쪽으로 확대하는 방식으로 대신한다.
function fitSprite(img,url){
  const fallback=()=>img.classList.add('zoomed');
  const probe=new Image();probe.crossOrigin='anonymous';
  probe.onload=()=>{try{
    const c=document.createElement('canvas');c.width=probe.naturalWidth;c.height=probe.naturalHeight;
    const g=c.getContext('2d');g.drawImage(probe,0,0);const px=g.getImageData(0,0,c.width,c.height).data;
    let x0=c.width,y0=c.height,x1=-1,y1=-1;
    for(let y=0;y<c.height;y++)for(let x=0;x<c.width;x++)if(px[(y*c.width+x)*4+3]>8){if(x<x0)x0=x;if(x>x1)x1=x;if(y<y0)y0=y;if(y>y1)y1=y;}
    if(x1<0)return fallback();
    const pad=2,w=x1-x0+1+pad*2,h=y1-y0+1+pad*2,out=document.createElement('canvas');out.width=w;out.height=h;
    out.getContext('2d').drawImage(c,x0-pad,y0-pad,w,h,0,0,w,h);
    img.src=out.toDataURL('image/png');img.classList.add('trimmed');
  }catch{fallback();}};
  probe.onerror=fallback;probe.src=url;
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
  const identity=el('div','stage-identity');identity.append(el('h2','',data.name),el('p','',`${data.world||'월드 미확인'} · ${data.job||'직업 미확인'}`));const sprite=nexonImage(data.image,data.name+' 실제 캐릭터 외형','hero-avatar');stage.append(identity,sprite);if(sprite.tagName==='IMG')fitSprite(sprite,data.image);
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
  else{
    // 프리셋 1~3. 누르면 그 프리셋의 장비로 장비창을 다시 그린다. 지금 게임에서 적용 중인 프리셋에 표시한다.
    const presets=data.equipment_presets||{};const current=data.equipment_preset!=null?String(data.equipment_preset):null;
    const extras=el('div','equipment-extra');const extraHint=el('p','hint','배치표에 자리가 없는 장비');
    const show=no=>{
      shownPreset=no;const rows=no&&presets[no]?presets[no]:data.equipment;
      equipmentGrid.replaceChildren();extras.replaceChildren();
      const firstItem=renderEquipmentBoard(equipmentGrid,extras,itemDetail,{...data,equipment:rows});
      extraHint.hidden=extras.hidden=!extras.childElementCount;
      renderEquipmentDetail(itemDetail,firstItem||rows[0]);
      tabs.querySelectorAll('button').forEach(b=>{const on=b.dataset.preset===String(no);b.classList.toggle('selected',on);b.setAttribute('aria-pressed',String(on));});
    };
    const tabs=el('div','preset-tabs');tabs.setAttribute('role','group');tabs.setAttribute('aria-label','장비 프리셋');
    ['1','2','3'].forEach(no=>{
      const b=el('button','choice',`프리셋 ${no}`+(no===current?' · 적용 중':''));b.type='button';b.dataset.preset=no;
      b.disabled=!presets[no];if(!presets[no])b.title='비어 있는 프리셋';b.onclick=()=>show(no);tabs.append(b);
    });
    if(Object.keys(presets).length)inventory.append(tabs);
    inventory.append(equipmentGrid,extraHint,extras);
    show(current&&presets[current]?current:null);
  }
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
  // 이 장비를 주제로 채팅을 연다. 부위를 말하지 않아도 이 장비 이야기로 알아듣는다.
  const talk=el('button','primary item-talk','이 장비로 대화');talk.type='button';talk.onclick=()=>startItemChat(item,selectedCharacterName,shownPreset);box.append(talk);
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

// 다른 캐릭터 검색 — 이름으로 프로필을 본다. 관리 목록에는 사용자가 '관리 목록에 추가'를 눌러야 들어간다.
$('#other-search').onsubmit=e=>{e.preventDefault();const name=e.target.elements.name.value.trim();if(!name)return;
  showCharacterProfile(name);$('#character-profile').scrollIntoView({behavior:'smooth',block:'start'});};

// 목표 전투력대 유저 — 같은 직업·비슷한 레벨 유저의 장비 통계와 대표 캐릭터 비교(mepiti/peers.py). 모으는 동안은 가끔 다시 불러온다.
let peerTimer=null;
function renderPeers(d){
  const st=$('#peer-status'),box=$('#peer-report');box.replaceChildren();clearTimeout(peerTimer);
  const t=d.target;
  const parts=[];
  const eok=v=>`${(Math.round(v/1e6)/100).toLocaleString()}억`;
  if(t&&t.cp){parts.push(`${t.job.split('-').pop()} · 전투력 ${eok(t.cp)} ±15% · 모은 유저 ${fmt(d.collected)}명`+(t.family?` (같은 직업 후보 ${fmt(t.pool||0)}명 + ${t.family} 계열 ${fmt(t.family_pool||0)}명에서 찾음)`:'')+(t.screened?` (확인 ${fmt(t.screened)}명 중 해당 ${fmt(t.matched||0)}명)`:'')+(d.queue?` · 남은 후보 ${fmt(d.queue)}명`:''));
    const f=$('#peer-form').elements.cp;if(!f.value)f.value=eok(t.cp);}
  else parts.push('목표 전투력을 적고 \'모으기 시작\'을 누르면 같은 직업 랭킹에서 그 전투력대를 찾아 천천히 모읍니다.');
  if(d.running)parts.push('모으는 중(한 명에 몇 초씩)');
  else if(d.queue&&!d.calls_left)parts.push('오늘 조회 몫을 다 써서 내일 이어서 모읍니다');
  parts.push(`오늘 남은 조회 ${fmt(d.calls_left)}/${fmt(d.daily_calls)}회`);
  if(d.error)parts.push(d.error);
  st.textContent=parts.join(' · ');
  $('#peer-collect').textContent=t&&t.cp?'다시 찾기':'모으기 시작';
  if(d.running&&activeView==='characters')peerTimer=setTimeout(()=>guard(loadPeers),15000);
  if(t&&!d.same_job){box.append(el('p','hint','대표 캐릭터의 직업이 바뀌었어요. \'다시 찾기\'를 누르세요.'));return;}
  if(!d.ready){if(t)box.append(el('p','hint',`${fmt(d.min_peers)}명 이상 모이면 비교를 보여 드려요.`));return;}
  const behind=el('div','peer-behind');behind.append(el('strong','',d.behind.length?`목표 전투력대 유저 ${fmt(d.people)}명보다 뒤처진 부위`:`목표 전투력대 유저 ${fmt(d.people)}명과 비교해 뒤처진 부위가 없어요`));
  const pct=v=>`${v>=0?'+':''}${v.toFixed(2)}%`,span=r=>r[0]===r[1]?pct(r[0]):`${pct(r[0])} ~ ${pct(r[1])}`;
  const simText=s=>(s.same_item?`같은 장비를 목표 전투력대 유저 수준(${s.starforce}성 · ${s.potential.join(', ')||'잠재 없음'})으로 맞추면`:`${s.item} ${s.starforce}성(${s.potential.join(', ')||'잠재 없음'})으로 바꾸면`)+` 스탯공격력 ${span(s.range)} · 보스 기준 ${span(s.boss_range)}`+(s.sets.length?` (세트 ${s.sets.join(', ')})`:'')+(s.unknown.length?` · 모름: ${s.unknown.join('; ')}`:'');
  d.behind.forEach(b=>{const row=el('div','peer-row');const body=el('span','',b.reasons.join(' · '));
    if(b.simulation){body.append(el('br'),el('small','peer-sim',simText(b.simulation)));}
    row.append(el('span','peer-slot',b.slot),body);behind.append(row);});
  box.append(behind);
  if(d.simulated){
    const list=(title,rows,cls)=>{const g=el('div','peer-behind '+cls);g.append(el('strong','',title));
      rows.forEach(s=>{const row=el('div','peer-row');row.append(el('span','peer-slot',s.slot),el('span','',simText(s)));g.append(row);});return g;};
    box.prepend(list(d.upgrades.length?'바꾸면 좋아지는 부위 (보스 기준 이득 큰 순)':'목표 전투력대 유저 장비로 바꿔서 좋아지는 부위는 없어요',d.upgrades,'up'));
    if(d.ahead.length)box.append(list('지금 장비가 더 나은 부위 (바꾸면 손해)',d.ahead,'ahead'));
  }
  const simBar=el('div','peer-simbar');
  if(d.simulated)simBar.append(el('p','hint',d.simulation_note));
  else{const run=el('button','secondary','교체 효과 계산');run.type='button';
    run.onclick=()=>task(run,async()=>{run.textContent='내 스탯 출처 조회 중(넥슨 약 22회)…';renderPeers(await api('peers/simulate',{}));});
    simBar.append(run,el('small','hint',' 내 스탯 출처를 하루 한 번 조회해, 목표 전투력대 유저가 많이 끼는 장비로 바꿀 때 스탯공격력·보스 기준(방어율 300%) 변화를 계산합니다.'));}
  box.append(simBar);
  const more=el('details','peer-more');more.append(el('summary','','부위별 통계 전체'));
  const table=el('table','peer-table');const head=el('tr');['부위','많이 끼는 장비','스타포스 중앙값','윗잠','아랫잠','내 장비','바꾸면(보스 기준)'].forEach(h=>head.append(el('th','',h)));table.append(head);
  const shares=o=>Object.entries(o).reverse().map(([g,v])=>`${g} ${v}%`).join(', ')||'—';
  d.slots.forEach(s=>{const tr=el('tr',s.behind.length?'behind':'');const me=s.mine||{};
    tr.append(el('td','',s.slot),el('td','',s.items.map(i=>`${i.name} ${i.share}%`).join(', ')),el('td','num',s.starforce_median==null?'—':`${s.starforce_median}성`),
      el('td','',shares(s.potential)),el('td','',shares(s.additional)),el('td','',me.name?`${me.name} ${me.starforce||0}성 · ${me.potential||'—'}/${me.additional||'—'}`:'—'),
      el('td','num',s.simulation?span(s.simulation.boss_range):'—'));table.append(tr);});
  more.append(table,el('p','hint','넥슨 Open API 랭킹(전날 기준)에서 같은 직업·비슷한 순위의 유저를 골라 모은 통계입니다. 채팅에서 "목표 전투력대 유저랑 비교해서 뭐부터 바꿀까?"처럼 물으면 이 통계로 상담합니다.'));
  box.append(more);
}
async function loadPeers(){if(activeView!=='characters')return;const d=await api('peers');if(activeView==='characters')renderPeers(d);}
$('#peer-form').onsubmit=e=>{e.preventDefault();const b=$('#peer-collect');task(b,async()=>{b.textContent='전투력대 찾는 중(넥슨 30회 안팎, 1~2분)…';
  renderPeers(await api('peers/collect',{cp:e.target.elements.cp.value}));});};
