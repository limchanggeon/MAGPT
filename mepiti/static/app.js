'use strict';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let token = '', sessionId = null, busy = false, confirmedText = '', previewUrl = null, downloadTimer;
let accountCatalog = null, accountLoading = false, managedNames = new Set(), managedCharacters = [];
const titles = {chat:'질의',characters:'캐릭터',calculator:'계산',library:'자료',settings:'설정'};
const fmt = (n) => new Intl.NumberFormat('ko-KR',{maximumFractionDigits:3}).format(n);
const el = (tag,cls,text) => { const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e; };
function toast(message,error=false){const e=$('#toast');e.textContent=message;e.classList.toggle('error',error);e.hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>e.hidden=true,6500);}
async function api(path,data){const response=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{'X-Mepiti-Token':token,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw new Error(result.error||'요청에 실패했습니다.');return result;}
async function guard(fn){try{return await fn();}catch(e){toast(e.message,true);}}
async function task(button,fn){button.disabled=true;try{return await guard(fn);}finally{button.disabled=false;}}
function formData(form){return Object.fromEntries(new FormData(form));}
function sourceLink(url,text){const a=el('a','',text);try{const parsed=new URL(url);if(parsed.protocol==='https:'){a.href=url;a.target='_blank';a.rel='noreferrer noopener';}}catch{}return a;}
function switchView(view){if(!titles[view])view='chat';$$('.view').forEach(e=>e.hidden=e.id!=='view-'+view);$$('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));$('#page-title').textContent=titles[view];if(view==='characters')guard(loadCharacters);if(view==='calculator')guard(loadConversionMeta);if(view==='library')guard(loadDocuments);if(view==='settings'){guard(loadStatus);guard(loadPrices);}location.hash=view;}
$$('[data-view]').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.view)));
window.addEventListener('hashchange',()=>switchView(location.hash.slice(1)));
function scrollBottom(){$('#chat-scroll').scrollTop=$('#chat-scroll').scrollHeight;}
function renderMessage(role,payload){$('#welcome').hidden=true;$$('#messages .choice-form').forEach(lockChoiceForm);const block=el('article','message '+role);if(role==='user'){block.textContent=payload.content;}else{const heading=el('div','message-heading');const icon=el('img');icon.src='/favicon.svg';icon.alt='';heading.append(icon,el('span','','메피티'));const status={held:'보류',clarify:'조건 확인',evidence:'근거 원문',term:'용어',context:'조회한 사실',analysis:'캐릭터 분석',ask_price:'노작값 필요',price:'값 저장됨',ask_conditions:'조건 선택',conditions:'조건 저장됨'};heading.append(el('span','badge',status[payload.status]||'안내'));block.append(heading,el('div','message-body',payload.content));if(payload.form)block.append(renderChoiceForm(payload.form));if(payload.sources?.length){const sources=el('div','sources');payload.sources.forEach(s=>{const a=sourceLink(s.source_url,`[${s.citation}] ${s.title}`);a.className='source';a.append(el('small','',`${s.source_type==='official'?'공식':'커뮤니티'} · 버전 ${s.version}\n적용 ${s.effective_from} · 수집 ${s.retrieved_at}\n재검토 기한 ${s.valid_until}`));sources.append(a);});block.append(sources);}if(payload.facts){const facts=el('details','fact-sheet');facts.append(el('summary','','근거로 쓴 조회 사실'),el('pre','',payload.facts));block.append(facts);}if(payload.conditions?.length){const conditions=el('div','conditions');payload.conditions.forEach(c=>conditions.append(el('p','',c)));block.append(conditions);}}$('#messages').append(block);scrollBottom();}
async function loadHistory(){const sessions=await api('sessions');const list=$('#history');list.replaceChildren();if(!sessions.length)list.append(el('div','history-empty','기록 없음'));sessions.forEach(s=>{const row=el('div','history-entry'+(s.id===sessionId?' active':''));const open=el('button','',s.title);open.title=s.title;open.addEventListener('click',()=>guard(async()=>{if(busy)return;sessionId=s.id;$('#messages').replaceChildren();const messages=await api('messages?session_id='+s.id);messages.forEach(m=>renderMessage(m.role,m.payload));switchView('chat');loadHistory();}));const del=el('button','','×');del.setAttribute('aria-label',s.title+' 대화 삭제');del.addEventListener('click',()=>guard(async()=>{if(busy)return;if(!confirm('이 기록을 삭제합니다.'))return;await api('sessions/delete',{id:s.id});if(sessionId===s.id)newChat();await loadHistory();}));row.append(open,del);list.append(row);});}
function newChat(){if(busy)return;sessionId=null;$('#messages').replaceChildren();$('#welcome').hidden=false;confirmedText='';renderAttachment();switchView('chat');guard(loadHistory);$('#message').focus();}
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
    const result=await api('chat',{message,session_id:sessionId,...(answer?{answer}:{})});
    sessionId=result.session_id;thinking.remove();renderMessage('assistant',result);await loadHistory();
  }catch(err){thinking.remove();$('#messages').lastElementChild?.remove();$('#welcome').hidden=$('#messages').children.length>0;throw err;}
  finally{busy=false;$('#send-button').disabled=false;}
}
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
function calculator(form,kind){form.onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{const r=await api('calculate',{...formData(form),kind});const box=$('#calc-result');box.hidden=false;box.replaceChildren(el('h2','','계산 결과'));const stats=el('div','stat-grid');const values=kind==='probability'?[['기대 시도 횟수',fmt(r.expected_trials)+'회'],['기대 비용',fmt(r.expected_cost)+' 메소'],['누적 성공확률',fmt(r.success_probability*100)+'%'],['50% 도달 시도 횟수',fmt(r.median_trials)+'회']]:[['남은 수량',fmt(r.remaining)],['필요 기간',fmt(r.days)+'일']];values.forEach(([k,v])=>{const stat=el('div');stat.append(el('small','',k),el('strong','',v));stats.append(stat);});box.append(stats);r.assumptions.forEach(a=>box.append(el('p','hint',a)));box.append(el('p','hint','계산 규칙 버전 · '+r.version));box.scrollIntoView({behavior:'smooth',block:'nearest'});});};}
calculator($('#probability-form'),'probability');calculator($('#growth-form'),'growth');
// 주스탯 환산 — 커뮤니티 계산기 절차. 공식 수치가 아니므로 출처를 항상 함께 표시한다.
let conversionMeta=null;
async function loadConversionMeta(){
  if(conversionMeta)return conversionMeta;
  conversionMeta=await api('conversion');
  const job=$('#conversion-job');job.replaceChildren();
  conversionMeta.jobs.forEach(name=>{const o=el('option','',name);o.value=name;job.append(o);});
  const cool=$('#conversion-cooldown');cool.replaceChildren();
  conversionMeta.cooldowns.forEach(name=>{const o=el('option','',name);o.value=name;cool.append(o);});
  return conversionMeta;
}
function percent(value){return (value>=0?'+':'')+fmt(Math.round(value*1000)/10)+'%';}
function renderConversion(r){
  const box=$('#conversion-result');box.hidden=false;box.replaceChildren();
  const head=el('div','conversion-headline');
  head.append(el('span','','환산 주스탯'),el('strong','',fmt(r.converted_stat)),
              el('span','conversion-grade','평가 · '+r.grade.name+' ('+(r.grade.gap>=0?'+':'')+fmt(r.grade.gap)+')'));
  box.append(head);
  const rows=el('div','conversion-compare');
  if(r.stat_sheet_ratio!=null){const d=el('div');d.append(el('span','','스탯창 주스탯 대비'),el('strong','',percent(r.stat_sheet_ratio)));rows.append(d);}
  (r.benchmarks||[]).forEach(b=>{const d=el('div');d.append(el('span','',b.name+' 대비'),el('strong','',percent(b.ratio)));rows.append(d);});
  box.append(rows);
  const detail=el('div','conversion-detail');
  [['샤드 한 줄 데미지',fmt(r.shard_damage)],['점수',fmt(r.score)],['스탯퍼',fmt(r.derived.stat_percent*100)+'%'],
   ['순스탯',fmt(r.derived.pure_stat)],['공격력/마력',fmt(r.derived.attack)],['부스탯 합',fmt(r.derived.total_sub_stat)]]
   .forEach(([k,v])=>{const d=el('div');d.append(el('span','',k),el('strong','',v));detail.append(d);});
  box.append(detail);
  r.assumptions.forEach(a=>box.append(el('p','hint',a)));
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}
$('#conversion-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{
  const data=formData(e.target);data.boss_ability=!!e.target.elements.boss_ability.checked;
  renderConversion(await api('conversion',data));});};
// 넥슨 API가 주는 값만 채운다. 메용 적용 스탯처럼 API에 없는 값은 비워 둔다.
$('#conversion-fill').onclick=e=>task(e.currentTarget,async()=>{
  const name=selectedCharacterName||managedCharacters.find(c=>c.main)?.name;
  if(!name)throw new Error('캐릭터 화면에서 캐릭터를 먼저 선택하세요.');
  const data=profileCache.get(name)?.data||await api('characters/profile',{name});
  await loadConversionMeta();
  const form=$('#conversion-form'),pick=k=>data.stats?.find(s=>s.name===k)?.value;
  const num=v=>v==null?null:Number(String(v).replace(/[,%]/g,''));
  const set=(field,value)=>{if(value!=null&&Number.isFinite(value))form.elements[field].value=value;};
  if(conversionMeta.jobs.includes(data.job))form.elements.job.value=data.job;
  set('level',data.level);
  set('damage',num(pick('데미지')));
  set('boss_damage',num(pick('보스 몬스터 데미지')));
  set('defense_ignore',num(pick('방어율 무시')));
  set('critical_damage',num(pick('크리티컬 데미지')));
  set('stat_attack',num(pick('최종 스탯공격력'))||num(pick('스탯공격력')));
  const main=[['STR'],['DEX'],['INT'],['LUK']].map(([k])=>[k,num(pick(k))||0]).sort((a,b)=>b[1]-a[1]);
  set('plain_stat',main[0][1]);set('sub_stat',main[1][1]);
  const cool=num(pick('재사용 대기시간 감소 (초)'));
  if(cool!=null&&conversionMeta.cooldowns.includes(cool+'초'))form.elements.cooldown.value=cool+'초';
  else if(cool===0)form.elements.cooldown.value='노쿨감';
  toast(name+' 스탯을 가져왔습니다. 메용 적용 스탯과 심볼·유니온 값은 직접 입력하세요.');
});

async function loadDocuments(){const docs=await api('documents');$('#doc-count').textContent=docs.length;const list=$('#document-list');list.replaceChildren();if(!docs.length)list.append(el('div','empty-state','등록된 자료 없음.\n원문과 적용 조건을 등록하세요.'));docs.forEach(d=>{const m=d.metadata;const details=el('details','panel document-card');const summary=el('summary','',d.title);summary.append(el('span','badge',m.verification_status==='reviewed'?'검토 완료':'검토 대기'));details.append(summary,sourceLink(m.source_url,m.source_url),el('div','doc-meta',`출처 ${m.source_type==='official'?'공식':'커뮤니티'} · ${m.region}/${m.server_type} · 버전 ${m.version||'미확인'}\n적용 ${m.effective_from||'미확인'} ~ ${m.effective_to||'종료 미지정'} · 재검토 ${m.valid_until||'미지정'}\n발행 ${m.published_at||'미확인'} · 수정 ${m.modified_at||'미확인'} · 수집 ${m.retrieved_at}\n주제 ${m.topic||'미지정'} · SHA-256 ${m.content_hash}`),el('pre','',d.body));const approved=m.verification_status==='reviewed';const approve=el('button','primary',approved?'승인 취소':'검토 완료로 승인');approve.onclick=()=>task(approve,async()=>{await api('documents/review',{id:d.id,approve:!approved});await loadDocuments();toast(approved?'검색에서 제외했습니다.':'승인했습니다. 유효 기간에만 검색됩니다.');});const del=el('button','secondary','삭제');del.onclick=()=>guard(async()=>{if(!confirm('이 자료를 삭제합니다.'))return;await api('documents/delete',{id:d.id});await loadDocuments();});details.append(approve,del);list.append(details);});}
$('#document-form').onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{const data=formData(e.target);const {title,body,...metadata}=data;await api('documents',{title,body,metadata});e.target.reset();toast('검토 대기로 저장했습니다.');await loadDocuments();});};
async function loadStatus(){const s=await api('status');$('#doc-count').textContent=s.documents;const ready=s.model.connected&&s.selected_model&&s.model.models.includes(s.selected_model);const pill=$('#model-pill');pill.replaceChildren(el('span','dot'+(ready?'':' amber')),el('span','',ready?s.selected_model:s.model.connected?'모델 선택 필요':'모델 연결 필요'),el('span','','↗'));$('#model-status').textContent=s.model.connected?`Ollama 연결됨 · 모델 ${s.model.models.length}개`:'Ollama 연결 실패 · 로컬에서 실행하세요';const select=$('#model-select');select.replaceChildren();if(!s.model.models.length){const option=el('option','','설치된 모델 없음');option.value='';select.append(option);}s.model.models.forEach(name=>{const option=el('option','',name);option.value=name;option.selected=name===s.selected_model;select.append(option);});$('#key-status').textContent=s.vault_error|| (s.key_present?'키 저장됨 · 인증은 캐릭터 조회로 확인':'등록된 키 없음');const sys=s.system;const strip=$('#system-info');strip.replaceChildren();[`${sys.os} · ${sys.architecture}`,sys.ram_gb?`RAM ${sys.ram_gb} GB`:'RAM 미확인',`여유 공간 ${sys.disk_free_gb} GB`,sys.gpu||'GPU 미확인',sys.ocr_available?'OCR 엔진 감지됨':'OCR 설치 필요'].forEach(t=>strip.append(el('span','',t)));$('#storage-path').textContent=s.storage_path+' · 키는 OS 보안 저장소에 별도 보관';if(s.download.running)pollDownload();else if(s.download.status)$('#download-status').textContent=s.download.status;}
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
