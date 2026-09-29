'use strict';
// 사용법 안내(온보딩 투어). 화면의 한 곳을 비추고 설명을 붙인다.
// 탭으로 넘어가는 단계는 비춘 탭을 직접 눌러야 넘어간다(다음 버튼도 같은 일을 한다).
// 끝냈는지는 서버 DB에 둔다. 앱 창(pywebview)은 브라우저 저장소가 다음 실행까지 남지 않을 수 있다.
const TOUR_STEPS = [
  {view:'chat', target:'[data-view="chat"]', title:'대화',
   text:'메피티와 이야기하는 곳이에요. 스타포스 기대값, 장비, 유니온 배치, 진행 중인 이벤트를 물어볼 수 있어요.'},
  {view:'chat', target:'#chat-form', title:'말로 물어보기',
   text:'"모자 22성까지 기대값 얼마야?"처럼 편하게 적으면 돼요. 강화 조건이나 노작값이 필요하면 메피티가 되물어요.'},
  {view:'chat', target:'#item-button', title:'장비 골라서 대화',
   text:'착용 장비를 하나 고르면 그 장비를 주제로 이어서 이야기해요. "21성은?"처럼 짧게 물어도 알아들어요.'},
  {view:'chat', target:'#model-pill', title:'AI 모델 바꾸기',
   text:'여기를 누르면 Gemini(무료)·Claude·ChatGPT·내 컴퓨터 모델 중에서 바로 바꿀 수 있어요.'},
  {view:'chat', target:'[data-view="characters"]', title:'캐릭터', click:true,
   text:'캐릭터 탭을 눌러 보세요.'},
  {view:'characters', target:['#character-profile .equipment-panel','#character-profile'], title:'캐릭터와 장비창',
   text:'게임 장비창과 같은 배치로 장비를 보여 줘요. 칸을 누르면 잠재능력·추가옵션을 볼 수 있고, 거기서 바로 대화를 시작할 수 있어요.'},
  {view:'characters', target:'[data-view="calculator"]', title:'수익', click:true,
   text:'수익 탭을 눌러 보세요.'},
  {view:'calculator', target:'#view-calculator .earnings-periods', title:'주·달 넘겨 보기',
   text:'지난주·지난달 수익도 ◀ ▶로 넘겨 볼 수 있어요. 아래에 캐릭터별 합계와 최근 12주·6개월 흐름이 나와요.'},
  {view:'calculator', target:'#view-calculator .earnings-forms', title:'재획·주보 기록',
   text:'재획에서 번 메소와 조각, 잡은 주간 보스를 캐릭터와 함께 적으면 수익을 계산해 쌓아 둬요. 결정석 가격은 공식 공지 기준이에요.'},
  {view:'calculator', target:'[data-view="library"]', title:'기록', click:true,
   text:'기록 탭을 눌러 보세요.'},
  {view:'library', target:'#view-library .history-toolbar', title:'스타포스 기록',
   text:'실제로 강화한 기록을 불러와, 장비마다 쓴 메소와 기대값을 비교해요. 운이 좋았는지 나빴는지 알 수 있어요.'},
  {view:'library', target:'[data-view="settings"]', title:'설정', click:true,
   text:'마지막으로 설정 탭을 눌러 보세요.'},
  {view:'settings', target:'#key-form', title:'넥슨 API 키',
   text:'캐릭터를 불러오려면 넥슨 Open API 키가 필요해요. 키는 이 컴퓨터의 보안 저장소에만 보관돼요.'},
  {view:'settings', target:'#replay-tour', title:'다 봤어요',
   text:'이 안내는 여기서 언제든 다시 볼 수 있어요. 이제 대화로 돌아가서 시작해 볼까요?'},
];

const tour = {index:-1, spot:null, card:null, started:false, timer:0, shown:null};

// 대상은 선택자 하나 또는 우선순위 목록. 앞의 것이 아직 없으면(캐릭터 장비를 불러오는 중 등) 다음 것을 쓴다.
function tourTarget(){
  const step=TOUR_STEPS[tour.index];if(!step)return null;
  for(const selector of [].concat(step.target)){const found=document.querySelector(selector);if(found&&found.getClientRects().length)return found;}
  return null;
}

function tourPlace(){
  const target=tourTarget();if(!target||!tour.card)return;
  // 대상이 바뀌면(내용이 늦게 떠서 더 알맞은 대상이 생김) 화면 가운데로 가져온다. 바닥에 붙으면 카드 자리가 없다.
  if(tour.shown!==target){tour.shown=target;target.scrollIntoView({block:target.offsetHeight<innerHeight*0.6?'center':'start'});}
  const r=target.getBoundingClientRect(),pad=6;
  Object.assign(tour.spot.style,{left:r.left-pad+'px',top:r.top-pad+'px',width:r.width+pad*2+'px',height:r.height+pad*2+'px'});
  const card=tour.card.getBoundingClientRect(),gap=14,margin=12;
  // 아래 자리가 모자라면 위, 둘 다 모자라면(큰 영역) 영역 안쪽 아래에 둔다.
  let top=r.bottom+gap;
  if(top+card.height>innerHeight-margin)top=r.top-gap-card.height;
  if(top<margin)top=Math.min(innerHeight-card.height-margin,Math.max(margin,r.bottom-card.height-gap));
  let left=r.left+r.width/2-card.width/2;
  // 왼쪽 메뉴를 비출 때는 메뉴 오른쪽에 붙인다.
  if(target.closest('.sidebar')){left=r.right+gap;top=Math.min(Math.max(margin,r.top+r.height/2-card.height/2),innerHeight-card.height-margin);}
  left=Math.min(Math.max(margin,left),innerWidth-card.width-margin);
  Object.assign(tour.card.style,{left:left+'px',top:top+'px'});
}

function tourShow(index){
  if(index<0)return;
  if(index>=TOUR_STEPS.length)return tourEnd(true);
  tour.index=index;const step=TOUR_STEPS[index];
  if(!$('#view-'+step.view)||$('#view-'+step.view).hidden)switchView(step.view);
  const target=tourTarget();
  if(!target){return tourShow(index+1);}             // 화면 구성이 바뀌어 대상이 없으면 건너뛴다
  const card=tour.card;card.replaceChildren();
  card.append(el('div','tour-count',`${index+1} / ${TOUR_STEPS.length}`),el('h3','',step.title),el('p','',step.text));
  const actions=el('div','tour-actions');
  const skip=el('button','tour-skip','건너뛰기');skip.type='button';skip.onclick=()=>tourEnd(true);
  const back=el('button','secondary','이전');back.type='button';back.disabled=index===0;back.onclick=()=>tourBack();
  const last=index===TOUR_STEPS.length-1;
  const next=el('button','primary',last?'시작하기':step.click?'눌러서 이동':'다음');next.type='button';
  next.onclick=()=>step.click?target.click():tourShow(index+1);
  actions.append(skip,el('span','tour-gap'),back,next);card.append(actions);
  tour.spot.classList.toggle('pulse',!!step.click);
  requestAnimationFrame(()=>{tourPlace();next.focus({preventScroll:true});});
}

function tourBack(){
  // '눌러서 넘어가는' 단계는 탭 전환만 하므로, 뒤로 갈 때는 그 앞의 설명 단계로 간다.
  let i=tour.index-1;while(i>0&&TOUR_STEPS[i].click)i--;tourShow(i);
}

// 안내 중에는 비춘 곳과 안내 카드만 누를 수 있다. 비춘 탭을 누르면 다음 단계로 간다.
function tourClick(e){
  if(tour.index<0)return;
  if(tour.card.contains(e.target))return;
  const target=tourTarget(),step=TOUR_STEPS[tour.index];
  if(step.click&&target&&target.contains(e.target)){setTimeout(()=>tourShow(tour.index+1),0);return;}
  e.preventDefault();e.stopPropagation();
}
function tourKey(e){
  if(tour.index<0)return;
  if(e.key==='Escape'){e.preventDefault();tourEnd(true);}
  else if(e.key==='ArrowRight'){e.preventDefault();const s=TOUR_STEPS[tour.index];s.click?tourTarget()?.click():tourShow(tour.index+1);}
  else if(e.key==='ArrowLeft'){e.preventDefault();tourBack();}
}

function tourStart(){
  if(tour.index>=0)return;
  tour.started=true;
  tour.spot=el('div','tour-spot');tour.card=el('div','tour-card');
  tour.card.setAttribute('role','dialog');tour.card.setAttribute('aria-live','polite');tour.card.setAttribute('aria-label','사용법 안내');
  document.body.append(tour.spot,tour.card);document.documentElement.classList.add('touring');
  document.addEventListener('click',tourClick,true);document.addEventListener('keydown',tourKey,true);
  addEventListener('resize',tourPlace);document.addEventListener('scroll',tourPlace,true);
  tour.timer=setInterval(tourPlace,250);   // 내용이 늦게 뜨거나 크기가 바뀌어도 따라간다
  tourShow(0);
}

function tourEnd(done){
  if(tour.index<0)return;
  tour.index=-1;tour.shown=null;clearInterval(tour.timer);tour.spot.remove();tour.card.remove();document.documentElement.classList.remove('touring');
  document.removeEventListener('click',tourClick,true);document.removeEventListener('keydown',tourKey,true);
  removeEventListener('resize',tourPlace);document.removeEventListener('scroll',tourPlace,true);
  switchView('chat');$('#message').focus();
  if(done)guard(()=>api('tour',{done:true}));
}

// 처음 실행하면 한 번 보여 준다. API 키 연결·AI 모델 준비 카드가 떠 있는 동안은 그것부터 끝내게 기다린다.
function tourBlocked(status){
  const key=$('#key-card');
  return status.tour_done||key.dataset.checking||!key.hidden||!$('#setup-card').hidden;
}
function maybeStartTour(status){
  if(tour.started||tourBlocked(status))return;
  // 켜자마자 저장된 키 확인이 이어서 시작될 수 있어, 잠깐 뒤 조건을 다시 본다.
  tour.started=true;setTimeout(()=>{tour.started=false;if(!tourBlocked(status))tourStart();},450);
}

$('#replay-tour').onclick=()=>tourStart();
