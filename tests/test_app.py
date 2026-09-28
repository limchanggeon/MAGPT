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
from mepiti import conditions, context, earnings, history, notices, prices, starforce, union
from mepiti.core import AppError, KST, Store, identifier, now
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
        def analyse(self,model,facts,question,history=None,numbers_shown=False):
            self.seen=facts; self.numbers_shown=numbers_shown; return self.text,{'eval_count':10}
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
                self.assertNotIn(made_up,r['content'])       # 서술은 버려진다
                self.assertIn('제네시스 창세검',r['facts'])     # 사실은 접이식으로 남는다
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
        self.assertIn('제네시스 창세검',r['facts'])
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
        # 값을 저장하고 원래 질문을 이어서 답한다.
        self.assertEqual(reply['status'],'analysis')
        self.assertIn('노작값을 저장했습니다',' '.join(reply['conditions']))
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
    def test_safeguard_charges_double_base_cost(self):
        # mesulive: 할인된 비용 + 할인 없는 기본 비용 x2. 파괴가 없으므로 기대 시도 x 시도당 비용과 같다.
        base=starforce.attempt_costs(250)[17]
        r=starforce.expected({'level':250,'current_star':17,'target_star':18,'spare_cost':0,
                              'event':'샤타포스','safeguard':[17]})
        self.assertEqual(r['safeguard'],[17])
        self.assertAlmostEqual(r['expected_cost'],(round(base*0.7)+base*2)/0.1575,delta=1)
    def test_safeguard_ignored_outside_15_to_17(self):
        plain=starforce.expected({'level':250,'current_star':18,'target_star':21,'spare_cost':4.5e9})
        asked=starforce.expected({'level':250,'current_star':18,'target_star':21,'spare_cost':4.5e9,
                                  'safeguard':[18,19,20]})
        self.assertEqual(asked['safeguard'],[])
        self.assertEqual(asked['safeguard_ignored'],[18,19,20])
        self.assertEqual(asked['expected_cost'],plain['expected_cost'])
    def test_safeguard_free_on_guaranteed_star(self):
        base=dict(level=250,current_star=15,target_star=16,spare_cost=0,event='5/10/15성 100%')
        self.assertEqual(starforce.expected({**base,'safeguard':[15]})['expected_cost'],
                         starforce.expected(base)['expected_cost'])
    def test_events_change_probability_and_cost(self):
        base=dict(level=250,current_star=18,target_star=22,spare_cost=4.5e9)
        plain=starforce.expected(base)
        # 파괴 30% 감소: 파괴 횟수만 줄고 비용 할인은 없다
        reduced=starforce.expected({**base,'event':'21성 이하 파괴 30% 감소'})
        self.assertLess(reduced['expected_destroys'],plain['expected_destroys'])
        self.assertAlmostEqual(reduced['event_discount'],0.0)
        # 샤타포스 = 파괴 감소 + 30% 할인
        shining=starforce.expected({**base,'event':'샤타포스'})
        self.assertAlmostEqual(shining['expected_destroys'],reduced['expected_destroys'],places=3)
        self.assertAlmostEqual(shining['event_discount'],0.3)
        self.assertLess(shining['expected_cost'],reduced['expected_cost'])
    def test_discounts_apply_to_attempt_cost_only(self):
        base=dict(level=250,current_star=18,target_star=22,spare_cost=4.5e9)
        plain=starforce.expected(base)
        cut=starforce.expected({**base,'event':'30% 할인'})
        spare_loss=plain['expected_destroys']*4.5e9
        attempts_only=plain['expected_cost']-spare_loss
        self.assertAlmostEqual(cut['expected_cost'],attempts_only*0.7+spare_loss,delta=2e7)
    def test_mvp_discount_stops_at_16_and_multiplies_with_event(self):
        # mesulive: MVP·PC방은 16성 이하에만, 이벤트 30%는 그 위에 곱한다.
        base=starforce.attempt_costs(250)
        at16=starforce.expected({'level':250,'current_star':16,'target_star':17,'spare_cost':0,
                                 'event':'샤타포스','discounts':['MVP 다이아'],'safeguard':[16]})
        self.assertEqual(at16['attempt_cost_at_current'],round(base[16]*0.9*0.7))
        at18=starforce.expected({'level':250,'current_star':18,'target_star':19,'spare_cost':0,
                                 'event':'샤타포스','discounts':['MVP 다이아']})
        self.assertEqual(at18['attempt_cost_at_current'],round(base[18]*0.7))
    def test_matches_reported_simulation(self):
        # 사용자가 보여 준 다른 계산기의 시뮬레이션 평균(샤타포스·MVP 다이아·스페어 2천만) 152억 811만.
        r=starforce.expected({'level':250,'current_star':18,'target_star':21,'spare_cost':2e7,
                              'event':'샤타포스','discounts':['MVP 다이아']})
        self.assertAlmostEqual(r['expected_cost'],15_208_113_892,delta=15_208_113_892*0.005)
    def test_restore_above_22_goes_to_22(self):
        r=starforce.expected({'level':250,'current_star':22,'target_star':24,'spare_cost':2e7,'use_restore':True})
        self.assertEqual(max(r['restore_stars']),22)          # 23성 파괴도 22성 복구로 계산한다
        no23=starforce.expected({'level':250,'current_star':22,'target_star':24,'spare_cost':2e7,'use_restore':False})
        self.assertLess(r['expected_attempts'],no23['expected_attempts'])
    def test_restore_meso_discount_event(self):
        base=dict(level=250,current_star=18,target_star=22,spare_cost=2e7,use_restore=True)
        plain=starforce.expected({**base,'event':'샤타포스'})
        cut=starforce.expected({**base,'event':'샤타포스(+흔적 복구 비용 20% 할인)'})
        self.assertLess(cut['expected_cost'],plain['expected_cost'])
        self.assertEqual(cut['expected_attempts'],plain['expected_attempts'])
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
    """기대값 질문 -> 강화 조건 -> 노작값 -> 앱이 계산해 모델에 사실로 넘긴다."""
    def setUp(self):
        super().setUp()
        conditions.save(self.store, dict(conditions.DEFAULTS))   # 조건은 이미 답한 상태로 둔다
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

class ConditionTests(unittest.TestCase):
    """강화 조건 슬롯. 어떤 조건이 빠졌는지는 코드가 판단하고 모델에 맡기지 않는다."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def test_defaults_before_answering(self):
        self.assertFalse(conditions.answered(self.store))
        c=conditions.load(self.store)
        self.assertEqual(c['event'],'없음');self.assertEqual(c['discounts'],[])
    def test_parses_free_form_answers(self):
        cases={
            '샤타포스':                        {'event':'샤타포스'},
            '샤타':                           {'event':'샤타포스'},
            '5/10/15성 100%':                 {'event':'5/10/15성 100%'},
            'MVP 다이아, PC방':                {'discounts':['MVP 다이아','PC방']},
            '안전모드 사용':                     {'safeguard':True},
            '안전모드 안 씀':                    {'safeguard':False},
            '복구 사용, 샤타포스':                {'use_restore':True,'event':'샤타포스'},
        }
        for text,expected in cases.items():
            with self.subTest(text=text):
                got=conditions.parse(text)
                self.assertIsNotNone(got)
                for k,v in expected.items(): self.assertEqual(got[k],v)
    def test_parse_returns_only_mentioned_keys(self):
        """후속 수정에서 말하지 않은 조건이 초기화되면 안 된다."""
        self.assertEqual(conditions.parse('샤타포스'),{'event':'샤타포스'})
        self.assertEqual(conditions.parse('안전모드도 쓸래'),{'safeguard':True})
        self.assertEqual(conditions.parse('파괴방지 사용'),{'safeguard':True})
        self.assertEqual(conditions.parse('파괴 방지는 안 씀'),{'safeguard':False})
        self.assertIn('파괴방지',conditions.summary(conditions.DEFAULTS))
        self.assertNotIn('안전모드',conditions.summary(conditions.DEFAULTS))
        # 이벤트 이름 속 '복구'는 흔적 복구 사용으로 읽지 않는다.
        self.assertEqual(conditions.parse('샤타포스(+흔적 복구 비용 20% 할인)'),
                         {'event':'샤타포스(+흔적 복구 비용 20% 할인)'})
        self.assertEqual(conditions.parse('MVP 다이아'),{'discounts':['MVP 다이아']})
    def test_basic_answer_accepted(self):
        self.assertEqual(conditions.parse('기본'),dict(conditions.DEFAULTS))
        self.assertIsNone(conditions.parse('안녕'))
    def test_saved_and_reset(self):
        conditions.save(self.store,{**conditions.DEFAULTS,'event':'샤타포스'})
        self.assertTrue(conditions.answered(self.store))
        self.assertEqual(conditions.load(self.store)['event'],'샤타포스')
        conditions.clear(self.store)
        self.assertFalse(conditions.answered(self.store))
    def test_safeguard_only_covers_destroy_range(self):
        # 15성 이상 요청 구간을 넘기고, 15~17성만 적용하는 것은 계산 쪽이 맡는다.
        self.assertEqual(conditions.to_arguments({'safeguard':True},14,18)['safeguard'],[15,16,17])
        self.assertEqual(conditions.to_arguments({'safeguard':True},18,22)['safeguard'],[18,19,20,21])
        self.assertEqual(conditions.to_arguments({'safeguard':True},0,10)['safeguard'],[])
        self.assertEqual(conditions.to_arguments({'safeguard':False},18,22)['safeguard'],[])
    def test_reset_phrases(self):
        for text in ('강화 조건 다시','강화 조건 바꿀래','조건 변경'):
            with self.subTest(text=text): self.assertTrue(conditions.RESET.search(text))

class ConditionConversationTests(CharacterAnalysisTests):
    """조건을 묻고 답을 저장한 뒤 그 조건으로 계산한다."""
    PROFILE=StarforceConversationTests.PROFILE
    def test_conditions_are_asked_first(self):
        r,_=self.ask('x','벨트 22성 기대값 얼마야?')
        self.assertEqual(r['status'],'ask_conditions')
        self.assertEqual(r['form']['kind'],'conditions')
        keys=[q['key'] for q in r['form']['questions']]
        self.assertEqual(keys,['event','safeguard','use_restore','discounts'])
        self.assertEqual(r['pending'],'벨트 22성 기대값 얼마야?')
    def test_answer_is_saved_then_price_is_asked(self):
        first,_=self.ask('x','벨트 22성 기대값 얼마야?')
        reply=answer(self.store,self.FakeModel('x'),
                     {'message':'샤타포스, 안전모드 사용','session_id':first['session_id']},
                     self.FakeNexon(self.PROFILE))
        # 조건을 저장하고 원래 질문을 이어서 진행해 바로 노작값을 묻는다.
        self.assertEqual(conditions.load(self.store)['event'],'샤타포스')
        self.assertEqual(reply['status'],'ask_price')
        self.assertEqual(reply['form']['kind'],'price')
        self.assertEqual(reply['pending'],'벨트 22성 기대값 얼마야?')
    def test_saved_conditions_reach_the_calculation(self):
        conditions.save(self.store,{**conditions.DEFAULTS,'event':'샤타포스','safeguard':True})
        self.store.price_save({'item':'골든 클로버 벨트','price':3.2e10,'source':'user'})
        r,model=self.ask('서술','벨트 22성 기대값 얼마야?')
        self.assertEqual(r['status'],'analysis')
        self.assertEqual(r['starforce']['event'],'샤타포스')
        # 18→22성은 안전모드를 쓸 수 없는 구간이다. 켜 둔 요청은 반영하지 않았다고 알린다.
        self.assertEqual(r['starforce']['safeguard'],[])
        self.assertEqual(r['starforce']['safeguard_ignored'],[18,19,20,21])
        self.assertIn('샤타포스',model.seen)
    def test_followup_condition_recalculates(self):
        """실제로 겪은 문제: '샤타포스일때는' 후속 질문이 자료 검색으로 빠져 보류됐다."""
        conditions.save(self.store,dict(conditions.DEFAULTS))
        self.store.price_save({'item':'골든 클로버 벨트','price':3.2e10,'source':'user'})
        first,_=self.ask('서술','벨트 22성 기대값 얼마야?')
        self.assertEqual(first['status'],'analysis')
        plain=first['starforce']['expected_cost']
        follow=answer(self.store,self.FakeModel('덧붙이는 말'),
                      {'message':'샤타포스일때는','session_id':first['session_id']},
                      self.FakeNexon(self.PROFILE))
        self.assertEqual(follow['status'],'analysis')          # 보류가 아니라 재계산
        self.assertEqual(follow['starforce']['event'],'샤타포스')
        self.assertEqual(follow['starforce']['target_star'],22)
        self.assertLess(follow['starforce']['expected_cost'],plain)
    def test_model_is_told_numbers_are_already_shown(self):
        conditions.save(self.store,dict(conditions.DEFAULTS))
        self.store.price_save({'item':'골든 클로버 벨트','price':3.2e10,'source':'user'})
        r,model=self.ask('덧붙이는 말','벨트 22성 기대값 얼마야?')
        self.assertTrue(model.numbers_shown)
        plain,plain_model=self.ask('서술','내 장비 어때')
        self.assertFalse(plain_model.numbers_shown)
    def test_numbers_are_written_by_the_app(self):
        """모델이 '계산할 수 없다'고 써도 앱이 만든 수치 블록은 답변에 남는다."""
        conditions.save(self.store,dict(conditions.DEFAULTS))
        self.store.price_save({'item':'골든 클로버 벨트','price':3.2e10,'source':'user'})
        r,_=self.ask('정확히 계산할 수 없습니다.','벨트 22성 기대값 얼마야?')
        self.assertEqual(r['status'],'analysis')
        self.assertIn('기대 비용',r['content'])
        self.assertIn(f"{r['starforce']['expected_cost']:,}",r['content'])
    def test_block_survives_rejected_model_text(self):
        conditions.save(self.store,dict(conditions.DEFAULTS))
        self.store.price_save({'item':'골든 클로버 벨트','price':3.2e10,'source':'user'})
        r,_=self.ask('시세 9,999,999,999 메소입니다.','벨트 22성 기대값 얼마야?')
        self.assertEqual(r['status'],'context')
        self.assertIn('기대 비용',r['content'])        # 계산 결과는 본문에 살아남는다
        self.assertNotIn('9,999,999,999',r['content'])
        self.assertIn('제네시스 창세검',r['facts'])     # 사실 전문은 접이식으로
    def test_followup_merges_instead_of_resetting(self):
        """실제로 겪은 문제: '안전모드도 쓸래'가 샤타포스를 없음으로 되돌렸다."""
        conditions.save(self.store,dict(conditions.DEFAULTS))
        self.store.price_save({'item':'골든 클로버 벨트','price':3.2e10,'source':'user'})
        first,_=self.ask('x','벨트 22성 기대값 얼마야?')
        answer(self.store,self.FakeModel('x'),
               {'message':'샤타포스','session_id':first['session_id']},self.FakeNexon(self.PROFILE))
        after=answer(self.store,self.FakeModel('x'),
                     {'message':'안전모드도 쓸래','session_id':first['session_id']},
                     self.FakeNexon(self.PROFILE))
        self.assertEqual(after['starforce']['event'],'샤타포스')       # 유지된다
        self.assertTrue(conditions.load(self.store)['safeguard'])     # 안전모드도 합쳐진다
        self.assertEqual(after['starforce']['safeguard_ignored'],[18,19,20,21])  # 18성 이상은 불가
    def test_basic_resets_everything_after_asking(self):
        conditions.save(self.store,{**conditions.DEFAULTS,'event':'샤타포스','safeguard':True})
        conditions.clear(self.store)
        r=answer(self.store,self.FakeModel('x'),{'message':'강화 조건 다시'},self.FakeNexon(self.PROFILE))
        answer(self.store,self.FakeModel('x'),
               {'message':'기본','session_id':r['session_id']},self.FakeNexon(self.PROFILE))
        self.assertEqual(conditions.load(self.store)['event'],'없음')
        self.assertFalse(conditions.load(self.store)['safeguard'])
    def test_facts_do_not_flood_the_answer(self):
        """거절·실패 시 사실 전문이 본문에 통째로 쏟아지던 문제."""
        conditions.save(self.store,dict(conditions.DEFAULTS))
        self.store.price_save({'item':'골든 클로버 벨트','price':3.2e10,'source':'user'})
        r,_=self.ask('시세 9,999,999,999 메소','벨트 22성 기대값 얼마야?')
        self.assertLess(len(r['content']),400)
        self.assertGreater(len(r['facts']),400)
    def test_long_message_does_not_change_conditions(self):
        conditions.save(self.store,dict(conditions.DEFAULTS))
        long_text='샤타포스 이벤트가 언제 열리는지 궁금한데 혹시 지난번에 했던 것처럼 이번에도 비슷한 기간으로 진행되나요'
        answer(self.store,self.FakeModel('x'),{'message':long_text},self.FakeNexon(self.PROFILE))
        self.assertEqual(conditions.load(self.store)['event'],'없음')
    def test_reset_asks_again(self):
        conditions.save(self.store,dict(conditions.DEFAULTS))
        r=answer(self.store,self.FakeModel('x'),{'message':'강화 조건 다시'},self.FakeNexon(self.PROFILE))
        self.assertEqual(r['status'],'ask_conditions')
        self.assertFalse(conditions.answered(self.store))

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
        with self.assertRaises(HTTPError) as e:self.request('/api/earnings',{'kind':'hunt','meso':'abc'})
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


class ChoiceAnswerTests(CharacterAnalysisTests):
    """되묻기를 선택창으로 답하고, 답하면 원래 질문을 이어서 계산한다."""
    PROFILE=StarforceConversationTests.PROFILE
    def reply(self,first,message,answer_value=None):
        data={'message':message,'session_id':first['session_id']}
        if answer_value is not None:
            data['answer']=answer_value
        return answer(self.store,self.FakeModel('x'),data,self.FakeNexon(self.PROFILE))
    def test_user_reported_conversation(self):
        """실제로 겪은 문제: 조건을 잘못 읽고, '2천만'을 읽지 못해 보류로 빠졌다."""
        first,_=self.ask('x','내 벨트 21성 기대값이 얼마야')
        self.assertEqual(first['status'],'ask_conditions')
        second=self.reply(first,'지금 샤타고, 파방은 못하고, 복구는 안할거임, mvp 다이아임')
        self.assertEqual(conditions.load(self.store)['discounts'],['MVP 다이아'])
        self.assertFalse(conditions.load(self.store)['use_restore'])
        self.assertFalse(conditions.load(self.store)['safeguard'])
        self.assertEqual(second['status'],'ask_price')
        third=self.reply(second,'지금 2천만메소정도')
        self.assertEqual(third['status'],'analysis')
        self.assertEqual(self.store.price_lookup('골든 클로버 벨트')['price'],20_000_000)
        self.assertEqual(third['starforce']['target_star'],21)
        self.assertEqual(third['starforce']['event'],'샤타포스')
        self.assertAlmostEqual(third['starforce']['discount_ratio'],0.1)    # MVP 다이아
        self.assertAlmostEqual(third['starforce']['event_discount'],0.3)    # 샤타포스
    def test_choice_answers_continue_to_calculation(self):
        first,_=self.ask('x','벨트 22성 기대값 얼마야?')
        second=self.reply(first,'샤타포스 · MVP 다이아',{'kind':'conditions','values':{
            'event':'샤타포스','safeguard':False,'use_restore':False,'discounts':['MVP 다이아']}})
        self.assertEqual(second['status'],'ask_price')
        item=second['form']['fields'][0]['item']
        third=self.reply(second,f'{item} 2천만',{'kind':'price','values':{item:'2천만'}})
        self.assertEqual(third['status'],'analysis')
        self.assertEqual(third['starforce']['spare_cost'],20_000_000)
        self.assertIn('강화 조건을 저장했습니다',' '.join(second['conditions'] or [])+' '.join(third['conditions']))
    def test_choice_values_are_validated(self):
        first,_=self.ask('x','벨트 22성 기대값 얼마야?')
        with self.assertRaises(AppError):
            self.reply(first,'x',{'kind':'conditions','values':{'event':'없는 이벤트'}})
        with self.assertRaises(AppError):
            self.reply(first,'x',{'kind':'price','values':{'골든 클로버 벨트':'1억'}})   # 값을 묻지 않았다
        with self.assertRaises(AppError):
            self.reply(first,'x',{'kind':'nope'})
    def test_price_for_unasked_item_is_refused(self):
        conditions.save(self.store,dict(conditions.DEFAULTS))
        first,_=self.ask('x','벨트 22성 기대값 얼마야?')
        self.assertEqual(first['status'],'ask_price')
        with self.assertRaises(AppError):
            self.reply(first,'x',{'kind':'price','values':{'제네시스 창세검':'1억'}})
    def test_star_number_is_not_taken_as_price(self):
        conditions.save(self.store,dict(conditions.DEFAULTS))
        first,_=self.ask('x','벨트 22성 기대값 얼마야?')
        self.reply(first,'22성 기대값')
        self.assertEqual(self.store.price_count(),0)
    def test_reset_offers_choices_and_remembers_target(self):
        conditions.save(self.store,dict(conditions.DEFAULTS))
        self.store.price_save({'item':'골든 클로버 벨트','price':3.2e10,'source':'user'})
        first,_=self.ask('x','벨트 22성 기대값 얼마야?')
        self.assertEqual(first['status'],'analysis')
        reset=self.reply(first,'강화 조건 다시')
        self.assertEqual(reset['form']['kind'],'conditions')
        again=self.reply(reset,'샤타포스',{'kind':'conditions','values':{
            'event':'샤타포스','safeguard':False,'use_restore':False,'discounts':[]}})
        self.assertEqual(again['status'],'analysis')
        self.assertEqual(again['starforce']['event'],'샤타포스')
        self.assertEqual(again['starforce']['target_star'],22)


class PriceScopeTests(CharacterAnalysisTests):
    """기대값 질문에서 상관없는 장비 값을 묻지 않고, 건너뛰겠다는 답을 알아듣는다."""
    PROFILE=dict(StarforceConversationTests.PROFILE)
    PROFILE['equipment']=[dict(e) for e in StarforceConversationTests.PROFILE['equipment']]+[
        {'slot':'모자','name':'에테르넬 나이트헬름','starforce':18,'scroll_upgrade':12,'equip_level':250,
         'potential_grade':'레전드리','potential':[],'additional_grade':None,'additional_potential':[],
         'add_grade':{'tier':None,'grade':183,'label':'183급'}}]+[
        {'slot':slot,'name':f'잡템{i}','starforce':0,'scroll_upgrade':0,'equip_level':160,
         'potential_grade':None,'potential':[],'additional_grade':None,'additional_potential':[],
         'add_grade':{'tier':None,'grade':20,'label':'20급'}} for i,slot in enumerate(('상의','하의','망토','장갑'))]
    def setUp(self):
        super().setUp()
        conditions.save(self.store,dict(conditions.DEFAULTS))
    def reply(self,first,message,answer_value=None):
        data={'message':message,'session_id':first['session_id']}
        if answer_value is not None: data['answer']=answer_value
        return answer(self.store,self.FakeModel('x'),data,self.FakeNexon(self.PROFILE))
    def test_expected_value_does_not_ask_unrelated_prices(self):
        """실제로 겪은 문제: 모자 값을 넣자 상의·하의 등 상관없는 장비 값을 되물었다."""
        first,_=self.ask('x','내 모자 21성가는 기대값이 얼마야')
        self.assertEqual(first['status'],'ask_price')
        self.assertEqual([a['item'] for a in first['asked']],['에테르넬 나이트헬름'])
        self.assertFalse(first['form']['skippable'])      # 스페어 값은 건너뛸 수 없다
        done=self.reply(first,'에테르넬 나이트헬름 2천만')
        self.assertEqual(done['status'],'analysis')
        self.assertEqual(done['starforce']['item'],'에테르넬 나이트헬름')
        self.assertIn('기대 비용',done['content'])
        again,_=self.ask('x','내 모자 21성가는 기대값이 얼마야')
        self.assertEqual(again['status'],'analysis')        # 다시 물어도 다른 장비 값을 묻지 않는다
    def test_spare_price_cannot_be_skipped(self):
        first,_=self.ask('x','내 모자 21성가는 기대값이 얼마야')
        r=self.reply(first,'그건 없어도 됨')
        self.assertEqual(r['status'],'ask_price')
        self.assertIn('건너뛸 수 없습니다',r['content'])
        self.assertEqual(r['pending'],first['pending'])
        done=self.reply(r,'2천만')                          # 다시 보여 준 칸에 답하면 이어서 계산
        self.assertEqual(done['status'],'analysis')
    def test_comparison_prices_can_be_skipped(self):
        first,_=self.ask('x','상의 바꾸는 게 이득이야? 노작값 기준으로')
        self.assertEqual(first['status'],'ask_price')
        self.assertTrue(first['form']['skippable'])
        r=self.reply(first,'그건 없어도 됨')
        self.assertEqual(r['status'],'analysis')
        self.assertIn('노작값 없이 진행',' '.join(r['conditions']))
        self.assertEqual(self.store.price_count(),0)
    def test_skip_button(self):
        first,_=self.ask('x','상의 바꾸는 게 이득이야? 노작값 기준으로')
        r=self.reply(first,'노작값 없이 진행',{'kind':'price','values':{},'skip':True})
        self.assertEqual(r['status'],'analysis')
    def test_item_price_question_still_asks(self):
        r,_=self.ask('x','모자 21성이면 시세 얼마야')
        self.assertEqual(r['status'],'ask_price')


class RestoreCompareTests(CharacterAnalysisTests):
    PROFILE=PriceScopeTests.PROFILE
    def test_restore_result_shows_cost_without_restore(self):
        """실제로 겪은 문제: 잘못 저장된 '흔적 복구 사용'으로 기대값이 크게 나와도 알아챌 수 없었다."""
        conditions.save(self.store,{**conditions.DEFAULTS,'event':'샤타포스','use_restore':True})
        self.store.price_save({'item':'에테르넬 나이트헬름','price':2e7,'source':'user'})
        r,_=self.ask('x','내 모자 21성가는 기대값이 얼마야')
        self.assertIn('흔적 복구를 안 쓰면',r['content'])
        self.assertLess(r['starforce']['without_restore'],r['starforce']['expected_cost'])
        conditions.save(self.store,{**conditions.DEFAULTS,'event':'샤타포스'})
        plain,_=self.ask('x','내 모자 21성가는 기대값이 얼마야')
        self.assertNotIn('흔적 복구를 안 쓰면',plain['content'])


class ItemTopicTests(CharacterAnalysisTests):
    """장비를 주제로 연 대화. 부위를 말하지 않아도 그 장비로 알아듣는다."""
    PROFILE=PriceScopeTests.PROFILE
    HAT={'slot':'모자','name':'에테르넬 나이트헬름'}
    def setUp(self):
        super().setUp()
        conditions.save(self.store,{**conditions.DEFAULTS,'event':'샤타포스'})
        self.store.price_save({'item':'에테르넬 나이트헬름','price':2e7,'source':'user'})
    def start(self,message,topic=None,model_text='x'):
        model=self.FakeModel(model_text)
        r=answer(self.store,model,{'message':message,'topic':topic or self.HAT},self.FakeNexon(self.PROFILE))
        return r,model
    def follow(self,first,message,model_text='x'):
        model=self.FakeModel(model_text)
        return answer(self.store,model,{'message':message,'session_id':first['session_id']},
                      self.FakeNexon(self.PROFILE)),model
    def test_session_keeps_item_topic(self):
        r,_=self.start('21성 기대값')
        self.assertEqual(r['starforce']['item'],'에테르넬 나이트헬름')      # '모자'라고 안 해도 된다
        self.assertEqual(r['topic_item']['starforce'],18)
        s=next(x for x in self.store.sessions() if x['id']==r['session_id'])
        self.assertEqual(s['title'],'모자 · 에테르넬 나이트헬름')
        self.assertEqual(s['topic'],self.HAT)
        again,_=self.follow(r,'22성은?')
        self.assertEqual(again['starforce']['target_star'],22)
        self.assertEqual(again['starforce']['item'],'에테르넬 나이트헬름')
    def test_general_question_gets_item_facts(self):
        r,model=self.start('이거 어때?',model_text='183급 추옵이라 괜찮습니다.')
        self.assertEqual(r['status'],'analysis')
        self.assertIn('[대화 주제 장비]',model.seen)
        self.assertIn('183급',model.seen)
    def test_other_slot_can_still_be_named(self):
        r,_=self.start('21성 기대값')
        self.store.price_save({'item':'골든 클로버 벨트','price':3e8,'source':'user'})
        other,_=self.follow(r,'벨트 21성 기대값')
        self.assertEqual(other['starforce']['item'],'골든 클로버 벨트')
    def test_unequipped_topic_is_reported(self):
        # 모자를 바꿨으면 지금 착용한 모자 기준이라고 알린다.
        r,_=self.start('이거 어때?',topic={'slot':'모자','name':'예전 모자'})
        self.assertIn('에테르넬 나이트헬름 기준',' '.join(r['conditions']))
        # 그 부위에 아무것도 없으면 착용하지 않았다고 알린다.
        r,_=self.start('이거 어때?',topic={'slot':'얼굴장식','name':'없는 장식'})
        self.assertIn('착용하고 있지 않습니다',' '.join(r['conditions']))
    def test_topic_is_validated(self):
        with self.assertRaises(AppError):
            self.start('x',topic={'slot':'','name':'a'})
        with self.assertRaises(AppError):
            self.start('x',topic='모자')
    def test_topic_ignored_on_existing_session(self):
        r,_=self.ask('x','내 캐릭터 약한 부위 알려줘')
        again=answer(self.store,self.FakeModel('x'),{'message':'x','session_id':r['session_id'],'topic':self.HAT},
                     self.FakeNexon(self.PROFILE))
        self.assertIsNone(self.store.session_topic(again['session_id']))
    def test_old_database_gets_topic_column(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            db=sqlite3.connect(str(Path(folder)/'mepiti.sqlite3'))
            db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY, title TEXT, created_at TEXT)')
            db.execute("INSERT INTO sessions VALUES('old','옛 대화','2026-09-01')"); db.commit(); db.close()
            store=Store(folder)
            self.assertEqual(store.sessions()[0]['title'],'옛 대화')
            self.assertIsNone(store.session_topic('old'))


class PresetTests(CharacterAnalysisTests):
    """장비 프리셋 1~3. 넥슨 item-equipment 응답의 item_equipment_preset_N을 읽는다."""
    def test_adapter_reads_presets(self):
        class TestVault:
            def get(self):return 'k'
        hat=lambda name,star:{'item_equipment_slot':'모자','item_equipment_part':'모자','item_name':name,'starforce':str(star),
                              'item_base_option':{'base_equipment_level':'250'}}
        equipment={'preset_no':1,'date':None,'item_equipment':[hat('현재 모자',18)],
                   'item_equipment_preset_1':[hat('현재 모자',18)],'item_equipment_preset_2':[hat('보스용 모자',22)],
                   'item_equipment_preset_3':[],'title':{'title_name':'시험 칭호'}}
        replies=[{'ocid':'a'},{'character_name':'테스트','character_level':285},{'final_stat':[]},equipment,{}]
        with patch('mepiti.adapters.request_json',side_effect=replies):
            snap=Nexon(TestVault()).character('테스트',details=True)
        self.assertEqual(sorted(snap['equipment_presets']),['1','2'])       # 빈 프리셋 3은 뺀다
        self.assertEqual(snap['equipment_presets']['2'][0]['name'],'보스용 모자')
        self.assertEqual(snap['equipment_presets']['2'][0]['starforce'],22)
        self.assertIn('칭호',[i['slot'] for i in snap['equipment_presets']['2']])   # 칭호는 모든 프리셋에
    def test_topic_uses_chosen_preset(self):
        profile=dict(PriceScopeTests.PROFILE); profile['equipment_preset']=1
        boss=dict(next(i for i in profile['equipment'] if i['slot']=='모자'),name='보스용 모자',starforce=20)
        profile['equipment_presets']={'1':profile['equipment'],'2':[boss]}
        conditions.save(self.store,dict(conditions.DEFAULTS))
        self.store.price_save({'item':'보스용 모자','price':1e8,'source':'user'})
        r=answer(self.store,self.FakeModel('x'),{'message':'22성 기대값',
                 'topic':{'slot':'모자','name':'보스용 모자','preset':2}},self.FakeNexon(profile))
        self.assertEqual(r['starforce']['item'],'보스용 모자')
        self.assertEqual(r['starforce']['current_star'],20)
        self.assertIn('프리셋 2번 기준',' '.join(r['conditions']))
        self.assertEqual(self.store.session_topic(r['session_id'])['preset'],'2')
    def test_invalid_preset_is_refused(self):
        with self.assertRaises(AppError):
            answer(self.store,self.FakeModel('x'),{'message':'x','topic':{'slot':'모자','name':'a','preset':7}},
                   self.FakeNexon(self.PROFILE))


class UnionTests(CharacterAnalysisTests):
    """유니온 공격대원 추천. 효과 표는 사용자 제공 커뮤니티 표(비공식)."""
    ROSTER=[{'name':'테스트','world':'크로아','job':'렌','level':291},
            {'name':'은월부캐','world':'크로아','job':'은월','level':210},
            {'name':'메르','world':'크로아','job':'메르세데스','level':120},
            {'name':'히어로부캐','world':'크로아','job':'히어로','level':250},
            {'name':'딴월드','world':'스카니아','job':'나이트로드','level':260},
            {'name':'캐슈','world':'크로아','job':'캐논슈터','level':150}]
    class UnionNexon(CharacterAnalysisTests.FakeNexon):
        def characters(self): return {'characters':UnionTests.ROSTER}
        def union(self,name): return {'level':9000,'grade':'그랜드 마스터 1','placed':[{'job':'은월','level':210}],'warnings':[]}
    def rows(self,job='렌',stat='STR'):
        return {r['job']:r for r in union.recommend(self.ROSTER,job,stat,'크로아',{'은월'},limit=99)}
    def test_grades_and_next_level(self):
        self.assertEqual(union.grade_index(59),None); self.assertEqual(union.grade_index(60),0)
        self.assertEqual(union.grade_index(249),3); self.assertEqual(union.grade_index(250),4)
        r=self.rows()['메르세데스']
        self.assertEqual((r['grade'],r['next_grade'],r['levels_left']),('A','S',20))
        self.assertEqual(r['next_effect'],'스킬 재사용 대기시간 감소 +4%')
    def test_world_sss_self_and_useless_are_excluded(self):
        rows=self.rows()
        self.assertNotIn('렌',rows)                        # 대표 캐릭터 자신
        self.assertNotIn('히어로',rows)                    # 이미 SSS
        self.assertEqual(rows['나이트로드']['level'],0)     # 다른 월드 캐릭터는 세지 않는다
        self.assertNotIn('비숍',rows)                      # STR 캐릭터에게 INT는 F
        self.assertTrue(rows['은월']['placed']); self.assertFalse(rows['메르세데스']['placed'])
    def test_rating_follows_main_stat(self):
        self.assertEqual(union.rating('STR','히어로','STR'),'A')
        self.assertEqual(union.rating('DEX','히어로','STR'),'C')
        self.assertEqual(union.rating('INT','비숍','INT'),'A')
        self.assertEqual(union.rating('LUK','비숍','INT'),'C')     # 마법사 부스탯은 LUK
        self.assertEqual(union.rating('STR','비숍','INT'),'F')
        self.assertEqual(union.rating('buff','다크나이트','STR'),'S')
        self.assertEqual(union.rating('speed','배틀메이지','INT'),'D')
        self.assertEqual(union.rating('hp_pct','데몬어벤져','HP'),'S')
    def test_alias_job_names(self):
        self.assertEqual(self.rows()['캐논마스터']['level'],150)   # 표의 '캐논슈터'도 같은 직업
    def test_chat_recommends(self):
        self.store.set_setting('model','')
        r=answer(self.store,self.FakeModel('x'),{'message':'다음에 뭐 키우는 게 좋을까?'},self.UnionNexon(self.PROFILE))
        self.assertEqual(r['status'],'context')
        self.assertIn('다음에 키우면 좋은 공격대원',r['content'])
        self.assertIn('메르세데스',r['content'])
        self.assertIn('커뮤니티 평가',' '.join(r['conditions']))
        self.assertEqual(r['union']['level'],9000)
    def test_model_text_with_invented_numbers_is_dropped(self):
        r=answer(self.store,self.FakeModel('메르세데스를 99999999 메소 들여 키우세요'),{'message':'유니온 뭐 키울까'},
                 self.UnionNexon(self.PROFILE))
        self.assertNotIn('99999999',r['content'])
        self.assertIn('다음에 키우면 좋은 공격대원',r['content'])


class EarningsTests(unittest.TestCase):
    """재획·주보 수익 기록. 금액은 사용자가 적은 값만 쓴다."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def test_hunt_total_is_meso_plus_pieces(self):
        r=earnings.add(self.store,{'kind':'hunt','meso':'12억 3500만','pieces':'42','piece_price':'650만','flasks':'2'})
        self.assertEqual(r['total'],1_235_000_000+42*6_500_000)
        self.assertEqual(self.store.setting(earnings.PIECE_PRICE),6_500_000)   # 다음 기록 기본값
        o=earnings.overview(self.store)
        self.assertEqual(o['summary']['hunt']['all']['total'],r['total'])
        self.assertEqual(o['summary']['hunt']['pieces'],42)
        self.assertEqual(o['summary']['hunt']['per_flask'],r['total']/2)
    def test_boss_share_split_by_party(self):
        r=earnings.add(self.store,{'kind':'boss','boss':'하드 세렌','crystal':'6억','party':'3','extra':'1000만'})
        self.assertEqual(r['total'],200_000_000+10_000_000)
        o=earnings.overview(self.store)
        self.assertEqual(o['boss_weeks'][0]['total'],r['total'])
        self.assertEqual(o['summary']['all']['all'],r['total'])
    def test_week_starts_on_thursday(self):
        from datetime import date
        self.assertEqual(earnings.week_start(date(2026,9,28)),date(2026,9,24))   # 월 -> 직전 목
        self.assertEqual(earnings.week_start(date(2026,9,24)),date(2026,9,24))   # 목 -> 그날
        self.assertEqual(earnings.week_start(date(2026,9,23)),date(2026,9,17))   # 수 -> 전주 목
    def test_old_records_leave_this_week(self):
        earnings.add(self.store,{'kind':'boss','boss':'노멀 루시드','crystal':'1억','day':'2026-01-01'})
        o=earnings.overview(self.store)
        self.assertEqual(o['summary']['boss']['all']['count'],1)
        self.assertEqual(o['summary']['boss']['week']['count'],0)
    def test_validation(self):
        for bad in [{'kind':'x'},{'kind':'hunt'},{'kind':'hunt','meso':'abc'},{'kind':'hunt','pieces':5},
                    {'kind':'hunt','meso':'1억','day':'2999-01-01'},{'kind':'boss','crystal':'1억'},
                    {'kind':'boss','boss':'세렌','crystal':'1억','party':9},{'kind':'boss','boss':'세렌'}]:
            with self.subTest(bad=bad):
                with self.assertRaises(AppError): earnings.add(self.store,bad)
    def test_delete(self):
        r=earnings.add(self.store,{'kind':'hunt','meso':'1억'})
        earnings.delete(self.store,r['id'])
        self.assertEqual(earnings.overview(self.store)['hunts'],[])
        with self.assertRaises(AppError): earnings.delete(self.store,r['id'])



class SchedulerImportTests(unittest.TestCase):
    """스케줄러로 이번 주에 잡은 보스를 불러온다. 가격·인원은 사용자가 적는다."""
    class Nexon:
        def __init__(self,states): self.states=states
        def scheduler(self,name,day=None):
            if name not in self.states: raise AppError('조회 실패')
            return self.states[name]
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(self.tmp.name)
        state=lambda name,bosses:{'character':name,'bosses':bosses,'weekly_clear':len(bosses),'weekly_limit':14,'level':280,'job':'렌'}
        boss=lambda n,d,done:{'name':n,'difficulty':d,'cycle':'주간','registered':True,'complete':done}
        self.nexon=self.Nexon({'본캐':state('본캐',[boss('세렌','하드',True),boss('칼로스','노멀',False)]),
                                '부캐':state('부캐',[boss('루시드','노멀',True)])})
    def tearDown(self): self.tmp.cleanup()
    def test_only_completed_bosses(self):
        d=earnings.scheduled_bosses(self.store,self.nexon,['본캐','부캐','없는캐'])
        self.assertEqual([(b['character'],b['boss']) for b in d['bosses']],[('본캐','세렌 (하드)'),('부캐','루시드 (노멀)')])
        self.assertEqual([b['price'] for b in d['bosses']],[302_000_000,17_800_000])   # 공식 공지 가격
        self.assertEqual(d['bosses'][0]['price_source'],'official')
        self.assertEqual(d['characters'][2]['error'],'조회 실패')
    def test_saved_boss_is_marked_and_not_duplicated(self):
        b=earnings.scheduled_bosses(self.store,self.nexon,['본캐'])['bosses'][0]
        earnings.add(self.store,{'kind':'boss','boss':b['boss'],'crystal':'3억 200만','character':'본캐','source_key':b['key']})
        again=earnings.scheduled_bosses(self.store,self.nexon,['본캐'])['bosses'][0]
        self.assertTrue(again['recorded'])
        with self.assertRaises(AppError):
            earnings.add(self.store,{'kind':'boss','boss':b['boss'],'crystal':'4억','source_key':b['key']})
    def test_flag_values(self):
        from mepiti.adapters import flag
        for v in ('true','Y','1',True,'완료'): self.assertTrue(flag(v))
        for v in ('false','N','0',None,''): self.assertFalse(flag(v))
    def test_old_earnings_table_gets_columns(self):
        import sqlite3
        with self.store.db() as db:
            db.execute('DROP TABLE IF EXISTS earnings')
            db.execute('CREATE TABLE earnings(id TEXT PRIMARY KEY, kind TEXT NOT NULL, day TEXT NOT NULL, meso REAL, pieces INTEGER, piece_price REAL, flasks REAL, boss TEXT, crystal REAL, party INTEGER, extra REAL, note TEXT, created_at TEXT NOT NULL)')
        earnings.add(self.store,{'kind':'boss','boss':'세렌','crystal':'1억','character':'본캐','source_key':'k'})
        self.assertEqual(earnings.overview(self.store)['bosses'][0]['character'],'본캐')


class CrystalPriceTests(unittest.TestCase):
    """결정석 판매가 — 공식 공지(업데이트 813) 표. 사용자가 붙여 준 내용."""
    def test_table_values(self):
        self.assertEqual(len(earnings.CRYSTALS),46)
        self.assertEqual(earnings.crystal_price('선택받은 세렌','하드'),302_000_000)
        self.assertEqual(earnings.crystal_price('세렌','하드'),302_000_000)          # 스케줄러의 짧은 이름
        self.assertEqual(earnings.crystal_price('감시자 칼로스','카오스'),1_230_000_000)
        self.assertEqual(earnings.crystal_price('자쿰','카오스'),4_040_000)
        self.assertIsNone(earnings.crystal_price('세렌','카오스'))                   # 없는 난이도
    def test_black_mage_from_october(self):
        self.assertEqual(earnings.crystal_price('검은 마법사','익스트림','2026-09-30'),8_740_000_000)
        self.assertEqual(earnings.crystal_price('검은 마법사','익스트림','2026-10-01'),5_680_000_000)
    def test_blank_crystal_uses_official_price(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            r=earnings.add(store,{'kind':'boss','boss':'카링 (하드)','party':'2'})
            self.assertEqual(r['crystal'],1_560_000_000)
            self.assertEqual(r['total'],780_000_000)
            self.assertEqual(store.setting(earnings.BOSS_PRICES) or {},{})           # 공식 가격은 따로 기억하지 않는다
            with self.assertRaises(AppError):
                earnings.add(store,{'kind':'boss','boss':'모르는 보스'})              # 표에 없으면 가격을 적어야 한다



class StarforceHistoryTests(unittest.TestCase):
    """스타포스 강화 기록 — 필드는 사용자가 붙여 준 넥슨 문서 기준(destroy_defence 등)."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(self.tmp.name)
        conditions.save(self.store,dict(conditions.DEFAULTS))
    def tearDown(self): self.tmp.cleanup()
    SHINING=[{'success_rate':'','destroy_decrease_rate':'30','cost_discount_rate':'30','plus_value':'',
              'starforce_event_range':'0~21','recovery_cost_discount_rate':''}]
    def api_row(self,i,before,after,result,guard='',events=None):
        return {'id':f'r{i}','item_upgrade_result':result,'before_starforce_count':before,'after_starforce_count':after,
                'starcatch_result':'성공','destroy_defence':guard,'character_name':'본캐','world_name':'크로아',
                'target_item':'에테르넬 나이트헬름','date_create':f'2026-09-2{i%8}T12:00:00.000+09:00',
                'starforce_event_list':events if events is not None else self.SHINING}
    def test_adapter_reads_documented_fields(self):
        class V:
            def get(self): return 'k'
        page={'count':2,'next_cursor':'','starforce_history':[self.api_row(1,17,18,'성공','파괴 방지 적용'),
                                                             self.api_row(2,18,12,'파괴'),{'id':'x'}]}
        with patch('mepiti.adapters.request_json',return_value=page) as call:
            rows=Nexon(V()).starforce_history('2026-09-27')
        self.assertEqual(call.call_args.args[0].split('?')[0].rsplit('/',2)[-2:],['history','starforce'])
        self.assertEqual(len(rows),2)                       # 알아볼 수 없는 기록은 건너뛴다
        self.assertTrue(rows[0]['safeguard']); self.assertFalse(rows[1]['safeguard'])
        self.assertEqual(rows[0]['events'][0]['discount'],0.3)
        self.assertEqual(rows[0]['events'][0]['range'],[0,21])
    def test_attempt_cost_uses_recorded_event(self):
        base=starforce.attempt_costs(250)[17]
        shining=[{'discount':0.3,'destroy_decrease':0.3,'range':[0,21]}]
        self.assertEqual(history.attempt_cost(250,17,False,{},shining),round(base*0.7))
        self.assertEqual(history.attempt_cost(250,17,True,{},shining),round(base*0.7)+base*2)   # 파괴방지
        self.assertEqual(history.attempt_cost(250,17,False,{},[]),base)
        self.assertEqual(history.event_name(shining,18),'샤타포스')
        self.assertEqual(history.event_name([{'discount':0.3,'range':[0,21]}],18),'30% 할인')
    def test_overview_compares_with_expected(self):
        class N:
            def __init__(s,rows): s.rows=rows
            def starforce_history(s,day): return s.rows if day=='2026-09-27' else []
            def character(s,name,details=False):
                return {'equipment':[{'name':'에테르넬 나이트헬름','equip_level':250}],'equipment_presets':{}}
        rows=[{'id':f'a{i}','character':'본캐','world':'크로아','item':'에테르넬 나이트헬름','before':b,'after':a,
               'result':r,'starcatch':None,'safeguard':False,'created':f'2026-09-27T1{i}:00','events':
               [{'discount':0.3,'destroy_decrease':0.3,'range':[0,21]}]}
              for i,(b,a,r) in enumerate([(18,18,'실패(유지)'),(18,19,'성공'),(19,12,'파괴'),(12,13,'성공')])]
        self.store.price_save({'item':'에테르넬 나이트헬름','price':2e7,'source':'user'})
        with patch('mepiti.history.today',return_value=__import__('datetime').date(2026,9,28)):
            r=history.fetch(self.store,N(rows),days=3)
        self.assertEqual(r['added'],4)
        g=history.overview(self.store)['groups'][0]
        self.assertEqual((g['level'],g['start'],g['reached'],g['end'],g['attempts'],g['destroy']),(250,18,19,13,4,1))
        self.assertEqual(g['event'],'샤타포스')
        costs=starforce.attempt_costs(250)
        spent=sum(round(costs[s]*0.7) for s in (18,18,19,12))+2e7
        self.assertEqual(g['actual']['total'],spent)
        # 기대값과는 19성을 처음 찍을 때까지(18 유지, 18→19)만 비교한다. 그 뒤 파괴·재강화는 따로.
        to_reach=round(costs[18]*0.7)*2
        self.assertEqual(g['actual']['to_reach'],to_reach)
        self.assertEqual((g['actual']['after_attempts'],g['actual']['after_destroys']),(2,1))
        self.assertEqual(g['actual']['after'],spent-to_reach)
        self.assertIsNotNone(g['expected']); self.assertAlmostEqual(g['difference'],to_reach-g['expected']['cost'])
    def test_fetch_skips_old_days_once_fetched(self):
        calls=[]
        class N:
            def starforce_history(s,day): calls.append(day); return []
            def character(s,name,details=False): return {}
        with patch('mepiti.history.today',return_value=__import__('datetime').date(2026,9,28)):
            history.fetch(self.store,N(),days=5); history.fetch(self.store,N(),days=5)
        self.assertEqual(len(calls),5+2)           # 두 번째는 오늘·어제만 다시 받는다
        with self.assertRaises(AppError): history.fetch(self.store,N(),days=91)
    def test_unknown_level_asks_and_can_be_set(self):
        with self.store.db() as db:
            history.ensure(self.store)
            db.execute("INSERT INTO starforce_history VALUES('z','본캐',null,'모르는 모자',15,16,'성공',null,0,'2026-09-27',null,0)")
        o=history.overview(self.store); g=o['groups'][0]
        self.assertIsNone(g['actual']); self.assertEqual(g['missing'],'level'); self.assertEqual(o['missing_level'],1)
        history.set_level(self.store,'모르는 모자',200)
        self.assertIsNotNone(history.overview(self.store)['groups'][0]['actual'])
        with self.assertRaises(AppError): history.set_level(self.store,'모르는 모자',999)
    def test_known_set_level_and_superior(self):
        history.ensure(self.store)
        with self.store.db() as db:
            db.execute("INSERT INTO starforce_history VALUES('a','본캐',null,'아케인셰이드 나이트햇',16,17,'성공',null,0,'2026-09-27',null,0)")
            db.execute("INSERT INTO starforce_history VALUES('b','본캐',null,'타일런트 히아데스 부츠',5,6,'성공',null,0,'2026-09-27',null,1)")
        groups={g['item']:g for g in history.overview(self.store)['groups']}
        arcane=groups['아케인셰이드 나이트햇']
        self.assertEqual((arcane['level'],arcane['level_guessed']),(200,True))
        self.assertIsNotNone(arcane['expected'])                        # 착용하지 않아도 이름으로 계산
        self.assertEqual(groups['타일런트 히아데스 부츠']['missing'],'superior')



class NoticeTests(unittest.TestCase):
    """넥슨 공지 — 필드는 사용자가 붙여 준 문서 기준(notice/notice-update/notice-event)."""
    class N:
        def __init__(s,end='2099-10-12T23:59+09:00',day='2026-09-20T10:00+09:00'):
            s.calls=[]; s.day=day
            s.lists={'notice-update':[{'kind':'notice-update','id':'900','title':'보스 결정 판매 가격 변경','url':'https://maplestory.nexon.com/news/update/900',
                                       'date':day,'start':None,'end':None}],
                     'notice-event':[{'kind':'notice-event','id':'77','title':'샤이닝 스타포스 타임','url':'https://maplestory.nexon.com/news/event/77',
                                      'date':'2026-09-24T10:00+09:00','start':'2026-09-24T10:00+09:00','end':end}],
                     'notice':[]}
        def notices(s,kind): s.calls.append(kind); return s.lists[kind]
        def notice_detail(s,kind,nid):
            s.calls.append(f'{kind}/{nid}')
            if kind=='notice-update':
                return {'title':'보스 결정 판매 가격 변경','url':'https://maplestory.nexon.com/news/update/900','date':s.day,
                        'contents':'<p>강렬한 힘의 결정 판매 가격이 조정됩니다.</p><table><tr><td>보스</td><td>가격</td></tr><tr><td>카링 (하드)</td><td>1,560,000,000</td></tr></table>'}
            return {'title':'샤이닝 스타포스 타임','url':'https://maplestory.nexon.com/news/event/77','date':'2026-09-24T10:00+09:00',
                    'contents':'<p>샤이닝 스타포스 기간 동안 강화 비용 30% 할인</p>','start':'2026-09-24T10:00+09:00','end':'2099-10-12T23:59+09:00'}
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def test_html_to_text(self):
        text=notices.text_of('<p>가&amp;나<br>다</p><table><tr><td>A</td><td>B</td></tr></table><script>x()</script>')
        self.assertEqual(text,'가&나\n다\nA | B')
    def test_sync_imports_reviewed_official_documents(self):
        n=self.N(); r=notices.sync(self.store,n)
        self.assertEqual(r['added'],2)
        docs=self.store.documents()
        self.assertTrue(all(d['metadata']['verification_status']=='reviewed' and d['metadata']['source_type']=='official' for d in docs))
        found,conflict=self.store.search('결정 판매 가격')
        self.assertFalse(conflict); self.assertIn('카링 (하드) | 1,560,000,000',found[0]['body'])
        again=notices.sync(self.store,n)                 # 30분 안에는 다시 받지 않는다
        self.assertTrue(again['skipped'])
        notices.sync(self.store,n,force=True)
        self.assertEqual(len(self.store.documents()),2)  # 이미 넣은 글은 다시 넣지 않는다
    def test_events_and_suggestion(self):
        notices.sync(self.store,self.N())
        self.assertEqual([e['title'] for e in notices.active_events(self.store)],['샤이닝 스타포스 타임'])
        self.assertEqual(notices.suggested_event(self.store)[0],'샤타포스')
        self.assertIn('10/12',notices.events_text(self.store))
    def test_ended_event_is_not_active(self):
        notices.sync(self.store,self.N(end='2026-01-01T00:00+09:00'))
        self.assertEqual(notices.active_events(self.store),[])
        self.assertIsNone(notices.suggested_event(self.store)[0])
    def test_crystal_alert(self):
        notices.sync(self.store,self.N())
        self.assertIsNone(earnings.overview(self.store)['crystal_alert'])   # 가격표를 넣기 전 공지는 알리지 않는다
        with patch('mepiti.notices.CRYSTAL_TABLE_DAY','2026-09-01'):
            notices.sync(self.store,self.N(),force=True)
        self.store.set_setting(notices.IMPORTED,{})
        with patch('mepiti.notices.CRYSTAL_TABLE_DAY','2026-09-01'):
            notices.sync(self.store,self.N(),force=True)
        self.assertEqual(earnings.overview(self.store)['crystal_alert']['title'],'보스 결정 판매 가격 변경')
    def test_chat_event_question(self):
        class Nexon(self.N):
            def character(s,name,details=False): return {}
        r=answer(self.store,CharacterAnalysisTests.FakeModel('x'),{'message':'지금 진행 중인 이벤트 뭐 있어?'},Nexon())
        self.assertEqual(r['status'],'evidence')
        self.assertIn('샤이닝 스타포스 타임',r['content'])
        self.assertEqual(r['links'][0]['url'],'https://maplestory.nexon.com/news/event/77')
    def test_condition_form_suggests_shining(self):
        notices.sync(self.store,self.N())
        self.store.character_save({'name':'테스트','budget':0,'main':True}); self.store.set_setting('model','m')
        class Nexon(self.N):
            def character(s,name,details=False): return StarforceConversationTests.PROFILE
        r=answer(self.store,CharacterAnalysisTests.FakeModel('x'),{'message':'벨트 22성 기대값 얼마야?'},Nexon())
        self.assertEqual(r['status'],'ask_conditions')
        event=next(q for q in r['form']['questions'] if q['key']=='event')
        self.assertEqual(next(o['value'] for o in event['options'] if o['selected']),'샤타포스')
        self.assertIn('진행 중이라 샤타포스',' '.join(r['conditions']))
