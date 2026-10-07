import re
from . import agent, language, conditions, consult, context, notices, peers, planner, prices, starforce, statcalc, union
from .core import AppError, TERMS, normalize, now

# 캐릭터 자신에 대한 질문으로 볼 표현. 여기 걸리면 API 사실을 근거로 모델이 서술한다.
CHARACTER_INTENT = re.compile(
    r'내\s*캐릭|제\s*캐릭|내\s*장비|제\s*장비|내\s*스펙|제\s*스펙|내\s*성장|제\s*성장|'
    r'내\s*예산|제\s*예산|뭘\s*올|어디를?\s*올|어느\s*부위|다음\s*단계|스펙업|약한\s*부위|'
    r'추옵\s*(?:상태|등급)|보완|우선순위|비슷한\s*(?:유저|사람|스펙)|(?:칠흑|여명|광휘|보장|에테르넬|아케인|앱솔)\s*(?:세트)?\s*(?:맞추|맞춰|모으|모아|껴|끼)|목표\s*전투력|전투력대|(?:뭘로|뭐로|무엇으로|어떤\s*걸로)\s*(?:바꿔|바꾸|갈아)|남들\s*(?:은|는|이랑|과|보다)|다른\s*(?:유저|사람)들?\s*(?:은|는|이랑|과|보다|장비)')
# 게임 규칙은 검토된 자료에서만 나와야 한다. 확률 관련 표현은 문구 자체로 막는다.
# '성공 확률: 38.5%', '파괴 확률은 3%'처럼 사이에 기호나 조사가 끼어도 잡는다.
# 크리티컬 확률처럼 캐릭터 능력치에 있는 확률은 대상이 아니다.
FABRICATION = re.compile(r'\d+\s*%\s*(?:확률|성공|파괴)|(?:성공|파괴|강화|큐브|등급\s*업)\s*확률[^\n\d]{0,8}\d|파괴\s*확률')
# 금액·수치를 찾는 패턴. 서술에 나온 값은 모두 넘겨준 사실 안에 있어야 한다.
AMOUNT = re.compile(r'(\d[\d,]*(?:\.\d+)?)\s*(조|억|만(?!큼))?(?!당)')    # '+28.45만큼'의 '만'은 단위가 아니다
PERCENT = re.compile(r'(\d+(?:\.\d+)?)\s*%')
UNIT_SCALE = {'조':1_0000_0000_0000,'억':1_0000_0000,'만':1_0000,None:1}
# 값을 따져야 답할 수 있는 질문. 노작값을 모르면 지어내지 말고 되물어야 한다.
PRICE_INTENT = re.compile(r'노작|시세|가격|얼마|값이|사는\s*게|살까|구매|바꾸는\s*게|'
                          r'가성비|예산|이득|싸[냐게]|비싸')
# 유니온·다음 육성 질문. 계정 캐릭터 목록과 공격대원 효과 표로 앱이 직접 추천한다.
UNION_INTENT = re.compile(r'유니온|공격대원|뭐\s*키우|뭘\s*키우|뭐\s*키울|뭘\s*키울|다음에?\s*(?:뭐|뭘|어떤)\s*(?:캐릭|직업)|'
                          r'키울\s*(?:캐릭|직업)|육성\s*추천|부캐\s*(?:추천|뭐)')
# 진행 중 이벤트 질문. 넥슨 공지(진행 중 이벤트 목록)로 답한다.
EVENT_INTENT = re.compile(r'진행\s*중인?\s*이벤트|이벤트\s*(?:뭐|뭣|언제|기간|목록|있|하)|샤타\s*(?:언제|하[나냐니는]|해\?|기간|중)|'
                          r'샤이닝|썬데이\s*메이플|이번\s*주\s*이벤트')
# 장비 값 자체를 묻는 표현. '기대값이 얼마야'의 '얼마'는 여기에 들지 않는다.
ITEM_PRICE = re.compile(r'노작|시세|가격|사는\s*게|살까|구매|바꾸는\s*게|가성비|이득|싸[냐게]|비싸')
# 되물은 노작값을 건너뛰겠다는 답.
PRICE_SKIP = re.compile(r'없어도|필요\s*없|몰라도|상관\s*없|괜찮|넘어가|건너뛰|스킵|skip|패스|몰라|모름|모르겠', re.I)
# 강화 기대값 질문. 목표 성을 함께 찾는다.
# 비슷한 유저와 비교하거나 무엇부터 바꿀지 묻는 질문 — 모아 둔 비슷한 유저 장비 통계를 사실로 넘긴다(mepiti/peers.py).
PEER_INTENT = re.compile(r'비슷한|남들|(?:칠흑|여명|광휘|보장|에테르넬|아케인|앱솔)\s*(?:세트)?\s*(?:맞추|맞춰|모으|모아|껴|끼)|목표\s*전투력|전투력대|(?:뭘로|뭐로|무엇으로|어떤\s*걸로)\s*(?:바꿔|바꾸|갈아)|다른\s*(?:사람|유저|분)|평균|추천|뭐\s*(?:부터|를)?\s*(?:바꿔|바꾸|올려|올리)|뭘\s*(?:바꿔|바꾸|올려|올리)|'
                         r'어디\s*(?:부터|를)|우선\s*순위|먼저\s*(?:바꿔|바꾸|해야|올려)|부족한')
STARFORCE_INTENT = re.compile(r'기대\s*값|기댓값|강화\s*비용|몇\s*번|스타포스|(\d+)\s*성')
TARGET_STAR = re.compile(r'(\d{1,2})\s*성')
# 장비 부위 이름. 값·교체 질문에 부위가 나오면 내 캐릭터 이야기로 본다.
SLOT_WORDS = re.compile('|'.join(sorted(
    (set(context.SLOT_ORDER) | {'장비','템','아이템','방어구','장신구','무기','반지','펜던트'}),
    key=len, reverse=True)))
# 사용자가 '골든 클로버 벨트 32억' 처럼 알려 주는 형태.
# 금액으로 볼 만한 표기. 단위가 있거나 10만 이상인 숫자만 값으로 받는다('22성' 같은 숫자를 걸러낸다).
MONEY = re.compile(r'\d[\d,.]*\s*(?:조|억|천|만)|\d{6,}')
PRICE_REPLY = re.compile(r'^(?P<item>.+?)\s*[:=]?\s*(?P<price>[\d,.]+\s*(?:조|억|만)?(?:\s*\d+\s*(?:억|만))?)\s*(?:메소)?$')


def answer(store, model, data, nexon=None, peer_runner=None):
    question = data.get('message','')
    if not isinstance(question,str) or not question.strip() or len(question)>12000:
        raise AppError('질문은 1~12,000자로 입력해 주세요.')
    question = said = question.strip()
    new_topic = clean_topic(data.get('topic')) if not data.get('session_id') else None
    title = f"{new_topic['slot']} · {new_topic['name']}" if new_topic else question
    sid = store.session(data.get('session_id'), title, new_topic)
    # 장비를 주제로 연 대화면, 부위를 말하지 않은 질문은 그 장비 이야기로 본다.
    topic = store.session_topic(sid)
    history = store.messages(sid)
    last = next((m['payload'] for m in reversed(history) if m['role']=='assistant'), None) or {}
    # 되물었던 원래 질문. 조건·노작값을 답하면 이 질문을 이어서 계산한다.
    pending = last.get('pending') if last.get('status') in ('ask_conditions','ask_price') else None
    # 재질문(부위·목표 성·목표 전투력·모호한 질문)에 답했으면 원래 질문에 그 답을 붙여 이어서 처리한다.
    if last.get('status') == 'ask' and last.get('pending') and not data.get('answer'):
        question = f"{last['pending']} {question}"
    asked_before = last.get('status') == 'ask'
    notes = []
    previous = [m['payload']['content'] for m in history if m['role']=='user'][-2:]
    # Preserve only a short prior topic for explicitly elliptical follow-ups.
    followup = bool(re.match(r'^(그럼|그러면|이벤트 때|그거|그건|같은|이 경우)',question))
    # 목표 전투력대 비교 상담 중이면 짧은 후속 질문('반지는?', '그 다음은?', '칠흑 맞추면?')도 상담으로 이어 간다.
    consulting = bool(last.get('consult')) and not EVENT_INTENT.search(question) and not UNION_INTENT.search(question) \
        and not re.search(r'기대\s*값|기댓값', question)
    query = '\n'.join(previous+[question]) if followup else question
    result = {'content':'','status':'held','sources':[],'conditions':[],'created_at':now(),'session_id':sid}
    structured = data.get('answer')
    if structured is not None and not isinstance(structured, dict):
        raise AppError('선택한 답의 형식이 올바르지 않습니다.')
    kind = (structured or {}).get('kind')
    if structured is not None and kind not in ('conditions','price'):
        raise AppError('선택한 답의 종류를 알 수 없습니다.')
    if not structured and conditions.RESET.search(question):
        conditions.clear(store)
        result.update(status='ask_conditions', content=conditions.ask_text(),
                      form=conditions.form(conditions.DEFAULTS))
        prior = last_starforce(history)
        if prior:
            result['pending'] = f"{prior['slot']} {prior['target_star']}성 기대값"
        store.message(sid,'user',{'content':question})
        store.message(sid,'assistant',result)
        return result
    if kind == 'conditions':
        picked = conditions.from_answer(structured.get('values'))
        conditions.save(store, picked)
    else:
        picked = None if structured else capture_conditions(store, question, history)
    if picked:
        prior = last_starforce(history)
        if pending and nexon:
            # 조건을 묻게 만든 원래 질문을 이어서 계산한다.
            notes.append('강화 조건을 저장했습니다: ' + conditions.summary(picked)
                         + '. 바꾸려면 "강화 조건 다시"라고 적어 주세요.')
            question = pending
        elif prior and nexon:
            # '샤타포스일때는' 같은 후속 질문. 바뀐 조건으로 직전 계산을 다시 돌린다.
            question = f"{prior['slot']} {prior['target_star']}성 기대값"
        else:
            result.update(status='conditions',content='강화 조건을 저장했습니다. 다음부터는 묻지 않습니다.\n\n'
                          + conditions.summary(picked) + '\n\n바꾸려면 "강화 조건 다시"라고 적어 주세요.')
            result['conditions'] = ['이 조건으로 기대값을 계산합니다. 이벤트는 기간이 지나면 다시 알려 주세요.']
            store.message(sid,'user',{'content':said})
            store.message(sid,'assistant',result)
            return result
    skip_prices = False
    if last.get('status') == 'ask_price' and pending and nexon and not picked and (
            (kind == 'price' and structured.get('skip'))
            or (not structured and PRICE_SKIP.search(question) and not MONEY.search(question))):
        if not (last.get('form') or {}).get('skippable'):
            # 강화 기대값의 스페어 값은 계산에 꼭 필요해 건너뛸 수 없다. 같은 입력칸을 다시 보여 준다.
            result.update(status='ask_price', form=last.get('form'), asked=last.get('asked'), pending=pending,
                          content='이 값은 건너뛸 수 없습니다. 강화 중 장비가 파괴되면 같은 장비를 하나 더 마련해야 해서, '
                                  '그 값이 없으면 기대 비용을 계산할 수 없습니다.\n'
                                  '정확하지 않아도 괜찮습니다. 대략적인 값을 적어 주세요.')
            result['conditions'] = ['예: 2천만, 1억 5천만, 32억']
            store.message(sid,'user',{'content':said})
            store.message(sid,'assistant',result)
            return result
        skip_prices = True
        notes.append('노작값 없이 진행했습니다. 값을 모르는 장비는 값을 따지지 않았습니다.')
        question = pending
    if kind == 'price' and not skip_prices:
        saved_prices = save_price_answer(store, structured.get('values'), last)
    else:
        saved_prices = [] if structured or picked or skip_prices else capture_prices(store, question, history)
    if saved_prices and pending and nexon:
        notes.append('노작값을 저장했습니다: ' + ', '.join(f"{r['item']} {r['price']:,.0f} 메소" for r in saved_prices)
                     + '. 사용자가 알려 준 값이며 실제 거래가와 다를 수 있습니다.')
        question = pending
    elif saved_prices:
        result.update(status='price',content='노작값을 저장했습니다. 다음부터는 이 값을 씁니다.\n\n'
                      +'\n'.join(f"- {r['item']}: {r['price']:,.0f} 메소" for r in saved_prices))
        result['conditions'] = ['사용자가 알려 준 값입니다. 조회 시점의 실제 거래가와 다를 수 있습니다.']
        store.message(sid,'user',{'content':question})
        store.message(sid,'assistant',result)
        return result
    # 로컬/클라우드 모두 맥락을 해석한다. 실패하면 기존 검증 도구 경로를 사용한다.
    plan = None
    if not structured and not picked and not skip_prices and not saved_prices:
        plan = make_plan(store, model, question, history, topic, result.setdefault('actions', []))
    elif pending and isinstance(last.get('plan'), dict) and last['plan'].get('intents'):
        # 조건·노작값을 답하고 원래 질문을 이어 갈 때는 그때 세운 계획(여러 장비 등)을 그대로 쓴다.
        plan = {**{k: None for k in planner.FIELDS}, 'options': [], 'targets': [], 'combine': False, 'money': False, **last['plan'],
                'rewritten': pending, 'clarify': None}
    if plan and plan.get('clarify') and not asked_before:
        ask(result, plan['clarify'], [{'label': o, 'reply': o} for o in plan['options']], question)
        store.message(sid, 'user', {'content': said})
        store.message(sid, 'assistant', result)
        return result
    if plan:
        question = planner.tool_question(plan, question)
        query = question   # 실제 검색에도 문맥·줄임말을 푼 질문을 전달한다.
        result.setdefault('actions', []).append({'action':'interpret','summary':'질문 해석: '+question[:300]})
        result['plan'] = {k: plan.get(k) for k in ('intents', 'slot', 'target_star', 'target_cp', 'rewritten', 'targets', 'item')}
    query = language.expand(query)
    known_terms = language.mappings(said)
    if known_terms:
        result.setdefault('actions', []).append({'action':'normalize','summary':'용어 연결: '+', '.join(t['term']+' → '+t['canonical'] for t in known_terms)})
    want = set(plan['intents']) if plan else set()
    if topic and not SLOT_WORDS.search(question) and topic['name'] not in question:
        question = f"{topic['slot']} {question}"
    terms = [t for t in TERMS if t['term'] in normalize(question)]
    if plan:
        character_route = bool(want & {'gear_status', 'starforce', 'consult'})
        events_route = 'events' in want and not character_route and bool(nexon)
        union_route = 'union' in want and not character_route
    else:
        character_route = bool(topic or consulting or CHARACTER_INTENT.search(question)
                               or ((PRICE_INTENT.search(question) or STARFORCE_INTENT.search(question))
                                   and SLOT_WORDS.search(question)))
        events_route = not topic and bool(nexon) and bool(EVENT_INTENT.search(question))
        union_route = not topic and bool(UNION_INTENT.search(question))
    flags = {'consult': 'consult' in want, 'starforce': 'starforce' in want, 'slot': plan.get('slot'),
             'targets': plan.get('targets') or []} if plan else None
    if terms and any(t['term'] in ('환산','대장장이') for t in terms) and not plan:
        result.update(status='clarify',content='\n\n'.join(t['meaning']+'\n'+t['question'] for t in terms))
        result['conditions'] = ['용어 해석: 요구사항 v0.1의 검토 용례. 현재 시세·수치·거래 조건의 근거는 아닙니다.']
    elif events_route:
        try:
            notices.sync(store, nexon)
        except AppError as e:
            result['conditions'] = [f'공지를 새로 받지 못해 저장된 목록으로 답합니다. {e}']
        events = notices.active_events(store)
        result.update(status='evidence' if events else 'held', content=notices.events_text(store),
                      links=[{'title': e['title'], 'url': e['url']} for e in events if e.get('url')])
        result['conditions'] = (result.get('conditions') or []) + [
            '넥슨 Open API의 진행 중 이벤트 목록(최근 20개) 기준입니다. 세부 조건은 공지 링크에서 확인하세요.']
    elif union_route:
        chars = store.characters()
        main = next((c for c in chars if c['main']),None) or (chars[0] if chars else None)
        if not main:
            result.update(status='clarify',content='캐릭터 화면에서 대표 캐릭터를 먼저 등록하세요. 그 캐릭터 기준으로 공격대원을 추천합니다.')
        elif not nexon:
            result.update(status='clarify',content='캐릭터 조회를 사용할 수 없습니다. 설정에서 넥슨 API 키를 확인하세요.')
        else:
            union_answer(store, model, nexon, main, question, history, result)
    elif character_route:
        chars = store.characters()
        main = next((c for c in chars if c['main']),None) or (chars[0] if chars else None)
        if not main and nexon:
            main = account_main(store, nexon, result)
        if topic and topic.get('character'):
            # 캐릭터 화면에서 고른 장비면 그 캐릭터를 조회한다(대표 캐릭터가 아니어도).
            main = next((c for c in chars if c['name'] == topic['character']), None) or \
                {'name': topic['character'], 'goal': None, 'budget': 0}
        if not main:
            result.update(status='clarify',content='캐릭터 화면에서 캐릭터를 먼저 등록하세요. 등록한 캐릭터의 실제 장비와 능력치를 근거로 정리합니다.')
        elif not nexon:
            result.update(status='clarify',content='캐릭터 조회를 사용할 수 없습니다. 설정에서 넥슨 API 키를 확인하세요.')
        else:
            analyse_character(store, model, nexon, main, question, history, result, skip_prices, topic,
                              consulting=consulting, peer_runner=peer_runner, flags=flags)
    elif plan and want <= {'chat', 'rules'} and 'rules' not in want:
        chat_reply(store, model, question, history, result)
    elif terms and any(k in question for k in ('뜻','뭐','무엇','의미')):
        result.update(status='term',content='\n'.join(t['meaning'] for t in terms))
        result['conditions'] = ['용어 설명은 요구사항 v0.1 기준입니다. 게임별 확률과 비용을 뜻하지 않습니다.']
    elif any(k in question for k in ('기대값','기댓값','강화 비용','확률 계산')):
        result.update(status='clarify',content='어떤 장비·현재 단계·목표 단계·이벤트 조건으로 계산할까요?\n\n현재 승인된 메이플 강화 확률·비용표가 없어 게임 강화 기대값은 보류합니다. 계산 도구에서는 직접 입력한 고정 확률·비용의 독립 시행과 일정한 일일 획득량만 계산할 수 있습니다.')
    else:
        if nexon and notices.stale(store):
            try:
                notices.sync(store, nexon)       # 최신 공지 본문을 근거 문서로 넣어 둔다.
            except AppError:
                pass
        if not agent.research(store, model, query, history, plan, result):
            docs, conflict = store.search(query)
            if conflict:
                result['content'] = '같은 주제에 서로 다른 적용 버전의 자료가 검색되었습니다. 후속 수정과 실제 적용 시점을 검토하기 전까지 답변을 보류합니다.'
            elif not docs:
                result['content'] = '현재 질문에 답할 수 있는 검토 완료된 한국 본서버 근거를 찾지 못했습니다. 확인되지 않은 내용으로 답변하지 않겠습니다.\n\n자료실에서 출처·적용일·버전을 갖춘 자료를 등록하고 검토하거나, 질문의 직업·대상·조건을 더 알려 주세요. 검색 실패가 해당 정보의 부재를 뜻하지는 않습니다.'
            else:
                passages = []
                tokens = re.findall(r'[가-힣A-Za-z0-9]{2,}',normalize(query))
                for d in docs:
                    ranked = sorted(d['passages'],key=lambda p:sum(t in p for t in tokens),reverse=True)
                    for p in ranked[:3]:
                        if len(p)<=1800:
                            passages.append({'id':len(passages),'text':p,'doc_id':d['id']})
                selected = list(range(min(3,len(passages))))
                selected_model = store.setting('model')
                model_note = 'AI 모델을 고르지 않아 원문 검색 결과를 표시합니다.'
                if selected_model and passages:
                    try:
                        selected, metrics = model.select(selected_model,query,passages)
                        result['metrics'] = metrics
                        model_note = 'AI 모델이 관련 문장을 골랐습니다. 출력은 검토된 원문으로 제한됩니다.'
                    except AppError as e:
                        model_note = str(e)
                if not selected:
                    result['content'] = '검색된 자료만으로 질문을 뒷받침하기 어려워 답변을 보류합니다. 대상과 조건을 구체적으로 알려 주세요.'
                else:
                    excerpts = []
                    for i in selected:
                        p = passages[i]
                        d = next(d for d in docs if d['id']==p['doc_id'])
                        ref = next((s for s in result['sources'] if s['id']==d['id']),None)
                        if ref is None:
                            ref = {'id':d['id'],'title':d['title'],**d['metadata'],'citation':len(result['sources'])+1}
                            result['sources'].append(ref)
                        excerpts.append(f"[{ref['citation']}] {p['text']}")
                    result.update(status='evidence',content='질문과 관련해 검색된 검토 원문입니다. 아래 발췌가 질문의 모든 조건을 설명하는지는 별도 확인이 필요합니다.\n\n'+'\n\n'.join(excerpts))
                    result['conditions'] = [model_note,'저장된 자료의 검토 시점 기준입니다. 현재 사이트의 변경 여부를 실시간 확인한 결과는 아닙니다.','커뮤니티 자료는 유저 설명·실험이며 공식 사실로 보장하지 않습니다.']
    notes += result.pop('auto_notes', None) or []
    notes += result.pop('topic_notes', None) or []
    if notes:
        result['conditions'] = notes + list(result.get('conditions') or [])
    store.message(sid,'user',{'content':said})
    store.message(sid,'assistant',result)
    return result


def analyse_character(store, model, nexon, managed, question, history, result, skip_prices=False, topic=None,
                      consulting=False, peer_runner=None, flags=None):
    """실제 조회한 캐릭터 사실만 넘겨 모델이 서술하게 한다.

    flags(질문 이해 결과)가 있으면 그것을 따르고(상담·기대값을 함께 물을 수도 있다), 없으면 정규식으로 판단한다."""
    if flags is not None:
        consulting = flags['consult']
        wants_starforce = flags['starforce']
    else:
        consulting = consulting or bool(PEER_INTENT.search(question))
        wants_starforce = bool(STARFORCE_INTENT.search(question))
    if consulting:
        result['consult'] = True        # 조회가 실패해도 다음 질문은 상담으로 이어 간다
    try:
        profile = nexon.character(managed['name'], details=True)
    except AppError as e:
        result.update(status='clarify',content=f"{managed['name']} 조회에 실패해 답변을 보류합니다. {e}")
        return
    preset_note = None
    if topic and topic.get('preset'):
        # 다른 프리셋의 장비를 골랐으면 그 프리셋 장비 기준으로 계산·서술한다.
        rows = (profile.get('equipment_presets') or {}).get(topic['preset'])
        if rows:
            profile = {**profile, 'equipment': rows}
            if str(profile.get('equipment_preset')) != topic['preset']:
                preset_note = (f"장비 프리셋 {topic['preset']}번 기준으로 답했습니다. "
                               f"지금 게임에서 적용 중인 프리셋은 {profile.get('equipment_preset') or '확인 불가'}번입니다.")
        else:
            preset_note = f"장비 프리셋 {topic['preset']}번을 조회하지 못해 현재 착용 장비 기준으로 답했습니다."
    facts = context.build(profile, managed)
    text = context.as_text(facts)
    # 어느 경로로 끝나든(모델 없음·실패·거절) 사용자에게 보여야 하는 안내. answer()가 조건 줄 맨 앞에 붙인다.
    result['topic_notes'] = [preset_note] if preset_note else []
    if preset_note:
        text = f"[장비 프리셋] {preset_note}\n" + text
    item = None
    if topic:
        item = context.find_item(profile, topic)
        if item:
            text = context.item_text(item) + '\n\n' + text
            result['topic_item'] = context.item_summary(item)
            if item.get('name') != topic['name']:
                result['topic_notes'].append(f"대화 주제였던 {topic['name']}은(는) 지금 착용하고 있지 않아, "
                                f"같은 부위에 착용한 {item.get('name')} 기준으로 답했습니다.")
        else:
            result['topic_notes'].append(f"대화 주제인 {topic['slot']} {topic['name']}을(를) 지금은 착용하고 있지 않습니다.")
            text = f"[대화 주제 장비] {topic['slot']} {topic['name']} — 지금은 착용하지 않아 상세를 알 수 없다.\n\n" + text
    # 질문이 가리키는 부위('내 모자 어때?', '반지들 잠재')의 상세를 맨 앞에 둔다. 답이 그 장비에 집중하게.
    if not topic:
        for asked in asked_slot_items(profile, question, (flags or {}).get('slot')):
            text = context.item_text(asked).replace('[대화 주제 장비] 사용자가 이 장비를 두고 묻고 있다. 부위를 말하지 않으면 이 장비 이야기다.',
                                                    '[질문한 장비] 사용자가 이 장비를 물었다.') + '\n\n' + text
    result['character'] = {'name':facts['name'],'level':facts['level'],'job':facts['job'],
                           'combat_power':facts['combat_power'],'retrieved_at':facts['retrieved_at']}
    consult_mode = consulting
    if consult_mode:
        result['consult'] = True
        # 질문에 목표 전투력이 있고 지금 모으는 기준과 다르면, 그 전투력대로 다시 모으기 시작한다(뒤에서).
        goal = consult.target_cp_in(question)
        target = store.setting(peers.TARGET) or {}
        if not goal and not target.get('cp'):
            now_cp = profile.get('combat_power') or 0
            picks = sorted({round(now_cp * k / 5e7) * 5e7 for k in (1.3, 1.6, 2.0)} - {0})
            ask(result, '목표 전투력을 얼마로 잡을까요? 그 전투력대 유저들과 비교해서 상담해 드릴게요.',
                [{'label': cp_label(v), 'reply': f'목표 전투력 {cp_label(v)}'} for v in picks], question,
                note=f"지금 전투력은 {cp_label(now_cp)}이에요. 원하는 값을 직접 적어도 됩니다(예: 2억5천)." if now_cp else
                     '원하는 값을 직접 적어 주세요(예: 2억5천).')
            return
        if goal and not target.get('cp') and not peer_runner:
            result['topic_notes'].append('목표 전투력은 캐릭터 화면의 \'목표 전투력대 유저\'에서 모으기를 시작하면 저장돼요.')
        if goal and peer_runner and (not target.get('cp') or abs(goal - target['cp']) > target['cp'] * 0.05):
            peer_runner.restart(managed['name'], goal)
            result['topic_notes'].append(f"목표 전투력을 {goal / 1e8:.2f}억으로 바꿔 그 전투력대 유저를 다시 모으기 시작했어요. "
                                         "몇 분~하루에 걸쳐 모이니, 그동안은 이전 기준으로 답해요.")
        ledger = None
        if hasattr(nexon, 'get'):
            try:    # 교체 시뮬레이션용 스탯 출처. 하루 한 번 넥슨에서 받는다(statcalc.load).
                ledger = statcalc.load(store, nexon.get, managed['name'])
            except AppError:
                ledger = None
        consult_text, consult_notes, _ = consult.build(store, profile, ledger, question, managed)
        result['topic_notes'] += consult_notes
        if consult_text:
            text += '\n\n' + consult_text
        else:
            text += ('\n\n[목표 전투력대 유저 장비 통계] 아직 모으지 못했다(캐릭터 화면의 \'목표 전투력대 유저\'에서 모으기 시작). '
                     '다른 유저 경향을 지어내지 말 것. 목표 전투력을 물어보고, 캐릭터 화면에서 모으기를 시작하라고 안내할 것.')
            result['topic_notes'].append("목표 전투력대 유저 장비 통계가 아직 없어요. 캐릭터 화면의 '목표 전투력대 유저'에 목표 전투력을 적고 모으기를 시작하세요.")
    # 강화 기대값은 앱이 직접 계산해 사실로 넘긴다. 모델이 확률을 지어내지 못하게 하려는 것이다.
    targets = (flags or {}).get('targets') or []
    if wants_starforce and targets and (len(targets) > 1 or any(not_worn(profile, x) for x in targets)):
        # 여러 장비, 또는 지금 끼지 않은 장비('에테 상의 18성')의 기대값: 장비마다 계산해 합계까지.
        computed = multi_starforce_facts(store, profile, targets, question, result)
        if computed is None:
            return
        text += computed
    elif wants_starforce:
        computed = starforce_facts(store, profile, question, result, item)
        if computed is None:
            return
        text += computed
    # 값을 따져야 하는 질문이면 노작값부터 확보한다. 모르면 지어내지 않고 되묻는다.
    # 강화 기대값 질문의 '얼마'는 기대 비용을 묻는 말이라, 장비 값을 직접 물을 때만 시세를 챙긴다.
    # 상담 중의 '얼마나 올라?', '가성비'는 상담이 아는 노작값으로 따로 다룬다(consult.build). 내 장비 값을 되묻지 않는다.
    asks_price = not consult_mode and PRICE_INTENT.search(question) and (
        not STARFORCE_INTENT.search(question) or ITEM_PRICE.search(question))
    if asks_price:
        resolved = prices.resolve_many(store, prices.named_targets(question) or price_targets(facts, question),
                                       facts.get('job'), prices.group_in(question))
        known = [r for r in resolved if r['known']]
        unknown = [r for r in resolved if not r['known']]
        group = prices.ask_group(unknown, facts.get('job')) if not skip_prices else None
        if group:                      # 제논 등: 직업군을 정하지 않고 다른 직업군 값을 쓰지 않는다
            ask(result, *group[:2], question, note=group[2])
            return
        if known:
            text += ('\n\n[저장된 노작값] 사용자가 알려 주었거나 조회해 둔 값이다. 여기 없는 장비의 값은 모른다.\n'
                     + '\n'.join(f"- {r['item']}: {r['price']:,.0f} 메소 ({r['source']}, {r['recorded_at'][:10]}"
                                  + (f", 저장 이름 '{r['matched']}'" if r.get('matched') else '') + ')'
                                  for r in known))
        # 하나도 모를 때만 멈추고 묻는다. 일부라도 알면 그걸로 답하고 모르는 것은 각주로 남긴다.
        if unknown and not known and not skip_prices:
            result.update(status='ask_price', content=prices.ask_text(unknown),
                          form=prices.form(unknown, skippable=True), pending=question)
            result['asked'] = [{'item':r['item']} for r in unknown]
            result['conditions'] = ['값을 모르는 채로 비용을 비교하지 않습니다. 알려 주시면 저장해 두고 다시 묻지 않습니다.',
                                    '경매장 조회가 실패했거나 한도를 넘었습니다.' if prices.status(store)['fetcher']
                                    else '외부 시세 조회기가 연결되어 있지 않아 저장된 값만 사용합니다.']
            return
        if unknown:
            text += f'\n\n[값을 모르는 장비] 아래는 노작값을 모른다. 값을 추측해서 비교하지 말 것.\n' \
                    + '\n'.join(f"- {r['item']}" for r in unknown[:6])
    selected_model = store.setting('model')
    if not selected_model:
        block = result.pop('starforce_text', None)
        result.update(status='context', content=block or '조회한 사실은 아래 항목에서 확인하세요.')
        result['facts'] = text
        result['conditions'] = ['AI 모델을 고르지 않아 조회한 사실만 정리했습니다. 설정에서 모델을 고르면 이 정보를 바탕으로 서술합니다.']
        return
    previous = [{'role':m['role'],'content':m['payload']['content']}
                for m in history[-4:] if m['payload'].get('content')]
    try:
        written, metrics = model.analyse(selected_model, text, question, previous,
                                         numbers_shown=bool(result.get('starforce_text')),
                                         **({'consult': True} if result.get('consult') else
                                            {'consult': 'chat'} if flags is not None else {}))
        result['metrics'] = metrics
    except AppError as e:
        kept = result.pop('starforce_text', None)
        result.update(status='context', content=kept or '조회한 사실은 아래 항목에서 확인하세요.')
        result['facts'] = text
        result['conditions'] = [f'모델 응답에 실패해 계산 결과와 조회한 사실만 표시합니다. {e}']
        return
    written = fix_name(written, facts['name'])
    # 넘겨준 사실에 없는 확률이나 수치가 섞이면 그 서술은 쓰지 않는다.
    # 사용자가 말한 수치(목표 전투력 '2억5천' 등)는 사실로 인정한다. 억 단위로 끊어 쓴 형태('2억')도 함께.
    said = question + ' ' + ' '.join(m['payload'].get('content', '') for m in history[-4:] if m['role'] == 'user')
    target = (store.setting(peers.TARGET) or {}).get('cp') if consult_mode else None
    spoken = ' '.join(f"{v // 100_000_000}억 {v:,}" for v in
                      [int(x) for x in (consult.target_cp_in(said), target) if x])
    invented = unsupported_numbers(written, text + '\n' + said + '\n' + spoken)
    if FABRICATION.search(written) or invented:
        kept = result.pop('starforce_text', None)
        result.update(status='context', content=kept or '조회한 사실은 아래 항목에서 확인하세요.')
        result['facts'] = text
        reason = ('모델이 조회한 사실에 없는 수치를 만들어 사용하지 않았습니다: '
                  + ', '.join(invented[:5])) if invented else \
                 '모델 서술에 근거 없는 확률 표현이 섞여 사용하지 않았습니다.'
        result['conditions'] = [reason + ' 조회한 사실만 표시합니다.',
                                '강화 확률과 시세는 검토된 자료가 등록되어야 답변합니다.']
        return
    block = result.pop('starforce_text', None)
    result.update(status='analysis', content=(block + '\n\n' + written) if block else written)
    result['facts'] = text
    # 기대값을 계산해 보여 준 답에 '강화 확률·비용은 근거가 없어 뺐다'고 쓰면 앞뒤가 맞지 않는다.
    source_note = ('강화 기대값은 앱이 mesulive 확률표·비용식으로 계산한 값입니다(넥슨 공시와 대조하지 않음). '
                   '시세·패치 내용은 서술에서 제외했습니다.' if result.get('starforce') else
                   '강화 확률·비용·시세·패치 내용은 근거가 없어 서술에서 제외했습니다. 해당 질문은 자료실에 자료를 등록해야 답변합니다.')
    result['conditions'] = [
        f"넥슨 Open API로 {facts['retrieved_at']}에 조회한 이 캐릭터의 실제 값만 근거로 삼았습니다.",
        source_note,
        '추가옵션 등급(급·n추)은 커뮤니티 약식 기준이며 게임이 제공하는 등급이 아닙니다.',
    ]


def union_answer(store, model, nexon, managed, question, history, result):
    """대표 캐릭터 기준으로 다음에 키울 공격대원을 추천한다. 목록과 수치는 앱이 쓴다."""
    try:
        profile = nexon.character(managed['name'], details=True)
        roster = nexon.characters().get('characters') or []
    except AppError as e:
        result.update(status='clarify', content=f"캐릭터 목록을 조회하지 못해 추천을 보류합니다. {e}")
        return
    try:
        info = nexon.union(managed['name'])
    except AppError as e:
        info = {'warnings': [f'유니온 조회: {e}'], 'placed': None}
    placed = {union.canonical(p['job']) for p in info.get('placed') or []} if info.get('placed') is not None else None
    if placed is not None and not placed and info.get('warnings'):
        placed = None      # 공격대 조회에 실패했으면 배치 여부를 모른다고 둔다.
    try:
        rows = union.recommend(roster, profile.get('job'), profile.get('main_stat'), profile.get('world'), placed)
    except AppError as e:
        result.update(status='clarify', content=str(e))
        return
    block = union.as_text(rows, profile.get('job'), profile.get('main_stat'), info)
    result['union'] = {'rows': rows, 'level': info.get('level'), 'grade': info.get('grade')}
    notes = [f"효과 수치와 평가(S~F)는 {union.SOURCE['name']}를 옮긴 것으로, 넥슨 공식 수치와 대조하지 않은 커뮤니티 평가입니다.",
             '등급 기준: 공격대원 레벨 60 B · 100 A · 140 S · 200 SS · 250 SSS. 같은 월드 캐릭터만 셉니다.',
             '배치할 수 있는 공격대원 수는 유니온 등급에 따라 제한됩니다. 미배치 캐릭터는 효과가 없습니다.',
             '메이플스토리M 공격대원(공격력/마력, S)은 넥슨 Open API로 확인할 수 없어 목록에서 뺐습니다.']
    notes += info.get('warnings') or []
    selected_model = store.setting('model')
    if selected_model:
        previous = [{'role':m['role'],'content':m['payload']['content']}
                    for m in history[-4:] if m['payload'].get('content')]
        facts = ('[유니온 공격대원 추천] 앱이 계산해 사용자에게 이미 보여 준 목록이다. 수치를 다시 나열하지 말고, '
                 '왜 이 순서인지 한두 줄만 덧붙여라.\n' + block)
        try:
            written, metrics = model.analyse(selected_model, facts, question, previous, numbers_shown=True)
            written = fix_name(written, profile.get('name'))
            if not FABRICATION.search(written) and not unsupported_numbers(written, facts):
                result['metrics'] = metrics
                result.update(status='analysis', content=block + '\n\n' + written, conditions=notes)
                return
        except AppError:
            pass
    result.update(status='context', content=block, conditions=notes)


def clean_topic(topic):
    """화면에서 보낸 대화 주제 장비를 검사한다. 부위·이름 외의 값은 표시용 요약만 받는다."""
    if topic is None:
        return None
    if not isinstance(topic, dict):
        raise AppError('대화 주제 장비 형식이 올바르지 않습니다.')
    slot, name = str(topic.get('slot') or '').strip(), str(topic.get('name') or '').strip()
    character = str(topic.get('character') or '').strip()
    preset = topic.get('preset')
    if not slot or not name or len(slot) > 20 or len(name) > 60 or len(character) > 30:
        raise AppError('대화 주제 장비의 부위와 이름을 확인해 주세요.')
    if preset is not None and str(preset) not in ('1', '2', '3'):
        raise AppError('장비 프리셋은 1~3번만 고를 수 있습니다.')
    return {'slot': slot, 'name': name, **({'character': character} if character else {}),
            **({'preset': str(preset)} if preset is not None else {})}


def fix_name(text, name):
    """모델이 캐릭터 이름 끝 글자를 늘려 쓰는 경우를 원래 이름으로 되돌린다.

    한국어 소형 모델에서 자주 나오는 반복 오류다. 이름은 API가 준 정확한 문자열이므로
    그 이름으로 시작하면서 같은 글자가 더 붙은 덩어리만 잘라낸다.
    """
    if not name or name in ('', None):
        return text
    pattern = re.compile(re.escape(name) + r'(' + re.escape(name[-1]) + r')+')
    return pattern.sub(name, text)


def capture_prices(store, question, history):
    """직전 답변이 노작값을 물었을 때만, 사용자가 적어 준 금액을 저장한다.

    아무 때나 숫자를 값으로 받아들이면 엉뚱한 기록이 쌓이므로 되묻기 직후로 제한한다.
    """
    last = next((m for m in reversed(history) if m['role']=='assistant'), None)
    if not last or last['payload'].get('status') != 'ask_price':
        return []
    asked = {a['item'] for a in last['payload'].get('asked') or []}
    saved = []
    for line in question.splitlines():
        line = line.strip().lstrip('-').strip()
        if not line:
            continue
        found = PRICE_REPLY.match(line)
        match = price = None
        if found:
            item = found.group('item').strip()
            price = prices.parse_price(found.group('price'))
            match = next((a for a in asked if a == item), None) or \
                    next((a for a in asked if a in item or item in a), None)
        if not match and len(asked) == 1 and MONEY.search(line):
            # 한 장비만 물었으면 이름 없이 '2천만 정도'라고만 답해도 그 장비 값으로 본다.
            match, price = next(iter(asked)), prices.parse_price(line)
        if match and price:
            saved.append(store.price_save({'item':match,'price':price,'source':'user'}))
    return saved


def save_price_answer(store, values, last):
    """입력칸으로 받은 노작값을 저장한다. 되물었던 장비만 받는다."""
    if last.get('status') != 'ask_price':
        raise AppError('노작값을 묻지 않은 상태입니다. 설정 화면에서 직접 입력해 주세요.')
    if not isinstance(values, dict) or not values:
        raise AppError('노작값을 입력해 주세요.')
    asked = {a['item'] for a in last.get('asked') or []}
    saved = []
    for item, text in values.items():
        if item not in asked:
            raise AppError(f'묻지 않은 장비입니다: {item}')
        if not str(text or '').strip():
            continue
        price = prices.parse_price(str(text), '억')        # 노작값 입력칸: 단위 없이 '32'면 32억
        if not price:
            raise AppError(f"{item} 값을 읽지 못했습니다. '2천만', '32억'처럼 적어 주세요.")
        saved.append(store.price_save({'item':item,'price':price,'source':'user'}))
    if not saved:
        raise AppError('노작값을 하나 이상 입력해 주세요.')
    return saved


def price_targets(facts, question):
    """질문과 관련된 장비를 추린다.

    장비 이름이나 부위가 질문에 나오면 그것만 본다. 아무것도 지목하지 않았을 때만
    손볼 후보를 몇 개 추린다. 한 번에 여러 개를 묻지 않으려는 것이다.
    """
    rows = (facts.get('weak_add_options') or []) + (facts.get('low_starforce') or [])
    named = [r for r in rows if r['name'] in question or (r.get('slot') and r['slot'] in question)]
    if named:
        picked, seen = [], set()
        for r in named:
            if r['name'] not in seen:
                seen.add(r['name'])
                picked.append((r['name'], r.get('grade'), r.get('slot')))
        return picked
    candidates = [(r['name'], r.get('grade'), r.get('slot')) for r in (facts.get('weak_add_options') or []) if not r.get('empty')]
    candidates += [(r['name'], None, r.get('slot')) for r in (facts.get('low_starforce') or [])]
    return candidates[:4]


def numbers_in(text):
    """글에 나온 수치를 실제 값으로 바꿔 모은다. '32억'과 '3,200,000,000'을 같은 값으로 본다."""
    found = set()
    for digits, unit in AMOUNT.findall(text or ''):
        try:
            value = float(digits.replace(',', ''))
        except ValueError:
            continue
        found.add(round(value * UNIT_SCALE.get(unit or None, 1)))
        if unit:
            found.add(round(value))      # '32억'을 '32'로 다시 쓴 경우도 인정한다.
    return found


def unsupported_numbers(written, facts_text, floor=1000):
    """서술에 있으나 넘겨준 사실에는 없는 수치. 지어낸 금액·퍼센트를 잡아내는 용도다.

    작은 정수(등급·성 등)는 모델이 세는 과정에서 나올 수 있어 통과시키고, 금액 규모의 수치와
    퍼센트만 사실과 대조한다. 퍼센트는 작은 모델이 '성공 확률: 38.5%'처럼 지어내는 일이 잦다.
    """
    known = numbers_in(facts_text)
    known_percent = {float(p) for p in PERCENT.findall(facts_text or '')}
    bad = [f'{p}%' for p in PERCENT.findall(written or '') if float(p) not in known_percent]
    for digits, unit in AMOUNT.findall(written or ''):
        try:
            value = float(digits.replace(',', ''))
        except ValueError:
            continue
        actual = round(value * UNIT_SCALE.get(unit or None, 1))
        if actual < floor or actual in known:
            continue
        bad.append(f"{digits}{unit or ''}")
    return bad


def starforce_facts(store, profile, question, result, topic_item=None):
    """질문이 가리키는 장비의 강화 기대값을 계산해 사실 묶음에 붙일 글을 돌려준다.

    스페어(노작값)를 모르면 계산해도 총비용이 성립하지 않으므로 먼저 되묻는다.
    되물어야 하면 result를 ask_price로 채우고 None을 돌려준다.
    장비를 골라 연 대화면 그 장비(topic_item)가 대상이다.
    """
    if topic_item is not None and (not topic_item.get('equip_level') or topic_item.get('starforce') is None):
        topic_item = None
    item = context.starforce_item(profile, question, topic_item)
    if not item:
        options = starforce_slot_options(profile, question)
        if options:
            ask(result, '어느 장비의 강화 기대값을 볼까요?', options, question,
                note='목록에 없는 장비면 이름을 직접 적어 주세요.')
            return None
    if item and context.starforce_possible(item) and not [t for t in TARGET_STAR.findall(question) if int(t) > item['starforce']]:
        top = max_star(item)
        steps = [s for s in range(item['starforce'] + 1, min(top, item['starforce'] + 5) + 1)]
        if steps:
            ask(result, f"{item['slot']} {item['name']}(지금 {item['starforce']}성)을 몇 성까지 볼까요?",
                [{'label': f'{s}성', 'reply': f'{s}성까지'} for s in steps], f"{item['slot']} {question}")
            return None
    if item and not context.starforce_possible(item):   # 강화 조건을 묻기 전에 걸러야 헛질문이 없다
        # 모델 서술을 버리는 경우에도 보이도록 앱이 직접 쓰는 답 머리에 둔다.
        result['starforce_text'] = f"**{item['slot']} {item['name']}**은 스타포스를 올릴 수 없는 장비라 기대값을 계산하지 않았습니다."
        return (f"\n\n[강화 기대값] {item['slot']}({item['name']})은 스타포스를 올릴 수 없는 장비라 계산하지 않았다. "
                '그렇게 알려 줄 것.')
    if not conditions.answered(store):
        now_conditions = conditions.load(store)
        suggested, event = notices.suggested_event(store)
        if suggested and now_conditions.get('event') in (None, '없음'):
            now_conditions['event'] = suggested
        result.update(status='ask_conditions', content=conditions.ask_text(),
                      form=conditions.form(now_conditions), pending=question)
        result['conditions'] = ['직접 적어도 됩니다. 예: 샤타포스, 파괴방지 미사용, MVP 다이아']
        if event:
            result['conditions'].insert(0, f"넥슨 공지 기준 지금 '{event['title']}' 진행 중이라 샤타포스를 미리 골라 두었습니다.")
        return None
    if not item:
        return '\n\n[강화 기대값] 어느 장비를 말하는지 몰라 계산하지 않았다. 부위나 장비 이름을 물어볼 것.'
    current = item['starforce']
    found = TARGET_STAR.findall(question)
    targets = [int(t) for t in found if int(t) > current]
    if not targets:
        return (f"\n\n[강화 기대값] {item['slot']}({item['name']})의 목표 성을 알 수 없어 계산하지 않았다. "
                f"현재 {current}성이다. 목표 성을 물어볼 것.")
    target = min(targets)
    price = prices.resolve(store, item['name'], (item.get('add_grade') or {}).get('grade'), profile.get('job'), item.get('slot'),
                           prices.group_in(question))
    group = prices.ask_group([price], profile.get('job'))
    if group:
        ask(result, *group[:2], question, note=group[2])
        return None
    if not price['known']:
        result.update(status='ask_price', content=prices.ask_text([price]),
                      form=prices.form([price]), pending=question)
        result['asked'] = [{'item': price['item']}]
        result['conditions'] = [
            f"{item['slot']}({item['name']})을 {current}성에서 {target}성으로 올리는 기대 비용은 "
            '파괴 시 쓸 스페어 장비 값이 있어야 계산됩니다. 노작값을 알려주시면 바로 계산합니다.',
            '값을 모르는 채로 비용을 내놓지 않습니다.']
        return None
    picked = conditions.load(store)
    try:
        calc = starforce.expected({'level': item['equip_level'], 'current_star': current,
                                   'target_star': target, 'spare_cost': price['price'],
                                   **conditions.to_arguments(picked, current, target)})
    except AppError as e:
        # 이유(예: 장비 레벨의 최대 성)를 앱이 직접 보여 준다. 모델에게만 맡기면 '계산할 수 없다'로 뭉개진다.
        result['starforce_text'] = f"**{item['slot']} {item['name']}** {current}성 → {target}성 기대값을 계산하지 못했습니다: {e}"
        return f"\n\n[강화 기대값] 계산하지 못했다: {e}"
    result['starforce'] = {**calc, 'slot': item['slot'], 'item': item['name']}
    # 흔적 복구는 스페어가 쌀 때 오히려 훨씬 비싸다. 판단할 수 있게 복구 없이 계산한 값도 보여 준다.
    compare = ''
    if calc['restore_stars']:
        alt = starforce.expected({'level': item['equip_level'], 'current_star': current,
                                  'target_star': target, 'spare_cost': price['price'],
                                  **conditions.to_arguments({**picked, 'use_restore': False}, current, target)})
        compare = (f"\n- 흔적 복구를 안 쓰면: {alt['expected_cost']:,} 메소 "
                   f"(시도 {alt['expected_attempts']}회, 파괴 {alt['expected_destroys']}회)")
        result['starforce']['without_restore'] = alt['expected_cost']
    result['starforce_text'] = (
        f"**{item['slot']} {item['name']}** {current}성 → {target}성\n\n"
        f"- 기대 비용: **{calc['expected_cost']:,} 메소**\n"
        f"- 기대 시도 횟수: {calc['expected_attempts']}회\n"
        f"- 기대 파괴 횟수: {calc['expected_destroys']}회\n"
        f"- 적용 조건: {conditions.summary(picked)}\n"
        f"- 스페어 장비 값: {price['price']:,.0f} 메소 (파괴 시 1개 소모로 계산)"
        + compare)
    return (f"\n\n[강화 기대값] 앱이 이미 계산해 사용자에게 그대로 보여 준 값이다.\n"
            f"계산할 수 없다고 쓰지 말 것. 아래 수치를 다시 나열하지도 말 것.\n"
            f"필요하면 이 수치가 무엇을 뜻하는지 한두 줄만 덧붙여라.\n"
            f"- 대상: {item['slot']} {item['name']} (레벨 {item['equip_level']})\n"
            f"- {current}성 -> {target}성\n"
            f"- 기대 비용: {calc['expected_cost']:,} 메소\n"
            f"- 기대 시도 횟수: {calc['expected_attempts']}회\n"
            f"- 기대 파괴 횟수: {calc['expected_destroys']}회\n"
            f"- 적용 조건: {conditions.summary(picked)}{compare}\n"
            f"- 확률표 출처: {calc['source']['name']} (넥슨 공시와 대조하지 않은 커뮤니티 값, 스타캐치 반영)")


def capture_conditions(store, question, history):
    """강화 조건 답변을 읽어 저장한다.

    되묻기 직후이거나, 조건만 짧게 말한 경우('샤타포스일때는')에 반응한다.
    긴 문장에서 우연히 단어가 걸려 조건이 바뀌는 일을 막으려고 길이를 제한한다.
    """
    last = next((m for m in reversed(history) if m['role'] == 'assistant'), None)
    asked = bool(last and last['payload'].get('status') == 'ask_conditions')
    if not asked and len(question) > 40:
        return None
    parsed = conditions.parse(question)
    if not parsed:
        return None
    if not asked and parsed == dict(conditions.DEFAULTS):
        return None      # '기본'만으로는 조건을 되돌리지 않는다. 되묻기 직후에만 인정한다.
    # 되묻기 답이면 처음부터 새로, 후속 수정이면 기존 조건 위에 얹는다.
    base = dict(conditions.DEFAULTS) if asked else conditions.load(store)
    merged = {k: v for k, v in {**base, **parsed}.items() if k in conditions.DEFAULTS}
    conditions.save(store, merged)
    return merged


def last_starforce(history):
    """직전에 계산한 강화 대상. 조건만 바꿔 다시 물을 때 쓴다."""
    for message in reversed(history):
        if message['role'] == 'assistant' and message['payload'].get('starforce'):
            return message['payload']['starforce']
    return None


def make_plan(store, model, question, history, topic=None, diagnostics=None):
    """모든 모델에 구조화 질문 해석을 요청하고 실패는 공개 상태로 남긴다."""
    selected = store.setting('model')
    if not selected or not hasattr(model, 'plan'):
        return None
    chars = store.characters()
    main = next((c for c in chars if c['main']), None) or (chars[0] if chars else None)
    target = store.setting(peers.TARGET) or {}
    context_info = {'대표 캐릭터 직업': ((main or {}).get('snapshots') or [{}])[0].get('data', {}).get('job') if main else None,
                    '저장된 목표 전투력': f"{target['cp'] / 1e8:.2f}억" if target.get('cp') else None,
                    '대화 주제 장비': f"{topic['slot']} {topic['name']}" if topic else None}
    turns = [{'role': m['role'], 'content': m['payload'].get('content', '')} for m in history[-4:] if m['payload'].get('content')]
    try:
        text, _ = model.plan(selected, planner.messages(question, turns, {k: v for k, v in context_info.items() if v}))
    except Exception:
        if diagnostics is not None:
            diagnostics.append({'action':'fallback','summary':'AI 질문 해석에 실패해 기본 도구 해석으로 전환했습니다.'})
        return None
    parsed = planner.parse(text)
    if parsed is None and diagnostics is not None:
        diagnostics.append({'action':'fallback','summary':'AI 계획 형식이 맞지 않아 기본 도구 해석으로 전환했습니다.'})
    return parsed


CAPABILITIES = (
    '[메피티가 할 수 있는 일] 넥슨 Open API로 사용자의 실제 캐릭터·장비를 보고 대화로 답한다:\n'
    '- 지금 장비 상태(스타포스·잠재·추옵 급, 약한 부위)\n'
    '- 스타포스 강화 기대값(이벤트·파괴방지 조건 반영, mesulive 방식 계산)\n'
    '- 목표 전투력대 유저와 비교한 스펙업 상담(무엇을 바꾸면 보스 기준 몇 % 오르는지, 세트 맞추기, 가성비)\n'
    '- 유니온 공격대원 추천, 진행 중 이벤트, 수익(재획·주보) 기록, 목표 메소·레벨업 기간\n'
    '예시 질문: "내 장비 어때?", "모자 22성 기대값", "목표 전투력 2억5천이면 뭐부터 바꿔?", "지금 이벤트 뭐 있어?"'
)


def chat_reply(store, model, question, history, result):
    """인사·잡담·사용법. 메피티가 할 수 있는 일만 사실로 주고 짧게 대화한다(게임 수치는 말하지 않는다)."""
    selected = store.setting('model')
    previous = [{'role': m['role'], 'content': m['payload']['content']} for m in history[-4:] if m['payload'].get('content')]
    try:
        written, metrics = model.analyse(selected, CAPABILITIES, question, previous, consult='chat')
    except AppError as e:
        result.update(status='clarify', content='무엇을 도와드릴까요? 예: "내 장비 어때?", "모자 22성 기대값", "목표 전투력 2억5천이면 뭐부터 바꿔?"')
        result['conditions'] = [str(e)]
        return
    invented = unsupported_numbers(written, CAPABILITIES + '\n' + question)
    if invented or FABRICATION.search(written):
        result.update(status='clarify', content='무엇을 도와드릴까요? 예: "내 장비 어때?", "모자 22성 기대값", "목표 전투력 2억5천이면 뭐부터 바꿔?"')
        return
    result.update(status='analysis', content=written)
    result['metrics'] = metrics


def ask(result, content, options, pending, note=None):
    """재질문: 버튼(고르면 그 답이 그대로 메시지로 간다)과 함께 되묻고, 원래 질문을 pending으로 남긴다. 직접 적어도 된다."""
    result.update(status='ask', content=content, pending=pending,
                  form={'kind': 'ask', 'options': [{'label': o['label'][:60], 'reply': o['reply'][:120]} for o in options[:8]]})
    result['conditions'] = [note] if note else []


def cp_label(value):
    """2.5e8 → '2억5천', 3e8 → '3억'."""
    eok = int(value // 1e8)
    rest = int(round((value - eok * 1e8) / 1e7))
    if rest >= 10:
        eok, rest = eok + 1, 0
    return f"{eok}억{rest}천" if rest else f"{eok}억"


def max_star(item):
    """장비 레벨별 최대 스타포스(일반 장비)."""
    return starforce.reachable_star(item.get('equip_level') or 0)


def starforce_slot_options(profile, question):
    """강화할 장비 고르기 버튼: 스타포스를 올릴 수 있는 장비. 질문에 부위 낱말(반지·펜던트 등)이 있으면 그 부위만."""
    items = [i for i in profile.get('equipment') or [] if i.get('equip_level') and i.get('starforce') is not None
             and context.starforce_possible(i)]
    hinted = [i for i in items if i.get('slot') and re.sub(r'\d+$', '', i['slot']) in question]
    pool = hinted or items
    goal = max((int(s) for s in TARGET_STAR.findall(question)), default=None)
    if goal:      # '22성 기대값'이면 이미 22성 이상이거나 22성까지 못 올리는 장비는 빼고 보여 준다
        pool = [i for i in pool if i['starforce'] < goal <= max_star(i)] or pool
    order = {s: n for n, s in enumerate(context.SLOT_ORDER)}
    pool.sort(key=lambda i: (order.get(re.sub(r'\d+$', '', i['slot']), 99), i['slot']))
    return [{'label': f"{i['slot']} · {i['name']} {i['starforce']}성", 'reply': i['slot']} for i in pool[:8]]


def account_main(store, nexon, result):
    """캐릭터를 등록하지 않았어도 넥슨 키가 있으면 계정 캐릭터 중 레벨이 가장 높은 캐릭터로 답한다(무엇을 썼는지 알린다)."""
    from .earnings import ACCOUNT_CHARACTERS
    listed = store.setting(ACCOUNT_CHARACTERS) or []
    if not listed:
        try:
            listed = nexon.characters().get('characters') or []
        except (AppError, AttributeError):
            return None
        store.set_setting(ACCOUNT_CHARACTERS, [{'name': c.get('name'), 'world': c.get('world'), 'level': c.get('level')} for c in listed])
    best = max(listed, key=lambda c: c.get('level') or 0, default=None)
    if not best or not best.get('name'):
        return None
    result.setdefault('auto_notes', []).append(
        f"대표 캐릭터를 정하지 않아 계정에서 레벨이 가장 높은 {best['name']}(Lv.{best.get('level')}) 기준으로 답했어요. 캐릭터 화면에서 바꿀 수 있어요.")
    return {'name': best['name'], 'goal': None, 'budget': 0}


def asked_slot_items(profile, question, slot=None):
    """질문(또는 질문 이해 결과)이 가리키는 부위의 장비들. '반지'면 반지1~4 전부, 이름이 나오면 그 장비."""
    items = [i for i in profile.get('equipment') or [] if i.get('slot')]
    named = [i for i in items if i.get('name') and i['name'] in question]
    if named:
        return named[:2]
    words = [w for w in ([slot] if slot else []) + SLOT_WORDS.findall(question) if w and w not in ('장비', '템', '아이템', '방어구', '장신구')]
    picked = []
    for w in words:
        for i in items:
            if (i['slot'] == w or re.sub(r'\d+$', '', i['slot']) == w) and i not in picked:
                picked.append(i)
    return picked[:4]


# 지금 끼지 않은 장비의 장비 레벨(스타포스 최대 성·비용에 필요). 방어구 세트는 세트마다 레벨이 같다.
SET_LEVELS = (('에테르넬', 250), ('아케인셰이드', 200), ('앱솔랩스', 160), ('루타비스', 150), ('하이네스', 150), ('이글아이', 150), ('트릭스터', 150))
SLOT_ALIAS = {'뚝': '모자', '뚝배기': '모자', '견장': '어깨장식', '어깨': '어깨장식', '얼장': '얼굴장식', '눈장': '눈장식', '귀걸이': '귀고리', '목걸이': '펜던트', '보조': '보조무기'}


def all_items(profile):
    """지금 장비 + 프리셋 1~3의 장비(이름으로 레벨·성을 찾을 때)."""
    out = list(profile.get('equipment') or [])
    for rows in (profile.get('equipment_presets') or {}).values():
        out += rows or []
    return out


def worn_in(profile, slot):
    slot = SLOT_ALIAS.get(slot, slot)
    return next((i for i in profile.get('equipment') or [] if i.get('slot') == slot), None)


def not_worn(profile, target):
    """질문한 장비가 그 부위에 지금 낀 장비와 다른가(세트 이름이 들어 있지 않으면 다르다고 본다)."""
    name = target.get('item')
    if not name:
        return False
    worn = worn_in(profile, target.get('slot') or '')
    if not worn:
        return True
    key = re.split(r'\s+', name)[0]
    return not (worn.get('name') or '').startswith(key[:2]) and key not in (worn.get('name') or '')


def target_level(profile, name):
    """장비 이름으로 장비 레벨: 내 장비·프리셋에 같은 이름 → 방어구 세트 표 → 모르면 None."""
    for i in all_items(profile):
        if name and i.get('name') and (i['name'] == name or name in i['name']) and i.get('equip_level'):
            return i['equip_level']
    for key, level in SET_LEVELS:
        if name and (key in name or name.startswith(key[:2])):
            return level
    return None


def multi_starforce_facts(store, profile, targets, question, result):
    """장비 여러 개(또는 끼지 않은 장비)의 강화 기대값. 모르는 레벨·노작값·조건은 한 번에 되묻는다."""
    rows, unknown_price, unknown_level = [], [], []
    # '에테 상하의·신발·견장·망토'처럼 한 세트로 물었는데 일부 장비 이름이 빠졌으면 같은 세트로 채운다.
    sets = {re.split(r'\s+', x['item'])[0] for x in targets if x.get('item')}
    if len(sets) == 1:
        prefix = sets.pop()
        targets = [x if x.get('item') or not x.get('slot') else {**x, 'item': f"{prefix} {SLOT_ALIAS.get(x['slot'], x['slot'])}"}
                   for x in targets]
    for x in targets:
        slot = SLOT_ALIAS.get(x.get('slot') or '', x.get('slot') or '')
        worn = worn_in(profile, slot)
        if x.get('item') and not_worn(profile, x):
            said_level = re.search(r'(\d{3})\s*(?:레벨|제)', question)          # 재질문에 '250레벨'로 답한 경우
            name, level, start, new = (x['item'], target_level(profile, x['item']) or (int(said_level.group(1)) if said_level else None),
                                       x.get('from_star') or 0, True)
        elif worn:
            name, level, start, new = worn['name'], worn.get('equip_level'), x.get('from_star') if x.get('from_star') is not None else worn.get('starforce') or 0, False
            if not context.starforce_possible(worn):
                rows.append({'slot': slot, 'name': name, 'skip': '스타포스를 올릴 수 없는 장비'})
                continue
        else:
            rows.append({'slot': slot or '?', 'name': x.get('item') or '?', 'skip': '어느 장비인지 모름'})
            continue
        if not level:
            unknown_level.append(name)
            continue
        if x['to_star'] <= start:
            rows.append({'slot': slot, 'name': name, 'skip': f'이미 {start}성'})
            continue
        if x['to_star'] > max_star({'equip_level': level}):
            rows.append({'slot': slot, 'name': name, 'skip': f"레벨 {level} 장비는 {max_star({'equip_level': level})}성까지"})
            continue
        price = prices.resolve(store, name, None, profile.get('job'), slot, prices.group_in(question))
        if not price['known']:
            unknown_price.append(price)
        rows.append({'slot': slot, 'name': name, 'level': level, 'from': start, 'to': x['to_star'], 'new': new, 'price': price})
    if unknown_level:
        ask(result, f"{', '.join(unknown_level)}의 장비 레벨을 몰라요. 몇 레벨 장비인가요?",
            [{'label': f'{lv}제', 'reply': f"{unknown_level[0]} {lv}레벨"} for lv in (140, 150, 160, 200, 250)], question,
            note='방어구 세트(에테르넬·아케인셰이드·앱솔랩스·루타비스)는 이름만으로 알아요.')
        return None
    group = prices.ask_group(unknown_price, profile.get('job'))
    if group:
        ask(result, *group[:2], question, note=group[2])
        return None
    if not conditions.answered(store):
        result.update(status='ask_conditions', content=conditions.ask_text(),
                      form=conditions.form(conditions.load(store)), pending=question)
        return None
    if unknown_price:
        # 스페어(파괴 시 같은 장비 하나 더) 값이 없으면 기대 비용이 성립하지 않는다. 모르는 것만 한 번에 묻는다.
        result.update(status='ask_price', content=prices.ask_text(unknown_price) + ' (파괴되면 같은 장비를 하나 더 마련하는 값)',
                      form=prices.form(unknown_price), pending=question)
        result['asked'] = [{'item': p['item']} for p in unknown_price]
        return None
    picked = conditions.load(store)
    total, lines, facts = 0, [], []
    for r in rows:
        if r.get('skip'):
            lines.append(f"- **{r['slot']} {r['name']}**: 계산 안 함({r['skip']})")
            facts.append(f"- {r['slot']} {r['name']}: 계산 안 함({r['skip']})")
            continue
        try:
            calc = starforce.expected({'level': r['level'], 'current_star': r['from'], 'target_star': r['to'],
                                       'spare_cost': r['price']['price'], **conditions.to_arguments(picked, r['from'], r['to'])})
        except AppError as e:
            lines.append(f"- **{r['slot']} {r['name']}**: 계산 못 함({e})")
            continue
        total += calc['expected_cost']
        tag = ' (새 장비 0성부터)' if r['new'] and r['from'] == 0 else ''
        lines.append(f"- **{r['slot']} {r['name']}**{tag} {r['from']}→{r['to']}성: **{calc['expected_cost']:,} 메소** "
                     f"(시도 {calc['expected_attempts']}회, 파괴 {calc['expected_destroys']}회)")
        facts.append(f"- {r['slot']} {r['name']} (레벨 {r['level']}{', 지금 끼지 않은 새 장비' if r['new'] else ''}) {r['from']}→{r['to']}성: "
                     f"{calc['expected_cost']:,} 메소, 시도 {calc['expected_attempts']}회, 파괴 {calc['expected_destroys']}회")
    swapped = [r for r in rows if r.get('new') and worn_in(profile, r['slot'])]
    note = ''
    if swapped:
        note = ('\n\n지금 끼고 있는 ' + ', '.join(f"{worn_in(profile, r['slot'])['name']}" for r in swapped)
                + '가 아니라, 물어보신 장비를 새로(0성부터) 맞추는 것으로 계산했어요. 이미 갖고 있다면 몇 성인지 알려 주세요.')
    result['starforce_text'] = ('\n'.join(lines) + (f"\n\n**합계: {total:,} 메소**" if total else '')
                                + f"\n- 적용 조건: {conditions.summary(picked)}" + note)
    return ('\n\n[강화 기대값 — 여러 장비] 앱이 이미 계산해 사용자에게 그대로 보여 준 값이다. 다시 나열하거나 더하지 말 것.\n'
            + '\n'.join(facts) + (f"\n- 합계: {total:,} 메소" if total else '') + f"\n- 적용 조건: {conditions.summary(picked)}")
