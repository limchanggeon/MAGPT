import base64
import io
import json
import re
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mepiti.adapters import Nexon, Ollama, Vault, recognize
from mepiti.chat import answer
from mepiti import context, prices, starforce
from mepiti.conversion import convert, cooldowns, jobs
from mepiti.core import AppError, KST, Store, calculate, identifier, now
from mepiti.server import Application, make_server
from pathlib import Path

# 넥슨 item-equipment API가 돌려주는 슬롯 이름. 장비창 배치표가 이 목록을 모두 덮어야 한다.
NEXON_EQUIPMENT_SLOTS=['모자','얼굴장식','눈장식','귀고리','상의','하의','신발','장갑','망토','보조무기','무기',
                       '반지1','반지2','반지3','반지4','펜던트','펜던트2','훈장','벨트','어깨장식','포켓 아이템',
                       '기계 심장','뱃지','엠블렘',
                       # item_equipment에는 없지만 장비창에 자리가 있는 칸
                       '칭호','안드로이드']

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def document(self, **meta):
        current=datetime.now(KST)
        return self.store.import_document({'title':'제논 스킬 시험 자료','body':'제논 시험 스킬은 조건 A에서 적용됩니다.\n실제 게임 정보가 아닌 테스트 전용 문서입니다.','metadata':{'source_url':'https://maplestory.nexon.com/test','source_type':'official','region':'KR','server_type':'live','topic':'제논','effective_from':(current-timedelta(days=1)).isoformat(),'valid_until':(current+timedelta(days=7)).isoformat(),'version':'test-v1',**meta}})['id']
    def test_unreviewed_never_used(self):
        self.document()
        self.assertEqual(self.store.search('제논')[0],[])
    def test_reviewed_evidence(self):
        did=self.document();self.store.review({'id':did,'approve':True})
        self.assertEqual(len(self.store.search('제논')[0]),1)
    def test_expired_future_test_world_excluded(self):
        current=datetime.now(KST)
        for meta in [{'effective_to':(current-timedelta(hours=1)).isoformat()},{'effective_from':(current+timedelta(days=1)).isoformat()},{'server_type':'test'},{'region':'other'}]:
            did=self.document(**meta)
            try:self.store.review({'id':did,'approve':True})
            except AppError:pass
        self.assertEqual(self.store.search('제논')[0],[])
    def test_review_requires_metadata(self):
        did=self.document(version=None)
        with self.assertRaises(AppError): self.store.review({'id':did,'approve':True})
    def test_conflict_held(self):
        for version in ['v1','v2']:
            did=self.document(version=version);self.store.review({'id':did,'approve':True})
        self.assertTrue(self.store.search('제논')[1])
        r=answer(self.store,Ollama(),{'message':'제논 스킬 알려줘'})
        self.assertEqual(r['status'],'held');self.assertIn('서로 다른',r['content'])
    def test_source_spoof_rejected(self):
        with self.assertRaises(AppError): self.document(source_url='https://nexon.com.evil.test/x')
        with self.assertRaises(AppError): self.document(source_url='javascript:alert(1)')
    def test_no_evidence_held_and_saved(self):
        r=answer(self.store,Ollama(),{'message':'이번 패치 알려줘'})
        self.assertEqual(r['status'],'held');self.assertEqual(len(self.store.messages(r['session_id'])),2)
    def test_alias_and_ambiguity(self):
        self.assertEqual(answer(self.store,Ollama(),{'message':'기댓값이 뭐야?'})['status'],'term')
        self.assertEqual(answer(self.store,Ollama(),{'message':'환산 알려줘'})['status'],'clarify')
        self.assertEqual(answer(self.store,Ollama(),{'message':'대장장이 비용'})['status'],'clarify')
    def test_llm_cannot_inject_game_claim(self):
        did=self.document();self.store.review({'id':did,'approve':True});self.store.set_setting('model','test')
        with patch('mepiti.adapters.request_json',return_value={'message':{'content':'{"ids": [0], "claim": "강화는 100% 성공합니다"}'}}):
            r=answer(self.store,Ollama(),{'message':'제논 스킬'})
        self.assertEqual(r['status'],'evidence');self.assertNotIn('100%',r['content']);self.assertEqual(r['sources'][0]['id'],did)
    def test_invalid_model_selection_fallback(self):
        did=self.document();self.store.review({'id':did,'approve':True});self.store.set_setting('model','test')
        with patch('mepiti.adapters.request_json',return_value={'message':{'content':'{"ids": [999]}'}}):
            r=answer(self.store,Ollama(),{'message':'제논 스킬'})
        self.assertEqual(r['status'],'evidence');self.assertIn('형식 검증',r['conditions'][0])
    def test_character_main_and_snapshots(self):
        first=self.store.character_save({'name':'테스트1','budget':0})['id']
        second=self.store.character_save({'name':'테스트2','budget':100,'main':True})['id']
        with self.store.db() as db:
            for level in [200,201]:db.execute('INSERT INTO snapshots VALUES(?,?,?,?)',(identifier(),second,json.dumps({'level':level}),now()))
            db.execute('INSERT INTO snapshots VALUES(?,?,?,?)',(identifier(),first,'{}',(datetime.now(KST)-timedelta(days=31)).isoformat()))
        chars=self.store.characters()
        self.assertEqual(sum(c['main'] for c in chars),1);self.assertEqual(chars[0]['changes']['level'],1);self.assertEqual(chars[1]['snapshots'],[])
    def test_duplicate_character_rolls_back_main(self):
        self.store.character_save({'name':'하나','budget':0})
        self.store.character_save({'name':'둘','budget':0,'main':True})
        with self.assertRaises(AppError): self.store.character_save({'name':'하나','budget':0,'main':True})
        self.assertEqual(self.store.characters()[0]['name'],'둘')

class CalculationTests(unittest.TestCase):
    def test_probability(self):
        r=calculate({'kind':'probability','probability':10,'cost':100,'trials':10})
        self.assertEqual(r['expected_trials'],10);self.assertEqual(r['expected_cost'],1000)
        self.assertAlmostEqual(r['success_probability'],1-.9**10);self.assertEqual(r['median_trials'],7)
    def test_boundaries(self):
        for trials,expected in [(0,0),(1,1)]:
            r=calculate({'kind':'probability','probability':100,'cost':0,'trials':trials})
            self.assertEqual(r['success_probability'],expected)
        for p in [0,-1,101,'NaN',float('inf'),True]:
            with self.assertRaises(AppError):calculate({'kind':'probability','probability':p,'cost':0,'trials':10})
        with self.assertRaises(AppError): calculate({'kind':'probability','probability':10,'cost':0,'trials':1.1})
    def test_growth(self):
        self.assertEqual(calculate({'kind':'growth','current':100,'target':151,'daily':10})['days'],6)
        self.assertEqual(calculate({'kind':'growth','current':100,'target':50,'daily':10})['days'],0)
        with self.assertRaises(AppError):calculate({'kind':'growth','current':0,'target':100,'daily':0})

class AdapterTests(unittest.TestCase):
    def test_nexon_fields_and_no_key_in_snapshot(self):
        class TestVault:
            def get(self):return 'secret-test-key'
        replies=[{'ocid':'abc'},{'character_name':'테스트','character_level':260,'character_class':'제논','world_name':'스카니아','irrelevant':'huge response'},{'final_stat':[{'stat_name':'전투력','stat_value':'1,000'}]}]
        with patch('mepiti.adapters.request_json',side_effect=replies) as call:
            snapshot=Nexon(TestVault()).character('테스트')
        self.assertEqual(snapshot['combat_power'],1000);self.assertNotIn('irrelevant',snapshot)
        self.assertNotIn('secret-test-key',json.dumps(snapshot));self.assertEqual(call.call_args.kwargs['headers']['x-nxopen-api-key'],'secret-test-key')
    def test_equipment_upgrade_and_add_option_conversion(self):
        class TestVault:
            def get(self):return 'secret-test-key'
        equipment={'preset_no':1,'date':'2026-09-28T00:00+09:00','item_equipment':[
            {'item_equipment_slot':'모자','item_equipment_part':'모자','item_name':'시험 투구','starforce':'18','scroll_upgrade':'12',
             'item_add_option':{'str':'123','dex':'28','all_stat':'6','attack_power':'0'},'item_total_option':{'str':'414'},
             'potential_option_grade':'레전드리','item_icon':'https://open.api.nexon.com/static/maplestory/a.png'},
            {'item_equipment_slot':'무기','item_equipment_part':'장검','item_name':'시험 검','starforce':'22','scroll_upgrade':'8',
             'item_add_option':{'str':'72','all_stat':'6','attack_power':'210'},'item_total_option':{'attack_power':'900'},
             'item_base_option':{'attack_power':'340'},
             'potential_option_grade':'레전드리','item_icon':'https://evil.test/x.png'}]}
        replies=[{'ocid':'abc'},{'character_name':'테스트','character_level':285,'character_class':'히어로'},
                 {'final_stat':[{'stat_name':'STR','stat_value':'50,000'},{'stat_name':'INT','stat_value':'4'}]},equipment,{}]
        with patch('mepiti.adapters.request_json',side_effect=replies):
            snapshot=Nexon(TestVault()).character('테스트',details=True)
        self.assertEqual(snapshot['equipment_status'],'available');self.assertEqual(snapshot['main_stat'],'STR')
        hat,weapon=snapshot['equipment']
        self.assertEqual((hat['starforce'],hat['scroll_upgrade']),(18,12))
        # 방어구 급 = 주스탯 + 올스탯% x 10 + 공/마 x 4 (커뮤니티 약식 기준)
        self.assertEqual(hat['add_grade']['grade'],183)   # 123 + 6*10 + 0*4
        self.assertIsNone(hat['add_grade']['tier'])
        # 무기는 급이 아니라 공/마 추옵 단계로 부른다. 210/340 = 0.618 -> 1추
        self.assertEqual(weapon['add_grade']['tier'],'1추')
        self.assertIsNone(weapon['add_grade']['grade'])
        self.assertEqual(weapon['add_grade']['label'],'1추 · 올 6%')
        self.assertIsNone(weapon['icon'])  # 넥슨 정적 주소가 아닌 아이콘은 버린다.
    def test_title_and_android_become_equipment_slots(self):
        class TestVault:
            def get(self):return 'secret-test-key'
        equipment={'item_equipment':[],'title':{'title_name':'쑥쑥 새싹','title_description':'올스탯 +10',
                   'title_icon':'https://open.api.nexon.com/static/maplestory/item/icon/A'}}
        android={'android_name':'시험 안드로이드','android_icon':'https://open.api.nexon.com/static/maplestory/item/icon/B'}
        replies=[{'ocid':'abc'},{'character_name':'테스트','character_level':285},{'final_stat':[]},equipment,android]
        with patch('mepiti.adapters.request_json',side_effect=replies):
            snapshot=Nexon(TestVault()).character('테스트',details=True)
        self.assertEqual([e['slot'] for e in snapshot['equipment']],['칭호','안드로이드'])
        self.assertEqual(snapshot['equipment'][0]['description'],'올스탯 +10')
        self.assertEqual(snapshot['warnings'],[])
    def test_android_failure_warns_without_losing_equipment(self):
        class TestVault:
            def get(self):return 'secret-test-key'
        replies=[{'ocid':'abc'},{'character_name':'테스트','character_level':285},{'final_stat':[]},
                 {'item_equipment':[]},AppError('안드로이드 없음',502)]
        def respond(*a,**k):
            reply=replies.pop(0)
            if isinstance(reply,AppError):raise reply
            return reply
        with patch('mepiti.adapters.request_json',side_effect=respond):
            snapshot=Nexon(TestVault()).character('테스트',details=True)
        self.assertEqual(snapshot['equipment_status'],'available')
        self.assertTrue(any('안드로이드' in w for w in snapshot['warnings']))
    def test_armour_grade_counts_attack_add_option(self):
        """벨트 STR 24 + 올스탯 5% + 공격력 6 -> 24 + 50 + 24 = 98급.

        실제 계정의 스크린샷 표기와 일치하는 값이다. 공/마를 빼면 74가 되어 어긋난다.
        """
        from mepiti.adapters import add_option_grade
        belt={'item_add_option':{'str':'24','all_stat':'5','attack_power':'6'}}
        self.assertEqual(add_option_grade(belt,'벨트','str')['grade'],98)
        self.assertEqual(add_option_grade(belt,'벨트','str')['label'],'98급')
    def test_weapon_tiers_follow_ratio_table(self):
        from mepiti.adapters import add_option_grade
        base={'attack_power':'295'}   # 아케인셰이드 투핸드소드
        for power,tier in ((182,'1추'),(142,'2추'),(108,'3추'),(78,'4추'),(54,'5추'),(10,None)):
            with self.subTest(power=power):
                item={'item_base_option':base,'item_add_option':{'attack_power':str(power)}}
                self.assertEqual(add_option_grade(item,'무기','str')['tier'],tier)
    def test_equipment_layout_covers_every_api_slot(self):
        source=(Path(__file__).resolve().parent.parent/'mepiti'/'static'/'characters.js').read_text(encoding='utf-8')
        body=re.search(r'const EQUIPMENT_LAYOUT=\[(.*?)\n\];',source,re.S).group(1)
        placed=[c for row in re.findall(r'\[(.*?)\]',body) for c in
                (v.strip().strip("'") for v in row.split(',')) if c!='null']
        self.assertEqual(sorted(placed),sorted(NEXON_EQUIPMENT_SLOTS))
    def test_keyring_refuses_plaintext(self):
        with patch('keyring.get_keyring',return_value=object()):
            with self.assertRaises(AppError):Vault().get()
    def test_bad_image(self):
        with self.assertRaises(AppError): recognize(base64.b64encode(b'not an image').decode())

class CharacterAnalysisTests(unittest.TestCase):
    """캐릭터 분석은 조회한 사실만 근거로 삼는다. 모델이 규칙을 지어내면 그 서술을 버린다."""
    PROFILE = {'name':'테스트','level':291,'job':'렌','world':'크로아','combat_power':75522712,
               'retrieved_at':'2026-09-28T14:00:00+09:00','api_date':None,'main_stat':'STR',
               'stats':[{'name':'전투력','value':'75522712'},{'name':'STR','value':'48547'},
                        {'name':'보스 몬스터 데미지','value':'221.00'}],
               'equipment':[
                   {'slot':'무기','name':'제네시스 창세검','starforce':22,'scroll_upgrade':8,
                    'potential_grade':'레전드리','potential':['공격력 +12%'],'additional_grade':None,
                    'additional_potential':[],'add_grade':{'tier':'1추','grade':None,'label':'1추 · 올 6%'}},
                   {'slot':'벨트','name':'골든 클로버 벨트','starforce':18,'scroll_upgrade':4,
                    'potential_grade':'레전드리','potential':[],'additional_grade':None,
                    'additional_potential':[],'add_grade':{'tier':None,'grade':98,'label':'98급'}},
                   {'slot':'반지2','name':'이터널 플레임 링','starforce':0,'scroll_upgrade':0,
                    'potential_grade':'레전드리','potential':[],'additional_grade':None,
                    'additional_potential':[],'add_grade':{'tier':None,'grade':0,'label':''}}]}
    class FakeNexon:
        def __init__(self,profile): self.profile=profile
        def character(self,name,details=False): return self.profile
    class FakeModel:
        def __init__(self,text): self.text=text; self.seen=None
        def analyse(self,model,facts,question,history=None):
            self.seen=facts; return self.text,{'eval_count':10}
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(self.tmp.name)
        self.store.character_save({'name':'테스트','budget':0,'main':True})
        self.store.set_setting('model','test-model')
    def tearDown(self): self.tmp.cleanup()
    def ask(self,text,question='내 캐릭터 약한 부위 알려줘'):
        model=self.FakeModel(text)
        r=answer(self.store,model,{'message':question},self.FakeNexon(self.PROFILE))
        return r,model
    def test_context_carries_real_equipment_facts(self):
        facts=context.as_text(context.build(self.PROFILE,{'goal':'세렌','budget':100}))
        self.assertIn('제네시스 창세검',facts)
        self.assertIn('1추',facts);self.assertIn('98급',facts)
        self.assertIn('반지2: 0성',facts)          # 스타포스가 낮은 부위로 추려진다
        self.assertIn('세렌',facts)
    def test_analysis_uses_model_text(self):
        r,model=self.ask('반지2가 0성이라 먼저 올릴 자리입니다.')
        self.assertEqual(r['status'],'analysis')
        self.assertIn('반지2',r['content'])
        self.assertIn('제네시스 창세검',model.seen)   # 모델에 실제 장비가 넘어갔다
        self.assertIn('facts',r)
    def test_invented_amount_is_dropped(self):
        """실제로 걸렸던 사례: 모델이 가상의 벨트 값 3,500,000,000 메소를 지어냈다.

        '35억 메소'만 막던 기존 규칙은 자릿점 표기를 통과시켰다. 이제 서술의 모든
        큰 수치가 넘겨준 사실 안에 있어야 한다.
        """
        r,_=self.ask('새로운 벨트 예시: 노작값 3,500,000,000 메소, 추가옵션 등급 100급')
        self.assertEqual(r['status'],'context')
        self.assertTrue(any('없는 수치' in c for c in r['conditions']))
    def test_restating_supplied_numbers_is_allowed(self):
        r,_=self.ask('전투력은 75,522,712이고 벨트는 98급, 18성입니다.')
        self.assertEqual(r['status'],'analysis')
    def test_equivalent_units_are_recognised(self):
        from mepiti.chat import unsupported_numbers
        self.assertEqual(unsupported_numbers('32억 메소','벨트: 3,200,000,000 메소'),[])
        self.assertEqual(unsupported_numbers('3,200,000,000 메소','벨트: 32억 메소'),[])
        self.assertTrue(unsupported_numbers('99억 메소','벨트: 32억 메소'))
    def test_fabricated_probability_is_dropped(self):
        for made_up in ['22성 성공 확률 3% 입니다.','스타포스 파괴 확률이 있습니다.',
                        '이 장비는 시세 50억 메소 정도입니다.','가격은 3000만 메소입니다.']:
            with self.subTest(text=made_up):
                r,_=self.ask(made_up)
                self.assertEqual(r['status'],'context')
                self.assertNotIn(made_up,r['content'])       # 서술은 버려지고 사실만 남는다
                self.assertIn('제네시스 창세검',r['content'])
    def test_repeated_name_syllable_is_corrected(self):
        from mepiti.chat import fix_name
        self.assertEqual(fix_name('시험렌렌렌의 장비','시험렌렌'),'시험렌렌의 장비')
        self.assertEqual(fix_name('시험렌렌렌렌은','시험렌렌'),'시험렌렌은')
        self.assertEqual(fix_name('시험렌렌은 강하다','시험렌렌'),'시험렌렌은 강하다')
        self.assertEqual(fix_name('다른 이름','시험렌렌'),'다른 이름')
    def test_analysis_output_uses_exact_name(self):
        r,_=self.ask('테스트트트의 장비는 좋습니다.')
        self.assertIn('테스트의 장비',r['content'])
    def test_model_failure_falls_back_to_facts(self):
        class Broken:
            def analyse(self,*a,**k): raise AppError('모델 없음',502)
        r=answer(self.store,Broken(),{'message':'내 장비 어때'},self.FakeNexon(self.PROFILE))
        self.assertEqual(r['status'],'context')
        self.assertIn('제네시스 창세검',r['content'])
    def test_without_model_only_facts(self):
        self.store.set_setting('model','')
        r,_=self.ask('아무 말')
        self.assertEqual(r['status'],'context')
        self.assertIn('모델을 고르면',' '.join(r['conditions']))
    def test_character_lookup_failure_holds(self):
        class Failing:
            def character(self,*a,**k): raise AppError('조회 실패',502)
        r=answer(self.store,self.FakeModel('x'),{'message':'내 스펙 봐줘'},Failing())
        self.assertEqual(r['status'],'clarify')
        self.assertIn('보류',r['content'])
    def test_non_character_question_still_needs_evidence(self):
        r=answer(self.store,self.FakeModel('아무거나'),{'message':'스타포스 확률 알려줘'},
                 self.FakeNexon(self.PROFILE))
        self.assertEqual(r['status'],'held')

class PriceTests(unittest.TestCase):
    """노작값: 저장된 값 -> 외부 조회기 -> 되묻기 순. 모르면 지어내지 않는다."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(self.tmp.name)
        prices.register_fetcher(None)
    def tearDown(self):
        prices.register_fetcher(None); self.tmp.cleanup()
    def test_parses_korean_amounts(self):
        cases={'32억':3_200_000_000,'1조':1_000_000_000_000,'3,000만':30_000_000,
               '25000000000':25_000_000_000,'1조 2000억':1_200_000_000_000,'2.5억':250_000_000}
        for text,expected in cases.items():
            with self.subTest(text=text): self.assertEqual(prices.parse_price(text),expected)
        self.assertIsNone(prices.parse_price('모름'))
    def test_unknown_is_not_invented(self):
        r=prices.resolve(self.store,'없는 장비')
        self.assertFalse(r['known']); self.assertNotIn('price',r)
    def test_saved_value_is_reused(self):
        self.store.price_save({'item':'골든 클로버 벨트','add_grade':98,'price':3.2e10,'source':'user'})
        r=prices.resolve(self.store,'골든 클로버 벨트',98)
        self.assertTrue(r['known']); self.assertEqual(r['price'],3.2e10); self.assertEqual(r['source'],'user')
    def test_fetcher_used_only_when_enabled(self):
        calls=[]
        prices.register_fetcher(lambda item,grade:(calls.append(item),{'price':1e10})[1])
        self.assertFalse(prices.resolve(self.store,'검')['known'])   # 기본은 꺼져 있다
        self.assertEqual(calls,[])
        self.store.set_setting(prices.FETCH_SETTING,'1')
        r=prices.resolve(self.store,'검')
        self.assertTrue(r['known']); self.assertEqual(calls,['검'])
        self.assertEqual(self.store.price_count(),1)                 # 조회 결과는 저장된다
        prices.resolve(self.store,'검')
        self.assertEqual(calls,['검'])                               # 두 번째는 조회하지 않는다
    def test_daily_limit_blocks_fetch(self):
        prices.register_fetcher(lambda item,grade:{'price':1e10})
        self.store.set_setting(prices.FETCH_SETTING,'1')
        self.store.set_setting(prices.DAILY_LIMIT_SETTING,'1')
        prices.resolve(self.store,'검1')
        r=prices.resolve(self.store,'검2')
        self.assertFalse(r['known']); self.assertEqual(r['reason'],'limit')
    def test_user_values_do_not_count_toward_limit(self):
        prices.register_fetcher(lambda item,grade:{'price':1e10})
        self.store.set_setting(prices.FETCH_SETTING,'1')
        self.store.set_setting(prices.DAILY_LIMIT_SETTING,'1')
        self.store.price_save({'item':'직접입력','price':1,'source':'user'})
        self.assertTrue(prices.resolve(self.store,'검')['known'])

class PriceConversationTests(CharacterAnalysisTests):
    """되묻기 -> 사용자가 답 -> 저장 -> 다음부터 묻지 않는다."""
    def test_price_question_asks_instead_of_guessing(self):
        r,_=self.ask('아무 말','벨트 바꾸는 게 이득이야? 노작값 기준으로')
        self.assertEqual(r['status'],'ask_price')
        self.assertIn('노작값',r['content'])
        self.assertTrue(r['asked'])
    def test_reply_is_stored_and_not_asked_again(self):
        first,_=self.ask('아무 말','벨트 사는 게 나아? 가격 기준으로')
        self.assertEqual(first['status'],'ask_price')
        item=first['asked'][0]['item']
        reply=answer(self.store,self.FakeModel('x'),{'message':f'{item} 32억','session_id':first['session_id']},
                     self.FakeNexon(self.PROFILE))
        self.assertEqual(reply['status'],'price')
        self.assertEqual(self.store.price_lookup(item)['price'],3_200_000_000)
        again=answer(self.store,self.FakeModel('벨트 관련 서술'),
                     {'message':'벨트 사는 게 나아? 가격 기준으로','session_id':first['session_id']},
                     self.FakeNexon(self.PROFILE))
        self.assertNotEqual(again['status'],'ask_price')
    def test_stray_number_is_not_captured(self):
        r=answer(self.store,self.FakeModel('x'),{'message':'골든 클로버 벨트 32억'},self.FakeNexon(self.PROFILE))
        self.assertNotEqual(r['status'],'price')
        self.assertEqual(self.store.price_count(),0)
    def test_known_price_reaches_the_model(self):
        first,_=self.ask('아무 말','벨트 가격 기준으로 이득이야?')
        item=first['asked'][0]['item']
        self.store.price_save({'item':item,'price':3.2e10,'source':'user'})
        r,model=self.ask('서술','벨트 가격 기준으로 이득이야?')
        self.assertEqual(r['status'],'analysis')
        self.assertIn('저장된 노작값',model.seen)
        self.assertIn(item,model.seen)

class StarforceTests(unittest.TestCase):
    """확률표·비용식은 mesulive 이식. 기대값은 연립방정식으로 정확히 푼다."""
    def test_probability_rows_sum_to_one_after_adjustment(self):
        table,adjusted=starforce.normalised_table()
        for i,row in enumerate(table):
            with self.subTest(star=i): self.assertAlmostEqual(sum(row),1.0,places=9)
        self.assertEqual([a['star'] for a in adjusted],[26])   # 원본에서 어긋난 행은 26성뿐
    def test_star_catch_is_baked_in(self):
        for star,base in ((0,0.95),(1,0.90),(10,0.50),(15,0.30),(17,0.15),(20,0.30)):
            with self.subTest(star=star):
                self.assertAlmostEqual(starforce.PROB_TABLE[star][0],base*1.05,places=9)
    def test_cost_formula_matches_mesulive(self):
        costs=starforce.attempt_costs(250)
        self.assertEqual(costs[0],round((1000+250**3*1/36)/100)*100)
        self.assertEqual(costs[18],1000+round(250**3*19**2.7/70/100)*100)
        self.assertEqual(costs[21],1000+round(250**3*22**2.7/125/100)*100)
    def test_reachable_star_limits(self):
        self.assertEqual(starforce.reachable_star(90),5)
        self.assertEqual(starforce.reachable_star(120),15)   # 127 이하가 15성
        self.assertEqual(starforce.reachable_star(130),20)   # 137 이하가 20성
        self.assertEqual(starforce.reachable_star(250),30)
        with self.assertRaises(AppError):
            starforce.expected({'level':120,'current_star':10,'target_star':20})
    def test_spare_cost_scales_with_destroys(self):
        base=starforce.expected({'level':250,'current_star':18,'target_star':22,'spare_cost':0})
        spare=32_000_000_000
        withspare=starforce.expected({'level':250,'current_star':18,'target_star':22,'spare_cost':spare})
        self.assertFalse(base['spare_cost_known'])
        self.assertTrue(withspare['spare_cost_known'])
        added=withspare['expected_cost']-base['expected_cost']
        self.assertAlmostEqual(added/spare,base['expected_destroys'],places=2)
    def test_safeguard_removes_destroy(self):
        plain=starforce.expected({'level':250,'current_star':15,'target_star':18,'spare_cost':1e10})
        safe=starforce.expected({'level':250,'current_star':15,'target_star':18,'spare_cost':1e10,
                                 'safeguard':[15,16,17]})
        self.assertEqual(safe['expected_destroys'],0.0)
        self.assertLess(safe['expected_cost'],plain['expected_cost'])
    def test_events_change_probability_and_cost(self):
        base=dict(level=250,current_star=18,target_star=22,spare_cost=4.5e9)
        plain=starforce.expected(base)
        # 파괴 30% 감소: 파괴 횟수만 줄고 비용 할인은 없다
        reduced=starforce.expected({**base,'event':'21성 이하 파괴 30% 감소'})
        self.assertLess(reduced['expected_destroys'],plain['expected_destroys'])
        self.assertAlmostEqual(reduced['discount_ratio'],0.0)
        # 샤타포스 = 파괴 감소 + 30% 할인
        shining=starforce.expected({**base,'event':'샤타포스'})
        self.assertAlmostEqual(shining['expected_destroys'],reduced['expected_destroys'],places=3)
        self.assertAlmostEqual(shining['discount_ratio'],0.3)
        self.assertLess(shining['expected_cost'],reduced['expected_cost'])
    def test_discounts_apply_to_attempt_cost_only(self):
        base=dict(level=250,current_star=18,target_star=22,spare_cost=4.5e9)
        plain=starforce.expected(base)
        cut=starforce.expected({**base,'event':'30% 할인'})
        spare_loss=plain['expected_destroys']*4.5e9
        attempts_only=plain['expected_cost']-spare_loss
        self.assertAlmostEqual(cut['expected_cost'],attempts_only*0.7+spare_loss,delta=2e7)
    def test_mvp_and_pcroom_discounts_add_up(self):
        base=dict(level=250,current_star=18,target_star=22,spare_cost=4.5e9)
        r=starforce.expected({**base,'discounts':['MVP 다이아','PC방']})
        self.assertAlmostEqual(r['discount_ratio'],0.15)
    def test_one_plus_one_halves_attempts(self):
        base=dict(level=250,current_star=0,target_star=10,spare_cost=0)
        self.assertLess(starforce.expected({**base,'event':'10성 이하 1+1'})['expected_attempts'],
                        starforce.expected(base)['expected_attempts']*0.6)
    def test_restore_keeps_star_instead_of_dropping(self):
        base=dict(level=250,current_star=18,target_star=22,spare_cost=4.5e9)
        plain=starforce.expected(base)
        restored=starforce.expected({**base,'use_restore':True})
        self.assertTrue(restored['restore_stars'])
        self.assertLess(restored['expected_attempts'],plain['expected_attempts'])
    def test_restore_table_matches_mesulive(self):
        self.assertEqual(starforce.restore_cost(250,18,0),round(78.21*1e8))
        self.assertEqual(starforce.restore_cost(250,19,1e9),2e9+round(129.77*1e8))
        self.assertIsNone(starforce.restore_cost(250,23,0))
        self.assertIsNone(starforce.restore_cost(155,18,0))
    def test_unknown_event_rejected(self):
        with self.assertRaises(AppError):
            starforce.expected({'level':250,'current_star':0,'target_star':5,'event':'없는이벤트'})
    def test_rejects_bad_targets(self):
        for bad in ({'level':250,'current_star':22,'target_star':22},
                    {'level':250,'current_star':0,'target_star':31},
                    {'level':250,'current_star':0,'target_star':'x'}):
            with self.subTest(bad=bad):
                with self.assertRaises(AppError): starforce.expected(bad)

class StarforceConversationTests(CharacterAnalysisTests):
    """기대값 질문 -> 노작값 필요 -> 알려주면 앱이 계산해 모델에 사실로 넘긴다."""
    PROFILE=dict(CharacterAnalysisTests.PROFILE)
    PROFILE['equipment']=[dict(e) for e in CharacterAnalysisTests.PROFILE['equipment']]
    PROFILE['equipment'][0].update(equip_level=250)
    PROFILE['equipment'][1].update(equip_level=160)
    PROFILE['equipment'][2].update(equip_level=160)
    def test_asks_for_spare_before_계산(self):
        r,_=self.ask('x','벨트 22성까지 기대값 얼마야?')
        self.assertEqual(r['status'],'ask_price')
        self.assertIn('스페어',' '.join(r['conditions']))
    def test_computes_and_hands_numbers_to_model(self):
        self.store.price_save({'item':'골든 클로버 벨트','price':3.2e10,'source':'user'})
        r,model=self.ask('벨트 강화 관련 서술','벨트 22성까지 기대값 얼마야?')
        self.assertEqual(r['status'],'analysis')
        self.assertIn('강화 기대값',model.seen)
        self.assertIn('기대 파괴 횟수',model.seen)
        self.assertEqual(r['starforce']['target_star'],22)
        self.assertEqual(r['starforce']['current_star'],18)
        self.assertEqual(r['starforce']['level'],160)
    def test_missing_target_star_is_reported(self):
        self.store.price_save({'item':'골든 클로버 벨트','price':3.2e10,'source':'user'})
        r,model=self.ask('서술','벨트 강화 기대값 알려줘')
        self.assertIn('목표 성을 알 수 없어',model.seen)

class ConversionTests(unittest.TestCase):
    """원본 엑셀 계산기가 계산해 둔 값과 대조한다.

    아델은 워크북에 저장된 값, 나머지 다섯 직업은 같은 입력으로 직업만 바꿔
    LibreOffice로 재계산해 얻은 값이다. 팔라딘(메용 0.16)과 카데나(부스탯2)는
    예외 분기를 지나므로 함께 고정한다.
    """
    SAMPLE = {'level':275,'buffed_stat':55784,'plain_stat':54277,'sub_stat':8395,'sub_stat2':0,
              'stat_attack':46760060,'damage':67,'boss_damage':398,'defense_ignore':89.03,
              'critical_damage':82,'item_attack_percent':99,'arcane_stat':13200,
              'authentic_stat':1300,'hyper_stat':150,'union_stat':240,'cooldown':'4초'}
    EXPECTED = {'아델':56554,'히어로':42212,'메르세데스':53390,'아크':53451,'카데나':66558,'팔라딘':54641}
    def result(self,job,**over):
        return convert({**self.SAMPLE,'job':job,**over})
    def broken(self,over):
        return convert({**self.SAMPLE,'job':'아델',**over})
    def test_matches_original_workbook(self):
        for job,expected in self.EXPECTED.items():
            with self.subTest(job=job):
                self.assertEqual(self.result(job)['converted_stat'],expected)
    def test_intermediate_values_match(self):
        r=self.result('아델')
        self.assertEqual(r['shard_damage'],547994677)
        self.assertEqual(r['score'],54.79)
        self.assertEqual(r['derived']['stat_percent'],6.25)
        self.assertEqual(r['derived']['pure_stat'],5433)
        self.assertEqual(r['derived']['attack'],2978)
        self.assertEqual(r['stat_sheet_ratio'],0.0347)
    def test_grade_and_benchmarks(self):
        r=self.result('아델')
        self.assertEqual(r['grade'],{'name':'적정','gap':770})
        self.assertEqual([(b['name'],b['ratio']) for b in r['benchmarks']],
                         [('레전둘둘',0.0589),('노해방 초고스펙',-0.1956)])
    def test_result_is_marked_unofficial(self):
        r=self.result('아델')
        self.assertFalse(r['source']['official'])
        self.assertTrue(any('공식' in a for a in r['assumptions']))
    def test_sub_stat2_only_for_three_jobs(self):
        with_second=self.result('카데나',sub_stat2=5000)['converted_stat']
        self.assertNotEqual(with_second,self.EXPECTED['카데나'])
        self.assertEqual(self.result('아델',sub_stat2=5000)['converted_stat'],self.EXPECTED['아델'])
    def test_rejects_bad_input(self):
        for bad in [{'job':'없는직업'},{'cooldown':'9초'},{'buffed_stat':100,'plain_stat':200},
                    {'level':1},{'damage':'abc'},{'arcane_stat':999999}]:
            with self.subTest(bad=bad):
                with self.assertRaises(AppError): self.broken(bad)
    def test_metadata_lists(self):
        self.assertIn('아델',jobs());self.assertEqual(len(jobs()),43)
        self.assertIn('노쿨감',cooldowns())

class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.app=Application(cls.tmp.name);cls.server=make_server(cls.app,0)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.tmp.cleanup()
    def request(self,path,body=None,headers=None):
        h={'X-Mepiti-Token':self.app.token,**(headers or {})}
        if body is not None:h['Content-Type']='application/json'
        return urlopen(Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=h))
    def test_index_and_csp(self):
        with self.request('/') as r:
            self.assertIn('메피티',r.read().decode());self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
    def test_csrf_host_and_token(self):
        for headers in [{'Origin':'https://evil.test'},{'Host':'evil.test'},{'X-Mepiti-Token':'wrong'}]:
            with self.assertRaises(HTTPError) as e:self.request('/api/characters',headers=headers)
            self.assertEqual(e.exception.code,403)
    def test_post_and_persistence(self):
        with self.request('/api/characters',{'name':'HTTP테스트','budget':100}) as r:self.assertEqual(r.status,200)
        with self.request('/api/characters') as r:self.assertIn('HTTP테스트',r.read().decode())
    def test_validation_error_not_server_error(self):
        with self.assertRaises(HTTPError) as e:self.request('/api/calculate',{'kind':'growth','current':0,'target':10,'daily':0})
        self.assertEqual(e.exception.code,400)

if __name__=='__main__':unittest.main()

class CharacterDiscoveryTests(unittest.TestCase):
    def test_flatten_multiple_accounts_sort_and_remove_identifiers(self):
        response={'account_list':[{'account_id':'private-account','character_list':[{'ocid':'private-ocid','character_name':'부캐','world_name':'월드','character_class':'직업','character_level':200}]},{'character_list':[{'character_name':'본캐','world_name':'월드','character_class':'직업','character_level':280}]}]}
        with patch.object(Nexon,'get',return_value=response) as get:
            result=Nexon(None).characters()
        get.assert_called_once_with('character/list',{})
        self.assertEqual([c['name'] for c in result['characters']],['본캐','부캐'])
        self.assertNotIn('private',json.dumps(result))
    def test_empty_list_is_success(self):
        with patch.object(Nexon,'get',return_value={'account_list':[]}):
            self.assertEqual(Nexon(None).characters()['characters'],[])
    def test_malformed_response_not_reported_as_empty(self):
        for response in [{}, {'account_list':[{}]}, {'account_list':[{'character_list':[{'character_name':'x','character_level':None}]}]}]:
            with patch.object(Nexon,'get',return_value=response):
                with self.assertRaises(AppError):Nexon(None).characters()
    def test_discovery_route_uses_stored_key_adapter(self):
        with tempfile.TemporaryDirectory() as folder:
            app=Application(folder)
            with patch.object(app.nexon,'characters',return_value={'characters':[]}) as discover:
                self.assertEqual(app.route('POST','/api/characters/discover',{},{}),{'characters':[]})
            discover.assert_called_once()
            self.assertEqual(app.store.characters(),[])
    def test_api_error_code_safe_no_raw_message(self):
        from mepiti.adapters import request_json
        error=HTTPError('https://open.api.nexon.com/maplestory/v1/character/list',403,'Forbidden',{},io.BytesIO(b'{"error":{"name":"OPENAPI00002","message":"private-secret"}}'))
        with patch('mepiti.adapters.urlopen',side_effect=error):
            with self.assertRaises(AppError) as caught:request_json(error.url)
        self.assertIn('OPENAPI00002',str(caught.exception))
        self.assertNotIn('private-secret',str(caught.exception))
    def test_character_image_origin_validated(self):
        for url,allowed in [('https://open.api.nexon.com/static/maplestory/character/look/test',True),('https://evil.test/track',False)]:
            with patch.object(Nexon,'get',side_effect=[{'ocid':'abc'},{'character_name':'이름','character_level':260,'character_image':url},{'final_stat':[]}]):
                data=Nexon(None).character('이름')
            self.assertEqual('image' in data,allowed)
