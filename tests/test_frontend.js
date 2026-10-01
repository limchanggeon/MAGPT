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

(async () => {
  await checkRequests();
  checkPeerTable();
  console.log('Frontend checks passed: GET 동시 요청·재시도·쓰기 분리, 뒤처진 부위 비교 표');
})().catch(error => {console.error(error); process.exitCode = 1;});
