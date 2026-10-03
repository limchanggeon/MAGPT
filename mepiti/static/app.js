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
// 글씨 크기(설정 → 화면). 화면 전체를 비율로 키운다. 이 컴퓨터(앱 저장소)에 남긴다.
const FONT_SCALES=['0.9','1','1.12','1.25'];
function applyFontScale(v){if(!FONT_SCALES.includes(v))v='1';document.documentElement.style.setProperty('--user-scale',v);return v;}
let fontScale='1';try{fontScale=applyFontScale(localStorage.getItem('mepiti-font-scale')||'1');}catch{}
let token = '', sessionId = null, busy = false, confirmedText = '', previewUrl = null, downloadTimer;
let accountCatalog = null, accountLoading = false, catalogRequest=0, managedNames = new Set(), managedCharacters = [];
const titles = {chat:'대화',characters:'캐릭터',calculator:'수익',goals:'목표',library:'기록',settings:'설정'};
const numberFormatter = new Intl.NumberFormat('ko-KR',{maximumFractionDigits:3});
const fmt = (n) => numberFormatter.format(n);
const el = (tag,cls,text) => { const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e; };
function toast(message,error=false){const e=$('#toast');e.textContent=message;e.classList.toggle('error',error);e.hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>e.hidden=true,6500);}
async function requestAPI(path,data){const response=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{'X-Mepiti-Token':token,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw new Error(result.error||'요청에 실패했습니다.');return result;}
const pendingGets = new Map();
function api(path,data){
  if(data!==undefined)return requestAPI(path,data).then(result=>{pendingGets.clear();return result;});
  if(pendingGets.has(path))return pendingGets.get(path);
  const pending=requestAPI(path).finally(()=>{if(pendingGets.get(path)===pending)pendingGets.delete(path);});
  pendingGets.set(path,pending);return pending;
}
async function guard(fn){try{return await fn();}catch(e){toast(e.message,true);}}
async function task(button,fn){button.disabled=true;try{return await guard(fn);}finally{button.disabled=false;}}
function formData(form){return Object.fromEntries(new FormData(form));}
function sourceLink(url,text){const a=el('a','',text);try{const parsed=new URL(url);if(parsed.protocol==='https:'){a.href=url;a.target='_blank';a.rel='noreferrer noopener';}}catch{}return a;}
let activeView=null;
function switchView(view){if(!titles[view])view='chat';if(view===activeView)return;activeView=view;clearTimeout(peerTimer);$$('.view').forEach(e=>e.hidden=e.id!=='view-'+view);$$('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));$('#page-title').textContent=titles[view];if(view==='characters'){guard(loadCharacters);guard(loadPeers);}if(view==='calculator')guard(loadEarnings);if(view==='library')guard(loadForgeHistory);if(view==='goals')guard(loadGoals);if(view==='settings'){guard(loadStatus);guard(loadPrices);guard(loadAuction);guard(loadDataPanel);}location.hash=view;}
$$('[data-view]').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.view)));
window.addEventListener('hashchange',()=>switchView(location.hash.slice(1)));
function scrollBottom(){$('#chat-scroll').scrollTop=$('#chat-scroll').scrollHeight;}
// **굵게**만 살린다. 텍스트 노드로만 만들어 HTML이 끼어들 틈이 없다.
function richText(cls,text){const box=el('div',cls);String(text||'').split(/(\*\*[^*\n]+?\*\*)/).forEach(part=>{if(/^\*\*[^*\n]+?\*\*$/.test(part))box.append(el('strong','',part.slice(2,-2)));else if(part)box.append(document.createTextNode(part));});return box;}
function renderMessage(role,payload){$('#welcome').hidden=true;$$('#messages .choice-form').forEach(lockChoiceForm);const block=el('article','message '+role);if(role==='user'){block.textContent=payload.content;}else{const heading=el('div','message-heading');const icon=el('img');icon.src='/favicon.svg';icon.alt='';heading.append(icon,el('span','','메피티'));const status={held:'보류',clarify:'조건 확인',evidence:'근거 원문',term:'용어',context:'조회한 사실',analysis:'캐릭터 분석',ask_price:'노작값 필요',price:'값 저장됨',ask_conditions:'조건 선택',conditions:'조건 저장됨',ask:'확인 필요'};heading.append(el('span','badge',status[payload.status]||'안내'));block.append(heading,richText('message-body',payload.content));if(payload.form)block.append(renderChoiceForm(payload.form));if(payload.sources?.length){const sources=el('div','sources');payload.sources.forEach(s=>{const a=sourceLink(s.source_url,`[${s.citation}] ${s.title}`);a.className='source';a.append(el('small','',`${s.source_type==='official'?'공식':'커뮤니티'} · 버전 ${s.version}\n적용 ${s.effective_from} · 수집 ${s.retrieved_at}\n재검토 기한 ${s.valid_until}`));sources.append(a);});block.append(sources);}if(payload.links?.length){const links=el('div','sources');payload.links.forEach(l=>{const a=sourceLink(l.url,l.title);a.className='source';links.append(a);});block.append(links);}if(payload.facts){const facts=el('details','fact-sheet');facts.append(el('summary','','근거로 쓴 조회 사실'),el('pre','',payload.facts));block.append(facts);}if(payload.conditions?.length){const conditions=el('div','conditions');payload.conditions.forEach(c=>conditions.append(el('p','',c)));block.append(conditions);}}$('#messages').append(block);scrollBottom();}
let historyRequest=0, chatRequest=0, chatLoading=false;
async function openSession(s){
  if(busy)return;
  const request=++chatRequest;
  chatLoading=true;$('#send-button').disabled=true;
  sessionId=s.id;chatTopic=null;renderTopicCard();$('#messages').replaceChildren();$('#welcome').hidden=true;
  confirmedText='';renderAttachment();
  try{
    const messages=await api('messages?session_id='+s.id);
    if(request!==chatRequest)return;
    const latest=[...messages].reverse().find(m=>m.payload?.topic_item)?.payload.topic_item;
    chatTopic=s.topic?{...s.topic,...(latest||{})}:null;renderTopicCard();
    messages.forEach(m=>renderMessage(m.role,m.payload));$('#welcome').hidden=messages.length>0;
    switchView('chat');await guard(loadHistory);
  }catch(err){if(request===chatRequest){$('#welcome').hidden=false;throw err;}}
  finally{if(request===chatRequest){chatLoading=false;$('#send-button').disabled=false;}}
}
async function loadHistory(){
  const request=++historyRequest, sessions=await api('sessions');
  if(request!==historyRequest)return;
  const list=$('#history');list.replaceChildren();
  if(!sessions.length)list.append(el('div','history-empty','기록 없음'));
  sessions.forEach(s=>{
    const row=el('div','history-entry'+(s.id===sessionId?' active':''));
    const open=el('button','',s.title);open.title=s.title;open.addEventListener('click',()=>guard(()=>openSession(s)));
    const del=el('button','','×');del.setAttribute('aria-label',s.title+' 대화 삭제');
    del.addEventListener('click',()=>guard(async()=>{if(busy)return;if(!confirm('이 기록을 삭제합니다.'))return;
      await api('sessions/delete',{id:s.id});if(sessionId===s.id)newChat();await loadHistory();}));
    row.append(open,del);list.append(row);
  });
}
function newChat(){if(busy)return;chatRequest++;chatLoading=false;$('#send-button').disabled=false;sessionId=null;chatTopic=null;renderTopicCard();$('#messages').replaceChildren();$('#welcome').hidden=false;confirmedText='';renderAttachment();switchView('chat');guard(loadHistory);$('#message').focus();}
$('#new-chat').addEventListener('click',newChat);
document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='k'){e.preventDefault();newChat();}});
$$('[data-prompt]').forEach(b=>b.addEventListener('click',()=>{$('#message').value=b.dataset.prompt;$('#message').focus();}));
$('#message').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#chat-form').requestSubmit();}});
$('#chat-form').addEventListener('submit',e=>{e.preventDefault();guard(async()=>{if(busy||chatLoading)return;let message=$('#message').value.trim();if(!message&&!confirmedText)return;if(!confirmedText&&/^(내|제)\s*캐릭터(?:\s*(보여줘|보여주세요|보기|볼래|보고 싶어|보여 줘|보여 주세요))?[.!?]*$/.test(message)){$('#message').value='';switchView('characters');return;}if(confirmedText)message+='\n\n[사용자가 확인한 스크린샷 내용]\n'+confirmedText;if(message.length>12000)throw new Error('질문과 첨부를 합쳐 12,000자 이하.');const original=$('#message').value;$('#message').value='';try{await sendChat(message);confirmedText='';renderAttachment();}catch(err){$('#message').value=original;throw err;}});});
// 질문을 보내고 답을 그린다. answer는 선택창에서 고른 값(구조화된 답)이다.
async function sendChat(message,answer){
  if(busy||chatLoading)return;
  busy=true;$('#send-button').disabled=true;
  renderMessage('user',{content:message});
  const thinking=el('div','thinking','근거 확인 중…');$('#messages').append(thinking);scrollBottom();
  try{
    const topic=!sessionId&&chatTopic?{topic:{slot:chatTopic.slot,name:chatTopic.name,...(chatTopic.character?{character:chatTopic.character}:{}),...(chatTopic.preset?{preset:chatTopic.preset}:{})}}:{};
    const result=await api('chat',{message,session_id:sessionId,...(answer?{answer}:{}),...topic});
    sessionId=result.session_id;thinking.remove();
    if(chatTopic&&result.topic_item){chatTopic={...chatTopic,...result.topic_item};renderTopicCard();}
    renderMessage('assistant',result);await guard(loadHistory);
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
  if(form.kind==='ask'){
    // 재질문: 고르면 그 답이 그대로 메시지로 간다. 직접 입력창에 적어도 된다.
    const box=el('div','choice-form ask-form');const row=el('div','choice-options');
    form.options.forEach(o=>{const b=el('button','choice',o.label);b.type='button';
      b.onclick=()=>{if(busy||box.classList.contains('answered'))return;guard(async()=>{lockChoiceForm(box);b.classList.add('selected');
        try{await sendChat(o.reply);}catch(err){unlockChoiceForm(box);throw err;}});};row.append(b);});
    box.append(row);return box;
  }
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
async function loadCharacters(){const chars=await api('characters');managedCharacters=chars;managedNames=new Set(chars.map(c=>c.name));renderAccountCharacters();const list=$('#character-list');list.replaceChildren();if(!chars.length)list.append(el('div','empty-state','등록한 캐릭터 없음'));chars.forEach(c=>{const card=el('article','panel character-card');const avatar=el('div','char-avatar','♧');const image= c.snapshots[0]?.data.image;if(image){try{const u=new URL(image);if(u.protocol==='https:'&&u.hostname==='open.api.nexon.com'&&u.pathname.startsWith('/static/maplestory/character/')){const img=el('img','character-image');img.src=image;img.alt=c.name+' 캐릭터 외형';img.loading='lazy';img.onerror=()=>{if(!img.dataset.proxied){img.dataset.proxied='1';img.src='/nexon-image?u='+encodeURIComponent(image);}else img.replaceWith(el('span','','♧'));};avatar.replaceChildren(img);}}catch{}}card.append(avatar);const title=el('h2','',c.name);if(c.main)title.append(el('span','badge','대표 캐릭터'));card.append(title,el('p','muted','목표 · '+(c.goal||'미설정')),el('p','','예산 · '+fmt(c.budget)+' 메소'));if(c.snapshots.length){const snap=c.snapshots[0],s=snap.data;card.append(el('p','muted',`${s.world||''} · ${s.job||''}`));const stats=el('div','stat-grid');[['레벨',s.level,'level'],['전투력',s.combat_power,'combat_power']].forEach(([label,value,key])=>{const box=el('div');box.append(el('small','',label),el('strong','',value==null?'미조회':fmt(value)));if(c.changes[key]!=null)box.append(el('small','',`직전 대비 ${c.changes[key]>=0?'+':''}${fmt(c.changes[key])}`));stats.append(box);});card.append(stats,el('p','hint','조회 '+snap.retrieved_at+' · API 기준일 '+(s.api_date||'미제공')));if(s.warning)card.append(el('p','hint',s.warning));if(c.snapshots.length<2)card.append(el('p','hint','다음 조회부터 변화를 비교합니다.'));}else card.append(el('p','hint','조회 기록 없음'));const refresh=el('button','primary','조회 · 스냅샷');refresh.onclick=()=>task(refresh,async()=>{setCharacterFeedback('조회 중');try{await api('characters/refresh',{id:c.id});setCharacterFeedback('스냅샷을 저장했습니다.');await loadCharacters();}catch(e){setCharacterFeedback('조회 실패: '+e.message);throw e;}});const edit=el('button','secondary','수정');edit.onclick=()=>{const form=$('#character-form');['id','name','goal','budget'].forEach(k=>form.elements[k].value=c[k]);form.elements.main.checked=!!c.main;$('#character-form-title').textContent='캐릭터 수정';$('.character-management').open=true;form.scrollIntoView({behavior:'smooth',block:'center'});};const del=el('button','secondary','삭제');del.onclick=()=>guard(async()=>{if(!confirm('캐릭터와 스냅샷을 삭제합니다.'))return;await api('characters/delete',{id:c.id});await loadCharacters();});const view=el('button','primary','캐릭터 보기');view.onclick=()=>showCharacterProfile(c.name);card.append(view,refresh,edit,del);list.append(card);});if(accountCatalog===null&&!accountLoading)await discoverCharacters();else if(accountCatalog)autoSelectCharacter();}
$('#character-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{const form=e.target;const saved=await api('characters',{...formData(form),main:form.elements.main.checked});form.reset();await refreshSavedCharacter(saved.id);});};
$('#character-form').onreset=()=>{$('#character-form-title').textContent='캐릭터 등록';setTimeout(()=>$('#character-form').elements.id.value='',0);};
// 수익 — 재획(메소 + 조각 x 조각 가격)과 주간 보스(결정석 / 파티 인원 + 추가 드롭).
function mesoText(v){return v==null?'—':amountText(Math.round(v))+' 메소';}
function readAmount(text,unit='억'){const v=String(text||'').trim();if(!v)return 0;if(/^0+(\.0+)?$/.test(v))return 0;return parsePrice(v,unit);}
function huntPreview(){
  const f=$('#hunt-form').elements;const m=readAmount(f.meso.value),price=readAmount(f.piece_price.value,'만'),n=Number(f.pieces.value||0);
  const box=$('#hunt-preview');
  if(m==null||price==null){box.textContent="금액은 '12억 3500만'처럼 적어 주세요.";return;}
  if(!m&&!n){box.textContent='';return;}
  if(price&&price<10000){box.textContent=`조각 가격이 ${fmt(price)}메소로 읽혀요. 1개 가격을 '650만'처럼 단위를 붙여 적어 주세요.`;return;}
  box.textContent=`이번 재획 총수익 ${mesoText(m+n*price)}`+(n?` (메소 ${mesoText(m)} + 조각 ${fmt(n)}개 × ${mesoText(price)} = ${mesoText(n*price)})`:'');
}
function todayText(){const d=new Date();return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10);}
// 하루에 재획을 여러 번 했으면 그날 몇 번째인지(기록한 순서) 붙인다: '재획 2회차'.
function huntOrder(rows){
  const byDay={},seen=new Set();
  // 서버 목록은 최신 먼저다. 같은 초에 적은 기록은 시각이 같아 목록 순서를 거꾸로 써서 먼저 적은 것이 앞에 오게 한다.
  rows.filter(r=>r&&!seen.has(r.id)&&seen.add(r.id)).map((r,i)=>[r,i]).sort((a,b)=>String(a[0].created_at).localeCompare(String(b[0].created_at))||b[1]-a[1]).map(x=>x[0])
    .forEach(r=>(byDay[r.day]=byDay[r.day]||[]).push(r.id));
  return r=>{const list=byDay[r.day]||[];return list.length>1?` ${list.indexOf(r.id)+1}회차`:'';};
}
function earningsRow(r,label,detail){
  const row=el('div','earnings-row');const left=el('div');
  left.append(el('strong','',label),el('small','',`${r.day} · ${detail}`+(r.note?` · ${r.note}`:'')));
  const right=el('div','earnings-amount',mesoText(r.total));
  const del=el('button','','×');del.setAttribute('aria-label',`${r.day} ${label} 기록 삭제`);
  del.onclick=()=>guard(async()=>{if(!confirm('이 기록을 삭제합니다.'))return;await api('earnings/delete',{id:r.id});await loadEarnings();});
  row.append(left,right,del);return row;
}
// 볼 주(목요일 시작)와 달. 비어 있으면 이번 주·이번 달. 막대나 ◀ ▶로 바꾼다.
let earningsWeek='',earningsMonth='',earningsTrend='weeks',lastEarnings=null,earningsCharacter='';
const dayShort=(iso)=>{const [,m,d]=iso.split('-');return `${Number(m)}/${Number(d)}`;};
const monthText=(ym)=>{const [y,m]=ym.split('-');return `${y}년 ${Number(m)}월`;};
function addDays(iso,days){const d=new Date(iso+'T00:00:00');d.setDate(d.getDate()+days);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10);}
function addMonths(ym,months){const [y,m]=ym.split('-').map(Number);const i=y*12+m-1+months;return `${Math.floor(i/12)}-${String(i%12+1).padStart(2,'0')}`;}
function characterTable(box,data,empty){
  box.replaceChildren();
  if(!data.characters.length){box.append(el('p','hint',empty));return;}
  const monthly=data.monthly>0;   // 검은 마법사(월보) 기록이 있을 때만 열을 보인다
  const table=el('table','totals-table');const head=el('tr');['캐릭터','재획','주보',...(monthly?['월보(검마)']:[]),'합계'].forEach(h=>head.append(el('th','',h)));table.append(head);
  const cells=(c)=>[el('td','num',mesoText(c.hunt)),el('td','num',mesoText(c.boss)),...(monthly?[el('td','num',mesoText(c.monthly||0))]:[]),el('td','num total',mesoText(c.total))];
  data.characters.forEach(c=>{const tr=el('tr');tr.append(el('td','',c.name),...cells(c));table.append(tr);});
  if(data.characters.length>1){const tr=el('tr','sum');tr.append(el('td','','합계'),...cells(data));table.append(tr);}
  box.append(table);
}
function renderTrend(d){
  const box=$('#earnings-trend');box.replaceChildren();
  const items=earningsTrend==='weeks'?d.weeks:d.months;const max=Math.max(1,...items.map(x=>x.total));
  items.forEach(x=>{
    const isWeek=earningsTrend==='weeks',key=isWeek?x.week_start:x.month;
    const picked=isWeek?key===d.week.start:key===d.month.month;
    const col=el('button','trend-col'+(picked?' picked':''));col.type='button';
    col.title=`${isWeek?dayShort(key)+' 주':monthText(key)} · 합계 ${mesoText(x.total)} (재획 ${mesoText(x.hunt)} · 주보 ${mesoText(x.boss)}`+(x.monthly?` · 월보(검마) ${mesoText(x.monthly)}`:'')+')';
    col.setAttribute('aria-label',col.title);
    const stack=el('div','trend-stack');
    const monthly=el('span','trend-monthly');monthly.style.height=`${(x.monthly||0)/max*100}%`;
    const boss=el('span','trend-boss');boss.style.height=`${x.boss/max*100}%`;const hunt=el('span','trend-hunt');hunt.style.height=`${x.hunt/max*100}%`;
    stack.append(monthly,boss,hunt);
    col.append(el('small','trend-value',x.total?amountText(Math.round(x.total)):''),stack,el('span','trend-label',isWeek?dayShort(key):`${Number(key.slice(5))}월`));
    col.onclick=()=>{if(isWeek)earningsWeek=key;else earningsMonth=key;guard(loadEarnings);};
    box.append(col);
  });
}
// 기록할 캐릭터: 관리 중(대표 먼저) → 계정 캐릭터(레벨순) → 예전 기록의 이름. 처음엔 대표 캐릭터.
function fillCharacterSelects(d){
  const choices=d.character_choices||(d.characters||[]).map(name=>({name,group:'관리 중'}));
  const names=choices.map(c=>c.name);
  $$('.earnings-character').forEach(select=>{
    const keep=select.value||earningsCharacter||d.default_character||'';select.replaceChildren();
    const none=el('option','','고르지 않음');none.value='';select.append(none);
    const groups=new Map();choices.forEach(c=>{if(!groups.has(c.group)){const g=el('optgroup');g.label=c.group==='계정'?'계정 캐릭터':c.group==='기록'?'예전 기록':'관리 중';groups.set(c.group,g);select.append(g);}
      const o=el('option','',c.name+(c.level?` · ${c.world||''} Lv.${c.level}`:''));o.value=c.name;groups.get(c.group).append(o);});
    select.value=names.includes(keep)?keep:'';
    select.onchange=()=>{earningsCharacter=select.value;$$('.earnings-character').forEach(s=>{if(s!==select)s.value=select.value;});bossPreview();};
  });
}
// 계정 캐릭터 목록을 한 번도 안 불러왔으면 한 번 불러와 고르기 목록을 채운다(넥슨 API 키가 있을 때).
let accountListTried=false;
async function ensureAccountCharacters(d){
  if(d.account_loaded||accountListTried)return;accountListTried=true;
  const request=catalogRequest;
  try{await api('characters/discover',{});if(request===catalogRequest)await loadEarnings();}catch{}
}
// 솔 에르다 조각 시세 — 경매장(검색 기준 캐릭터 월드)의 판매 중 최저 개당 가격. 한 시간 안에 본 값은 다시 검색하지 않는다.
let pieceEdited=false;
function showPieceAuction(p,fill){
  const note=$('#piece-auction-note');if(!p){note.textContent='';return;}
  const at=String(p.at||'').replace('T',' ').slice(5,16);
  note.textContent=`${p.world} 경매장 최저가 ${mesoText(p.price)} (${at}${p.cached?' · 한 시간 안에 본 값':''})`;
  if(fill)$('#hunt-form').elements.piece_price.value=amountText(p.price),huntPreview();
}
async function autoPieceAuction(d){
  if(pieceEdited)return;
  showPieceAuction(d.piece_auction,false);
  try{const a=await api('auction/status');if(!a.logged_in)return;       // 경매장에 연결돼 있을 때만 자동으로 본다
    showPieceAuction(await api('earnings/piece-price',{}),!pieceEdited);}catch{}
}
$('#piece-auction').onclick=e=>task(e.currentTarget,async()=>{pieceEdited=false;showPieceAuction(await api('earnings/piece-price',{}),true);});
$('#hunt-form').elements.piece_price.addEventListener('input',()=>{pieceEdited=true;});
let earningsRequest=0;
async function loadEarnings(){
  const request=++earningsRequest;
  const query=new URLSearchParams();if(earningsWeek)query.set('week',earningsWeek);if(earningsMonth)query.set('month',earningsMonth);
  const d=await api('earnings'+(query.toString()?'?'+query:''));if(request!==earningsRequest)return;lastEarnings=d;bossPrices=d.boss_prices||{};renderBossChecklist(d.crystals||[]);
  earningsWeek=d.week.current?'':d.week.start;earningsMonth=d.month.current?'':d.month.month;
  ['hunt-form','boss-form'].forEach(id=>{const f=$('#'+id).elements;if(!f.day.value)f.day.value=todayText();});
  if(d.piece_price&&!$('#hunt-form').elements.piece_price.value)$('#hunt-form').elements.piece_price.value=amountText(d.piece_price);
  fillCharacterSelects(d);ensureAccountCharacters(d);autoPieceAuction(d);
  const alertBox=$('#crystal-alert');if(alertBox){alertBox.replaceChildren();alertBox.hidden=!d.crystal_alert;
    if(d.crystal_alert){alertBox.append(el('span','',`새 업데이트에 결정석 판매가 이야기가 있습니다: ${d.crystal_alert.title}. 앱의 결정석 가격표(업데이트 813 기준)가 바뀌었을 수 있으니 확인해 주세요. `),sourceLink(d.crystal_alert.url,'공지 보기'));}}
  const w=d.week,m=d.month,a=d.all,weekText=`${dayShort(w.start)}(목) ~ ${dayShort(w.end)}(수)`;
  $('#week-label').textContent=weekText+(w.current?' · 이번 주':'');$('#month-label').textContent=monthText(m.month)+(m.current?' · 이번 달':'');
  $('#week-next').disabled=w.current;$('#month-next').disabled=m.current;$('#week-now').hidden=w.current;$('#month-now').hidden=m.current;
  const box=$('#earnings-summary');box.replaceChildren();
  const mb=(x)=>x.monthly?` · 월보(검마) ${mesoText(x.monthly)}`:'';
  [[w.current?'이번 주':`${dayShort(w.start)} 주`,w.total,`재획 ${fmt(w.hunt_count)}회 ${mesoText(w.hunt)} · 주보 ${fmt(w.boss_count)}건 ${mesoText(w.boss)}`+mb(w)],
   [m.current?'이번 달':monthText(m.month),m.total,`재획 ${mesoText(m.hunt)} · 주보 ${mesoText(m.boss)}`+mb(m)],
   ['전체',a.total,(a.first_day?`${a.first_day}부터 · `:'')+`조각 ${fmt(a.pieces)}개 · 재획 평균 ${mesoText(a.hunt_average)}`+(a.per_flask?` · 소재비 1개당 ${mesoText(a.per_flask)}`:'')]]
   .forEach(([label,value,sub])=>{const c=el('div','earnings-stat');c.append(el('span','',label),el('strong','',mesoText(value)),el('small','',sub));box.append(c);});
  $('#week-chars-title').textContent=`캐릭터별 · ${w.current?'이번 주':weekText}`;$('#month-chars-title').textContent=`캐릭터별 · ${m.current?'이번 달':monthText(m.month)}`;
  characterTable($('#week-characters'),w,'이 주에는 기록이 없습니다.');characterTable($('#month-characters'),m,'이 달에는 기록이 없습니다.');
  renderTrend(d);renderCalendar(d);
  $('#hunt-list-title').textContent=`재획 기록 · ${weekText}`;$('#boss-list-title').textContent=`주보 기록 · ${weekText}`;
  const who=(r)=>r.character?` · ${r.character}`:'';
  const hunts=$('#hunt-list');hunts.replaceChildren();
  if(!d.hunts.length)hunts.append(el('p','hint','이 주에는 재획 기록이 없습니다.'));
  const nth=huntOrder(d.month_hunts&&d.month_hunts.length?[...d.month_hunts,...d.hunts]:d.hunts);
  d.hunts.forEach(r=>hunts.append(earningsRow(r,'재획'+nth(r)+who(r)+(r.flasks?` · 소재비 ${fmt(r.flasks)}개`:''),
    `메소 ${mesoText(r.meso)}`+(r.pieces?` + 조각 ${fmt(r.pieces)}개 × ${mesoText(r.piece_price)} = ${mesoText(r.pieces*(r.piece_price||0))}`:''))));
  applyBossLimit();
  const bosses=$('#boss-list');bosses.replaceChildren();
  if(!d.bosses.length)bosses.append(el('p','hint','이 주에는 주보 기록이 없습니다.'));
  // 여러 캐릭터로 보스를 도는 사람을 위해 캐릭터별로 묶고, 캐릭터마다 주간 n/12를 보인다(검은 마법사 제외).
  const byChar=new Map();d.bosses.forEach(r=>{const k=r.character||'';if(!byChar.has(k))byChar.set(k,[]);byChar.get(k).push(r);});
  byChar.forEach((rows,name)=>{
    const used=(d.boss_counts||{})[name]||0,sum=rows.reduce((s,r)=>s+r.total,0);
    const head=el('div','boss-char-head');head.append(el('strong','',name||'캐릭터 미지정'),el('small','',`주간 보스 ${fmt(used)}/${fmt(d.boss_limit||12)} · ${mesoText(sum)}`));
    if(byChar.size>1||name)bosses.append(head);
    rows.forEach(r=>bosses.append(earningsRow(r,r.boss,
      `결정석 ${mesoText(r.crystal)}`+(r.party>1?` ÷ ${r.party}명`:'')+(r.extra?` · 드롭 ${mesoText(r.extra)}`:''))));
  });
}
$('#week-prev').onclick=()=>{earningsWeek=addDays(lastEarnings?lastEarnings.week.start:todayText(),-7);guard(loadEarnings);};
$('#week-next').onclick=()=>{earningsWeek=addDays(lastEarnings?lastEarnings.week.start:todayText(),7);guard(loadEarnings);};
$('#week-now').onclick=()=>{earningsWeek='';guard(loadEarnings);};
$('#month-prev').onclick=()=>{earningsMonth=addMonths(lastEarnings?lastEarnings.month.month:todayText().slice(0,7),-1);guard(loadEarnings);};
$('#month-next').onclick=()=>{earningsMonth=addMonths(lastEarnings?lastEarnings.month.month:todayText().slice(0,7),1);guard(loadEarnings);};
$('#month-now').onclick=()=>{earningsMonth='';guard(loadEarnings);};
$$('.trend-tabs button').forEach(b=>b.onclick=()=>{earningsTrend=b.dataset.trend;
  $$('.trend-tabs button').forEach(x=>{x.classList.toggle('active',x===b);x.setAttribute('aria-selected',x===b?'true':'false');});if(lastEarnings)renderTrend(lastEarnings);});
function earningsForm(id,kind,after){
  $('#'+id).onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{
    const data=formData(e.target);await api('earnings',{...data,kind});
    const keep={day:data.day,piece_price:data.piece_price,party:data.party,character:data.character};
    e.target.reset();Object.entries(keep).forEach(([k,v])=>{if(e.target.elements[k]&&v)e.target.elements[k].value=v;});
    after();await loadEarnings();toast('기록을 저장했습니다.');});};
}
earningsForm('hunt-form','hunt',huntPreview);
// 입력하는 동안 총수익 미리보기(2026-09-28 주보 체크박스 작업 때 빠졌던 것을 되살림).
['meso','pieces','piece_price'].forEach(k=>$('#hunt-form').elements[k].addEventListener('input',huntPreview));
// 수익 하위 탭 — 요약 · 재획(달력·캡처) · 주보.
let earnTab='summary';
function showEarnTab(name){earnTab=name;$$('.earn-tabs button').forEach(b=>{const on=b.dataset.earn===name;b.classList.toggle('active',on);b.setAttribute('aria-selected',on?'true':'false');});
  ['summary','hunt','boss'].forEach(n=>$('#earn-'+n).hidden=n!==name);}
$$('.earn-tabs button').forEach(b=>b.onclick=()=>showEarnTab(b.dataset.earn));
// 정확한 금액 표기(입력 칸 채우기용): 1234567890 -> '12억 3456만 7890'
function exactAmount(v){v=Math.round(Math.abs(v));const parts=[];[[1e12,'조'],[1e8,'억'],[1e4,'만']].forEach(([s,u])=>{const n=Math.floor(v/s);if(n){parts.push(n+u);v-=n*s;}});if(v||!parts.length)parts.push(String(v));return parts.join(' ');}
function shortAmount(v){if(!v)return '';if(v>=1e8)return (Math.round(v/1e7)/10)+'억';if(v>=1e4)return Math.round(v/1e4)+'만';return fmt(v);}
// 재획 달력: 고른 달을 목요일 시작 주로 나눈다. 칸에는 그날 재획 합계·소재비, 오른쪽 끝은 주 합계. 날짜를 누르면 그날 기록을 보고 그 날짜로 적는다.
let calendarDay='';
function renderCalendar(d){
  const box=$('#hunt-calendar');if(!box)return;box.replaceChildren();
  const days=d.hunt_days||{},m=d.month,first=m.start,last=m.end;
  $('#calendar-title').textContent=`재획 달력 · ${monthText(m.month)}`;
  const monthTotal=Object.values(days).reduce((s,x)=>s+x.total,0),flasks=Object.values(days).reduce((s,x)=>s+x.flasks,0);
  $('#calendar-total').textContent=`이 달 재획 ${mesoText(monthTotal)}`+(flasks?` · 소재비 ${fmt(flasks)}개`:'');
  ['목','금','토','일','월','화','수','주 합계'].forEach(w=>box.append(el('div','cal-head',w)));
  const dow=new Date(first+'T00:00:00').getDay();let cursor=addDays(first,-((dow-4+7)%7));const today=todayText();
  while(cursor<=last){
    let week=0;
    for(let i=0;i<7;i++){
      const day=cursor,inMonth=day>=first&&day<=last,info=days[day];
      const cell=el('button','cal-cell'+(inMonth?'':' outside')+(info?' has':'')+(day===today?' today':'')+(day===calendarDay?' selected':''));cell.type='button';
      cell.append(el('span','cal-date',String(Number(day.slice(8)))));
      if(info){week+=info.total;cell.append(el('strong','',shortAmount(info.total)));if(info.flasks)cell.append(el('small','',`소재비 ${fmt(info.flasks)}`));}
      cell.title=info?`${day} · ${mesoText(info.total)} · ${info.count}회`:day;
      cell.disabled=day>today;
      cell.onclick=()=>{calendarDay=day;renderCalendar(d);};
      box.append(cell);cursor=addDays(cursor,1);
    }
    box.append(el('div','cal-week',week?shortAmount(week):''));
  }
  renderCalendarDay(d);
}
function renderCalendarDay(d){
  const box=$('#calendar-day');box.replaceChildren();if(!calendarDay)return;
  const rows=(d.month_hunts||[]).filter(r=>r.day===calendarDay);
  const head=el('div','earnings-head');head.append(el('strong','',`${calendarDay} 재획`));
  const add=el('button','secondary','이 날짜로 기록');add.type='button';
  add.onclick=()=>{$('#hunt-form').elements.day.value=calendarDay;$('#hunt-form').scrollIntoView({behavior:'smooth',block:'center'});};
  head.append(add);box.append(head);
  if(!rows.length){box.append(el('p','hint',calendarDay>=d.month.start&&calendarDay<=d.month.end?'이 날은 재획 기록이 없습니다.':'다른 달의 날짜입니다. ◀ ▶로 그 달을 보세요.'));return;}
  const nth=huntOrder(rows);
  rows.sort((a,b)=>String(a.created_at).localeCompare(String(b.created_at)));
  rows.forEach(r=>box.append(earningsRow(r,'재획'+nth(r)+(r.character?` · ${r.character}`:'')+(r.flasks?` · 소재비 ${fmt(r.flasks)}개`:''),
    `메소 ${mesoText(r.meso)}`+(r.pieces?` + 조각 ${fmt(r.pieces)}개 × ${mesoText(r.piece_price)}`:''))));
}
// 캡처로 입력 — 사냥 전·후 캡처를 클라우드 모델이 읽고(메소·조각), 차이를 재획 기록 칸에 채운다. 저장은 사용자가 확인하고 누른다.
const captures={before:null,after:null};let captureSlot='before';
function captureReport(){
  const out=$('#capture-result'),b=captures.before?.values,a=captures.after?.values;
  const line=(v)=>[v.inventory_meso!=null?`인벤 ${exactAmount(v.inventory_meso)}`:null,v.storage_meso!=null?`창고 ${exactAmount(v.storage_meso)}`:null,v.sol_erda_pieces!=null?`조각 ${fmt(v.sol_erda_pieces)}개`:'조각 못 찾음'].filter(Boolean).join(' · ');
  $$('.capture-slot').forEach(s=>{const c=captures[s.dataset.slot];const body=s.querySelector('.capture-body');
    if(c&&c.values)body.textContent=line(c.values);else if(c&&c.loading)body.textContent='읽는 중…';else body.textContent='여기를 누르고 붙여 넣기';
    s.classList.toggle('filled',!!(c&&c.values));s.classList.toggle('active',s.dataset.slot===captureSlot);});
  $('#capture-apply').disabled=true;out.textContent='';
  captures.diff=null;
  const flasks=Number($('#capture-flasks').value||0);
  if(!(b&&a))return;
  // 사냥 전에 0이면 캡처에 안 보이기도 한다(0조각이면 아이템 칸 자체가 없음). 사냥 후에만 보이면 사냥 전은 0으로 본다.
  const assumed=[];
  const before=(k,label)=>{if(b[k]!=null)return b[k];if(a[k]!=null){assumed.push(label);return 0;}return null;};
  const bm=before('inventory_meso','메소'),bp=before('sol_erda_pieces','조각');
  const both=(k)=>b[k]!=null&&a[k]!=null;
  const meso=bm!=null&&a.inventory_meso!=null?(a.inventory_meso-bm)+(both('storage_meso')?a.storage_meso-b.storage_meso:0):null;
  const pieces=bp!=null&&a.sol_erda_pieces!=null?a.sol_erda_pieces-bp:null;
  captures.diff={meso,pieces};
  out.textContent=(meso!=null?`번 메소 ${meso<0?'-':''}${exactAmount(meso)}`:'메소 차이를 못 구했어요(두 캡처에 인벤 메소가 보여야 해요)')
    +(pieces!=null?` · 조각 ${pieces>=0?'+':''}${fmt(pieces)}개`:' · 조각 차이 없음(한쪽에서 못 찾음)')
    +(both('storage_meso')?' · 창고 메소 변화 포함':'')+(meso!=null&&meso<0?' — 메소가 줄었어요. 캡처 순서를 확인하세요.':'')
    +(flasks>0&&meso>0?` · 소재비 ${fmt(flasks)}개 → 1개당 메소 ${exactAmount(meso/flasks)}`:'')
    +(assumed.length?` (사냥 전 캡처에서 ${assumed.join('·')}을 못 찾아 0으로 봤어요)`:'');
  $('#capture-apply').disabled=!(meso>0||pieces>0);
}
async function readCaptureFile(file,slot){
  if(!file||!/^image\/(png|jpeg|webp)$/.test(file.type))throw new Error('PNG·JPG 이미지를 넣어 주세요.');
  const request={loading:true};captures[slot]=request;captureReport();
  try{
  let url=await new Promise((ok,no)=>{const r=new FileReader();r.onload=()=>ok(r.result);r.onerror=no;r.readAsDataURL(file);});
  if(captures[slot]!==request)return;
  if(url.length>7_500_000){ // 너무 크면 JPEG로 다시 담는다(서버 요청 한도 8MB)
    const img=await new Promise((ok,no)=>{const i=new Image();i.onload=()=>ok(i);i.onerror=no;i.src=url;});
    const scale=Math.min(1,2560/Math.max(img.width,img.height)),c=document.createElement('canvas');c.width=img.width*scale;c.height=img.height*scale;
    c.getContext('2d').drawImage(img,0,0,c.width,c.height);url=c.toDataURL('image/jpeg',0.92);}
  if(captures[slot]!==request)return;
  const result=await api('earnings/capture',{image:url});
  if(captures[slot]!==request)return;
  captures[slot]=result;
  if(slot==='before'&&!captures.after)captureSlot='after';
  captureReport();
  }catch(e){if(captures[slot]!==request)return;captures[slot]=null;captureReport();throw e;}
}
$$('.capture-slot').forEach(s=>{
  s.onclick=()=>{captureSlot=s.dataset.slot;captureReport();};
  s.ondblclick=()=>{captureSlot=s.dataset.slot;$('#capture-file').click();};
  s.ondragover=e=>{e.preventDefault();s.classList.add('drag');};s.ondragleave=()=>s.classList.remove('drag');
  s.ondrop=e=>{e.preventDefault();s.classList.remove('drag');captureSlot=s.dataset.slot;guard(()=>readCaptureFile(e.dataTransfer.files[0],s.dataset.slot));};
});
$('#capture-file').onchange=()=>{const f=$('#capture-file').files[0];$('#capture-file').value='';if(f)guard(()=>readCaptureFile(f,captureSlot));};
document.addEventListener('paste',e=>{
  if($('#earn-hunt').hidden||$('#view-calculator').hidden)return;
  if(e.target.closest&&e.target.closest('input,textarea'))return;
  const item=[...(e.clipboardData?.items||[])].find(i=>i.type.startsWith('image/'));if(!item)return;
  e.preventDefault();guard(()=>readCaptureFile(item.getAsFile(),captureSlot));
});
$('#capture-apply').onclick=()=>{const f=$('#hunt-form').elements,d=captures.diff||{};
  if(d.meso!=null&&d.meso>=0)f.meso.value=exactAmount(d.meso);if(d.pieces!=null&&d.pieces>=0)f.pieces.value=d.pieces;
  const flasks=Number($('#capture-flasks').value||0);f.flasks.value=flasks>0?flasks:'';
  if(!f.day.value)f.day.value=todayText();huntPreview();toast('캡처 차이를 채웠어요. 확인한 뒤 저장하세요.');f.meso.focus();};
$('#capture-reset').onclick=()=>{captures.before=captures.after=captures.diff=null;captureSlot='before';$('#capture-flasks').value='';captureReport();};
$('#capture-flasks').addEventListener('input',()=>captureReport());
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
// 주간 보스 결정석은 캐릭터당 주 12개(검은 마법사 제외). 이미 기록한 수 + 지금 체크한 수가 12면 더 못 고르게 한다.
const MONTHLY_BOSS=/^검은 마법사/;
function bossLimitState(){
  const f=$('#boss-form').elements,d=lastEarnings||{};const limit=d.boss_limit||12;
  const recorded=((d.boss_counts||{})[f.character.value||'']||0);
  const picked=[...$('#boss-checklist').querySelectorAll('input:checked')].filter(i=>!MONTHLY_BOSS.test(i.value)).length+(f.boss.value.trim()&&!MONTHLY_BOSS.test(f.boss.value.trim())?1:0);
  return {limit,recorded,picked,left:limit-recorded-picked};
}
function applyBossLimit(){
  const s=bossLimitState();
  $('#boss-checklist').querySelectorAll('input').forEach(i=>{const full=s.left<=0&&!i.checked&&!MONTHLY_BOSS.test(i.value);
    i.disabled=full;i.closest('.boss-check').classList.toggle('full',full);});
  const note=$('#boss-limit');if(note)note.textContent=`이번 주 이 캐릭터: 기록 ${fmt(s.recorded)} + 선택 ${fmt(s.picked)} / ${fmt(s.limit)} (검은 마법사 제외)`+(rebootRate()<1?' · 리부트(에오스·헬리오스) 캐릭터라 결정석 가격 절반':'')+(s.left<0?' — 12개를 넘었어요':'');
}
// 리부트 월드(에오스·헬리오스) 캐릭터는 결정석 시세가 절반이라 공식 판매가에 0.5를 곱해 보여 준다(저장도 서버가 같은 규칙으로 계산).
function rebootRate(){const d=lastEarnings||{},name=$('#boss-form').elements.character.value||'';
  return (d.reboot_characters||[]).includes(name)?(d.reboot_rate||0.5):1;}
function checkedBosses(){const rate=rebootRate();
  return [...$('#boss-checklist').querySelectorAll('input:checked')].map(i=>({boss:i.value,price:Number(i.dataset.price)*rate,
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
  applyBossLimit();
}
['crystal','party','extra','boss'].forEach(k=>$('#boss-form').elements[k].addEventListener('input',bossPreview));
$('#boss-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{
  const f=e.target.elements;const day=f.day.value,note=f.note.value,extra=f.extra.value.trim(),character=f.character.value;
  const entries=checkedBosses().map(b=>({boss:b.boss,party:b.party}));
  if(f.boss.value.trim())entries.push({boss:f.boss.value.trim(),crystal:f.crystal.value,party:f.party.value});
  if(!entries.length&&!extra)throw new Error('잡은 보스를 체크하거나 직접 적어 주세요.');
  if(!entries.length)entries.push({boss:'추가 드롭'});
  if(extra)entries[0].extra=extra;
  let saved=0;const errors=[];
  for(const entry of entries){try{await api('earnings',{kind:'boss',day,note,character,...entry});saved++;}catch(err){errors.push(`${entry.boss}: ${err.message}`);}}
  $('#boss-checklist').querySelectorAll('input:checked').forEach(i=>{i.checked=false;i.closest('.boss-row').classList.remove('picked');});
  ['boss','crystal','extra','note'].forEach(k=>f[k].value='');f.party.value=1;bossPreview();
  await loadEarnings();
  if(errors.length)toast(`${saved}건 저장, ${errors.length}건 실패 — ${errors[0]}`,true);else toast(`${saved}건 저장했습니다.`);
});};
// 스케줄러(넥슨 Open API)에서 이번 주에 잡은 보스를 불러온다. 결정석 가격·파티 인원만 적으면 된다.
$('#boss-import-button').onclick=e=>task(e.currentTarget,async()=>{
  const box=$('#boss-import');box.hidden=false;box.replaceChildren(el('p','hint','캐릭터별 스케줄러 조회 중'));
  const d=await api('earnings/scheduler',{});box.replaceChildren();
  // 불러온 목록을 닫는 버튼(저장하지 않고 접기). 다시 누르면 새로 불러온다.
  const bar=el('div','boss-import-bar');const close=el('button','secondary','닫기');close.type='button';
  close.onclick=()=>{box.hidden=true;box.replaceChildren();};
  bar.append(el('strong','',`${d.week_start} 주 스케줄러에서 불러온 보스`),close);box.append(bar);
  const limit=d.boss_limit||12;
  const failed=d.characters.filter(c=>c.error);if(failed.length)box.append(el('p','hint',failed.map(c=>`${c.name}: ${c.error}`).join(' / ')));
  if(!d.bosses.length){box.append(el('p','hint',`${d.week_start} 주에 완료한 보스가 없습니다. 스케줄러는 보스를 잡은 뒤에 갱신됩니다.`));return;}
  // 캐릭터별로 묶는다. 결정석 판매가는 공식 가격표로 자동(모르는 보스만 직접 입력), '포함'을 끄면 저장하지 않는다.
  const groups=new Map();d.bosses.forEach(b=>{if(!groups.has(b.character))groups.set(b.character,[]);groups.get(b.character).push(b);});
  const rows=[];
  const refresh=()=>groups.forEach((list,name)=>{
    const g=box.querySelector(`[data-char="${CSS.escape(name)}"]`);if(!g)return;
    const info=d.characters.find(c=>c.name===name)||{};const used=info.recorded_count||0;
    const on=rows.filter(r=>r.character===name&&r.check.checked&&!r.check.disabled&&!MONTHLY_BOSS.test(r.b.boss));
    const left=limit-used-on.length;
    rows.filter(r=>r.character===name&&!r.b.recorded).forEach(r=>{if(!MONTHLY_BOSS.test(r.b.boss))r.check.disabled=!r.check.checked&&left<=0;});
    const share=rows.filter(r=>r.character===name&&r.check.checked&&!r.b.recorded).reduce((s,r)=>s+(r.price()||0)/Math.max(1,Number(r.party.value||1)),0);
    g.querySelector('.boss-char-count').textContent=`기록 ${fmt(used)} + 이번에 ${fmt(on.length)} / ${fmt(limit)} · 내 몫 ${mesoText(share)}`+(left<0?' — 12개 초과':'');
  });
  groups.forEach((list,name)=>{
    const info=d.characters.find(c=>c.name===name)||{};
    const g=el('div','boss-import-group');g.dataset.char=name;
    const head=el('div','boss-char-head');head.append(el('strong','',name),el('small','boss-char-count',''));
    if(info.weekly_clear!=null)head.append(el('small','',`스케줄러 주보 ${info.weekly_clear}/${info.weekly_limit??'—'}`));
    g.append(head);
    list.forEach(b=>{
      const row=el('div','boss-import-row'+(b.recorded?' recorded':''));
      const toggle=el('label','boss-include');const check=el('input');check.type='checkbox';check.checked=!b.recorded;check.disabled=b.recorded;
      toggle.append(check,el('span','',b.recorded?'기록함':'포함'));check.setAttribute('aria-label',`${b.character} ${b.boss} 포함`);
      const label=el('div');label.append(el('strong','',b.boss),el('small','',(b.cycle?`${({bossWeekly:'주간',bossMonthly:'월간',bossMonthl:'월간',bossDaily:'일간'})[b.cycle]||b.cycle} · `:'')+(b.price_source==='official'||b.price_source==='community'?(b.price_source==='official'?'공식 판매가':'정리표 판매가(커뮤니티)')+(info.reboot?'의 절반(리부트)':''):b.price_source==='remembered'?'지난번 입력 가격':'가격표에 없음 — 직접 적어 주세요')));
      let priceBox,price;
      if(b.price){priceBox=el('span','boss-price',mesoText(b.price));price=()=>b.price;}
      else{priceBox=el('input');priceBox.placeholder='결정석 판매가(억)';priceBox.autocomplete='off';priceBox.disabled=b.recorded;price=()=>readAmount(priceBox.value);priceBox.oninput=refresh;}
      const party=el('select','boss-party');[1,2,3,4,5,6].forEach(n=>{const o=el('option','',n===1?'솔로':`${n}인`);o.value=n;party.append(o);});party.disabled=b.recorded;party.onchange=refresh;
      check.onchange=refresh;
      row.append(toggle,label,priceBox,party);g.append(row);
      rows.push({b,character:name,check,party,price,priceBox,row});
    });
    box.append(g);
  });
  const save=el('button','primary','포함한 보스 저장');save.type='button';
  save.onclick=()=>task(save,async()=>{
    const picked=rows.filter(r=>r.check.checked&&!r.b.recorded);
    if(!picked.length)throw new Error('저장할 보스를 켜 주세요.');
    let saved=0;const errors=[];
    for(const r of picked){
      const crystal=(r.b.price_source==='official'||r.b.price_source==='community')?'':(r.b.price?String(r.b.price):r.priceBox.value);
      try{await api('earnings',{kind:'boss',boss:r.b.boss,crystal,party:r.party.value,character:r.character,source_key:r.b.key,day:todayText()});saved++;
        r.b.recorded=true;r.row.classList.add('recorded');r.check.checked=false;r.check.disabled=true;r.party.disabled=true;}
      catch(err){errors.push(`${r.character} ${r.b.boss}: ${err.message}`);}}
    await loadEarnings();refresh();
    if(errors.length)toast(`${saved}건 저장, ${errors.length}건 실패 — ${errors[0]}`,true);else toast(`${saved}건 저장했습니다.`);
  });
  box.append(save);refresh();
});

// 기록 — 스타포스 강화 기록(넥슨 Open API)을 장비마다 묶어 실제 비용과 기대값을 비교한다.
// 강화 기록은 계정 전체가 섞여 온다. 캐릭터를 고르면 그 캐릭터 장비만 합계·카드로 본다(2026-10-03 사용자 요청).
let historyCharacter='',lastHistory=null;
function renderHistoryCharacters(d){
  const box=$('#history-characters');box.replaceChildren();
  const names=[...new Set(d.groups.map(g=>g.character||'캐릭터 미확인'))];
  if(historyCharacter&&!names.includes(historyCharacter))historyCharacter='';
  if(names.length<2){box.hidden=true;return;}
  box.hidden=false;
  const chip=(label,value,groups)=>{
    const compared=groups.filter(g=>g.difference!=null&&g.expected&&g.actual);const diff=compared.reduce((s,g)=>s+g.difference,0);
    const b=el('button','history-chip'+(historyCharacter===value?' active':''),label);b.type='button';b.setAttribute('aria-pressed',String(historyCharacter===value));
    b.append(el('small','',`장비 ${fmt(groups.length)}개`+(compared.length?` · ${diff>0?'손해':'이득'} ${mesoText(Math.abs(diff))}`:'')));
    b.onclick=()=>{historyCharacter=value;renderForgeHistory(d);};box.append(b);
  };
  chip('전체','',d.groups);
  names.sort((a,b)=>d.groups.filter(g=>(g.character||'캐릭터 미확인')===b).length-d.groups.filter(g=>(g.character||'캐릭터 미확인')===a).length)
    .forEach(n=>chip(n,n,d.groups.filter(g=>(g.character||'캐릭터 미확인')===n)));
}
async function loadForgeHistory(){
  const d=await api('history/starforce');lastHistory=d;renderForgeHistory(d);
}
function renderForgeHistory(full){
  renderHistoryCharacters(full);
  const d={...full,groups:historyCharacter?full.groups.filter(g=>(g.character||'캐릭터 미확인')===historyCharacter):full.groups};
  d.missing_level=d.groups.filter(g=>g.missing==='level').length;
  $('#history-status').textContent=d.fetched_days?`받아 둔 날짜 ${fmt(d.fetched_days)}일 · 최근 ${d.latest_day} · 강화 조건(MVP 할인) ${d.conditions}`:'아직 불러온 기록이 없습니다.';
  // 위쪽 합계: 기대값과 비교할 수 있는 장비들의 실제 쓴 돈 − 기대값. 카드를 하나씩 보지 않아도 이득·손해를 한눈에.
  const sum=$('#history-summary');sum.replaceChildren();
  const compared=d.groups.filter(g=>g.difference!=null&&g.expected&&g.actual);
  if(compared.length){
    const diff=compared.reduce((s,g)=>s+g.difference,0),expected=compared.reduce((s,g)=>s+g.expected.cost,0),spent=compared.reduce((s,g)=>s+g.actual.to_reach,0);
    const more=diff>0,box=el('div','panel history-total '+(more?'history-bad':'history-good'));
    box.append(el('span','',`${historyCharacter?historyCharacter+' · ':''}기대값과 비교한 장비 ${fmt(compared.length)}개 합계`),
      el('strong','',`기대보다 ${mesoText(Math.abs(diff))} ${more?'더 씀':'덜 씀'}`),
      el('small','',`실제 ${mesoText(spent)} · 기대 ${mesoText(expected)}`+(expected?` · 기대값의 ${fmt(Math.round(spent/expected*100))}%`:'')+
        ` · 이득 ${fmt(compared.filter(g=>g.difference<=0).length)}개 · 손해 ${fmt(compared.filter(g=>g.difference>0).length)}개`));
    sum.append(box);
  }
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
async function loadStatus(){const s=await api('status');if(s.version){$('.brand .alpha').textContent=s.version.split('.').slice(0,2).join('.')+'α';$('#app-version').textContent='v'+s.version;}const ready=s.ready;const pill=$('#model-pill');pill.replaceChildren(el('span','dot'+(ready?'':' amber')),el('span','',ready?modelLabel(s):s.selected_model?'모델 연결 필요':'모델 선택 필요'),el('span','pill-go','▾'));if(!$('#model-menu').hidden)renderModelMenu(s);$('#model-status').textContent=`클라우드 키: ${Object.entries(s.clouds||{}).filter(([,c])=>c.key_present).map(([p])=>CLOUDS[p].name).join(', ')||'없음'} · 로컬: `+(s.model.connected?`Ollama 연결됨, 모델 ${s.model.models.length}개`:'Ollama 꺼짐');const select=$('#model-select');select.replaceChildren();if(!s.model.models.length){const option=el('option','','설치된 모델 없음');option.value='';select.append(option);}s.model.models.forEach(name=>{const option=el('option','',name);option.value=name;option.selected=name===s.selected_model;select.append(option);});$('#key-status').textContent=s.vault_error|| (s.key_present?'키 저장됨 · 인증은 캐릭터 조회로 확인':'등록된 키 없음');const sys=s.system;const strip=$('#system-info');strip.replaceChildren();[`${sys.os} · ${sys.architecture}`,sys.ram_gb?`RAM ${sys.ram_gb} GB`:'RAM 미확인',`여유 공간 ${sys.disk_free_gb} GB`,sys.gpu||'GPU 미확인',sys.ocr_available?'OCR 엔진 감지됨':'OCR 설치 필요'].forEach(t=>strip.append(el('span','',t)));$('#storage-path').textContent=s.storage_path+' · 키는 OS 보안 저장소에 별도 보관';renderPresets(s);renderKeyCard(s);renderSetup(s);if(typeof maybeStartTour==='function')maybeStartTour(s);if(s.download.running)pollDownload();else if(s.download.status)$$('.download-status').forEach(e=>e.textContent=s.download.status);return s;}
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
// 단위 없이 적은 작은 수(10만 미만)는 unit(억·만)으로 읽는다: 12.5 → 12억 5천만. unit이 없으면 그대로(캡처 등).
function parsePrice(text,unit){
  if(!text)return null;let c=String(text).replaceAll(',','').trim().replace(/(\d+(?:\.\d+)?)\s*천/g,(_,n)=>String(parseFloat(n)*1000));let total=0,hit=false;
  [['조',1e12],['억',1e8],['만',1e4]].forEach(([u,scale])=>{
    const m=c.match(new RegExp('(\\d+(?:\\.\\d+)?)\\s*'+u));if(m){total+=parseFloat(m[1])*scale;hit=true;c=c.replace(m[0],' ');}});
  if(hit){const rest=c.match(/\d+(?:\.\d+)?/g)||[];if(rest.length===1&&parseFloat(rest[0])<10000)total+=parseFloat(rest[0]);return total;}  // '2764만 4807'
  if(!/^\d+(\.\d+)?$/.test(c))return null;const v=parseFloat(c);
  return unit&&v<100000?v*({'억':1e8,'만':1e4}[unit]||1):v;
}
// 업데이트 — 새 버전이 있으면 위쪽에 알린다. '업데이트'를 누르면 받아서 확인한 뒤 업데이터가 바꾸고 다시 켠다.
let updateTimer,updateDismissed=false;
function renderUpdate(u){
  const line=$('#version-line');
  if(line)line.textContent=`지금 버전 v${u.current||''}`+(u.latest?(u.newer?` · 새 버전 v${u.latest} 있음`:' · 최신 버전이에요'):'')+(u.error?` · ${u.error}`:'');
  const box=$('#update-banner'),st=u.state||{};
  if(!(u.newer||st.running||st.error)||(updateDismissed&&!st.running)){box.hidden=true;return;}
  box.replaceChildren();box.hidden=false;
  if(st.running||st.restarting){
    const pct=st.total?` ${Math.round((st.completed||0)/st.total*100)}%`:'';
    box.append(el('span','',`${st.status||'업데이트 중'}${pct}`));return;
  }
  box.append(el('strong','',`새 버전 v${u.latest}이(가) 나왔어요.`));
  if(st.error)box.append(el('span','notice-inline',st.status));
  if(u.can_apply){const go=el('button','primary','업데이트');go.type='button';go.onclick=()=>task(go,async()=>{renderUpdate(await api('update/apply',{}));watchUpdate();});box.append(go);}
  else box.append(el('span','hint',u.reason||''));
  box.append(sourceLink(u.page||'https://github.com/limchanggeon/MAGPT/releases/latest','바뀐 점 ↗'));
  const close=el('button','update-close','×');close.type='button';close.setAttribute('aria-label','알림 닫기');close.onclick=()=>{updateDismissed=true;box.hidden=true;};box.append(close);
}
function watchUpdate(){clearTimeout(updateTimer);updateTimer=setTimeout(()=>guard(async()=>{
  const u=await api('update');renderUpdate(u);if(u.state&&(u.state.running||u.state.restarting))watchUpdate();}),1000);}
async function checkUpdate(){renderUpdate(await api('update/check',{}));}
$('#update-check').onclick=e=>task(e.currentTarget,async()=>{updateDismissed=false;await checkUpdate();const u=await api('update');toast(u.error||(u.newer?`새 버전 v${u.latest}이 있어요.`:'최신 버전이에요.'),!!u.error);});
// 목표 — 메소 목표까지 걸릴 날(수익 기록 기준), 다음 레벨까지 걸릴 날(최근 7일 경험치).
let lastMesoPlan=null;
const dayText=(iso)=>{if(!iso)return '';const [y,m,d]=iso.split('-');return `${Number(m)}월 ${Number(d)}일`;};
const daysText=(n)=>n>=14?`${fmt(n)}일 (약 ${fmt(Math.round(n/7*10)/10)}주)`:`${fmt(n)}일`;
function renderMesoPlan(p,flasksOverride){
  lastMesoPlan=p;const box=$('#meso-plan');box.replaceChildren();
  const f=$('#meso-goal-form').elements;if(p.target&&!f.target.value)f.target.value=amountText(p.target);if(p.current&&!f.current.value)f.current.value=amountText(p.current);
  const flasks=flasksOverride??p.flasks_per_day;
  const huntDay=p.per_flask!=null?p.per_flask*flasks+(p.hunt_per_day-p.per_flask*p.flasks_per_day):p.hunt_per_day;
  const daily=huntDay+p.boss_per_week/7;
  if(p.target){
    const days=p.remaining===0?0:daily>0?Math.ceil(p.remaining/daily):null;
    const head=el('div','goal-head');
    head.append(el('span','',`목표 ${mesoText(p.target)}까지 남은 ${mesoText(p.remaining)}`));
    if(days===0)head.append(el('strong','','이미 목표에 닿았어요'));
    else if(days==null)head.append(el('strong','','수익 기록이 있어야 계산돼요'));
    else{const eta=new Date();eta.setDate(eta.getDate()+days);head.append(el('strong','',`약 ${daysText(days)}`),el('small','',`${eta.getMonth()+1}월 ${eta.getDate()}일쯤 · 하루 평균 ${mesoText(daily)}`));}
    box.append(head);
  }
  const basis=el('dl','goal-basis');const pair=(k,v)=>basis.append(el('dt','',k),el('dd','',v));
  pair('사냥',p.per_flask!=null?`하루 소재비 ${fmt(Math.round(flasks*10)/10)}개(${fmt(Math.round(flasks*p.flask_minutes/6)/10)}시간) × 1개당 ${mesoText(p.per_flask)} = 하루 ${mesoText(huntDay)}`:`하루 ${mesoText(huntDay)} (소재비 개수를 적은 기록이 없어 시간은 몰라요)`);
  pair('주보',p.boss_weeks?`주 ${mesoText(p.boss_per_week)} (최근 ${fmt(p.boss_weeks)}주 평균) = 하루 ${mesoText(p.boss_per_week/7)}`:'기록 없음');
  pair('기준',`최근 ${fmt(p.data_days)}일 기록 · 조각은 기록한 가격으로 포함`);
  box.append(basis);
  if(p.per_flask!=null){
    const whatif=el('label','goal-whatif');const input=el('input');input.type='number';input.min=0;input.max=48;input.step=0.5;input.value=Math.round(flasks*10)/10;
    input.oninput=()=>renderMesoPlan(lastMesoPlan,Number(input.value)||0);
    whatif.append(document.createTextNode('하루 소재비를 '),input,document.createTextNode('개 쓰면'));box.append(whatif);
    if(flasksOverride!=null)requestAnimationFrame(()=>box.querySelector('.goal-whatif input').focus());   // 다시 그려도 계속 입력하게
  }
  p.notes.forEach(n=>box.append(el('p','hint',n)));
}
async function loadGoals(){
  renderMesoPlan(await api('goals'));
  const select=$('#exp-character');if(!select.childElementCount){
    const d=await api('earnings');(d.character_choices||[]).forEach(c=>{const o=el('option','',c.name+(c.level?` · Lv.${c.level}`:''));o.value=c.name;select.append(o);});
    if(d.default_character)select.value=d.default_character;
  }
}
$('#meso-goal-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{const f=formData(e.target);renderMesoPlan(await api('goals/meso',{target:f.target,current:f.current}));});};
$('#exp-load').onclick=e=>task(e.currentTarget,async()=>{
  const box=$('#exp-plan');box.replaceChildren(el('p','hint','넥슨에서 최근 7일 경험치를 불러오는 중'));
  const p=await api('goals/exp',{name:$('#exp-character').value});box.replaceChildren();
  const head=el('div','goal-head');head.append(el('span','',`${p.name} · Lv.${p.level} · ${p.rate!=null?p.rate.toFixed(3)+'%':''}`));
  if(p.days!=null)head.append(el('strong','',`다음 레벨까지 약 ${p.days<1?Math.max(1,Math.round(p.days*24))+'시간':daysText(Math.ceil(p.days))}`),el('small','',`${dayText(p.eta)}쯤 · 하루 평균 +${p.per_day_percent.toFixed(2)}%`));
  box.append(head);
  if(p.per_day!=null){const basis=el('dl','goal-basis');const pair=(k,v)=>basis.append(el('dt','',k),el('dd','',v));
    pair('최근',`${p.since}부터 ${fmt(p.span_days)}일 동안 +${(p.gain/p.required*100).toFixed(2)}% (현재 레벨 기준)`);
    if(p.per_flask_percent!=null)pair('소재비 1개당',`약 +${p.per_flask_percent.toFixed(2)}% (이 캐릭터 재획 기록 ${fmt(p.flasks)}개 기준)`);
    box.append(basis);}
  (p.notes||[]).forEach(n=>box.append(el('p','hint',n)));
});
// 데이터 보관 — 데이터 위치, OS 보안 저장소의 키, 백업. 키 값은 보여 주지 않는다.
function renderDataPanel(d){
  const info=$('#data-info');info.replaceChildren();
  const pair=(k,v)=>{info.append(el('dt','',k),el('dd','',v));};
  pair('데이터 파일',`${d.data_file} (${fmt(d.size_kb)} KB)`);
  pair('API 키',`${d.key_store}에 따로 보관 · `+Object.entries(d.keys).map(([n,v])=>`${n} ${v===null?'읽기 실패':v?'있음':'없음'}`).join(' · '));
  const list=$('#backup-list');list.replaceChildren();
  list.append(el('p','hint',d.backups.length?`백업 ${d.backups.length}개 (최근 ${d.keep}개까지 보관) · ${d.folder}/backups`:'아직 백업이 없습니다.'));
  d.backups.forEach(b=>{const row=el('div','backup-row');row.append(el('span','',b.created.replace('T',' ').slice(0,16)),el('code','',b.name),el('small','',`${fmt(b.size_kb)} KB`));list.append(row);});
}
async function loadDataPanel(){renderDataPanel(await api('data'));}
$('#backup-now').onclick=e=>task(e.currentTarget,async()=>{const d=await api('data/backup',{});renderDataPanel(d);toast(`백업했어요: ${d.made}`);});
// 경매장 연결(mepiti/auction.py). 경매장 창에서 로그인하면 알아서 확인하고 노작값 자동 조회를 켠다.
let auctionTimer;
function renderAuction(a){
  const state=$('#auction-state'),text=$('#auction-status');
  $('#auction-open').disabled=$('#auction-check').disabled=!a.available;
  if(!a.available){state.textContent='쓸 수 없음';text.textContent=a.reason||'';return;}
  const ready=a.logged_in&&a.character&&!a.error;
  state.textContent=ready?'연결됨':a.open?'로그인 필요':'꺼짐';state.classList.toggle('gold',!!ready);
  text.textContent=a.error?a.error
    :ready?`${a.character}(${a.world}) 기준으로 검색합니다 · 오늘 남은 검색 ${a.remaining??'?'}회`
    :a.logged_in?'경매장에 로그인됐습니다. 연결 확인을 누르세요.'
    :a.open?'경매장 창에서 넥슨 로그인을 마쳐 주세요. 로그인하면 자동으로 확인합니다.':'경매장 창을 열어 넥슨에 로그인하세요.';
}
async function loadAuction(){renderAuction(await api('auction/status'));}
// 창을 연 뒤 로그인할 때까지(최대 5분) 상태를 지켜보다가, 경매장 화면이 뜨면 한 번 확인한다.
function watchAuction(tries=100){clearTimeout(auctionTimer);auctionTimer=setTimeout(()=>guard(async()=>{
  const a=await api('auction/status');renderAuction(a);
  if(a.logged_in&&!a.character){renderAuction(await api('auction/check',{}));await loadPrices();return;}
  if(a.open&&!a.logged_in&&tries>0)watchAuction(tries-1);}),3000);}
$('#auction-open').onclick=e=>task(e.currentTarget,async()=>{renderAuction(await api('auction/open',{}));watchAuction();});
$('#auction-check').onclick=e=>task(e.currentTarget,async()=>{renderAuction(await api('auction/check',{}));await loadPrices();});
$('#price-fetch').onchange=e=>guard(async()=>{await api('prices/fetch',{enabled:e.target.checked});await loadPrices();});
$('#key-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{const status=$('#key-card-status');if(await connectKey(e.target,status)){$('#account-characters').replaceChildren();switchView('characters');}else throw new Error(status.textContent);});};
$('#delete-key').onclick=e=>task(e.currentTarget,async()=>{if(!confirm('저장된 API 키를 삭제합니다.'))return;await api('settings/key/delete',{});resetCharacterCache();$('#account-characters').replaceChildren();$('#account-status').textContent='키 삭제됨 · 설정에서 등록하세요';await loadStatus();toast('키를 삭제했습니다.');});
$('#model-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{await api('settings/model',formData(e.target));await loadStatus();toast('모델을 저장했습니다.');});};
$('#pull-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{await api('model/pull',formData(e.target));pollDownload();});};
function progressText(d){return (d.status||'대기')+(d.total?` · ${Math.round((d.completed||0)/d.total*100)}% (${(d.completed/1e9).toFixed(2)} / ${(d.total/1e9).toFixed(2)} GB)`:'');}
async function pollDownload(){clearTimeout(downloadTimer);try{const d=await api('download');$$('.download-status').forEach(e=>e.textContent=progressText(d));$('#pull-form button').disabled=d.running;$$('.preset-card button').forEach(b=>b.disabled=d.running);if(d.running)downloadTimer=setTimeout(pollDownload,1500);else await loadStatus();}catch(e){toast(e.message,true);$('#pull-form button').disabled=false;}}
// AI 모델 선택: 클라우드(Gemini 무료, Claude·ChatGPT 유료)·2B·8B. 앱이 정하지 않고 사용자가 고른다(설치 마법사·첫 실행·설정).
// 첫 실행 카드와 설정 화면이 같은 선택 화면(modelChooser)을 쓴다.
const CLOUD='gemini';
const b=(text)=>el('b','',text),code=(text)=>el('code','',text),link=(url,text)=>sourceLink(url,text);
// 클라우드마다 키 받는 법. 유료(Claude·ChatGPT)는 선불 크레딧이 필요하고 구독(Pro·Plus)과는 별개다.
const CLOUDS={
  gemini:{name:'Gemini',company:'Google',title:'Gemini API 키 받기 (무료)',
    steps:[[link('https://aistudio.google.com/apikey','Google AI Studio ↗'),'에 들어가 Google 계정으로 로그인하세요.'],
           [b('Get API key'),'(API 키 받기) → ',b('Create API key'),'(API 키 만들기)를 누르세요. 약관 창이 뜨면 동의하세요.'],
           ['만들어진 키(',code('AIza'),'로 시작하는 긴 글자)를 복사해 아래 칸에 붙여 넣으세요.']],
    // 문구 근거: Gemini API 추가 약관(무료 서비스) — 제공·개선에 사용, 사람이 검토할 수 있음, 만 18세 이상. 2026-09-29 확인.
    hint:'결제 정보는 넣지 않아도 돼요. 질문과 캐릭터 정보가 Google로 전송되고, 무료 등급에서는 Google이 제품 개선에 쓰거나 사람이 검토할 수 있어요. Google 약관상 만 18세 이상만 쓸 수 있어요. 키는 이 컴퓨터의 보안 저장소에만 보관돼요.'},
  claude:{name:'Claude',company:'Anthropic',title:'Claude API 키 받기 (유료)',
    steps:[[link('https://platform.claude.com/settings/keys','Claude 개발자 콘솔 ↗'),'에 들어가 로그인하세요(처음이면 가입).'],
           [b('결제(Billing)'),'에서 크레딧을 충전하세요. 선불이며 Claude Pro 구독과는 별개예요.'],
           [b('API Keys'),' → ',b('Create Key'),'를 누르고 이름(예: 메피티)을 적으세요.'],
           ['만들어진 키(',code('sk-ant-'),'로 시작)를 복사해 아래 칸에 붙여 넣으세요. 키는 만들 때 한 번만 보여요.']],
    hint:'질문과 캐릭터 정보가 Anthropic으로 전송돼요. 쓴 만큼 크레딧에서 빠져요(질문 1번에 대략 8~40원, 모델에 따라). 키는 이 컴퓨터의 보안 저장소에만 보관돼요.'},
  openai:{name:'ChatGPT',company:'OpenAI',title:'OpenAI API 키 받기 (유료)',
    steps:[[link('https://platform.openai.com/api-keys','OpenAI 개발자 플랫폼 ↗'),'에 들어가 로그인하세요(ChatGPT 계정으로도 돼요).'],
           [b('Billing'),'에서 크레딧을 충전하세요. 선불이며 ChatGPT Plus 구독과는 별개예요.'],
           [b('API keys'),' → ',b('Create new secret key'),'를 누르세요.'],
           ['만들어진 키(',code('sk-'),'로 시작)를 복사해 아래 칸에 붙여 넣으세요. 키는 만들 때 한 번만 보여요.']],
    hint:'질문과 캐릭터 정보가 OpenAI로 전송돼요. 쓴 만큼 크레딧에서 빠져요(질문 1번에 대략 1~85원, 모델에 따라). 키는 이 컴퓨터의 보안 저장소에만 보관돼요.'},
};
// cloudFormOpen: 키 입력을 연 클라우드 이름(없으면 null). cloudFormUse: 연결하면 바로 사용 모델로 정할지.
let setupSkipped=false,setupAutoStarted=false,ollamaTimer,cloudFormOpen=null,cloudFormUse=false,localHelpOpen=false;
function rerenderModels(){if(lastStatus){renderPresets(lastStatus);renderSetup(lastStatus);}}
function cloudModelName(s,provider){const c=(s.clouds||{})[provider]||{};const hit=(c.choices||[]).find(x=>x.id===(c.chosen||c.model));return hit?hit.label.split(' · ')[0]:(c.model||'');}
function modelLabel(s){const p=s.selected_model;return CLOUDS[p]?`${CLOUDS[p].name} · ${cloudModelName(s,p)}`:p;}
function openCloudForm(provider,use){
  cloudFormOpen=provider;cloudFormUse=use;rerenderModels();
  // 입력칸이 카드들 아래에 생기므로 보이게 하고 바로 붙여 넣게 둔다.
  const form=[...$$('.cloud-key')].find(e=>e.offsetParent);if(form){form.scrollIntoView({block:'nearest',behavior:'smooth'});form.querySelector('input').focus({preventScroll:true});}
}
async function choosePreset(id){
  const r=await api('model/preset',{id});
  if(r.need_key){openCloudForm(r.provider||CLOUD,true);return;}   // 그 회사 키부터 받는다
  if(r.selected){toast(`${CLOUDS[r.selected]?CLOUDS[r.selected].name:r.selected}를 사용합니다.`);await loadStatus();}else pollDownload();
}
function presetCard(p,s){
  const busy=s.download.running,card=el('div','preset-card'+(p.id==='cloud'?' cloud':'')+(p.selected?' selected':''));
  const top=el('div','preset-top');top.append(el('strong','',p.label));
  if(p.recommended)top.append(el('span','badge gold','추천'));else if(p.fits_device)top.append(el('span','badge','이 기기에 맞음'));
  if(p.selected)top.append(el('span','badge','사용 중'));else if(p.installed)top.append(el('span','badge',p.cloud?'키 연결됨':'받아 둠'));
  card.append(top,el('p','preset-fits',p.fits),el('p','hint',p.note));
  const meta=el('div','preset-meta');
  (p.cloud?['설치 없음','인터넷 필요',p.license]:[`모델 ${p.model}`,`내려받기 ${p.download_gb}GB`,`메모리 약 ${p.memory_gb}GB`,p.license]).forEach(t=>meta.append(el('span','',t)));
  card.append(meta);
  const localBlocked=!p.cloud&&!s.model.connected;      // 로컬은 Ollama가 떠 있어야 받고 쓸 수 있다
  const label=p.selected?'사용 중':p.cloud?(p.installed?'이 모델 쓰기':`${CLOUDS[p.model].name} 키 넣고 쓰기`):localBlocked?'Ollama 필요':p.installed?'이 모델 쓰기':`받고 쓰기 (${p.download_gb}GB)`;
  const button=el('button',p.selected?'secondary':'primary',label);button.type='button';button.disabled=p.selected||(busy&&!p.cloud);
  button.onclick=()=>{if(localBlocked){localHelpOpen=true;rerenderModels();return;}task(button,()=>choosePreset(p.id));};
  card.append(button);return card;
}
// 키 받는 법과 입력칸. 키는 그 회사에 먼저 확인하고, 거절된 키는 저장하지 않는다.
function cloudKeyForm(provider){
  const info=CLOUDS[provider],box=el('div','cloud-key');box.append(el('h3','',info.title));
  const steps=el('ol','key-steps');info.steps.forEach(parts=>{const li=el('li');parts.forEach(x=>li.append(typeof x==='string'?document.createTextNode(x):x));steps.append(li);});box.append(steps);
  const form=el('form','key-card-form');const input=el('input');input.type='password';input.name='key';input.required=true;input.autocomplete='off';input.maxLength=300;input.placeholder=`복사한 ${info.name} API 키 붙여 넣기`;input.setAttribute('aria-label',`${info.name} API 키`);
  const submit=el('button','primary','연결');submit.type='submit';form.append(input,submit);box.append(form);
  const status=el('p','notice');status.hidden=true;status.setAttribute('role','status');box.append(status);
  box.append(el('p','hint',info.hint));
  const cancel=el('button','secondary','닫기');cancel.type='button';cancel.onclick=()=>{cloudFormOpen=null;rerenderModels();};box.append(cancel);
  form.onsubmit=e=>{e.preventDefault();task(submit,async()=>{
    status.hidden=true;let r;
    try{r=await api('cloud/key/connect',{provider,key:input.value,use:cloudFormUse});}
    catch(err){status.textContent=err.message;status.hidden=false;return;}          // 거절된 키는 저장하지 않았다
    cloudFormOpen=null;
    toast(r.state==='ok'?`${info.name}를 연결했어요(${r.model}).${cloudFormUse?' 이제 답변에 씁니다.':''}`:`키를 저장했어요. 지금은 ${info.company}에서 확인하지 못했어요(${r.message}).`);
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
// 화면 위쪽 모델 선택. 어느 탭에서든 눌러 회사·모델을 바로 바꾼다. 키가 없는 회사를 고르면 설정의 키 입력으로 간다.
function closeModelMenu(){$('#model-menu').hidden=true;$('#model-pill').setAttribute('aria-expanded','false');}
function renderModelMenu(s){
  const menu=$('#model-menu');menu.replaceChildren();
  const pick=async(provider,model)=>{
    const r=await api('model/select',{provider,model});closeModelMenu();
    if(r.need_key){switchView('settings');requestAnimationFrame(()=>openCloudForm(r.provider,true));toast(`${CLOUDS[r.provider].name} API 키를 넣으면 바로 씁니다.`);return;}
    await loadStatus();toast(`${modelLabel(lastStatus)}로 바꿨어요.`);
  };
  const item=(label,current,onclick,hint)=>{const b=el('button','model-menu-item'+(current?' current':''));b.type='button';b.setAttribute('role','menuitemradio');b.setAttribute('aria-checked',current?'true':'false');
    b.append(el('span','model-menu-check',current?'✓':''),el('span','',label));if(hint)b.append(el('small','',hint));b.onclick=()=>guard(onclick);return b;};
  Object.entries(s.clouds||{}).forEach(([provider,c])=>{
    const info=CLOUDS[provider];if(!info)return;
    const head=el('div','model-menu-head');head.append(el('strong','',info.name),el('span','',provider===CLOUD?'무료':'유료'));
    if(!c.key_present)head.append(el('span','model-menu-need','키 필요'));
    menu.append(head);
    (c.choices||[]).forEach(x=>{const [name,...rest]=x.label.split(' · ');menu.append(item(name,s.selected_model===provider&&c.chosen===x.id,()=>pick(provider,x.id),rest.filter(r=>r!=='기본').join(' · ')));});
  });
  const local=s.model.models||[];const head=el('div','model-menu-head');head.append(el('strong','','로컬'),el('span','','내 컴퓨터'));menu.append(head);
  if(local.length)local.forEach(name=>menu.append(item(name,s.selected_model===name,()=>pick('local',name))));
  else menu.append(el('p','model-menu-empty',s.model.connected?'받아 둔 로컬 모델이 없어요.':'Ollama가 꺼져 있어요.'));
  const more=el('button','model-menu-more','AI 모델 설정 열기');more.type='button';more.onclick=()=>{closeModelMenu();switchView('settings');};menu.append(more);
}
$('#model-pill').onclick=e=>{e.stopPropagation();const menu=$('#model-menu');
  if(!menu.hidden){closeModelMenu();return;}
  if(lastStatus)renderModelMenu(lastStatus);menu.hidden=false;$('#model-pill').setAttribute('aria-expanded','true');
  guard(loadStatus);};                                    // 열 때 최신 상태로 다시 그린다
document.addEventListener('click',e=>{if(!$('#model-menu').hidden&&!e.target.closest('.model-switch'))closeModelMenu();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('#model-menu').hidden){closeModelMenu();$('#model-pill').focus();}});
function modelChooser(s){
  const parts=[],grid=el('div','model-presets');(s.presets||[]).forEach(p=>grid.append(presetCard(p,s)));parts.push(grid);
  if(cloudFormOpen)parts.push(cloudKeyForm(cloudFormOpen));
  // 키를 넣어 둔 클라우드마다: 연결 상태, (유료는) 모델 고르기, 키 바꾸기·삭제.
  Object.entries(s.clouds||{gemini:s.cloud||{}}).forEach(([provider,c])=>{
    const info=CLOUDS[provider];if(!info)return;
    if(c.vault_error)parts.push(el('p','notice',c.vault_error));
    if(!c.key_present||cloudFormOpen===provider)return;
    const row=el('div','cloud-row');row.append(el('span','',`${info.name} 키 연결됨`));
    if((c.choices||[]).length){
      const pick=el('select');pick.setAttribute('aria-label',`${info.name} 모델`);
      c.choices.forEach(x=>{const o=el('option','',x.label);o.value=x.id;o.selected=x.id===(c.chosen||c.model);pick.append(o);});
      pick.onchange=()=>guard(async()=>{await api('cloud/model',{provider,model:pick.value});await loadStatus();toast(`${info.name} 모델을 바꿨어요.`);});
      row.append(pick);
    }
    const change=el('button','secondary','키 바꾸기');change.type='button';change.onclick=()=>openCloudForm(provider,s.selected_model===provider);
    const remove=el('button','secondary','키 삭제');remove.type='button';remove.onclick=()=>task(remove,async()=>{if(!confirm(`저장된 ${info.name} API 키를 삭제합니다.`))return;await api('cloud/key/delete',{provider});await loadStatus();toast(`${info.name} 키를 삭제했습니다.`);});
    row.append(change,remove);parts.push(row);
  });
  if(s.model.connected)clearInterval(ollamaTimer);
  else if(localHelpOpen||(s.ollama_setup||{}).running)parts.push(ollamaHelp(s));
  return parts;
}
function renderPresets(s){$('#model-presets').replaceChildren(...modelChooser(s));}
// 처음 실행하면 넥슨 API 키부터 연결하게 안내한다. 캐릭터·장비·기록이 모두 이 키로 조회된다.
// 키 상태: missing(없음) · vault_error(보안 저장소를 못 읽음 — 키가 있을 수도 있다) · invalid(넥슨이 거절: 만료·삭제 등)
// · connected(연결됨, 설정의 '발급 방법 보기'로 연 경우만 보인다). 인터넷·한도·점검으로 확인 못 한 경우는 카드를 띄우지 않는다.
let keySkipped=false,keyOpened=false,keyState=null,keyChecking=false,lastStatus=null,keyRequest=0;
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
  const request=++keyRequest;
  keyChecking=true;renderKeyCard(s);
  try{const state=(await api('settings/key/check',{})).state;if(request===keyRequest)keyState=state;}
  catch{if(request===keyRequest)keyState='unverified';}
  finally{if(request===keyRequest)keyChecking=false;}
  if(request!==keyRequest)return;
  renderKeyCard(lastStatus||s);if(typeof maybeStartTour==='function')maybeStartTour(lastStatus||s);
}
async function connectKey(form,status){
  status.hidden=true;
  let result;
  try{result=await api('settings/key/connect',formData(form));}
  catch(err){status.textContent=err.message;status.hidden=false;return false;}  // 넥슨이 거절한 키는 저장하지 않았다
  form.reset();resetCharacterCache();keyState=result.state;keyOpened=false;
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
  if(chosen&&wasHidden){if(chosen.cloud&&!chosen.installed){cloudFormOpen=chosen.model;cloudFormUse=true;}if(!chosen.cloud&&!s.model.connected)localHelpOpen=true;}
  card.replaceChildren(el('h2','','AI 모델 준비'));
  card.append(el('p','',chosen?(chosen.cloud?`설치할 때 ${CLOUDS[chosen.model].name}를 골랐어요. 아래에 API 키를 넣으면 바로 쓸 수 있어요.`
                                            :`설치할 때 고른 ${chosen.label} 모델을 받습니다. 끝나면 바로 쓸 수 있습니다.`)
                               :'답변을 쓸 AI를 고르세요. 설치할 것이 없는 클라우드를 추천해요. 나중에 설정에서 바꿀 수 있습니다.'));
  card.append(...modelChooser(s));
  card.append(el('p','download-status',s.download.running||s.download.status?progressText(s.download):''));
  const later=el('button','secondary','나중에');later.type='button';later.onclick=()=>guard(async()=>{setupSkipped=true;cloudFormOpen=null;await api('model/setup/skip',{});card.hidden=true;if(typeof maybeStartTour==='function')maybeStartTour(s);});card.append(later);
  if(chosen&&!chosen.cloud&&!chosen.installed&&s.model.connected&&!s.download.running&&!setupAutoStarted){setupAutoStarted=true;guard(()=>choosePreset(chosen.id));}
}
// 처음 켤 때 로딩 화면. 첫 자료(대화 목록·상태)를 받을 때까지 가리고, 실패하면 빈 화면 대신 이유와 '다시 시도'를 보여 준다.
function bootDone(){const boot=$('#boot');boot.classList.add('done');setTimeout(()=>boot.hidden=true,250);}
async function boot(){
  const text=$('#boot-text'),retry=$('#boot-retry');retry.hidden=true;$('#boot').classList.remove('failed');
  text.textContent='준비하고 있어요';
  const slow=setTimeout(()=>{text.textContent='AI 연결·컴퓨터 사양을 확인하고 있어요';},2500);
  try{
    const r=await fetch('/api/bootstrap');const b=await r.json();token=b.token;if(!token)throw new Error('앱 연결에 실패했습니다.');
    const [,status]=await Promise.all([loadHistory(),loadStatus()]);
    switchView(location.hash.slice(1)||'chat');bootDone();
    checkSavedKey(status);setTimeout(()=>guard(checkUpdate),2500);
  }catch(e){
    // 브라우저 내부 오류 문구(영어)는 보여 주지 않는다.
    $('#boot').classList.add('failed');text.textContent='메피티에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요. 계속되면 메피티를 껐다 켜 주세요.';retry.hidden=false;
  }finally{clearTimeout(slow);}
}
$('#boot-retry').onclick=()=>boot();
boot();

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
  if(accountLoading)return;const request=++catalogRequest;accountLoading=true;const button=$('#discover-characters');button.disabled=true;$('#account-status').textContent='목록 조회 중…';
  try{const found=await api('characters/discover',{});if(request!==catalogRequest)return;accountCatalog=found;$('#account-status').textContent=`캐릭터 ${accountCatalog.characters.length}개 · 조회 ${accountCatalog.retrieved_at}`;renderAccountCharacters();autoSelectCharacter();}
  catch(e){if(request!==catalogRequest)return;accountCatalog=null;$('#account-characters').replaceChildren();$('#account-status').textContent='목록 조회 실패: '+e.message+' 아래에서 이름으로 직접 등록할 수 있습니다.';if(!selectedCharacterName)showProfileEmpty(e.message);}
  finally{if(request===catalogRequest){accountLoading=false;button.disabled=false;}}
}
$('#discover-characters').onclick=discoverCharacters;
$('#account-search').oninput=renderAccountCharacters;

{const select=$('#font-scale');if(select){select.value=fontScale;select.onchange=()=>{const v=applyFontScale(select.value);try{localStorage.setItem('mepiti-font-scale',v);}catch{}toast('글씨 크기를 바꿨어요.');};}}

// 시세표 이미지로 노작값 넣기 — 클라우드 모델이 이름·가격을 읽고(저장 안 함), 사용자가 확인·수정·선택한 것만 저장한다.
async function imageDataUrl(file){
  if(!file||!/^image\/(png|jpeg|webp)$/.test(file.type))throw new Error('PNG·JPG·WEBP 이미지를 넣어 주세요.');
  let url=await new Promise((ok,no)=>{const r=new FileReader();r.onload=()=>ok(r.result);r.onerror=no;r.readAsDataURL(file);});
  if(url.length>7_500_000){const img=await new Promise((ok,no)=>{const i=new Image();i.onload=()=>ok(i);i.onerror=no;i.src=url;});
    const scale=Math.min(1,2560/Math.max(img.width,img.height)),c=document.createElement('canvas');c.width=img.width*scale;c.height=img.height*scale;
    c.getContext('2d').drawImage(img,0,0,c.width,c.height);url=c.toDataURL('image/jpeg',0.92);}
  return url;
}
async function readPriceTable(file){
  const drop=$('#price-drop'),box=$('#price-review');
  const url=await imageDataUrl(file);drop.querySelector('.capture-body').textContent='읽는 중…';box.replaceChildren();
  let d;try{d=await api('prices/image',{image:url});}finally{drop.querySelector('.capture-body').textContent='여기를 누르고 붙여 넣기';}
  renderPriceReview(d);
}
function renderPriceReview(d){
  const box=$('#price-review');box.replaceChildren();
  if(!d.rows.length){box.append(el('p','hint','표에서 아이템과 가격을 찾지 못했어요.'));return;}
  box.append(el('p','hint',`${fmt(d.rows.length)}줄 읽음`+(d.unit?` · 표 단위 ${d.unit}`:' · 표에 단위가 없어 숫자는 억으로 읽었어요')+(d.server?` · ${d.server}`:'')+' · 저장 전에 이름·가격을 확인하세요.'));
  const prefixRow=el('div','inline-form');const prefix=el('input');prefix.placeholder="앞에 붙일 이름(예: 아케인셰이드) — 표에 세트 이름이 없을 때";prefix.maxLength=40;
  const apply=el('button','secondary','모든 이름 앞에 붙이기');apply.type='button';prefixRow.append(prefix,apply);box.append(prefixRow);
  const table=el('table','peer-table price-review-table');const head=el('tr');['저장','아이템 이름','가격','읽은 글자'].forEach(h=>head.append(el('th','',h)));table.append(head);
  const rows=d.rows.map(r=>{
    const tr=el('tr');const check=el('input');check.type='checkbox';check.checked=!!r.price;
    const name=el('input');name.value=r.item;name.maxLength=100;const price=el('input');price.value=r.price?exactAmount(r.price):'';price.placeholder='예: 32(억)';
    tr.append(Object.assign(el('td'),{}),el('td'),el('td'),el('td','hint',r.price_text));tr.children[0].append(check);tr.children[1].append(name);tr.children[2].append(price);table.append(tr);
    return {check,name,price};
  });
  apply.onclick=()=>{const p=prefix.value.trim();if(!p)return;rows.forEach(r=>{if(!r.name.value.startsWith(p))r.name.value=`${p} ${r.name.value}`;});};
  box.append(table);
  const save=el('button','primary','고른 값 저장');save.type='button';
  save.onclick=()=>task(save,async()=>{
    const picked=rows.filter(r=>r.check.checked&&r.name.value.trim()&&r.price.value.trim()).map(r=>({item:r.name.value.trim(),price:r.price.value.trim()}));
    if(!picked.length)throw new Error('저장할 줄을 고르세요.');
    const res=await api('prices/bulk',{rows:picked,note:`시세표 이미지${d.server?' · '+d.server:''} · ${todayText()}`});
    toast(`노작값 ${fmt(res.saved)}개를 저장했어요.`);box.replaceChildren();await loadPrices();
  });
  box.append(save);
}
{const drop=$('#price-drop');if(drop){
  drop.ondblclick=()=>$('#price-file').click();
  drop.ondragover=e=>{e.preventDefault();drop.classList.add('drag');};drop.ondragleave=()=>drop.classList.remove('drag');
  drop.ondrop=e=>{e.preventDefault();drop.classList.remove('drag');guard(()=>readPriceTable(e.dataTransfer.files[0]));};
  $('#price-file').onchange=()=>{const f=$('#price-file').files[0];$('#price-file').value='';if(f)guard(()=>readPriceTable(f));};
  document.addEventListener('paste',e=>{
    if($('#view-settings').hidden)return;if(e.target.closest&&e.target.closest('input,textarea'))return;
    const item=[...(e.clipboardData?.items||[])].find(i=>i.type.startsWith('image/'));if(!item)return;
    e.preventDefault();guard(()=>readPriceTable(item.getAsFile()));
  });
}}
