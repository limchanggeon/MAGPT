"""Agent regression checks use synthetic evidence, never account data or live APIs."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from mepiti import agent, chat, language, planner
from mepiti.adapters import ModelRouter, Ollama
from mepiti.core import AppError, KST, Store


def decision(action, **kwargs):
    return dict(action=action, query='', question='', claims=[], **kwargs) if not kwargs else {**decision(action), **kwargs}


def claim(text='시험 장비에는 시험 옵션을 사용합니다.', eid=1, quote='시험 옵션을 사용합니다.'):
    return decision('answer', claims=[dict(text=text, evidence_id=eid, quote=quote)])


class Model:
    def __init__(self, *steps):
        self.steps=iter(steps);self.inputs=[]

    def research(self, model, messages):
        self.inputs.append(json.loads(messages[-1]['content']))
        step=next(self.steps)
        if isinstance(step, Exception):raise step
        return json.dumps(step, ensure_ascii=False), {}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name);self.store.set_setting('model','test')

    def document(self, body='시험 장비에는 시험 옵션을 사용합니다.', approve=True, **meta):
        now=datetime.now(KST)
        metadata=dict(source_url='https://maplestory.nexon.com/test',source_type='official',region='KR',server_type='live',
                      topic='시험',version='v1',effective_from=(now-timedelta(days=1)).isoformat(),
                      valid_until=(now+timedelta(days=1)).isoformat())
        metadata.update(meta)
        did=self.store.import_document(dict(title='시험 자료',body=body,metadata=metadata))['id']
        if approve:self.store.review(dict(id=did,approve=True))
        return did

    def run_agent(self, model, query='시험'):
        result={}
        handled=agent.research(self.store,model,query,[],None,result)
        return handled,result

    def test_miss_refines_and_answers(self):
        self.document()
        m=Model(decision('search_documents',query='시험 옵션'),claim())
        handled,r=self.run_agent(m,'없는검색어')
        self.assertTrue(handled);self.assertEqual(r['status'],'answer')
        self.assertEqual(m.inputs[0]['reviewed_evidence'],[])
        self.assertTrue(m.inputs[1]['reviewed_evidence'])
        self.assertEqual(len(r['sources']),1)

    def test_invalid_id_quote_and_number_are_rejected(self):
        self.document()
        for c in [claim(eid=99),claim(quote='원문에 없는 내용'),claim(text='성공 확률은 99%입니다.')]:
            with self.subTest(c=c):
                _,r=self.run_agent(Model(c,decision('hold')))
                self.assertEqual(r['status'],'held');self.assertNotIn('sources',r)
                self.assertIn('validation',[a['action'] for a in r['actions']])

    def test_unreviewed_future_and_expired_never_reach_model(self):
        self.document(approve=False)
        self.document(effective_from=(datetime.now(KST)+timedelta(hours=1)).isoformat())
        self.document(effective_to=(datetime.now(KST)-timedelta(hours=1)).isoformat())
        m=Model(decision('hold'));self.run_agent(m)
        self.assertEqual(m.inputs[0]['reviewed_evidence'],[])

    def test_conflicting_versions_stop_before_generation(self):
        self.document(version='v1');self.document(version='v2')
        m=Model();_,r=self.run_agent(m)
        self.assertEqual(r['status'],'held');self.assertEqual(m.inputs,[])

    def test_repeated_search_has_call_budget(self):
        m=Model(*[decision('search_documents',query='시험')]*3)
        with patch.object(self.store,'search',wraps=self.store.search) as search:
            _,r=self.run_agent(m)
            self.assertEqual(search.call_count,1)
        self.assertEqual(len(m.inputs),3);self.assertEqual(r['status'],'held')

    def test_source_links_cannot_be_answer_evidence(self):
        m=Model(decision('find_sources',query='보스'),claim(),decision('hold'))
        _,r=self.run_agent(m,'보스')
        self.assertTrue(m.inputs[1]['unreviewed_source_links'])
        self.assertEqual(r['status'],'held');self.assertNotIn('sources',r)
        self.assertTrue(r['links'])

    def test_known_term_missing_evidence_retries_instead_of_quizzing_user(self):
        m=Model(decision('clarify',query='방어율 무시',question='방무의 뜻을 설명해 주세요.'),decision('hold'))
        _,r=self.run_agent(m,'방무 설명해줘')
        self.assertEqual(m.inputs[1]['searches'][-1]['query'],'방어율 무시')
        self.assertEqual(r['status'],'held')

    def test_free_text_clarification(self):
        _,r=self.run_agent(Model(decision('clarify',question='어느 장비를 말하나요?')),'이거')
        self.assertEqual(r['status'],'ask');self.assertEqual(r['pending'],'이거')
        self.assertEqual(r['form']['options'],[])

    def test_provider_failure_falls_back_visibly(self):
        handled,r=self.run_agent(Model(AppError('연결 실패')))
        self.assertFalse(handled);self.assertEqual(r['actions'][-1]['action'],'fallback')

    def test_new_search_evidence_visible_after_full_window(self):
        docs=[dict(id=str(i),title='시험',metadata={'source_type':'official','version':'v1'},passages=[f'옛 자료 {i} 문단 {j}' for j in range(3)]) for i in range(5)]
        new=dict(id='new',title='새 자료',metadata={'source_type':'official','version':'v1'},passages=['새로 찾은 근거 문장'])
        m=Model(decision('search_documents',query='새 검색'),decision('hold'))
        with patch.object(self.store,'search',side_effect=[(docs,False),([new],False)]):self.run_agent(m)
        self.assertEqual(len(m.inputs[1]['reviewed_evidence']),agent.MAX_PASSAGES)
        self.assertTrue(any(p['doc_id']=='new' for p in m.inputs[1]['reviewed_evidence']))
        ids=[p['id'] for p in m.inputs[1]['reviewed_evidence']];self.assertEqual(len(ids),len(set(ids)))

    def test_rewritten_question_reaches_actual_retrieval(self):
        class Planned(Model):
            def plan(self,model,messages):
                return json.dumps({'intents':['rules'],'rewritten':'방어율 무시 시험'}),{}
        m=Planned(decision('hold'))
        with patch.object(self.store,'search',wraps=self.store.search) as search:
            chat.answer(self.store,m,{'message':'방무 설명해줘'})
        self.assertIn('방어율 무시',search.call_args.args[0])


class LanguageAndProviderTests(unittest.TestCase):
    def test_aliases_preserve_numbers_unknowns_and_are_idempotent(self):
        raw='에테뚝 18→22성 보공 30% 방무는? 알수없는약어'
        expanded=language.expand(raw)
        self.assertIn('에테르넬 모자 18→22성',expanded)
        self.assertIn('보스 몬스터 데미지 30%',expanded)
        self.assertIn('방어율 무시는',expanded)
        self.assertIn('알수없는약어',expanded)
        self.assertEqual(language.expand(expanded),expanded)
        self.assertEqual(language.expand('보공책임자'), '보공책임자')

    def test_local_router_supports_plan_and_research(self):
        class Local:
            def plan(self,*args):return 'plan',{}
            def research(self,*args):return 'research',{}
        router=ModelRouter(Local(),{})
        self.assertEqual(router.plan('qwen3.5:2b',[])[0],'plan')
        self.assertEqual(router.research('qwen3.5:2b',[])[0],'research')

    def test_ollama_uses_structured_schema(self):
        local=Ollama();local._capabilities['test']=[]
        with patch('mepiti.adapters.request_json',return_value={'message':{'content':'{}'}}) as call:
            local.research('test',[])
        payload=call.call_args.args[1]
        self.assertEqual(payload['format'],agent.schema_json())
        self.assertFalse(payload['stream'])

    def test_followup_plan_keeps_calculation_intent(self):
        p=planner.parse(json.dumps({'intents':['gear_status'],'target_star':21,'rewritten':'에테뚝 21성 기대값'}))
        self.assertIn('starforce',p['intents'])
        self.assertIn('에테르넬 모자',planner.tool_question(p,'그럼 21성은?'))

    def test_malformed_tool_and_plan_shapes_rejected(self):
        for value in [{'action':'execute_shell'}, {'action':'answer','claims':[{'text':'x','quote':'x','evidence_id':True}]}, {'action':'search_documents','query':[]}]:
            self.assertIsNone(agent.parse(json.dumps(value)))
        self.assertIsNone(planner.parse('{"intents": "rules"}'))
        self.assertIsNone(planner.parse('{"intents": [{}]}'))
