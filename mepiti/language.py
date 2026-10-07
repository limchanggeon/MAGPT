"""Search vocabulary, not a source of current game rules or prices.

Keep the original utterance; expand only recognized terms. Unknown slang is left to
contextual model interpretation/clarification, never taught as an invented fact.
"""
import re

# Stable language mappings; semantic ambiguity (환산, 대장장이, 해방) is not replaced.
ALIASES = {
    '기댓값': '기대값', '에테뚝': '에테르넬 모자', '에테 뚝': '에테르넬 모자',
    '에테 뚝배기': '에테르넬 모자', '에테 상하의': '에테르넬 상의 하의',
    '아케인뚝': '아케인셰이드 모자', '앱솔뚝': '앱솔랩스 모자',
    '무보엠': '무기 보조무기 엠블렘', '보공': '보스 몬스터 데미지',
    '방무': '방어율 무시', '크확': '크리티컬 확률', '크뎀': '크리티컬 데미지',
    '추옵': '추가옵션', '에디': '에디셔널 잠재능력', '샤타포스': '샤이닝 스타포스',
    '샤타': '샤이닝 스타포스', '카벨': '카오스 벨룸',
}
# Suffixes preserve Korean particles without matching unrelated words or names.
_SUFFIX = r'(?=$|[\s\d?!.,/·+~%\-]|은|는|이|가|을|를|의|에|도|만|랑|하고|보다|까지|에서|으로|인데|뭐|어때|얼마)'
_PATTERN = re.compile(r'(?<![가-힣A-Za-z])(' + '|'.join(re.escape(k) for k in sorted(ALIASES,key=len,reverse=True)) + ')' + _SUFFIX)


def expand(text):
    return _PATTERN.sub(lambda m: ALIASES[m.group(1)], str(text))


def mappings(text):
    return [{'term': key, 'canonical': ALIASES[key]} for key in dict.fromkeys(m.group(1) for m in _PATTERN.finditer(text))]


def hints(text):
    known = mappings(text)
    return {'original': text, 'expanded': expand(text), 'recognized_terms': known,
            'ambiguity_policy': '뜻이 여러 개인 용어는 대화와 캐릭터·장비 문맥으로 판단. 모르면 뜻을 만들지 말고 확인. 직업군·확률·시세는 용어로 추정하지 않음.'}


def query_variants(text):
    expanded = expand(text).strip()
    variants = [expanded, text.strip()]
    # Drop question endings from retrieval, without modifying numbers or the user's utterance.
    tokens = re.findall(r'[가-힣A-Za-z0-9]+', expanded)
    noise = {'그럼','그러면','내','제','어떻게','어떤','왜','뭐야','뭐예요','알려줘','알려주세요','설명해줘','궁금해','좀'}
    tokens = [re.sub(r'(?:에서는|으로는|이랑|에서|까지|은|는|을|를|의)$','',t) for t in tokens if t not in noise]
    variants.append(' '.join(t for t in tokens if len(t)>1))
    return list(dict.fromkeys(v[:300] for v in variants if v))
