'use strict';
const $ = (selector) => document.querySelector(selector);
// 앱 창(pywebview)에서 열렸으면 웹페이지 티가 나는 동작을 막는다. 브라우저로 열었을 때는 그대로 둔다.
const inAppWindow=new URLSearchParams(location.search).get('shell')==='window';
if(inAppWindow){
  document.documentElement.classList.add('in-app');
  // 새로고침·인쇄·저장 단축키는 앱에 맞지 않는다.
  document.addEventListener('keydown',e=>{const k=(e.key||'').toLowerCase();if(e.key==='F5'||((e.metaKey||e.ctrlKey)&&['r','p','s'].includes(k)))e.preventDefault();},true);
  // 오른쪽 클릭의 '새로 고침·검사' 메뉴를 숨긴다. 입력칸과 고른 글자에서는 복사·붙여넣기 메뉴를 둔다.
  document.addEventListener('contextmenu',e=>{if(e.target.closest('input,textarea,[contenteditable]')||String(window.getSelection()).trim())return;e.preventDefault();});
  document.addEventListener('dragstart',e=>{if(e.target.closest('img,a'))e.preventDefault();});
}
const $$ = (selector) => [...document.querySelectorAll(selector)];
let token = '', sessionId = null, busy = false, confirmedText = '', previewUrl = null, downloadTimer;
let accountCatalog = null, accountLoading = false, managedNames = new Set(), managedCharacters = [];
const titles = {chat:'대화',characters:'캐릭터',calculator:'수익',library:'기록',settings:'설정'};
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
// **굵게**만 살린다. 텍스트 노드로만 만들어 HTML이 끼어들 틈이 없다.
function richText(cls,text){const box=el('div',cls);String(text||'').split(/(\*\*[^*\n]+?\*\*)/).forEach(part=>{if(/^\*\*[^*\n]+?\*\*$/.test(part))box.append(el('strong','',part.slice(2,-2)));else if(part)box.append(document.createTextNode(part));});return box;}
function renderMessage(role,payload){$('#welcome').hidden=true;$$('#messages .choice-form').forEach(lockChoiceForm);const block=el('article','message '+role);if(role==='user'){block.textContent=payload.content;}else{const heading=el('div','message-heading');const icon=el('img');icon.src='/favicon.svg';icon.alt='';heading.append(icon,el('span','','메피티'));const status={held:'보류',clarify:'조건 확인',evidence:'근거 원문',term:'용어',context:'조회한 사실',analysis:'캐릭터 분석',ask_price:'노작값 필요',price:'값 저장됨',ask_conditions:'조건 선택',conditions:'조건 저장됨'};heading.append(el('span','badge',status[payload.status]||'안내'));block.append(heading,richText('message-body',payload.content));if(payload.form)block.append(renderChoiceForm(payload.form));if(payload.sources?.length){const sources=el('div','sources');payload.sources.forEach(s=>{const a=sourceLink(s.source_url,`[${s.citation}] ${s.title}`);a.className='source';a.append(el('small','',`${s.source_type==='official'?'공식':'커뮤니티'} · 버전 ${s.version}\n적용 ${s.effective_from} · 수집 ${s.retrieved_at}\n재검토 기한 ${s.valid_until}`));sources.append(a);});block.append(sources);}if(payload.links?.length){const links=el('div','sources');payload.links.forEach(l=>{const a=sourceLink(l.url,l.title);a.className='source';links.append(a);});block.append(links);}if(payload.facts){const facts=el('details','fact-sheet');facts.append(el('summary','','근거로 쓴 조회 사실'),el('pre','',payload.facts));block.append(facts);}if(payload.conditions?.length){const conditions=el('div','conditions');payload.conditions.forEach(c=>conditions.append(el('p','',c)));block.append(conditions);}}$('#messages').append(block);scrollBottom();}
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
  const alertBox=$('#crystal-alert');if(alertBox){alertBox.replaceChildren();alertBox.hidden=!d.crystal_alert;
    if(d.crystal_alert){alertBox.append(el('span','',`새 업데이트에 결정석 판매가 이야기가 있습니다: ${d.crystal_alert.title}. 앱의 결정석 가격표(업데이트 813 기준)가 바뀌었을 수 있으니 확인해 주세요. `),sourceLink(d.crystal_alert.url,'공지 보기'));}}
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
  if(d.missing_level)list.append(el('div','notice history-missing',`장비 레벨을 몰라 이득·손해를 계산하지 못한 장비가 ${fmt(d.missing_level)}개 있습니다. 카드에서 장비 레벨을 눌러 주세요.`));
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
  if(g.missing==='level'){
    const quick=el('div','history-levels');quick.append(el('strong','','장비 레벨을 골라 주세요'));
    [140,150,160,200,250].forEach(lv=>{const b=el('button','choice',`${lv}레벨`);b.type='button';
      b.onclick=()=>task(b,async()=>{await api('history/starforce/level',{item:g.item,level:lv});await loadForgeHistory();});quick.append(b);});
    card.append(quick);
  }
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
async function loadStatus(){const s=await api('status');if(s.version){$('.brand .alpha').textContent=s.version.split('.').slice(0,2).join('.')+'α';$('#app-version').textContent='v'+s.version;}const ready=s.ready;const pill=$('#model-pill');pill.replaceChildren(el('span','dot'+(ready?'':' amber')),el('span','',ready?modelLabel(s):s.selected_model?'모델 연결 필요':'모델 선택 필요'),el('span','','↗'));$('#model-status').textContent=`클라우드: Gemini 키 ${(s.cloud||{}).key_present?'연결됨':'없음'} · 로컬: `+(s.model.connected?`Ollama 연결됨, 모델 ${s.model.models.length}개`:'Ollama 꺼짐');const select=$('#model-select');select.replaceChildren();if(!s.model.models.length){const option=el('option','','설치된 모델 없음');option.value='';select.append(option);}s.model.models.forEach(name=>{const option=el('option','',name);option.value=name;option.selected=name===s.selected_model;select.append(option);});$('#key-status').textContent=s.vault_error|| (s.key_present?'키 저장됨 · 인증은 캐릭터 조회로 확인':'등록된 키 없음');const sys=s.system;const strip=$('#system-info');strip.replaceChildren();[`${sys.os} · ${sys.architecture}`,sys.ram_gb?`RAM ${sys.ram_gb} GB`:'RAM 미확인',`여유 공간 ${sys.disk_free_gb} GB`,sys.gpu||'GPU 미확인',sys.ocr_available?'OCR 엔진 감지됨':'OCR 설치 필요'].forEach(t=>strip.append(el('span','',t)));$('#storage-path').textContent=s.storage_path+' · 키는 OS 보안 저장소에 별도 보관';renderPresets(s);renderKeyCard(s);renderSetup(s);if(typeof maybeStartTour==='function')maybeStartTour(s);if(s.download.running)pollDownload();else if(s.download.status)$$('.download-status').forEach(e=>e.textContent=s.download.status);return s;}
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
$('#key-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{const status=$('#key-card-status');if(await connectKey(e.target,status)){$('#account-characters').replaceChildren();switchView('characters');}else throw new Error(status.textContent);});};
$('#delete-key').onclick=e=>task(e.currentTarget,async()=>{if(!confirm('저장된 API 키를 삭제합니다.'))return;await api('settings/key/delete',{});accountCatalog=null;$('#account-characters').replaceChildren();$('#account-status').textContent='키 삭제됨 · 설정에서 등록하세요';await loadStatus();toast('키를 삭제했습니다.');});
$('#model-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{await api('settings/model',formData(e.target));await loadStatus();toast('모델을 저장했습니다.');});};
$('#pull-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{await api('model/pull',formData(e.target));pollDownload();});};
function progressText(d){return (d.status||'대기')+(d.total?` · ${Math.round((d.completed||0)/d.total*100)}% (${(d.completed/1e9).toFixed(2)} / ${(d.total/1e9).toFixed(2)} GB)`:'');}
async function pollDownload(){clearTimeout(downloadTimer);try{const d=await api('download');$$('.download-status').forEach(e=>e.textContent=progressText(d));$('#pull-form button').disabled=d.running;$$('.preset-card button').forEach(b=>b.disabled=d.running);if(d.running)downloadTimer=setTimeout(pollDownload,1500);else await loadStatus();}catch(e){toast(e.message,true);$('#pull-form button').disabled=false;}}
// AI 모델 선택: 클라우드(Gemini 무료)·2B·8B. 앱이 정하지 않고 사용자가 고른다(설치 마법사·첫 실행·설정).
// 첫 실행 카드와 설정 화면이 같은 선택 화면(modelChooser)을 쓴다.
const CLOUD='gemini';
let setupSkipped=false,setupAutoStarted=false,ollamaTimer,cloudFormOpen=false,cloudFormUse=false,localHelpOpen=false;
function rerenderModels(){if(lastStatus){renderPresets(lastStatus);renderSetup(lastStatus);}}
function modelLabel(s){return s.selected_model===CLOUD?`Gemini · 클라우드`:s.selected_model;}
async function choosePreset(id){
  const r=await api('model/preset',{id});
  if(r.need_key){   // Gemini 키부터 받는다. 입력칸이 카드들 아래에 생기므로 보이게 하고 바로 붙여 넣게 둔다.
    cloudFormOpen=true;cloudFormUse=true;rerenderModels();
    const form=[...$$('.cloud-key')].find(e=>e.offsetParent);if(form){form.scrollIntoView({block:'nearest',behavior:'smooth'});form.querySelector('input').focus({preventScroll:true});}
    return;}
  if(r.selected){toast(`${r.selected===CLOUD?'클라우드(Gemini)':r.selected}를 사용합니다.`);await loadStatus();}else pollDownload();
}
function presetCard(p,s){
  const busy=s.download.running,card=el('div','preset-card'+(p.cloud?' cloud':'')+(p.selected?' selected':''));
  const top=el('div','preset-top');top.append(el('strong','',p.label));
  if(p.recommended)top.append(el('span','badge gold','추천'));else if(p.fits_device)top.append(el('span','badge','이 기기에 맞음'));
  if(p.selected)top.append(el('span','badge','사용 중'));else if(p.installed)top.append(el('span','badge',p.cloud?'키 연결됨':'받아 둠'));
  card.append(top,el('p','preset-fits',p.fits),el('p','hint',p.note));
  const meta=el('div','preset-meta');
  (p.cloud?['설치 없음','인터넷 필요',p.license]:[`모델 ${p.model}`,`내려받기 ${p.download_gb}GB`,`메모리 약 ${p.memory_gb}GB`,p.license]).forEach(t=>meta.append(el('span','',t)));
  card.append(meta);
  const localBlocked=!p.cloud&&!s.model.connected;      // 로컬은 Ollama가 떠 있어야 받고 쓸 수 있다
  const label=p.selected?'사용 중':p.cloud?(p.installed?'이 모델 쓰기':'Gemini 키 넣고 쓰기'):localBlocked?'Ollama 필요':p.installed?'이 모델 쓰기':`받고 쓰기 (${p.download_gb}GB)`;
  const button=el('button',p.selected?'secondary':'primary',label);button.type='button';button.disabled=p.selected||(busy&&!p.cloud);
  button.onclick=()=>{if(localBlocked){localHelpOpen=true;rerenderModels();return;}task(button,()=>choosePreset(p.id));};
  card.append(button);return card;
}
// Gemini 키 받는 법과 입력칸. 결제 정보 없이 Google 계정만으로 받는다.
function cloudKeyForm(){
  const box=el('div','cloud-key');box.append(el('h3','','Gemini API 키 받기 (무료)'));
  const steps=el('ol','key-steps');
  const first=el('li');first.append(sourceLink('https://aistudio.google.com/apikey','Google AI Studio ↗'),document.createTextNode('에 들어가 Google 계정으로 로그인하세요.'));
  const second=el('li');second.append(el('b','','Get API key'),document.createTextNode('(API 키 받기) → '),el('b','','Create API key'),document.createTextNode('(API 키 만들기)를 누르세요. 약관 창이 뜨면 동의하세요.'));
  const third=el('li');third.append(document.createTextNode('만들어진 키('),el('code','','AIza'),document.createTextNode('로 시작하는 긴 글자)를 복사해 아래 칸에 붙여 넣으세요.'));
  steps.append(first,second,third);box.append(steps);
  const form=el('form','key-card-form');const input=el('input');input.type='password';input.name='key';input.required=true;input.autocomplete='off';input.maxLength=200;input.placeholder='복사한 Gemini API 키 붙여 넣기';input.setAttribute('aria-label','Gemini API 키');
  const submit=el('button','primary','연결');submit.type='submit';form.append(input,submit);box.append(form);
  const status=el('p','notice');status.hidden=true;status.setAttribute('role','status');box.append(status);
  // 문구 근거: Gemini API 추가 약관(무료 서비스) — 제공·개선에 사용, 사람이 검토할 수 있음, 만 18세 이상. 2026-09-29 확인.
  box.append(el('p','hint','결제 정보는 넣지 않아도 돼요. 질문과 캐릭터 정보가 Google로 전송되고, 무료 등급에서는 Google이 제품 개선에 쓰거나 사람이 검토할 수 있어요. Google 약관상 만 18세 이상만 쓸 수 있어요. 키는 이 컴퓨터의 보안 저장소에만 보관돼요.'));
  form.onsubmit=e=>{e.preventDefault();task(submit,async()=>{
    status.hidden=true;let r;
    try{r=await api('cloud/key/connect',{key:input.value,use:cloudFormUse});}
    catch(err){status.textContent=err.message;status.hidden=false;return;}          // Google이 거절한 키는 저장하지 않았다
    cloudFormOpen=false;
    toast(r.state==='ok'?`Gemini를 연결했어요(${r.model}).${cloudFormUse?' 이제 답변에 씁니다.':''}`:`키를 저장했어요. 지금은 Google에서 확인하지 못했어요(${r.message}).`);
    await loadStatus();});};
  return box;
}
// 로컬 모델을 고르려는데 Ollama가 없거나 꺼져 있을 때.
function ollamaHelp(s){
  const box=el('div','ollama-help'),sys=s.system||{},setup=s.ollama_setup||{};
  if(setup.running){box.append(el('p','download-status',progressText(setup)));return box;}
  if(setup.error)box.append(el('p','notice',setup.status));
  if(!sys.ollama_installed&&sys.os==='Darwin'){
    box.append(el('p','','로컬 모델에는 Ollama가 필요합니다. 공식 Ollama 앱(약 190MB)을 내려받아 사용자 폴더에 설치합니다. 관리자 권한은 필요 없습니다.'));
    const install=el('button','primary','Ollama 설치');install.type='button';
    install.onclick=()=>task(install,async()=>{await api('ollama/install',{});clearInterval(ollamaTimer);ollamaTimer=setInterval(()=>guard(loadStatus),2000);});box.append(install);
  }else if(!sys.ollama_installed){
    box.append(el('p','','로컬 모델에는 Ollama가 필요합니다. 설치 프로그램을 다시 실행해 로컬 모델을 고르면 함께 설치되고, 공식 사이트에서 받아도 됩니다.'));
    box.append(sourceLink('https://ollama.com/download','Ollama 내려받기 ↗'));
  }else{
    box.append(el('p','','Ollama가 설치되어 있지만 실행 중이 아닙니다. Ollama를 실행한 뒤 다시 확인하세요.'));
    const again=el('button','secondary','다시 확인');again.type='button';again.onclick=()=>task(again,loadStatus);box.append(again);
  }
  return box;
}
function modelChooser(s){
  const parts=[],grid=el('div','model-presets');(s.presets||[]).forEach(p=>grid.append(presetCard(p,s)));parts.push(grid);
  const cloud=s.cloud||{};
  if(cloudFormOpen)parts.push(cloudKeyForm());
  else if(cloud.key_present){
    const row=el('div','cloud-row');row.append(el('span','',`Gemini 키 연결됨${cloud.model?' · '+cloud.model:''}`));
    const change=el('button','secondary','키 바꾸기');change.type='button';change.onclick=()=>{cloudFormOpen=true;cloudFormUse=s.selected_model===CLOUD;rerenderModels();};
    const remove=el('button','secondary','키 삭제');remove.type='button';remove.onclick=()=>task(remove,async()=>{if(!confirm('저장된 Gemini API 키를 삭제합니다.'))return;await api('cloud/key/delete',{});await loadStatus();toast('Gemini 키를 삭제했습니다.');});
    row.append(change,remove);parts.push(row);
  }
  if(cloud.vault_error)parts.push(el('p','notice',cloud.vault_error));
  if(s.model.connected)clearInterval(ollamaTimer);
  else if(localHelpOpen||(s.ollama_setup||{}).running)parts.push(ollamaHelp(s));
  return parts;
}
function renderPresets(s){$('#model-presets').replaceChildren(...modelChooser(s));}
// 처음 실행하면 넥슨 API 키부터 연결하게 안내한다. 캐릭터·장비·기록이 모두 이 키로 조회된다.
// 키 상태: missing(없음) · vault_error(보안 저장소를 못 읽음 — 키가 있을 수도 있다) · invalid(넥슨이 거절: 만료·삭제 등)
// · connected(연결됨, 설정의 '발급 방법 보기'로 연 경우만 보인다). 인터넷·한도·점검으로 확인 못 한 경우는 카드를 띄우지 않는다.
let keySkipped=false,keyOpened=false,keyState=null,keyChecking=false,lastStatus=null;
const KEY_CARD={
  missing:{title:'넥슨 API 키 연결',lead:'내 캐릭터와 장비를 불러오려면 넥슨이 무료로 발급하는 API 키가 필요해요. 처음 한 번만 하면 됩니다.',submit:'연결',close:'나중에'},
  invalid:{title:'저장된 넥슨 API 키를 쓸 수 없어요',lead:'넥슨이 저장된 키를 받아 주지 않았어요. 키가 만료됐거나 넥슨 Open API에서 삭제됐을 수 있어요. 아래 순서대로 키를 확인하거나 새로 받아 붙여 넣으세요.',submit:'바꾸기',close:'나중에'},
  connected:{title:'넥슨 API 키가 연결되어 있어요',lead:'지금 키로 잘 쓰고 있어요. 다른 키로 바꾸려면 아래 순서대로 새 키를 받아 붙여 넣으세요.',submit:'바꾸기',close:'닫기'},
  vault_error:{title:'저장된 키를 읽지 못했어요',lead:'이 컴퓨터의 보안 저장소(Mac 키체인, Windows 자격 증명 관리자)를 열지 못했어요. 키를 새로 받을 필요는 없을 수 있어요. Mac에서 \'키체인 접근 허용\' 창이 떴다면 허용을 누른 뒤 다시 확인하세요.',close:'나중에'},
};
function keyCardMode(s){
  if(s.vault_error)return 'vault_error';
  if(!s.key_present)return 'missing';
  if(keyState==='invalid')return 'invalid';
  return keyOpened?'connected':null;
}
function renderKeyCard(s){
  lastStatus=s;const card=$('#key-card'),mode=keyCardMode(s);
  card.dataset.checking=keyChecking?'1':'';                 // 확인이 끝나기 전에는 사용법 안내를 시작하지 않는다
  card.hidden=!mode||(keySkipped&&!keyOpened);if(card.hidden)return;
  const text=KEY_CARD[mode];card.dataset.mode=mode;
  $('#key-card-title').textContent=text.title;$('#key-card-lead').textContent=text.lead;
  const canEnter=mode!=='vault_error';
  $('#key-card-steps').hidden=!canEnter;$('#key-card-form').hidden=!canEnter;$('#key-card-hint').hidden=!canEnter;
  $('#key-card-retry').hidden=canEnter;$('#key-card-later').textContent=text.close;
  if(canEnter)$('#key-card-submit').textContent=text.submit;
  if(mode==='vault_error'){$('#key-card-status').textContent=s.vault_error;$('#key-card-status').hidden=false;}
}
// 앱을 켤 때 저장된 키가 아직 쓸 수 있는지 한 번 확인한다.
async function checkSavedKey(s){
  if(!s.key_present||keyState)return;
  keyChecking=true;renderKeyCard(s);
  try{keyState=(await api('settings/key/check',{})).state;}catch{keyState='unverified';}
  finally{keyChecking=false;}
  renderKeyCard(lastStatus||s);if(typeof maybeStartTour==='function')maybeStartTour(lastStatus||s);
}
async function connectKey(form,status){
  status.hidden=true;
  let result;
  try{result=await api('settings/key/connect',formData(form));}
  catch(err){status.textContent=err.message;status.hidden=false;return false;}  // 넥슨이 거절한 키는 저장하지 않았다
  form.reset();accountCatalog=null;keyState=result.state;keyOpened=false;
  toast(result.state==='ok'?`연결됐어요. 캐릭터 ${result.characters}개를 찾았어요. 캐릭터 탭에서 볼 수 있어요.`
                          :`키를 저장했어요. 지금은 넥슨에서 확인하지 못했어요(${result.message}). 잠시 뒤 캐릭터 탭에서 다시 시도해 보세요.`);
  await loadStatus();return true;
}
$('#key-card-form').onsubmit=e=>{e.preventDefault();task(e.submitter,()=>connectKey(e.target,$('#key-card-status')));};
$('#key-card-retry').onclick=e=>task(e.currentTarget,async()=>{$('#key-card-status').hidden=true;keyState=null;const s=await loadStatus();if(s)await checkSavedKey(s);});
$('#show-key-guide').onclick=()=>{keySkipped=false;keyOpened=true;$('#key-card-status').hidden=true;renderKeyCard(lastStatus);switchView('chat');requestAnimationFrame(()=>$('#key-card').scrollIntoView({block:'start'}));};
$('#key-card-later').onclick=()=>{keySkipped=true;keyOpened=false;$('#key-card').hidden=true;if(lastStatus&&typeof maybeStartTour==='function')maybeStartTour(lastStatus);};
function renderSetup(s){
  const card=$('#setup-card');
  const wasHidden=card.hidden;card.hidden=s.ready||setupSkipped;if(card.hidden)return;
  // 카드가 처음 뜰 때는 맨 위를 보여 준다. 앱 창(pywebview)에서 스크롤이 중간에 걸린 채 열려 카드 위쪽이 가려졌다.
  if(wasHidden)requestAnimationFrame(()=>{$('#chat-scroll').scrollTop=0;});
  const chosen=s.setup_choice&&(s.presets||[]).find(p=>p.id===s.setup_choice);
  // 설치 마법사에서 고른 것이 있으면 그 준비부터 보여 준다(클라우드는 키 입력, 로컬은 Ollama 확인 후 자동 다운로드).
  if(chosen&&wasHidden){if(chosen.cloud&&!chosen.installed){cloudFormOpen=true;cloudFormUse=true;}if(!chosen.cloud&&!s.model.connected)localHelpOpen=true;}
  card.replaceChildren(el('h2','','AI 모델 준비'));
  card.append(el('p','',chosen?(chosen.cloud?'설치할 때 클라우드(Gemini)를 골랐어요. 아래에 Gemini API 키를 넣으면 바로 쓸 수 있어요.'
                                            :`설치할 때 고른 ${chosen.label} 모델을 받습니다. 끝나면 바로 쓸 수 있습니다.`)
                               :'답변을 쓸 AI를 고르세요. 설치할 것이 없는 클라우드를 추천해요. 나중에 설정에서 바꿀 수 있습니다.'));
  card.append(...modelChooser(s));
  card.append(el('p','download-status',s.download.running||s.download.status?progressText(s.download):''));
  const later=el('button','secondary','나중에');later.type='button';later.onclick=()=>guard(async()=>{setupSkipped=true;cloudFormOpen=false;await api('model/setup/skip',{});card.hidden=true;if(typeof maybeStartTour==='function')maybeStartTour(s);});card.append(later);
  if(chosen&&!chosen.cloud&&!chosen.installed&&s.model.connected&&!s.download.running&&!setupAutoStarted){setupAutoStarted=true;guard(()=>choosePreset(chosen.id));}
}
(async()=>{try{const r=await fetch('/api/bootstrap');const b=await r.json();token=b.token;if(!token)throw new Error('앱 연결에 실패했습니다.');const [,status]=await Promise.all([loadHistory(),loadStatus()]);switchView(location.hash.slice(1)||'chat');checkSavedKey(status);}catch(e){toast('앱 연결 실패 · 실행 상태 확인 후 새로고침',true);}})();

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
