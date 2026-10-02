'use strict';
// 실행: node tests/test_frontend.js — 외부 패키지 없이 실제 화면 함수를 실행한다.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..', 'mepiti', 'static');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const characters = fs.readFileSync(path.join(root, 'characters.js'), 'utf8');

async function checkRequests() {
  let calls = 0, release;
  const context = vm.createContext({token: 'test', fetch: async () => {
    calls++;
    if (calls === 1) await new Promise(resolve => {release = resolve;});
    return {ok: true, json: async () => ({value: 1})};
  }});
  vm.runInContext(app.slice(app.indexOf('async function requestAPI('), app.indexOf('async function guard(')), context);
  const pending = vm.runInContext("Promise.all([api('sessions'),api('sessions'),api('sessions')])", context);
  assert.equal(calls, 1);
  release();
  await pending;
  await vm.runInContext("api('sessions')", context);
  assert.equal(calls, 2, '완료된 GET은 영구 캐시하지 않는다');
  await vm.runInContext("Promise.all([api('tour',{}),api('tour',{})])", context);
  assert.equal(calls, 4, '쓰기 요청은 합치지 않는다');
  context.fetch = async () => {calls++; throw new Error('offline');};
  await assert.rejects(vm.runInContext("api('sessions')", context), /offline/);
  context.fetch = async () => {calls++; return {ok: true, json: async () => ({})};};
  await vm.runInContext("api('sessions')", context);
  assert.equal(calls, 6, '실패한 요청은 다시 시도할 수 있다');
}

async function checkWriteInvalidation() {
  const calls = [];
  const context = vm.createContext({token: 'test', fetch: (path, options) => new Promise(resolve => calls.push({path, options, resolve}))});
  vm.runInContext(app.slice(app.indexOf('async function requestAPI('), app.indexOf('async function guard(')), context);
  const finish = i => calls[i].resolve({ok: true, json: async () => ({revision: i})});
  const old = vm.runInContext("api('sessions')", context);
  const write = vm.runInContext("api('sessions/delete',{id:'A'})", context);
  finish(1); await write;
  const fresh = vm.runInContext("api('sessions')", context);
  assert.equal(calls.length, 3, '쓰기 후에는 이전 GET을 재사용하지 않는다');
  finish(0); await old;
  const shared = vm.runInContext("api('sessions')", context);
  assert.equal(calls.length, 3, '이전 GET 완료가 새 GET을 지우지 않는다');
  finish(2); await Promise.all([fresh, shared]);
}

async function checkChatHistory() {
  const ids = new Map(), calls = [], rendered = [], errors = [];
  function node(tag) {return {tag, children: [], listeners: {}, append(...items) {this.children.push(...items);},
    replaceChildren(...items) {this.children = items;}, addEventListener(name, fn) {this.listeners[name] = fn;},
    setAttribute() {}, focus() {}};}
  const context = vm.createContext({
    sessionId: null, chatTopic: null, busy: false, confirmedText: '',
    $: id => {if (!ids.has(id)) ids.set(id, node('div')); return ids.get(id);}, el: node,
    api: path => path === 'sessions' ? Promise.resolve([]) : new Promise((resolve, reject) => calls.push({path, resolve, reject})),
    guard: async fn => {try {return await fn();} catch (error) {errors.push(error);}},
    renderTopicCard() {}, renderAttachment() {}, switchView() {}, renderMessage: (role, payload) => rendered.push(payload.content),
  });
  vm.runInContext(app.slice(app.indexOf('let historyRequest='), app.indexOf("$('#new-chat').addEventListener")), context);
  const open = id => vm.runInContext(`openSession({id:'${id}',topic:{name:'${id}'}})`, context);
  const old = open('A'), newer = open('B');
  assert.equal(ids.get('#send-button').disabled, true);
  calls[1].resolve([{role: 'assistant', payload: {content: 'B'}}]); await newer;
  calls[0].resolve([{role: 'assistant', payload: {content: 'A'}}]); await old;
  assert.deepEqual(rendered, ['B']); assert.equal(context.chatTopic.name, 'B');
  assert.equal(ids.get('#send-button').disabled, false);
  const reset = open('C');
  vm.runInContext('newChat()', context);
  calls[2].resolve([{role: 'assistant', payload: {content: 'C'}}]); await reset;
  assert.deepEqual(rendered, ['B']); assert.equal(context.sessionId, null); assert.equal(context.chatTopic, null);
  // 오래된 실패가 현재 화면의 오류/로딩 상태를 바꾸지 않는다.
  const failedOld = open('D'), current = open('E');
  calls[3].reject(new Error('stale')); await failedOld;
  assert.equal(ids.get('#send-button').disabled, true);
  calls[4].resolve([]); await current;
  assert.equal(ids.get('#welcome').hidden, false);
  const failedCurrent = open('F'); calls[5].reject(new Error('offline'));
  await assert.rejects(failedCurrent, /offline/);
  assert.equal(ids.get('#send-button').disabled, false);
  // 대화 목록도 마지막 요청만 반영한다.
  const lists = [];
  context.api = () => new Promise(resolve => lists.push(resolve));
  const firstList = vm.runInContext('loadHistory()', context), secondList = vm.runInContext('loadHistory()', context);
  lists[1]([{id: 'E', title: 'E'}]); await secondList;
  lists[0]([{id: 'A', title: 'A'}]); await firstList;
  assert.equal(ids.get('#history').children[0].children[0].title, 'E');
}

function checkAccountReset() {
  const context = vm.createContext({keyRequest: 0, keyChecking: true, catalogRequest: 0, accountLoading: true,
    accountCatalog: {}, accountListTried: true, profileCache: new Map(), selectedCharacterName: 'old',
    shownPreset: 1, profileRequest: 0, $: () => ({removeAttribute() {}}), showProfileEmpty() {}});
  vm.runInContext(characters.slice(characters.indexOf('function resetCharacterCache()'), characters.indexOf('function nexonImage(')), context);
  vm.runInContext('resetCharacterCache()', context);
  assert.equal(context.accountListTried, false);
}

function checkPeerTable() {
  const nodes = [];
  function node(tag) {
    // 브라우저 createElement는 'tr behind'와 같이 공백이 있는 태그를 거절한다.
    assert.match(tag, /^[a-z][a-z0-9-]*$/);
    const value = {tag, children: [], elements: {cp: {value: ''}},
      append(...children) {this.children.push(...children);},
      prepend(...children) {this.children.unshift(...children);},
      replaceChildren(...children) {this.children = children;}};
    nodes.push(value);
    return value;
  }
  const ids = new Map();
  const context = vm.createContext({
    document: {createElement: node}, $: selector => {
      if (!ids.has(selector)) ids.set(selector, node('div'));
      return ids.get(selector);
    }, fmt: String, activeView: 'characters', clearTimeout() {}, setTimeout() {},
  });
  vm.runInContext(app.slice(app.indexOf('const el ='), app.indexOf('function toast(')), context);
  vm.runInContext(characters.slice(characters.indexOf('let peerTimer='), characters.indexOf('async function loadPeers(')), context);
  context.data = {
    target: {job: '전사-히어로', cp: 250000000}, same_job: true, ready: true, running: false,
    calls_left: 60, daily_calls: 60, collected: 8, people: 8, behind: [{slot: '모자', reasons: ['스타포스']}],
    slots: [{slot: '모자', count: 8, items: [{name: '시험 모자', share: 100}], starforce_median: 22,
      potential: {}, additional: {}, mine: {name: '시험 모자', starforce: 18}, behind: ['스타포스']}],
  };
  vm.runInContext('renderPeers(data)', context);
  const rows = nodes.filter(n => n.tag === 'tr');
  assert.equal(rows.length, 2);
  assert.equal(rows[1].className, 'behind');
  assert.equal(rows[1].children.length, 7);
}

async function checkCaptures() {
  const ids = new Map();
  const readers = [], calls = [];
  const field = value => ({value, focus() {}});
  const form = {elements: {meso: field('999'), pieces: field(99), flasks: field(4), day: field('')}};
  ids.set('#hunt-form', form);
  const context = vm.createContext({
    $: id => {if (!ids.has(id)) ids.set(id, {value: '', textContent: '', addEventListener() {}}); return ids.get(id);},
    $$: () => [], fmt: String, exactAmount: String, todayText: () => '2026-10-03', huntPreview() {}, toast() {},
    FileReader: class {readAsDataURL(file) {readers.push(this);}},
    api: (path, data) => new Promise((resolve, reject) => calls.push({resolve, reject})),
  });
  vm.runInContext(app.slice(app.indexOf('const captures='), app.indexOf("$$('.capture-slot').forEach(s=>{\n  s.onclick")), context);
  vm.runInContext(app.slice(app.indexOf("$('#capture-apply').onclick="), app.indexOf('// 보스별로 기억한 결정석')), context);
  const read = () => vm.runInContext("readCaptureFile({type:'image/png'},'before')", context);
  const finishFile = i => {readers[i].result = 'data:image/png;base64,AAAA'; readers[i].onload();};
  const tick = async () => {await Promise.resolve(); await Promise.resolve();};
  // 파일 읽기부터 순서를 정한다. 늦게 읽힌 이전 파일로 유료 모델 요청을 하지 않는다.
  const old = read(), newer = read();
  finishFile(1); await tick(); finishFile(0); await tick();
  assert.equal(calls.length, 1);
  await old;
  calls[0].resolve({values: {inventory_meso: 2}}); await newer;
  assert.equal(vm.runInContext('captures.before.values.inventory_meso', context), 2);
  // 모델 응답도 현재 선택한 캡처만 반영한다.
  const firstAPI = read(); finishFile(2); await tick();
  const secondAPI = read(); finishFile(3); await tick();
  calls[2].resolve({values: {inventory_meso: 4}}); await secondAPI;
  calls[1].resolve({values: {inventory_meso: 3}}); await firstAPI;
  assert.equal(vm.runInContext('captures.before.values.inventory_meso', context), 4);
  // 초기화한 뒤 돌아온 모델 결과는 버린다.
  const resetPending = read(); finishFile(4); await tick();
  ids.get('#capture-reset').onclick();
  calls[3].resolve({values: {inventory_meso: 5}}); await resetPending;
  assert.equal(vm.runInContext('captures.before', context), null);
  assert.equal(vm.runInContext('captures.diff', context), null);
  vm.runInContext('captures.diff={meso:0,pieces:5}', context);
  ids.get('#capture-apply').onclick();
  assert.equal(form.elements.meso.value, '0');
  assert.equal(form.elements.pieces.value, 5);
  assert.equal(form.elements.flasks.value, '');
  vm.runInContext('captures.diff={meso:5,pieces:0}', context);
  ids.get('#capture-apply').onclick();
  assert.equal(form.elements.pieces.value, 0);
}

const timeout = setTimeout(() => {console.error('Frontend checks timed out'); process.exit(1);}, 5000);
(async () => {
  await checkRequests();
  await checkWriteInvalidation();
  await checkChatHistory();
  checkAccountReset();
  checkPeerTable();
  await checkCaptures();
  console.log('Frontend checks passed: GET 동시 요청·재시도·쓰기 분리, 뒤처진 부위 비교 표, 캡처 순서·초기화·0 값, 쓰기 뒤 GET·대화 전환·키 초기화');
})().then(() => clearTimeout(timeout)).catch(error => {clearTimeout(timeout); console.error(error); process.exitCode = 1;});
