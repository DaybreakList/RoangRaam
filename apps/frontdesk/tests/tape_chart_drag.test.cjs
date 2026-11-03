const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup(fetchImpl = async () => ({ok: true, status: 200, json: async () => ({message: 'ย้ายห้องเรียบร้อยแล้ว'})})) {
    let factory;
    const env = {
        document: {addEventListener: (_, callback) => callback()},
        Alpine: {data: (_, value) => { factory = value; }},
        URLSearchParams, AbortController, setTimeout, clearTimeout,
        performance: {now: () => 0}, fetch: fetchImpl,
        window: {location: {pathname: '/p/hotel-a/frontdesk/tape-chart/', search: '?start=2026-11-01&floor=1'}},
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../../static/frontdesk/tape_chart.js'), 'utf8'), env);
    const app = factory();
    app.$root = {querySelector: () => ({value: 'csrf-test-token'})};
    app.$nextTick = async () => {};
    app.now = Date.parse('2026-11-01T00:00:00Z');
    app.data = {
        rooms: {
            '10': {id: '10', typeId: '1', active: true, number: '101', stays: []},
            '20': {id: '20', typeId: '1', active: true, number: '102', stays: []},
        },
        stays: {'99': {id: '99', roomId: '10', typeId: '1', start: '2026-11-01', end: '2026-12-01',
                      canMove: true, expiresAt: null, url: '/p/hotel-a/frontdesk/tape-chart/rooms/99/move/'}},
    };
    app.selected = '99';
    app.refresh = async () => { app.version++; };
    return {app, env, factory};
}

test('full stay overlap blocks even beyond visible fourteen days; checkout boundary allows adjacency', () => {
    const {app} = setup();
    app.data.rooms['20'].stays = [{id: '100', start: '2026-11-25', end: '2026-11-26'}];
    assert.match(app.reason('20'), /ไม่ว่าง/);
    app.data.rooms['20'].stays[0].start = '2026-12-01';
    app.data.rooms['20'].stays[0].end = '2026-12-02';
    assert.equal(app.reason('20'), '');
});

test('inactive, wrong type, missing and same room are blocked', () => {
    const {app} = setup();
    assert.notEqual(app.reason('10'), '');
    assert.notEqual(app.reason('999'), '');
    app.data.rooms['20'].active = false;
    assert.notEqual(app.reason('20'), '');
    app.data.rooms['20'].active = true;
    app.data.rooms['20'].typeId = '2';
    assert.notEqual(app.reason('20'), '');
});

test('same-booking sibling still blocks; eligibility expires at exact deadline', () => {
    const {app} = setup();
    app.data.rooms['20'].stays = [{id: '100', start: '2026-11-01', end: '2026-12-01'}];
    assert.notEqual(app.reason('20'), '');
    app.data.stays['99'].expiresAt = '2026-11-01T00:00:00Z';
    assert.equal(app.canMove('99'), false);
    app.now--;
    assert.equal(app.canMove('99'), true);
    app.data.stays['99'].canMove = false;
    assert.equal(app.canMove('99'), false);
});

test('blocked drop sends no request', async () => {
    let calls = 0;
    const {app} = setup(async () => { calls++; });
    await app.submitMove('10');
    assert.equal(calls, 0);
    assert.match(app.message, /อยู่ในห้องนี้/);
});

test('one form POST with CSRF and BookingRoom URL; no dates or price sent, duplicate blocked', async () => {
    let finish;
    const requests = [];
    const {app} = setup((url, options) => {
        requests.push({url, options});
        return new Promise(resolve => { finish = resolve; });
    });
    const saving = app.submitMove('20');
    await app.submitMove('20');
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/p/hotel-a/frontdesk/tape-chart/rooms/99/move/');
    assert.equal(requests[0].options.body.toString(), 'target_room_id=20');
    assert.equal(requests[0].options.headers['X-CSRFToken'], 'csrf-test-token');
    assert.equal(requests[0].options.credentials, 'same-origin');
    assert.equal(app.data.stays['99'].roomId, '10');
    finish({ok: true, status: 200, json: async () => ({message: 'ย้ายห้องเรียบร้อยแล้ว'})});
    await saving;
    assert.equal(app.busy, false);
    assert.equal(app.version, 1);
    assert.equal(app.message, 'ย้ายห้องเรียบร้อยแล้ว');
});

test('server collision shows Thai error and keeps original position', async () => {
    const {app} = setup(async () => ({ok: false, status: 400, json: async () => ({errors: {target_room_id: ['ห้องไม่ว่างแล้ว']}})}));
    await app.submitMove('20');
    assert.equal(app.message, 'ห้องไม่ว่างแล้ว');
    assert.equal(app.data.stays['99'].roomId, '10');
    assert.equal(app.version, 1);
});

test('network uncertainty and failed refresh disable further moves', async () => {
    const {app} = setup(async () => { throw new Error('offline'); });
    app.refresh = async () => { throw new Error('offline'); };
    await app.submitMove('20');
    assert.equal(app.stale, true);
    assert.equal(app.canMove('99'), false);
    assert.match(app.message, /ไม่สามารถยืนยันผล/);
    assert.match(app.message, /โหลดหน้าใหม่/);
});

test('refresh preserves filter URL and updates only the chart panel', async () => {
    const {app, env, factory} = setup();
    let request;
    env.htmx = {ajax: async (...args) => { request = args; app.version++; }};
    app.refresh = factory().refresh;
    await app.refresh();
    assert.equal(request[0], 'GET');
    assert.equal(request[1], '/p/hotel-a/frontdesk/tape-chart/?start=2026-11-01&floor=1');
    assert.equal(request[2].target, '#tc-panel');
});

test('Escape/pointer cancel resets selection without submitting', () => {
    const {app} = setup();
    app.dragging = true;
    app.pointerId = 1;
    app.cancel();
    assert.equal(app.selected, null);
    assert.equal(app.dragging, false);
    assert.equal(app.pointerId, null);
});


test('pointer drag uses hit-tested room and submits only on matching pointer release', async () => {
    const {app, env} = setup();
    const row = {dataset: {roomId: '20'}};
    env.document.elementFromPoint = () => ({closest: () => row});
    app.$root.contains = value => value === row;
    app.$root.querySelector = () => null;
    let submitted;
    app.submitMove = async id => { submitted = id; };
    app.beginDrag({isPrimary: true, button: 0, pointerId: 7, clientX: 10, clientY: 10}, '99');
    app.drop({pointerId: 8, clientX: 20, clientY: 20});
    assert.equal(submitted, undefined);
    await app.drop({pointerId: 7, clientX: 20, clientY: 20});
    assert.equal(submitted, '20');
    assert.equal(app.dragging, false);
});

test('release outside chart keeps selection without submitting', () => {
    const {app, env} = setup();
    env.document.elementFromPoint = () => null;
    app.$root.querySelector = () => null;
    app.beginDrag({isPrimary: true, button: 0, pointerId: 7, clientX: 10, clientY: 10}, '99');
    app.drop({pointerId: 7, clientX: 0, clientY: 0});
    assert.equal(app.dragging, false);
    assert.equal(app.selected, '99');
    assert.match(app.message, /กรุณาเลือกห้องปลายทาง/);
});
