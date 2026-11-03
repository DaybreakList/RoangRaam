const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup(fetchImpl = async () => ({ok: true, status: 200, json: async () => ({message: 'ย้ายห้องเรียบร้อยแล้ว'})})) {
    let factory, frameId = 0;
    const frames = new Map();
    const env = {
        document: {addEventListener: (_, callback) => callback()},
        Alpine: {data: (_, value) => { factory = value; }},
        URLSearchParams, AbortController, setTimeout, clearTimeout,
        performance: {now: () => 0}, fetch: fetchImpl,
        requestAnimationFrame: callback => { frames.set(++frameId, callback); return frameId; },
        cancelAnimationFrame: id => frames.delete(id),
        window: {location: {pathname: '/p/hotel-a/frontdesk/tape-chart/', search: '?start=2026-11-01&days=60&floor=1&room_type=2'}},
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
    const realRefresh = app.refresh;
    app.refresh = async () => { app.version++; };
    return {app, env, factory, frames, realRefresh, tick() {
        const callbacks = [...frames.values()]; frames.clear();
        callbacks.forEach(callback => callback());
    }};
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
    assert.equal(request[1], '/p/hotel-a/frontdesk/tape-chart/?start=2026-11-01&days=60&floor=1&room_type=2');
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


function mount(app) {
    const buttons = Object.keys(app.data.stays).map(id => ({dataset: {bookingRoomId: id}, disabled: true}));
    const targets = Object.keys(app.data.rooms).map(id => ({dataset: {targetRoom: id}, hidden: true, disabled: true}));
    const rows = Object.keys(app.data.rooms).map(id => {
        const classes = new Set();
        return {dataset: {roomId: id}, classes, classList: {
            add: name => classes.add(name), remove: (...names) => names.forEach(name => classes.delete(name)),
        }};
    });
    const scroll = {scrollTop: 0, getBoundingClientRect: () => ({top: 0, bottom: 300, left: 0, right: 500})};
    const panel = {
        querySelector: selector => selector === '#tc-move-data' ? {textContent: JSON.stringify({
            ...app.data, serverNow: '2026-11-01T00:00:00Z',
        })} : scroll,
        querySelectorAll: selector => selector === '.tc-movable' ? buttons : selector === '[data-target-room]' ? targets : rows,
    };
    app.$root.contains = node => [...rows, ...buttons, ...targets].includes(node);
    app.loadPanel(panel);
    return {buttons, targets, rows, panel, scroll};
}

const pointer = (x, y, id = 7) => ({isPrimary: true, button: 0, pointerId: id, clientX: x, clientY: y});

test('many pointer events coalesce into one frame, using the latest coordinates and cached collisions', () => {
    const {app, env, frames, tick} = setup();
    const {rows} = mount(app);
    const hits = [];
    env.document.elementFromPoint = (x, y) => { hits.push([x, y]); return {closest: () => rows[1]}; };
    app.beginDrag(pointer(1, 1), '99');
    app.reason = () => { throw new Error('Hover must not rescan occupancy'); };
    for (let x = 10; x <= 50; x++) app.dragOver(pointer(x, 100));
    assert.equal(frames.size, 1);
    tick();
    assert.deepEqual(hits, [[50, 100]]);
    assert.equal(app.pointerX, 50);
    assert.equal(app.hoverRoom, '20');
    assert.equal(rows[1].classes.has('tc-drop-valid'), true);
});

test('release before pending frame uses final hit-test, cancels frame and revalidates expiry before POST', async () => {
    let calls = 0;
    const {app, env, frames} = setup(async () => { calls++; });
    const {rows} = mount(app);
    env.document.elementFromPoint = x => ({closest: () => x === 50 ? rows[1] : rows[0]});
    app.data.stays['99'].expiresAt = '2026-11-01T00:00:01Z';
    app.beginDrag(pointer(1, 1), '99');
    app.dragOver(pointer(10, 100));
    app.now += 1000;
    await app.drop(pointer(50, 100));
    assert.equal(calls, 0);
    assert.equal(frames.size, 0);
    assert.equal(app.dragging, false);
    assert.match(app.message, /หมดเวลา/);
});

test('auto-scroll continues at edge without new pointer events and stops on cancel', () => {
    const {app, env, frames, tick} = setup();
    const {rows, scroll} = mount(app);
    env.document.elementFromPoint = () => ({closest: () => rows[1]});
    app.beginDrag(pointer(10, 100), '99');
    app.dragOver(pointer(10, 290));
    tick(); tick();
    assert.equal(scroll.scrollTop, 32);
    assert.equal(frames.size, 1);
    app.cancel();
    assert.equal(frames.size, 0);
    assert.equal(rows[1].classes.size, 0);
});

test('keyboard selection and target activation use delegated native buttons', async () => {
    const {app} = setup();
    const {buttons, targets} = mount(app);
    app.handleClick({detail: 0, target: {closest: selector => selector === '.tc-movable' ? buttons[0] : null}});
    assert.equal(app.selected, '99');
    assert.equal(targets[0].disabled, true);
    assert.equal(targets[1].disabled, false);
    assert.equal(targets[1].hidden, false);
    let destination;
    app.submitMove = async id => { destination = id; };
    await app.handleClick({detail: 0, target: {closest: selector => selector === '[data-target-room]' ? targets[1] : null}});
    assert.equal(destination, '20');
    app.cancel();
    assert.equal(targets[1].hidden, true);
});

test('pending payment expires while idle: disables native control and all targets, stops drag', () => {
    const {app, env, frames} = setup();
    let timer;
    env.setTimeout = callback => { timer = callback; return 1; };
    env.clearTimeout = () => {};
    app.data.stays['99'].expiresAt = '2026-11-01T00:00:01Z';
    const {buttons, targets} = mount(app);
    app.beginDrag(pointer(10, 100), '99');
    app.dragOver(pointer(20, 100));
    assert.equal(targets[1].disabled, false);
    app.now += 1000;
    timer();
    assert.equal(buttons[0].disabled, true);
    assert.equal(targets[1].disabled, true);
    assert.equal(app.dragging, false);
    assert.equal(frames.size, 0);
    assert.match(app.message, /หมดเวลา/);
});

test('refresh replaces cached destinations and cancels pending drag frame', () => {
    const {app, frames} = setup();
    mount(app);
    app.beginDrag(pointer(10, 100), '99');
    app.dragOver(pointer(20, 100));
    app.data.rooms['20'].stays.push({id: '100', start: '2026-11-05', end: '2026-11-06'});
    const {targets} = mount(app);
    assert.equal(frames.size, 0);
    assert.equal(app.selected, null);
    app.select('99');
    assert.equal(targets[1].disabled, true);
    assert.match(targets[1].title, /ไม่ว่าง/);
});

test('saving disables native move controls, and failed refresh keeps them disabled', async () => {
    const {app} = setup(async () => { throw new Error('offline'); });
    const {buttons, targets} = mount(app);
    app.select('99');
    app.refresh = async () => { throw new Error('offline'); };
    const saving = app.submitMove('20');
    assert.equal(buttons[0].disabled, true);
    assert.equal(targets[1].disabled, true);
    await saving;
    assert.equal(buttons[0].disabled, true);
    assert.equal(targets[1].hidden, true);
});


// Small DOM double for row lifecycle/focus tests. No browser library is required.
function virtualMount(context, rowHeight = 112) {
    const {app, env} = context, doc = env.document;
    class Element {
        constructor(tag) {
            this.tagName = tag; this.children = []; this.attributes = {}; this.dataset = {};
            this.style = {}; this.listeners = new Map(); this.disabled = false; this.hidden = false;
            this.tabIndex = -1; this.scrollTop = 0; this.scrollLeft = 0; this.clientHeight = 500;
            this.classList = {add: value => this.attributes.class = [...new Set((this.attributes.class || '').split(' ').concat(value))].join(' '),
                remove: (...values) => this.attributes.class = (this.attributes.class || '').split(' ').filter(value => !values.includes(value)).join(' ')};
        }
        setAttribute(name, value) {
            this.attributes[name] = String(value);
            if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = String(value);
            if (name === 'tabindex') this.tabIndex = Number(value);
        }
        matches(selector) {
            if (selector === 'button:not([disabled]):not([hidden]), [tabindex="0"]') return this.tagName === 'button' && !this.disabled && !this.hidden || this.tabIndex === 0;
            if (selector === '[name=csrfmiddlewaretoken]') return this.attributes.name === 'csrfmiddlewaretoken';
            if (selector.startsWith('.')) return (this.attributes.class || '').split(' ').includes(selector.slice(1));
            if (selector.startsWith('#')) return this.attributes.id === selector.slice(1);
            const match = selector.match(/^\[([^=]+)(?:="([^"]*)")?\]$/);
            return !!match && match[1] in this.attributes && (match[2] === undefined || this.attributes[match[1]] === match[2]);
        }
        querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
        closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector) || null; }
        contains(element) { return element === this || this.children.some(child => child.contains(element)); }
        append(...elements) { for (const element of elements) this.insertBefore(element, null); }
        insertBefore(element, anchor) {
            element.remove();
            const index = anchor ? this.children.indexOf(anchor) : this.children.length;
            this.children.splice(index, 0, element); element.parent = this;
        }
        remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; }
        get nextElementSibling() { return this.parent?.children[this.parent.children.indexOf(this) + 1] || null; }
        focus() { doc.activeElement = this; }
        addEventListener(name, callback) { this.listeners.set(name, callback); }
        removeEventListener(name) { this.listeners.delete(name); }
        getBoundingClientRect() { return {top: 0, bottom: 500, left: 0, right: 500}; }
    }
    doc.createElement = tag => new Element(tag);
    const element = (tag, attrs = {}) => { const value = new Element(tag); for (const [name, text] of Object.entries(attrs)) value.setAttribute(name, text); return value; };
    const root = element('main'), panel = element('section'), script = element('script', {id: 'tc-move-data'});
    const scroll = element('div', {class: 'tc-scroll'}), grid = element('div', {class: 'tc-grid', 'data-virtual-rows': 'true'});
    grid.append(element('div', {'data-spacer': 'top'}), element('div', {'data-spacer': 'bottom'}));
    const csrf = element('input', {name: 'csrfmiddlewaretoken'}); csrf.value = 'csrf-test';
    root.append(panel); panel.append(script, csrf, scroll); scroll.append(grid);
    const sample = app.data.stays['99'];
    const data = {serverNow: '2026-11-01T00:00:00Z', viewport: {rowHeight, headerHeight: 64, overscan: 3},
        labels: {CONFIRMED: 'ยืนยันแล้ว'}, dates: {'2026-11-01': '1 พ.ย. 2026', '2026-12-01': '1 ธ.ค. 2026'}, order: [], rooms: {}, stays: {}};
    for (let i = 0; i < 90; i++) {
        const id = String(i + 1), stayId = String(i + 99);
        data.order.push(id);
        data.rooms[id] = {id, typeId: '1', active: true, number: id, floor: 1, typeName: 'Twin', hkStatus: 'DIRTY', hkLabel: 'รอทำความสะอาด',
            bars: i === 89 ? [] : [stayId], stays: i === 89 ? [] : [{id: stayId, start: sample.start, end: sample.end}]};
        if (i !== 89) data.stays[stayId] = {...sample, id: stayId, roomId: id, display: {bookingId: 'booking-' + id,
            code: 'CODE-' + id, guest: 'Synthetic guest', status: 'CONFIRMED', column: 1, span: 30, before: false, after: false}};
    }
    const reload = () => {
        for (const row of grid.querySelectorAll('[data-room-id]')) row.remove();
        script.textContent = JSON.stringify(data); app.loadPanel(panel);
    };
    app.$root = root; reload();
    doc.elementFromPoint = (_, y) => grid.querySelectorAll('[data-room-id]').find(row =>
        row.dataset.roomId === data.order[Math.floor((y + scroll.scrollTop - data.viewport.headerHeight) / data.viewport.rowHeight)]) || null;
    return {data, grid, scroll, panel, reload, doc, rows: () => grid.querySelectorAll('[data-room-id]')};
}

test('virtual range bounds include overscan and clamp first/last/empty viewports', () => {
    const {app} = setup();
    assert.equal(JSON.stringify(app.windowRange(0, 500, 90)), JSON.stringify({start: 0, end: 7}));
    assert.equal(JSON.stringify(app.windowRange(112 * 40, 500, 90)), JSON.stringify({start: 37, end: 47}));
    assert.equal(app.windowRange(999999, 500, 90).end, 90);
    assert.equal(app.windowRange(0, 500, 0).end, 0);
});

test('virtual DOM stays bounded, preserves all model data, and inserts guest text without HTML interpretation', () => {
    const context = setup(), view = virtualMount(context), {app} = context;
    assert.equal(view.rows().length, 7);
    assert.equal(Object.keys(app.data.rooms).length, 90);
    view.data.stays['187'].display.guest = '<img src=x onerror=alert(1)>'; view.reload();
    app.focusRoom(88);
    assert.ok(view.rows().length <= Math.ceil((view.scroll.clientHeight - 64) / 112) + 1 + 6);
    assert.equal(view.grid.querySelectorAll('.tc-guest').at(-1).textContent, '<img src=x onerror=alert(1)>');
    assert.equal(view.grid.querySelectorAll('[data-spacer="top"]')[0].style.height, 82 * 112 + 'px');
});

test('End/Home/PageDown and Tab/Shift-Tab move focus across recycled rows', () => {
    const context = setup(), view = virtualMount(context), {app} = context;
    const key = (key, shiftKey = false) => { let prevented = false; app.handleKey({key, shiftKey, target: view.doc.activeElement,
        preventDefault() { prevented = true; }}); return prevented; };
    app.focusRoom(6);
    assert.equal(key('Tab'), true);
    assert.equal(view.doc.activeElement.closest('[data-room-id]').dataset.roomId, '8');
    key('Tab', true);
    assert.equal(view.doc.activeElement.closest('[data-room-id]').dataset.roomId, '7');
    key('End'); assert.equal(view.doc.activeElement.closest('[data-room-id]').dataset.roomId, '90');
    key('Home'); assert.equal(view.doc.activeElement.closest('[data-room-id]').dataset.roomId, '1');
    key('PageDown'); assert.equal(view.doc.activeElement.closest('[data-room-id]').dataset.roomId, '4');
});

test('scrolling away from a focused recycled row moves focus to chart without losing keyboard navigation', () => {
    const context = setup(), view = virtualMount(context), {app, tick} = context;
    app.focusRoom(0);
    view.scroll.scrollTop = 112 * 40;
    view.scroll.listeners.get('scroll')(); tick();
    assert.equal(view.doc.activeElement, view.scroll);
    app.handleKey({key: 'ArrowDown', target: view.scroll, preventDefault() {}});
    assert.equal(view.doc.activeElement.closest('[data-room-id]').dataset.roomId, '2');
});

test('release mounts the destination at final scroll position before hit-test, even with pending drag frame', async () => {
    const context = setup(), view = virtualMount(context), {app, frames} = context;
    app.beginDrag(pointer(10, 100), '99');
    view.scroll.scrollTop = 90 * 112 - 436;
    app.dragOver(pointer(100, 480));
    let target; app.submitMove = async id => { target = id; };
    await app.drop(pointer(100, 480));
    assert.equal(target, '90');
    assert.ok(view.rows().some(row => row.dataset.roomId === '90'));
    assert.equal(frames.size, 0);
});

test('edge auto-scroll recycles rows and cancellation clears pending pointer work', () => {
    const context = setup(), view = virtualMount(context), {app, tick, frames} = context;
    view.scroll.scrollTop = 112 * 20; app.renderWindow();
    const first = view.rows()[0].dataset.roomId;
    app.beginDrag(pointer(100, 100), '99'); app.dragOver(pointer(100, 490));
    for (let i = 0; i < 12; i++) tick();
    assert.notEqual(view.rows()[0].dataset.roomId, first);
    assert.ok(view.rows().length <= Math.ceil((view.scroll.clientHeight - 64) / 112) + 1 + 6);
    app.cancel(); assert.equal(frames.size, 0);
});

test('HTMX move refresh restores vertical/horizontal scroll and focuses moved booking in newly mounted row', async () => {
    const context = setup(), view = virtualMount(context), {app, env, factory} = context;
    app.focusRoom(89); view.scroll.scrollLeft = 1000; app.selected = '99';
    const top = view.scroll.scrollTop;
    env.htmx = {ajax: async () => {
        view.data.rooms['1'].bars = []; view.data.rooms['90'].bars = ['99']; view.data.stays['99'].roomId = '90';
        view.reload();
    }};
    app.refresh = context.realRefresh; await app.submitMove('90');
    assert.equal(view.scroll.scrollTop, top); assert.equal(view.scroll.scrollLeft, 1000);
    assert.equal(view.doc.activeElement.dataset.bookingRoomId, '99');
    assert.equal(view.doc.activeElement.closest('[data-room-id]').dataset.roomId, '90');
});

test('offscreen expiry and stale refresh disable controls when those rows are mounted later', () => {
    const context = setup(), view = virtualMount(context), {app} = context;
    view.data.stays['187'].expiresAt = '2026-11-01T00:00:01Z'; view.reload();
    app.now += 1000; app.focusRoom(88);
    assert.equal(view.rows().find(row => row.dataset.roomId === '89').querySelector('.tc-movable').disabled, true);
    app.stale = true; app.focusRoom(0); app.syncControls();
    assert.ok(view.grid.querySelectorAll('.tc-movable').every(bar => bar.disabled));
    app.destroy();
});

test('reload/destroy remove old scroll listeners and cancel queued virtual window frames', () => {
    const context = setup(), view = virtualMount(context), {app, frames} = context;
    view.reload(); assert.equal(view.scroll.listeners.size, 1);
    app.queueWindow(); assert.equal(frames.size, 1);
    app.destroy(); assert.equal(frames.size, 0); assert.equal(view.scroll.listeners.size, 0);
});


test('resize coalesces window work and changes mounted row count without resetting selection or focus', () => {
    const context = setup(), view = virtualMount(context), {app, tick, frames} = context;
    app.focusRoom(0); app.select('99');
    const focused = view.doc.activeElement;
    view.scroll.clientHeight = 1200;
    for (let i = 0; i < 10; i++) app.queueWindow();
    assert.equal(frames.size, 1); tick();
    assert.equal(view.rows().length, 14);
    assert.equal(app.selected, '99'); assert.equal(view.doc.activeElement, focused);
    view.scroll.clientHeight = 400; app.queueWindow(); tick();
    assert.equal(view.rows().length, 6);
    assert.equal(app.selected, '99');
    app.destroy();
});

for (const rowHeight of [112, 144]) {
    test(`row geometry ${rowHeight}px keeps spacers, keyboard and targets aligned through selection`, () => {
        const context = setup(), view = virtualMount(context, rowHeight), {app} = context;
        view.scroll.clientHeight = 512;
        app.focusRoom(40);
        const before = view.scroll.scrollTop;
        app.select('99');
        app.renderWindow();
        assert.equal(view.scroll.scrollTop, before);
        assert.ok(view.grid.querySelectorAll('[data-target-room]').every(target => !target.hidden));
        const top = Number.parseInt(view.grid.querySelectorAll('[data-spacer="top"]')[0].style.height);
        const bottom = Number.parseInt(view.grid.querySelectorAll('[data-spacer="bottom"]')[0].style.height);
        assert.equal(top + view.rows().length * rowHeight + bottom, 90 * rowHeight);
        app.handleKey({key: 'PageDown', target: view.doc.activeElement, preventDefault() {}});
        assert.equal(view.doc.activeElement.closest('[data-room-id]').dataset.roomId, rowHeight === 112 ? '45' : '44');
        app.cancel();
        assert.ok(view.grid.querySelectorAll('[data-target-room]').every(target => target.hidden));
        app.focusRoom(89);
        assert.equal(view.rows().at(-1).dataset.roomId, '90');
        assert.equal(view.grid.querySelectorAll('[data-spacer="bottom"]')[0].style.height, '0px');
    });
}
