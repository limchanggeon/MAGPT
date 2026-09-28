"""넥슨 공지 — 진행 중 이벤트 안내, 공지 본문을 채팅 근거로 수집, 결정석 가격 변경 알림.

공지·업데이트·진행 중 이벤트는 넥슨 Open API(공식)에서 받는다. 본문은 공식 출처라 근거 문서로 바로 승인한다.
공지마다 주제를 따로 두어 서로 '버전 충돌'로 보류되지 않게 한다. 재검토 기한은 업데이트·공지 60일, 이벤트는 종료일.

본문의 표에서 숫자를 자동으로 뽑아 가격표를 바꾸지는 않는다. 공지 모양이 달라 잘못 읽으면 틀린 가격이 들어가기 때문이다.
결정석 판매가 이야기가 있는 새 업데이트가 보이면 알리기만 한다.
"""
import html
import re
from datetime import datetime, timedelta

from .core import AppError, KST, now

SYNCED = 'notice_synced_at'
IMPORTED = 'notice_documents'    # '종류:번호' -> 문서 id
EVENTS = 'notice_events'         # 진행 중 이벤트 목록(목록 API에 기간이 있다)
UPDATES = 'notice_updates'       # 최근 업데이트 제목
ALERT = 'crystal_price_alert'
CRYSTAL_TABLE_DAY = '2026-09-28'  # 앱에 넣은 결정석 가격표(업데이트 813)를 기록한 날
STALE = timedelta(minutes=30)
KINDS = {'notice-update': '업데이트', 'notice-event': '이벤트', 'notice': '공지'}
DETAILS_PER_KIND = 8             # 한 번에 본문까지 받을 새 글 수. 호출 수를 아끼려는 것이다.


def text_of(contents):
    """공지 HTML을 읽을 수 있는 줄글로. 표 칸은 ' | '로, 줄·문단은 줄바꿈으로."""
    body = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', contents or '')
    body = re.sub(r'(?i)<br\s*/?>|</(p|div|li|tr|h[1-6]|table)>', '\n', body)
    body = re.sub(r'(?i)</t[dh]>', ' | ', body)
    body = html.unescape(re.sub(r'<[^>]+>', ' ', body))
    lines = [re.sub(r'[ \t ]+', ' ', line).strip(' |') for line in body.split('\n')]
    return '\n'.join(line for line in lines if line)[:100000]


def when(text):
    try:
        return datetime.fromisoformat(str(text)).astimezone(KST)
    except (TypeError, ValueError):
        return None


def stale(store):
    last = when(store.setting(SYNCED))
    return last is None or datetime.now(KST) - last > STALE


def sync(store, nexon, force=False):
    """목록을 받고, 새 글의 본문을 근거 문서로 넣는다. 30분 안에는 다시 받지 않는다."""
    if not hasattr(nexon, 'notices') or (not force and not stale(store)):
        return {'skipped': True}
    imported = dict(store.setting(IMPORTED) or {})
    added, failed = 0, []
    for kind, label in KINDS.items():
        try:
            rows = nexon.notices(kind)
        except AppError as e:
            failed.append(f'{label}: {e}')
            continue
        if kind == 'notice-event':
            store.set_setting(EVENTS, [{k: r[k] for k in ('title', 'url', 'start', 'end', 'date')} for r in rows])
        if kind == 'notice-update':
            store.set_setting(UPDATES, [{k: r[k] for k in ('title', 'url', 'date', 'id')} for r in rows[:10]])
        fresh = [r for r in rows if f"{kind}:{r['id']}" not in imported][:DETAILS_PER_KIND]
        for r in fresh:
            try:
                detail = nexon.notice_detail(kind, r['id'])
            except AppError as e:
                failed.append(f"{r['title']}: {e}")
                continue
            doc = to_document(kind, label, r, detail)
            if not doc:
                imported[f"{kind}:{r['id']}"] = None
                continue
            did = store.import_document(doc)['id']
            store.review({'id': did, 'approve': True})
            imported[f"{kind}:{r['id']}"] = did
            added += 1
            if kind == 'notice-update':
                check_crystal(store, r, doc['body'])
    store.set_setting(IMPORTED, imported)
    store.set_setting(SYNCED, now())
    return {'added': added, 'failed': failed[:5]}


def to_document(kind, label, row, detail):
    body = text_of(detail.get('contents'))
    url = detail.get('url') or row.get('url')
    published = when(detail.get('date') or row.get('date')) or datetime.now(KST)
    if not body or not url or not url.startswith('https://'):
        return None
    end = when(detail.get('end') or row.get('end'))
    until = end if end and end > datetime.now(KST) else published + timedelta(days=60)
    if until <= datetime.now(KST):
        until = datetime.now(KST) + timedelta(days=7)   # 오래된 글도 잠깐은 찾을 수 있게
    return {'title': f"[{label}] {detail.get('title') or row['title']}"[:200], 'body': body,
            'metadata': {'source_url': url, 'source_type': 'official', 'published_at': published.isoformat(),
                         'effective_from': published.isoformat(), 'valid_until': until.isoformat(),
                         'version': f"{kind}-{row['id']}", 'topic': f"nexon-{kind}-{row['id']}",
                         'region': 'KR', 'server_type': 'live'}}


def check_crystal(store, row, body):
    """새 업데이트에 결정석 판매가 이야기가 있으면 알린다. 숫자는 자동으로 바꾸지 않는다."""
    published = when(row.get('date'))
    if not published or published.date().isoformat() <= CRYSTAL_TABLE_DAY:
        return
    if re.search(r'결정', body) and re.search(r'판매\s*(가격|가)', body):
        store.set_setting(ALERT, {'title': row['title'], 'url': row.get('url'), 'date': row.get('date')})


def active_events(store, at=None):
    at = at or datetime.now(KST)
    result = []
    for e in store.setting(EVENTS) or []:
        start, end = when(e.get('start')), when(e.get('end'))
        if (start is None or start <= at) and (end is None or end >= at):
            result.append(e)
    return result


def suggested_event(store):
    """진행 중인 샤이닝 스타포스가 있으면 강화 조건의 이벤트로 제안한다."""
    for e in active_events(store):
        if re.search(r'샤이닝\s*스타\s*포스|샤타', e.get('title') or ''):
            return '샤타포스', e
    return None, None


def events_text(store):
    events = active_events(store)
    if not events:
        return '지금 진행 중인 이벤트를 넥슨 공지에서 찾지 못했습니다. (최근 이벤트 공지 20개 기준)'
    lines = ['**지금 진행 중인 이벤트** (넥슨 공식 공지)', '']
    for e in events:
        end = when(e.get('end'))
        lines.append(f"- {e['title']}" + (f" — {end:%m/%d %H:%M}까지" if end else ''))
    return '\n'.join(lines)
