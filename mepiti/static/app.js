'use strict';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let token = '', sessionId = null, busy = false, confirmedText = '', previewUrl = null, downloadTimer;
let accountCatalog = null, accountLoading = false, managedNames = new Set(), managedCharacters = [];
const titles = {chat:'질의',characters:'캐릭터',calculator:'수익',library:'기록',settings:'설정'};
const fmt = (n) => new Intl.NumberFormat('ko-KR',{maximumFractionDigits:3}).format(n);
const el = (tag,cls,text) => { const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e; };
function toast(message,error=false){const e=$('#toast');e.textContent=message;e.classList.toggle('error',error);e.hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>e.hidden=true,6500);}
async function api(path,data){const response=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{'X-Mepiti-Token':token,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw new Error(result.error||'요청에 실패했습니다.');return result;}
async function guard(fn){try{return await fn();}catch(e){toast(e.message,true);}}
async function task(button,fn){button.disabled=true;try{return await guard(fn);}finally{button.disabled=false;}}
function formData(form){return Object.fromEntries(new FormData(form));}
function sourceLink(url,text){const a=el('a','',text);try{const parsed=new URL(url);if(parsed.protocol==='https:'){a.href=url;a.target='_blank';a.rel='noreferrer noopener';}}catch{}return a;}
function switchView(view){if(!titles[view])view='chat';$$('.view').forEach(e=>e.hidden=e.id!=='view-'+view);$$('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));$('#page-title').textContent=titles[view];if(view==='characters')guard(loadCharacters);if(view==='calculator')guard(loadEarnings);if(view==='library')guard(loadForgeHistory);if(view==='settings'){guard(loadStatus);guard(loadPrices);}location.hash=view;}
$$('[data-view]').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.view)));
window.addEventListener('hashchange',()=>switchView(location.hash.slice(1)));
function scrollBottom(){$('#chat-scroll').scrollTop=$('#chat-scroll').scrollHeight;}
function renderMessage(role,payload){$('#welcome').hidden=true;$$('#messages .choice-form').forEach(lockChoiceForm);const block=el('article','message '+role);if(role==='user'){block.textContent=payload.content;}else{const heading=el('div','message-heading');const icon=el('img');icon.src='/favicon.svg';icon.alt='';heading.append(icon,el('span','','메피티'));const status={held:'보류',clarify:'조건 확인',evidence:'근거 원문',term:'용어',context:'조회한 사실',analysis:'캐릭터 분석',ask_price:'노작값 필요',price:'값 저장됨',ask_conditions:'조건 선택',conditions:'조건 저장됨'};heading.append(el('span','badge',status[payload.status]||'안내'));block.append(heading,el('div','message-body',payload.content));if(payload.form)block.append(renderChoiceForm(payload.form));if(payload.sources?.length){const sources=el('div','sources');payload.sources.forEach(s=>{const a=sourceLink(s.source_url,`[${s.citation}] ${s.title}`);a.className='source';a.append(el('small','',`${s.source_type==='official'?'공식':'커뮤니티'} · 버전 ${s.version}\n적용 ${s.effective_from} · 수집 ${s.retrieved_at}\n재검토 기한 ${s.valid_until}`));sources.append(a);});block.append(sources);}if(payload.facts){const facts=el('details','fact-sheet');facts.append(el('summary','','근거로 쓴 조회 사실'),el('pre','',payload.facts));block.append(facts);}if(payload.conditions?.length){const conditions=el('div','conditions');payload.conditions.forEach(c=>conditions.append(el('p','',c)));block.append(conditions);}}$('#messages').append(block);scrollBottom();}
async function loadHistory(){const sessions=await api('sessions');const list=$('#history');list.replaceChildren();if(!sessions.length)list.append(el('div','history-empty','기록 없음'));sessions.forEach(s=>{const row=el('div','history-entry'+(s.id===sessionId?' active':''));const open=el('button','',s.title);open.title=s.title;open.addEventListener('click',()=>guard(async()=>{if(busy)return;sessionId=s.id;$('#messages').replaceChildren();const messages=await api('messages?session_id='+s.id);const latest=[...messages].reverse().find(m=>m.payload?.topic_item)?.payload.topic_item;chatTopic=s.topic?{...s.topic,...(latest||{})}:null;renderTopicCard();messages.forEach(m=>renderMessage(m.role,m.payload));switchView('chat');loadHistory();}));const del=el('button','','×');del.setAttribute('aria-label',s.title+' 대화 삭제');del.addEventListener('click',()=>guard(async()=>{if(busy)return;if(!confirm('이 기록을 삭제합니다.'))return;await api('sessions/delete',{id:s.id});if(sessionId===s.id)newChat();await loadHistory();}));row.append(open,del);list.append(row);});}
function newChat(){if(busy)return;sessionId=null;chatTopic=null;renderTopicCard();$('#messages').replaceChildren();$('#welcome').hidden=false;confirmedText='';renderAttachment();switchView('chat');guard(loadHistory);$('#message').focus();}
$('#new-chat').addEventListener('click',newChat);
document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='k'){e.preventDefault();newChat();}});
$$('[data-prompt]').forEach(b=>b.addEventListener('click',()=>{$('#message').value=b.dataset.prompt;$('#message').focus();}));
$('#message').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#chat-form').requestSubmit();}});
$('#chat-form').addEventListener('submit',e=>{e.preventDefault();guard(async()=>{if(busy)return;let message=$('#message').value.trim();if(!message&&!confirmedText)return;if(!confirmedText&&/^(내|제)\s*캐릭터(?:\s*(보여줘|보여주세요|보기|볼래|보고 싶어|보여 줘|보여 주세요))?[.!?]*$/.test(message)){$('#message').value='';switchView('characters');return;}if(confirmedText)message+='\n\n[사용자가 확인한 스크린샷 내용]\n'+confirmedText;if(message.length>12000)throw new Error('질문과 첨부를 합쳐 12,000자 이하.');const original=$('#message').value;$('#message').value='';try{await sendChat(message);confirmedText='';renderAttachment();}catch(err){$('#message').value=original;throw err;}});});
// 질문을 보내고 답을 그린다. answer는 선택창에서 고른 값(구조화된 답)이다.
async function sendChat(message,answer){
  busy=true;$('#send-button').disabled=true;
  renderMessage('user',{content:message});
  const thinking=el('div','thinking','근거 확인 중…');$('#messages').append(thinking);scrollBottom();
  try{
    const topic=!sessionId&&chatTopic?{topic:{slot:chatTopic.slot,name:chatTopic.name,...(chatTopic.character?{character:chatTopic.character}:{}),...(chatTopic.preset?{preset:chatTopic.preset}:{})}}:{};
    const result=await api('chat',{message,session_id:sessionId,...(answer?{answer}:{}),...topic});
    sessionId=result.session_id;thinking.remove();
    if(chatTopic&&result.topic_item){chatTopic={...chatTopic,...result.topic_item};renderTopicCard();}
    renderMessage('assistant',result);await loadHistory();
  }catch(err){thinking.remove();$('#messages').lastElementChild?.remove();$('#welcome').hidden=$('#messages').children.length>0;throw err;}
  finally{busy=false;$('#send-button').disabled=false;}
}
// 장비를 주제로 한 대화. 새 대화를 시작할 때 서버에 넘기고, 그다음부터는 서버가 대화에 묶어 기억한다.
let chatTopic=null;
function itemSummary(item){return {slot:item.slot,name:item.name,icon:item.icon,starforce:item.starforce,scroll_upgrade:item.scroll_upgrade,
  equip_level:item.equip_level,add_label:item.add_grade?.label||null,potential_grade:item.potential_grade,additional_grade:item.additional_grade};}
function topicLine(t){
  const parts=[];if(t.starforce!=null)parts.push('★'+t.starforce);if(t.scroll_upgrade)parts.push('주문서 +'+t.scroll_upgrade);
  if(t.add_label)parts.push('추옵 '+t.add_label);if(t.potential_grade)parts.push('잠재 '+t.potential_grade);if(t.additional_grade)parts.push('에디 '+t.additional_grade);
  return parts.join(' · ');
}
function topicPrompts(t){
  const list=[],s=t.starforce;
  if(s!=null&&t.equip_level){if(s<30)list.push([`${s+1}성 기대값`,`${s+1}성 기대값 얼마야`]);if(s<21)list.push(['22성 기대값','22성 기대값 얼마야']);}
  list.push(['지금 상태 어때?','이 장비 지금 상태 어때?'],['추옵·잠재 평가','추옵이랑 잠재 평가해줘'],['바꾸는 게 나아?','이 장비 바꾸는 게 나아? 노작값 기준으로']);
  return list;
}
function renderTopicCard(){
  const box=$('#topic-card');box.replaceChildren();box.hidden=!chatTopic;
  $('#message').placeholder=chatTopic?`${chatTopic.slot} 이야기로 질문 (부위를 말하지 않아도 됩니다)`:'질문 입력';
  if(!chatTopic)return;
  $('#welcome').hidden=true;
  const head=el('div','topic-head');head.append(nexonImage(chatTopic.icon,chatTopic.name,'topic-icon'));
  const names=el('div','topic-names');names.append(el('span','topic-slot',`${chatTopic.slot} · 대화 주제`+(chatTopic.character?` · ${chatTopic.character}`:'')+(chatTopic.preset?` · 프리셋 ${chatTopic.preset}`:'')),el('strong','',chatTopic.name));
  const line=topicLine(chatTopic);if(line)names.append(el('span','topic-line',line));
  const close=el('button','topic-close','×');close.type='button';close.setAttribute('aria-label','장비 주제 없이 새 대화');close.onclick=newChat;
  head.append(names,close);box.append(head);
  const chips=el('div','topic-prompts');
  topicPrompts(chatTopic).forEach(([label,question])=>{const b=el('button','choice',label);b.type='button';b.onclick=()=>{if(!busy)guard(()=>sendChat(question));};chips.append(b);});
  box.append(chips);
}
function startItemChat(item,character,preset){newChat();chatTopic={...itemSummary(item),...(character?{character}:{}),...(preset?{preset:String(preset)}:{})};renderTopicCard();switchView('chat');$('#message').focus();}
// 착용 장비를 골라 대화 주제로 삼는다. 캐릭터 화면에서 보고 있는 캐릭터, 없으면 대표 캐릭터 기준.
async function openItemPicker(){
  const dialog=$('#item-dialog'),list=$('#item-picker'),status=$('#item-dialog-status');
  list.replaceChildren();status.textContent='착용 장비를 불러오는 중';dialog.showModal();
  try{
    if(!managedCharacters.length)managedCharacters=await api('characters');
    const name=selectedCharacterName||managedCharacters.find(c=>c.main)?.name||managedCharacters[0]?.name;
    if(!name){status.textContent='캐릭터 화면에서 캐릭터를 먼저 등록하세요.';return;}
    const cached=profileCache.get(name);
    const data=cached&&Date.now()-cached.at<5*60*1000?cached.data:await api('characters/profile',{name});
    profileCache.set(name,{data,at:Date.now()});
    const order=EQUIPMENT_LAYOUT.flat().filter(Boolean);const rank=slot=>{const i=order.indexOf(slot);return i<0?99:i;};
    const presets=data.equipment_presets||{},current=data.equipment_preset!=null?String(data.equipment_preset):null;
    const draw=no=>{
      list.replaceChildren();const rows=(no&&presets[no]?presets[no]:data.equipment||[]).filter(i=>i.slot&&i.name).sort((a,b)=>rank(a.slot)-rank(b.slot));
      status.textContent=`${name} · ${no?'프리셋 '+no:'착용 장비'} ${rows.length}개. 고르면 그 장비를 주제로 새 대화를 엽니다.`;
      tabs.querySelectorAll('button').forEach(b=>{const on=b.dataset.preset===String(no);b.classList.toggle('selected',on);b.setAttribute('aria-pressed',String(on));});
      rows.forEach(item=>{
        const b=el('button','picker-item');b.type='button';b.append(nexonImage(item.icon,item.name,'picker-icon'));
        const text=el('span','picker-text');text.append(el('small','',item.slot+(item.starforce?` · ★${item.starforce}`:'')),el('strong','',item.name));
        b.append(text);b.onclick=()=>{dialog.close();startItemChat(item,name,no);};list.append(b);
      });
    };
    const tabs=$('#item-presets');tabs.replaceChildren();
    ['1','2','3'].forEach(no=>{const b=el('button','choice',`프리셋 ${no}`+(no===current?' · 적용 중':''));b.type='button';b.dataset.preset=no;b.disabled=!presets[no];b.onclick=()=>draw(no);tabs.append(b);});
    tabs.hidden=!Object.keys(presets).length;
    draw(current&&presets[current]?current:null);
  }catch(e){status.textContent='장비를 불러오지 못했습니다: '+e.message;}
}
$('#item-button').onclick=()=>guard(openItemPicker);
$('#welcome-pick-item').onclick=()=>guard(openItemPicker);
// 되묻기 선택창 — 조건은 버튼으로 고르고, 노작값은 칸에 적는다. 직접 입력창에 적어도 된다.
function renderChoiceForm(form){
  const box=el('form','choice-form');
  if(form.kind==='conditions'){
    form.questions.forEach(q=>{
      const group=el('fieldset','choice-group');group.dataset.key=q.key;group.dataset.type=q.type;
      group.append(el('legend','',q.label));
      const row=el('div','choice-options');
      q.options.forEach(o=>{
        const b=el('button','choice',o.label);b.type='button';b.dataset.value=JSON.stringify(o.value);
        const set=on=>{b.classList.toggle('selected',on);b.setAttribute('aria-pressed',String(on));};
        set(!!o.selected);
        b.onclick=()=>{
          if(q.type==='single'){row.querySelectorAll('.choice').forEach(x=>{x.classList.remove('selected');x.setAttribute('aria-pressed','false');});set(true);}
          else set(!b.classList.contains('selected'));
        };
        row.append(b);
      });
      group.append(row);box.append(group);
    });
  }else{
    form.fields.forEach(f=>{
      const label=el('label','choice-field');const input=el('input');
      input.name=f.item;input.placeholder=f.placeholder||'';input.autocomplete='off';input.inputMode='text';
      label.append(el('span','',f.item),input);box.append(label);
    });
  }
  const actions=el('div','choice-actions');
  const submit=el('button','primary choice-submit',form.submit||'확인');submit.type='submit';
  actions.append(submit);
  if(form.kind==='price'&&form.skippable){
    // 시세 비교용 질문이면 값을 몰라도 넘어갈 수 있다.
    const skip=el('button','secondary choice-skip','모르는 값은 건너뛰기');skip.type='button';
    skip.onclick=()=>{if(busy||box.classList.contains('answered'))return;guard(async()=>{
      lockChoiceForm(box);
      try{await sendChat('노작값 없이 진행',{kind:'price',values:{},skip:true});}
      catch(err){unlockChoiceForm(box);throw err;}
    });};
    actions.append(skip);
  }
  box.append(actions);
  box.onsubmit=e=>{e.preventDefault();if(busy||box.classList.contains('answered'))return;guard(async()=>{
    const {values,summary}=collectChoices(box,form);
    lockChoiceForm(box);
    try{await sendChat(summary,{kind:form.kind,values});}
    catch(err){unlockChoiceForm(box);throw err;}
  });};
  return box;
}
function collectChoices(box,form){
  if(form.kind==='price'){
    const values={},parts=[];
    box.querySelectorAll('input').forEach(i=>{const v=i.value.trim();if(v){values[i.name]=v;parts.push(i.name+' '+v);}});
    if(!parts.length)throw new Error('노작값을 입력하세요.');
    return {values,summary:parts.join('\n')};
  }
  const values={},parts=[];
  box.querySelectorAll('.choice-group').forEach(g=>{
    const picked=[...g.querySelectorAll('.choice.selected')];
    const label=picked.map(b=>b.textContent);
    if(g.dataset.type==='multi'){values[g.dataset.key]=picked.map(b=>JSON.parse(b.dataset.value));parts.push('할인 '+(label.join(', ')||'없음'));}
    else{
      values[g.dataset.key]=picked.length?JSON.parse(picked[0].dataset.value):null;
      const name={safeguard:'파괴방지 ',use_restore:'흔적 복구 '}[g.dataset.key]||'';
      parts.push(name+(label[0]||''));
    }
  });
  return {values,summary:parts.join(' · ')};
}
function lockChoiceForm(box){box.classList.add('answered');box.querySelectorAll('button,input').forEach(x=>x.disabled=true);}
function unlockChoiceForm(box){box.classList.remove('answered');box.querySelectorAll('button,input').forEach(x=>x.disabled=false);}
function renderAttachment(){const box=$('#attachment');box.replaceChildren();box.hidden=!confirmedText;if(confirmedText){box.append(el('span','',`OCR 텍스트 ${confirmedText.length}자 첨부`));const remove=el('button','','×');remove.setAttribute('aria-label','첨부 취소');remove.onclick=()=>{confirmedText='';renderAttachment();};box.append(remove);}}
$('#attach-button').onclick=()=>$('#image-file').click();
$('#image-file').addEventListener('change',()=>guard(async()=>{const file=$('#image-file').files[0];if(!file)return;$('#image-file').value='';if(file.size>6000000)throw new Error('이미지는 6MB 이하여야 합니다.');$('#attach-button').disabled=true;toast('OCR 처리 중');try{const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});const result=await api('ocr',{image:data});if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=URL.createObjectURL(file);$('#ocr-preview').src=previewUrl;$('#ocr-text').value=result.text;$('#ocr-dialog').showModal();}finally{$('#attach-button').disabled=false;}}));
$('#confirm-ocr').onclick=()=>{confirmedText=$('#ocr-text').value.trim();if(!confirmedText){toast('인식 결과가 비어 있습니다.',true);return;}$('#ocr-dialog').close();renderAttachment();};
$('#ocr-dialog').addEventListener('close',()=>{if(previewUrl){URL.revokeObjectURL(previewUrl);previewUrl=null;}$('#ocr-preview').removeAttribute('src');});
async function loadCharacters(){const chars=await api('characters');managedCharacters=chars;managedNames=new Set(chars.map(c=>c.name));renderAccountCharacters();const list=$('#character-list');list.replaceChildren();if(!chars.length)list.append(el('div','empty-state','등록한 캐릭터 없음'));chars.forEach(c=>{const card=el('article','panel character-card');const avatar=el('div','char-avatar','♧');const image= c.snapshots[0]?.data.image;if(image){try{const u=new URL(image);if(u.protocol==='https:'&&u.hostname==='open.api.nexon.com'&&u.pathname.startsWith('/static/maplestory/character/')){const img=el('img','character-image');img.src=image;img.alt=c.name+' 캐릭터 외형';img.loading='lazy';img.onerror=()=>img.replaceWith(el('span','','♧'));avatar.replaceChildren(img);}}catch{}}card.append(avatar);const title=el('h2','',c.name);if(c.main)title.append(el('span','badge','대표 캐릭터'));card.append(title,el('p','muted','목표 · '+(c.goal||'미설정')),el('p','','예산 · '+fmt(c.budget)+' 메소'));if(c.snapshots.length){const snap=c.snapshots[0],s=snap.data;card.append(el('p','muted',`${s.world||''} · ${s.job||''}`));const stats=el('div','stat-grid');[['레벨',s.level,'level'],['전투력',s.combat_power,'combat_power']].forEach(([label,value,key])=>{const box=el('div');box.append(el('small','',label),el('strong','',value==null?'미조회':fmt(value)));if(c.changes[key]!=null)box.append(el('small','',`직전 대비 ${c.changes[key]>=0?'+':''}${fmt(c.changes[key])}`));stats.append(box);});card.append(stats,el('p','hint','조회 '+snap.retrieved_at+' · API 기준일 '+(s.api_date||'미제공')));if(s.warning)card.append(el('p','hint',s.warning));if(c.snapshots.length<2)card.append(el('p','hint','다음 조회부터 변화를 비교합니다.'));}else card.append(el('p','hint','조회 기록 없음'));const refresh=el('button','primary','조회 · 스냅샷');refresh.onclick=()=>task(refresh,async()=>{setCharacterFeedback('조회 중');try{await api('characters/refresh',{id:c.id});setCharacterFeedback('스냅샷을 저장했습니다.');await loadCharacters();}catch(e){setCharacterFeedback('조회 실패: '+e.message);throw e;}});const edit=el('button','secondary','수정');edit.onclick=()=>{const form=$('#character-form');['id','name','goal','budget'].forEach(k=>form.elements[k].value=c[k]);form.elements.main.checked=!!c.main;$('#character-form-title').textContent='캐릭터 수정';$('.character-management').open=true;form.scrollIntoView({behavior:'smooth',block:'center'});};const del=el('button','secondary','삭제');del.onclick=()=>guard(async()=>{if(!confirm('캐릭터와 스냅샷을 삭제합니다.'))return;await api('characters/delete',{id:c.id});await loadCharacters();});const view=el('button','primary','캐릭터 보기');view.onclick=()=>showCharacterProfile(c.name);card.append(view,refresh,edit,del);list.append(card);});if(accountCatalog===null&&!accountLoading)await discoverCharacters();else if(accountCatalog)autoSelectCharacter();}
$('#character-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{const form=e.target;const saved=await api('characters',{...formData(form),main:form.elements.main.checked});form.reset();await refreshSavedCharacter(saved.id);});};
$('#character-form').onreset=()=>{$('#character-form-title').textContent='캐릭터 등록';setTimeout(()=>$('#character-form').elements.id.value='',0);};
// 수익 — 재획(메소 + 조각 x 조각 가격)과 주간 보스(결정석 / 파티 인원 + 추가 드롭).
function mesoText(v){return v==null?'—':amountText(Math.round(v))+' 메소';}
function readAmount(text){const v=String(text||'').trim();if(!v)return 0;if(/^0+(\.0+)?$/.test(v))return 0;return parsePrice(v);}
function huntPreview(){
  const f=$('#hunt-form').elements;const m=readAmount(f.meso.value),price=readAmount(f.piece_price.value),n=Number(f.pieces.value||0);
  const box=$('#hunt-preview');
  if(m==null||price==null){box.textContent="금액은 '12억 3500만'처럼 적어 주세요.";return;}
  if(!m&&!n){box.textContent='';return;}
  box.textContent=`이번 재획 총수익 ${mesoText(m+n*price)}`+(n?` (메소 ${mesoText(m)} + 조각 ${fmt(n)}개 × ${mesoText(price)})`:'');
}
function todayText(){const d=new Date();return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10);}
function earningsRow(r,label,detail){
  const row=el('div','earnings-row');const left=el('div');
  left.append(el('strong','',label),el('small','',`${r.day} · ${detail}`+(r.note?` · ${r.note}`:'')));
  const right=el('div','earnings-amount',mesoText(r.total));
  const del=el('button','','×');del.setAttribute('aria-label',`${r.day} ${label} 기록 삭제`);
  del.onclick=()=>guard(async()=>{if(!confirm('이 기록을 삭제합니다.'))return;await api('earnings/delete',{id:r.id});await loadEarnings();});
  row.append(left,right,del);return row;
}
async function loadEarnings(){
  const d=await api('earnings');const s=d.summary;bossPrices=d.boss_prices||{};renderBossChecklist(d.crystals||[]);
  ['hunt-form','boss-form'].forEach(id=>{const f=$('#'+id).elements;if(!f.day.value)f.day.value=todayText();});
  if(d.piece_price&&!$('#hunt-form').elements.piece_price.value)$('#hunt-form').elements.piece_price.value=amountText(d.piece_price);
  const box=$('#earnings-summary');box.replaceChildren();
  [['이번 주',s.all.week,`목요일(${s.week_start}) 기준 · 재획 ${fmt(s.hunt.week.count)}회 · 주보 ${fmt(s.boss.week.count)}건`],
   ['이번 달',s.all.month,`재획 ${mesoText(s.hunt.month.total)} · 주보 ${mesoText(s.boss.month.total)}`],
   ['전체',s.all.all,`조각 ${fmt(s.hunt.pieces)}개 · 재획 평균 ${mesoText(s.hunt.average)}`+(s.hunt.per_flask?` · 재획비 1개당 ${mesoText(s.hunt.per_flask)}`:'')]]
   .forEach(([label,value,sub])=>{const c=el('div','earnings-stat');c.append(el('span','',label),el('strong','',mesoText(value)),el('small','',sub));box.append(c);});
  const hunts=$('#hunt-list');hunts.replaceChildren();
  if(!d.hunts.length)hunts.append(el('p','hint','아직 기록이 없습니다.'));
  d.hunts.forEach(r=>hunts.append(earningsRow(r,'재획'+(r.flasks?` · 재획비 ${fmt(r.flasks)}개`:''),
    `메소 ${mesoText(r.meso)}`+(r.pieces?` · 조각 ${fmt(r.pieces)}개 × ${mesoText(r.piece_price)}`:''))));
  const weeks=$('#boss-weeks');weeks.replaceChildren();
  d.boss_weeks.slice(0,6).forEach(w=>{const c=el('div','earnings-week');c.append(el('span','',`${w.week_start} 주`),el('strong','',mesoText(w.total)),el('small','',`${fmt(w.count)}건`));weeks.append(c);});
  const bosses=$('#boss-list');bosses.replaceChildren();
  if(!d.bosses.length)bosses.append(el('p','hint','아직 기록이 없습니다.'));
  d.bosses.forEach(r=>bosses.append(earningsRow(r,r.boss,
    `결정석 ${mesoText(r.crystal)}`+(r.party>1?` ÷ ${r.party}명`:'')+(r.extra?` · 드롭 ${mesoText(r.extra)}`:''))));
}
function earningsForm(id,kind,after){
  $('#'+id).onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{
    const data=formData(e.target);await api('earnings',{...data,kind});
    const keep={day:data.day,piece_price:data.piece_price,party:data.party};
    e.target.reset();Object.entries(keep).forEach(([k,v])=>{if(e.target.elements[k]&&v)e.target.elements[k].value=v;});
    after();await loadEarnings();toast('기록을 저장했습니다.');});};
}
earningsForm('hunt-form','hunt',huntPreview);
// 보스별로 기억한 결정석 가격을 직접 입력 칸에도 채운다.
let bossPrices={};
// 주보 체크리스트 — 보스 이름별 한 줄, 난이도마다 체크박스. 같은 보스는 한 난이도만 고른다.
function renderBossChecklist(crystals){
  const box=$('#boss-checklist');if(box.childElementCount)return;
  const groups=new Map();crystals.forEach(c=>{if(!groups.has(c.name))groups.set(c.name,[]);groups.get(c.name).push(c);});
  [...groups.entries()].sort((a,b)=>Math.max(...b[1].map(c=>c.price))-Math.max(...a[1].map(c=>c.price))).forEach(([name,list])=>{
    const row=el('div','boss-row');row.append(el('span','boss-name',name));
    const options=el('div','boss-options');
    list.sort((a,b)=>a.price-b.price).forEach(c=>{
      const label=el('label','boss-check');const input=el('input');input.type='checkbox';input.value=c.label;input.dataset.price=c.price;
      input.onchange=()=>{if(input.checked)options.querySelectorAll('input').forEach(x=>{if(x!==input)x.checked=false;});
        row.classList.toggle('picked',!!options.querySelector('input:checked'));bossPreview();};
      label.append(input,el('span','',c.difficulty),el('small','',amountText(c.price)));options.append(label);
    });
    const party=el('select','boss-party');party.setAttribute('aria-label',name+' 파티 인원');
    [1,2,3,4,5,6].forEach(n=>{const o=el('option','',n===1?'솔로':`${n}인`);o.value=n;party.append(o);});party.onchange=bossPreview;
    row.append(options,party);box.append(row);
  });
}
function checkedBosses(){
  return [...$('#boss-checklist').querySelectorAll('input:checked')].map(i=>({boss:i.value,price:Number(i.dataset.price),
    party:Number(i.closest('.boss-row').querySelector('.boss-party').value)}));
}
function bossPreview(){
  const f=$('#boss-form').elements,box=$('#boss-preview');
  const picked=checkedBosses();const x=readAmount(f.extra.value),c=readAmount(f.crystal.value),party=Math.max(1,Number(f.party.value||1));
  if(x==null||c==null){box.textContent="금액은 '3억 8000만'처럼 적어 주세요.";return;}
  const custom=f.boss.value.trim()&&c?c/party:0;
  const sum=picked.reduce((s,b)=>s+b.price/b.party,0)+custom+x;
  const count=picked.length+(custom?1:0);
  box.textContent=count||x?`${count}개 보스 · 내 몫 합계 ${mesoText(sum)}`:'';
}
['crystal','party','extra','boss'].forEach(k=>$('#boss-form').elements[k].addEventListener('input',bossPreview));
$('#boss-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{
  const f=e.target.elements;const day=f.day.value,note=f.note.value,extra=f.extra.value.trim();
  const entries=checkedBosses().map(b=>({boss:b.boss,party:b.party}));
  if(f.boss.value.trim())entries.push({boss:f.boss.value.trim(),crystal:f.crystal.value,party:f.party.value});
  if(!entries.length&&!extra)throw new Error('잡은 보스를 체크하거나 직접 적어 주세요.');
  if(!entries.length)entries.push({boss:'추가 드롭'});
  if(extra)entries[0].extra=extra;
  let saved=0;const errors=[];
  for(const entry of entries){try{await api('earnings',{kind:'boss',day,note,...entry});saved++;}catch(err){errors.push(`${entry.boss}: ${err.message}`);}}
  $('#boss-checklist').querySelectorAll('input:checked').forEach(i=>{i.checked=false;i.closest('.boss-row').classList.remove('picked');});
  ['boss','crystal','extra','note'].forEach(k=>f[k].value='');f.party.value=1;bossPreview();
  await loadEarnings();
  if(errors.length)toast(`${saved}건 저장, ${errors.length}건 실패 — ${errors[0]}`,true);else toast(`${saved}건 저장했습니다.`);
});};
// 스케줄러(넥슨 Open API)에서 이번 주에 잡은 보스를 불러온다. 결정석 가격·파티 인원만 적으면 된다.
$('#boss-import-button').onclick=e=>task(e.currentTarget,async()=>{
  const box=$('#boss-import');box.hidden=false;box.replaceChildren(el('p','hint','캐릭터별 스케줄러 조회 중'));
  const d=await api('earnings/scheduler',{});box.replaceChildren();
  const chips=el('div','boss-import-chars');
  d.characters.forEach(c=>chips.append(el('span','badge',c.error?`${c.name} · 조회 실패`:`${c.name} · 주보 ${c.weekly_clear??'—'}/${c.weekly_limit??'—'}`)));
  box.append(chips);
  const failed=d.characters.filter(c=>c.error);if(failed.length)box.append(el('p','hint',failed.map(c=>`${c.name}: ${c.error}`).join(' / ')));
  if(!d.bosses.length){box.append(el('p','hint',`${d.week_start} 주에 완료한 보스가 없습니다. 스케줄러는 보스를 잡은 뒤에 갱신됩니다.`));return;}
  const list=el('div','boss-import-list');
  d.bosses.forEach(b=>{
    const row=el('div','boss-import-row'+(b.recorded?' recorded':''));
    const check=el('input');check.type='checkbox';check.checked=!b.recorded;check.disabled=b.recorded;check.setAttribute('aria-label',`${b.character} ${b.boss} 저장`);
    const name=el('div');name.append(el('strong','',b.boss),el('small','',b.character+(b.cycle?` · ${b.cycle}`:'')+(b.price_source==='official'?' · 공식 가격':b.price_source==='remembered'?' · 지난 입력 가격':'')+(b.recorded?' · 이미 기록함':'')));
    const price=el('input');price.placeholder='결정석 판매가';price.autocomplete='off';price.setAttribute('aria-label',`${b.boss} 결정석 판매가`);if(b.price)price.value=amountText(b.price);price.disabled=b.recorded;
    const party=el('input');party.type='number';party.min=1;party.max=6;party.value=1;party.setAttribute('aria-label',`${b.boss} 파티 인원`);party.disabled=b.recorded;
    row.append(check,name,price,party);row._boss=b;list.append(row);
  });
  const head=el('div','boss-import-row boss-import-head');head.append(el('span',''),el('span','','보스'),el('span','','결정석 판매가'),el('span','','인원'));
  const save=el('button','primary','선택한 보스 저장');save.type='button';
  save.onclick=()=>task(save,async()=>{
    const rows=[...list.children].filter(r=>r.querySelector('input[type=checkbox]').checked);
    if(!rows.length)throw new Error('저장할 보스를 고르세요.');
    let saved=0;const errors=[];
    for(const r of rows){const [,price,party]=r.querySelectorAll('input');const b=r._boss;
      try{await api('earnings',{kind:'boss',boss:b.boss,crystal:price.value,party:party.value,character:b.character,source_key:b.key,day:todayText()});saved++;
        r.classList.add('recorded');r.querySelectorAll('input').forEach(i=>i.disabled=true);r.querySelector('input[type=checkbox]').checked=false;}
      catch(err){errors.push(`${b.character} ${b.boss}: ${err.message}`);}}
    await loadEarnings();
    if(errors.length)toast(`${saved}건 저장, ${errors.length}건 실패 — ${errors[0]}`,true);else toast(`${saved}건 저장했습니다.`);
  });
  box.append(head,list,save);
});

// 기록 — 스타포스 강화 기록(넥슨 Open API)을 장비마다 묶어 실제 비용과 기대값을 비교한다.
async function loadForgeHistory(){
  const d=await api('history/starforce');
  $('#history-status').textContent=d.fetched_days?`받아 둔 날짜 ${fmt(d.fetched_days)}일 · 최근 ${d.latest_day} · 강화 조건(MVP 할인) ${d.conditions}`:'아직 불러온 기록이 없습니다.';
  const list=$('#history-list');list.replaceChildren();
  if(!d.groups.length)list.append(el('div','empty-state','강화 기록이 없습니다.\n기간을 고르고 기록 불러오기를 누르세요.'));
  d.groups.forEach(g=>list.append(historyCard(g)));
  const notes=$('#history-notes');notes.replaceChildren();d.notes.forEach(n=>notes.append(el('p','',n)));
}
function historyCard(g){
  const card=el('article','panel history-card');
  const head=el('div','history-head');const names=el('div');
  names.append(el('strong','',g.item),el('small','',`${g.character||'캐릭터 미확인'}${g.level?` · ${g.level}레벨`:''} · ${g.first}${g.last!==g.first?' ~ '+g.last:''}${g.event&&g.event!=='없음'?' · '+g.event:''}`));
  const stars=el('div','history-stars',`★${g.start} → ★${g.end}`+(g.reached>g.end?` (최고 ★${g.reached})`:''));
  head.append(names,stars);card.append(head);
  const stats=el('div','history-stats');
  const stat=(label,value,sub,cls)=>{const b=el('div',cls||'');b.append(el('span','',label),el('strong','',value));if(sub)b.append(el('small','',sub));stats.append(b);};
  const exp=g.expected;
  const a=g.actual;
  if(exp&&a){
    // 기대값과는 최고 성을 처음 찍을 때까지만 비교한다.
    stat(`★${g.start}→★${g.reached} 시도`,`${fmt(a.to_reach_attempts)}회`,`기대 ${fmt(exp.attempts)}회`);
    stat('그동안 파괴',`${fmt(a.to_reach_destroys)}회`,`기대 ${fmt(exp.destroys)}회`);
    stat(`★${g.reached} 달성까지 쓴 돈`,mesoText(a.to_reach),'강화 비용 + 파괴 × 노작값');
    stat('기대값',mesoText(exp.cost),`★${g.start} → ★${g.reached} 평균`);
    const more=g.difference>0;
    stat(more?'기대보다 더 씀':'기대보다 덜 씀',mesoText(Math.abs(g.difference)),g.ratio?`기대값의 ${fmt(Math.round(g.ratio*100))}%`:'',more?'history-bad':'history-good');
  }else{
    stat('시도',`${fmt(g.attempts)}회`,`성공 ${fmt(g.success)} · 실패 ${fmt(g.fail)}`);
    stat('파괴',`${fmt(g.destroy)}회`,g.safeguard?`파괴방지 ${fmt(g.safeguard)}회`:'');
  }
  if(a)stat('전체 쓴 돈',mesoText(a.total),`강화 ${mesoText(a.attempts_cost)}`+(g.destroy?` + 파괴 ${mesoText(a.destroy_cost)}`:''));
  card.append(stats);
  if(a&&a.after_attempts)card.append(el('p','history-after',
    `★${g.reached} 달성 이후 추가 도전 ${fmt(a.after_attempts)}회 · 파괴 ${fmt(a.after_destroys)}회 · ${mesoText(a.after)} (기대값 비교에서 제외)`));
  if(!g.level||(g.destroy&&g.spare_price==null)){
    const fix=el('form','history-fix');
    if(!g.level){const l=el('label','','장비 레벨');const i=el('input');i.name='level';i.type='number';i.min=1;i.max=300;i.placeholder='예: 250';l.append(i);fix.append(l);}
    if(g.destroy&&g.spare_price==null){const l=el('label','','노작값');const i=el('input');i.name='price';i.placeholder='예: 2천만';i.autocomplete='off';l.append(i);fix.append(l);}
    const b=el('button','secondary','저장하고 다시 계산');b.type='submit';fix.append(b);
    fix.onsubmit=e=>{e.preventDefault();task(b,async()=>{const f=fix.elements;
      if(f.level&&f.level.value)await api('history/starforce/level',{item:g.item,level:f.level.value});
      if(f.price&&f.price.value){const price=parsePrice(f.price.value);if(price==null)throw new Error("노작값은 '2천만'처럼 적어 주세요.");await api('prices',{item:g.item,price,source:'user'});}
      await loadForgeHistory();});};
    card.append(fix);
  }
  g.notes.forEach(n=>card.append(el('p','hint',n)));
  return card;
}
$('#history-fetch').onclick=e=>task(e.currentTarget,async()=>{
  $('#history-status').textContent='넥슨 기록을 날짜별로 불러오는 중…';
  const r=await api('history/starforce/fetch',{days:Number($('#history-days').value)});
  toast(`${fmt(r.requested_days)}일 조회 · 새 기록 ${fmt(r.added)}건`+(r.failed.length?` · 실패 ${r.failed.length}일`:''),!!r.failed.length);
  await loadForgeHistory();
});
async function loadStatus(){const s=await api('status');const ready=s.model.connected&&s.selected_model&&s.model.models.includes(s.selected_model);const pill=$('#model-pill');pill.replaceChildren(el('span','dot'+(ready?'':' amber')),el('span','',ready?s.selected_model:s.model.connected?'모델 선택 필요':'모델 연결 필요'),el('span','','↗'));$('#model-status').textContent=s.model.connected?`Ollama 연결됨 · 모델 ${s.model.models.length}개`:'Ollama 연결 실패 · 로컬에서 실행하세요';const select=$('#model-select');select.replaceChildren();if(!s.model.models.length){const option=el('option','','설치된 모델 없음');option.value='';select.append(option);}s.model.models.forEach(name=>{const option=el('option','',name);option.value=name;option.selected=name===s.selected_model;select.append(option);});$('#key-status').textContent=s.vault_error|| (s.key_present?'키 저장됨 · 인증은 캐릭터 조회로 확인':'등록된 키 없음');const sys=s.system;const strip=$('#system-info');strip.replaceChildren();[`${sys.os} · ${sys.architecture}`,sys.ram_gb?`RAM ${sys.ram_gb} GB`:'RAM 미확인',`여유 공간 ${sys.disk_free_gb} GB`,sys.gpu||'GPU 미확인',sys.ocr_available?'OCR 엔진 감지됨':'OCR 설치 필요'].forEach(t=>strip.append(el('span','',t)));$('#storage-path').textContent=s.storage_path+' · 키는 OS 보안 저장소에 별도 보관';if(s.download.running)pollDownload();else if(s.download.status)$('#download-status').textContent=s.download.status;}
$('#refresh-status').onclick=e=>task(e.currentTarget,loadStatus);
// 노작값 — 저장된 값 목록과 직접 입력.
function amountText(v){
  const units=[[1e12,'조'],[1e8,'억'],[1e4,'만']];
  for(const [scale,name] of units){if(v>=scale)return fmt(Math.round(v/scale*100)/100)+name;}
  return fmt(v);
}
async function loadPrices(){
  const d=await api('prices');
  $('#price-status').textContent=`${d.stored}건`+(d.fetcher?` · 오늘 조회 ${d.used_today}/${d.daily_limit}`:' · 조회기 없음');
  $('#price-fetch').checked=!!d.fetch_enabled;$('#price-fetch').disabled=!d.fetcher;
  const list=$('#price-list');list.replaceChildren();
  if(!d.prices.length){list.append(el('p','hint','저장된 값이 없습니다.'));return;}
  d.prices.forEach(p=>{
    const row=el('div','price-row');
    const left=el('div');left.append(el('strong','',p.item),el('small','',
      `${amountText(p.price)} 메소 · ${p.source==='user'?'직접 입력':p.source} · ${String(p.recorded_at).slice(0,10)}`
      +(p.add_grade?` · ${p.add_grade}급`:'')+(p.note?` · ${p.note}`:'')));
    const del=el('button','','×');del.setAttribute('aria-label',p.item+' 삭제');
    del.onclick=()=>guard(async()=>{await api('prices/delete',{id:p.id});await loadPrices();});
    row.append(left,del);list.append(row);
  });
}
$('#price-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{
  const d=formData(e.target);
  const price=parsePrice(d.price);
  if(price==null)throw new Error("노작값은 '32억' 또는 숫자로 입력하세요.");
  await api('prices',{item:d.item,price,add_grade:d.add_grade||null,note:d.note||null,source:'user'});
  e.target.reset();await loadPrices();toast('노작값을 저장했습니다.');});};
function parsePrice(text){
  if(!text)return null;let c=String(text).replaceAll(',','').trim().replace(/(\d+(?:\.\d+)?)\s*천/g,(_,n)=>String(parseFloat(n)*1000));let total=0,hit=false;
  [['조',1e12],['억',1e8],['만',1e4]].forEach(([u,scale])=>{
    const m=c.match(new RegExp('(\\d+(?:\\.\\d+)?)\\s*'+u));if(m){total+=parseFloat(m[1])*scale;hit=true;}});
  if(hit)return total;
  return /^\d+(\.\d+)?$/.test(c)?parseFloat(c):null;
}
$('#price-fetch').onchange=e=>guard(async()=>{await api('prices/fetch',{enabled:e.target.checked});await loadPrices();});
$('#key-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{await api('settings/key',formData(e.target));e.target.reset();accountCatalog=null;$('#account-characters').replaceChildren();toast('키를 저장했습니다.');switchView('characters');await loadStatus();});};
$('#delete-key').onclick=e=>task(e.currentTarget,async()=>{if(!confirm('저장된 API 키를 삭제합니다.'))return;await api('settings/key/delete',{});accountCatalog=null;$('#account-characters').replaceChildren();$('#account-status').textContent='키 삭제됨 · 설정에서 등록하세요';await loadStatus();toast('키를 삭제했습니다.');});
$('#model-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{await api('settings/model',formData(e.target));await loadStatus();toast('모델을 저장했습니다.');});};
$('#pull-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{await api('model/pull',formData(e.target));pollDownload();});};
async function pollDownload(){clearTimeout(downloadTimer);try{const d=await api('download');$('#download-status').textContent=(d.status||'대기')+(d.total?` · ${Math.round((d.completed||0)/d.total*100)}%`:'');$('#pull-form button').disabled=d.running;if(d.running)downloadTimer=setTimeout(pollDownload,1500);else await loadStatus();}catch(e){toast(e.message,true);$('#pull-form button').disabled=false;}}
(async()=>{try{const r=await fetch('/api/bootstrap');const b=await r.json();token=b.token;if(!token)throw new Error('앱 연결에 실패했습니다.');await Promise.all([loadHistory(),loadStatus()]);switchView(location.hash.slice(1)||'chat');}catch(e){toast('앱 연결 실패 · 실행 상태 확인 후 새로고침',true);}})();

$('#quit-app').onclick=()=>guard(async()=>{if(!confirm('앱을 종료합니다. 저장된 데이터는 유지됩니다.'))return;await api('shutdown',{});clearTimeout(downloadTimer);toast('종료했습니다. 탭을 닫아도 됩니다.');});

function setCharacterFeedback(text){$('#character-feedback').hidden=false;$('#character-feedback').textContent=text;}
async function refreshSavedCharacter(id){
  setCharacterFeedback('등록 완료 · 조회 중');
  try{await api('characters/refresh',{id});setCharacterFeedback('스냅샷을 저장했습니다.');}
  catch(e){setCharacterFeedback('등록됨 · 상세 조회 실패: '+e.message);}
  await loadCharacters();
  if(selectedCharacterName)showCharacterProfile(selectedCharacterName,true);
}
function renderAccountCharacters(){renderCharacterRoster();}
async function discoverCharacters(){
  if(accountLoading)return;accountLoading=true;const button=$('#discover-characters');button.disabled=true;$('#account-status').textContent='목록 조회 중…';
  try{accountCatalog=await api('characters/discover',{});$('#account-status').textContent=`캐릭터 ${accountCatalog.characters.length}개 · 조회 ${accountCatalog.retrieved_at}`;renderAccountCharacters();autoSelectCharacter();}
  catch(e){accountCatalog=null;$('#account-characters').replaceChildren();$('#account-status').textContent='목록 조회 실패: '+e.message+' 아래에서 이름으로 직접 등록할 수 있습니다.';if(!selectedCharacterName)showProfileEmpty(e.message);}
  finally{accountLoading=false;button.disabled=false;}
}
$('#discover-characters').onclick=discoverCharacters;
$('#account-search').oninput=renderAccountCharacters;
