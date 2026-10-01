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


class FakeNexonProfile:
    """대표 캐릭터 하나의 장비만 돌려준다."""
    def character(self, name, details=False):
        eq = [{'slot': '모자', 'name': '에테르넬 나이트헬름', 'starforce': 18, 'equip_level': 250},
              {'slot': '반지1', 'name': '어웨이크 링', 'starforce': 0, 'equip_level': 0},
              {'slot': '반지2', 'name': '마이스터링', 'starforce': 15, 'equip_level': 140},
              {'slot': '반지3', 'name': '이터널 플레임 링', 'starforce': 0, 'equip_level': 0},
              {'slot': '장갑', 'name': '에테르넬 나이트글러브', 'starforce': 18, 'equip_level': 250}]
        return {'name': name, 'level': 291, 'job': '렌', 'combat_power': 1.5e8, 'stats': [], 'equipment': eq,
                'retrieved_at': '2026-10-01', 'warnings': []}


class AskTests(unittest.TestCase):
    def setUp(self):
        from mepiti import conditions
        self.store = Store(tempfile.mkdtemp())
        self.store.set_setting('model', 'gemini')
        conditions.save(self.store, conditions.DEFAULTS)
        self.store.character_save({'name': '나', 'budget': 0, 'goal': '', 'main': True})
        self.nexon = FakeNexonProfile()

    def ask(self, message, p, session=None):
        model = FakePlanModel(p)
        data = {'message': message, **({'session_id': session} if session else {})}
        return chat.answer(self.store, model, data, self.nexon), model

    def test_starforce_without_slot_asks_which_item(self):
        r, _ = self.ask('22성 기대값 얼마야?', plan(intents=['starforce'], target_star=22, rewritten='22성 기대값 얼마야?'))
        self.assertEqual(r['status'], 'ask')
        labels = [o['label'] for o in r['form']['options']]
        self.assertIn('모자 · 에테르넬 나이트헬름 18성', labels)
        self.assertFalse(any('어웨이크' in l for l in labels))       # 스타포스를 못 올리는 장비는 빼고
        r2, _ = self.ask('16성 기대값', plan(intents=['starforce'], target_star=16, rewritten='16성 기대값'))
        self.assertEqual([o['reply'] for o in r2['form']['options']], ['반지2'])   # 이미 16성 이상인 장비는 빼고
        self.assertEqual(r['pending'], '22성 기대값 얼마야?')

    def test_ring_word_narrows_options(self):
        r, _ = self.ask('반지 22성 기대값', plan(intents=['starforce'], slot='반지', target_star=22, rewritten='반지 22성 기대값'))
        self.assertEqual([o['reply'] for o in r['form']['options']], ['반지2'])

    def test_missing_target_star_asks_with_steps(self):
        r, _ = self.ask('모자 강화 비용', plan(intents=['starforce'], slot='모자', rewritten='모자 강화 비용'))
        self.assertEqual(r['status'], 'ask')
        self.assertEqual([o['label'] for o in r['form']['options']], ['19성', '20성', '21성', '22성', '23성'])

    def test_reply_is_joined_with_pending_question(self):
        r, _ = self.ask('22성 기대값 얼마야?', plan(intents=['starforce'], target_star=22, rewritten='22성 기대값 얼마야?'))
        r2, model = self.ask('모자', plan(intents=['starforce'], slot='모자', target_star=22, rewritten='모자 22성 기대값'),
                             session=r['session_id'])
        self.assertIn('22성 기대값 얼마야? 모자', model.plan_messages[1]['content'])
        self.assertNotEqual(r2['status'], 'ask')                     # 이제 계산으로 넘어간다(노작값을 묻는 단계)

    def test_consult_without_target_asks_target_power(self):
        r, _ = self.ask('다음 스펙업 뭐 해?', plan(intents=['consult'], rewritten='다음 스펙업 뭐 해?'))
        self.assertEqual(r['status'], 'ask')
        self.assertEqual([o['label'] for o in r['form']['options']], ['2억', '2억5천', '3억'])   # 1.5억의 1.3·1.6·2배 근처
        self.assertTrue(r['form']['options'][1]['reply'].startswith('목표 전투력'))

    def test_vague_question_uses_planner_clarify(self):
        r, _ = self.ask('이거 어때?', plan(intents=['gear_status'], rewritten='이거 어때?', clarify='어떤 걸 볼까요?',
                                          options=['내 장비 전체', '특정 부위']))
        self.assertEqual(r['status'], 'ask')
        self.assertEqual(r['content'], '어떤 걸 볼까요?')

    def test_cp_label(self):
        self.assertEqual(chat.cp_label(2.5e8), '2억5천')
        self.assertEqual(chat.cp_label(3e8), '3억')
        self.assertEqual(chat.cp_label(2.96e8), '3억')
