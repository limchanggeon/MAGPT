"""질문 이해(mepiti/planner.py)와 대화 길 고르기. 실제 AI는 부르지 않는다(계획 JSON을 돌려주는 가짜 모델)."""
import json
import tempfile
import unittest

from mepiti import chat, planner
from mepiti.core import AppError, Store


class FakePlanModel:
    """plan은 정해 둔 JSON을, analyse는 받은 사실 일부를 돌려준다."""
    def __init__(self, plan):
        self.plan_json, self.analysed = plan, []

    def plan(self, model, messages):
        self.plan_messages = messages
        if isinstance(self.plan_json, Exception):
            raise self.plan_json
        return json.dumps(self.plan_json, ensure_ascii=False), {}

    def analyse(self, model, facts, question, history=None, numbers_shown=False, consult=False):
        self.analysed.append({'facts': facts, 'question': question, 'consult': consult})
        return '도와드릴게요. 장비 상태나 기대값을 물어보세요.', {}

    def select(self, model, question, passages):
        return [0], {}


def plan(**kw):
    base = {'intents': ['chat'], 'slot': None, 'item': None, 'current_star': None, 'target_star': None,
            'target_cp': None, 'combine': False, 'money': False, 'rewritten': ''}
    return {**base, **kw}


class PlannerParseTests(unittest.TestCase):
    def test_parse_validates(self):
        self.assertIsNone(planner.parse('아님'))
        self.assertIsNone(planner.parse(json.dumps({'intents': ['모름']})))
        p = planner.parse(json.dumps(plan(intents=['starforce', 'starforce', 'consult'], target_star=99, slot='모자')))
        self.assertEqual(p['intents'], ['starforce', 'consult'])
        self.assertIsNone(p['target_star'])                       # 0~30만
        self.assertEqual(p['slot'], '모자')

    def test_tool_question_fills_context(self):
        p = planner.parse(json.dumps(plan(intents=['starforce'], slot='모자', target_star=21, rewritten='그럼 그건?')))
        self.assertEqual(planner.tool_question(p, '그럼 21성은?'), '그럼 그건? 모자 21성 기대값')
        p = planner.parse(json.dumps(plan(intents=['consult'], target_cp='3억', combine=True, rewritten='뭐부터 바꿔?')))
        q = planner.tool_question(p, '3억 가려면?')
        self.assertIn('목표 전투력 3억', q)
        self.assertIn('추천대로 다 바꾸면', q)

    def test_history_goes_into_messages(self):
        msgs = planner.messages('그럼 21성은?', [{'role': 'user', 'content': '모자 22성 기대값'}], {'저장된 목표 전투력': '2.50억'})
        self.assertIn('[앞 대화]', msgs[1]['content'])
        self.assertIn('모자 22성 기대값', msgs[1]['content'])
        self.assertIn('2.50억', msgs[1]['content'])


class PlannedRoutingTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(tempfile.mkdtemp())
        self.store.set_setting('model', 'gemini')

    def test_small_talk_gets_conversational_reply(self):
        model = FakePlanModel(plan(intents=['chat'], rewritten='넌 뭐 할 수 있어?'))
        result = chat.answer(self.store, model, {'message': 'ㅎㅇ 넌 뭐 할 줄 알아?'})
        self.assertEqual(result['status'], 'analysis')
        self.assertEqual(model.analysed[0]['consult'], 'chat')
        self.assertIn('[메피티가 할 수 있는 일]', model.analysed[0]['facts'])

    def test_character_intent_without_character_asks_to_register(self):
        model = FakePlanModel(plan(intents=['starforce'], slot='모자', target_star=22, rewritten='모자 22성 기대값'))
        result = chat.answer(self.store, model, {'message': '모자 22성 가려면 얼마?'}, nexon=object())
        self.assertEqual(result['status'], 'clarify')
        self.assertIn('캐릭터', result['content'])
        self.assertEqual(result['plan']['intents'], ['starforce'])

    def test_planner_failure_falls_back_to_rules(self):
        model = FakePlanModel(AppError('한도', 429))
        result = chat.answer(self.store, model, {'message': 'ㅎㅇ'})
        self.assertNotIn('plan', result)
        self.assertNotEqual(result['status'], 'analysis')         # 정규식 길: 근거 검색 → 보류

    def test_local_model_is_not_planned(self):
        from mepiti.adapters import ModelRouter
        router = ModelRouter(object(), {'gemini': object()})
        self.store.set_setting('model', 'qwen3.5:2b')
        self.assertIsNone(chat.make_plan(self.store, router, '내 장비 어때?', []))


if __name__ == '__main__':
    unittest.main()
