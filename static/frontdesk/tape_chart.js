/* The server is authoritative. This component only previews allowed destinations. */
(function () {
    function tapeChart() {
        // Large, immutable payload and DOM references do not need Alpine proxies.
        const state = {data: {rooms: {}, stays: {}}, buttons: [], targets: [], rows: new Map(),
            reasons: {}, frame: null, point: null, scroll: null, expiry: null,
            grid: null, top: null, bottom: null, range: null, windowFrame: null,
            onScroll: null, restore: null, focusIndex: 0};
        function node(tag, attributes = {}, text) {
            const element = document.createElement(tag);
            for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
            if (text !== undefined) element.textContent = text;
            return element;
        }
        function bookingBar(id) {
            const stay = state.data.stays[id], display = stay.display;
            const label = state.data.labels[display.status];
            const bar = node(stay.canMove ? 'button' : 'div', {
                class: 'tc-bar tc-' + display.status.toLowerCase() + (stay.canMove ? ' tc-movable' : ''),
                'data-booking-id': display.bookingId, 'data-booking-room-id': id,
                title: `${display.code} · ${display.guest} · ${label} · ${state.data.dates[stay.start]} ถึง ${state.data.dates[stay.end]} (วันเช็คเอาต์)`,
            });
            if (stay.canMove) {
                bar.type = 'button'; bar.disabled = true;
                bar.setAttribute('aria-label', 'ย้ายห้องสำหรับการจอง ' + display.code);
            } else bar.tabIndex = 0;
            bar.style.gridColumn = display.column + ' / span ' + display.span;
            bar.append(node('strong', {}, (display.before ? '← ' : '') + display.code + (display.after ? ' →' : '')),
                node('span', {class: 'tc-guest'}, display.guest), node('span', {}, label));
            return bar;
        }
        function roomRow(id) {
            const room = state.data.rooms[id];
            const row = node('section', {class: 'tc-row', 'data-room-id': id, tabindex: '-1', 'aria-label': 'ห้อง ' + room.number});
            const info = node('div', {class: 'tc-room'}), title = node('div', {class: 'tc-room-title'});
            title.append(node('strong', {title: room.number}, room.number),
                node('span', {class: 'tc-housekeeping tc-housekeeping--' + room.hkStatus.toLowerCase()}, room.hkLabel));
            const details = 'ชั้น ' + room.floor + ' · ' + room.typeName;
            info.append(title, node('span', {class: 'tc-room-details', title: details}, details));
            if (!room.active) info.append(node('span', {class: 'tc-closed'}, 'ปิดใช้งาน'));
            const target = node('button', {class: 'tc-target', 'data-target-room': id, type: 'button'}, 'ย้ายมาห้อง ' + room.number);
            target.hidden = target.disabled = true;
            info.append(target);
            const track = node('div', {class: 'tc-track'});
            for (const barId of room.bars) track.append(bookingBar(barId));
            if (!room.bars.length) track.append(node('span', {class: 'tc-empty'}, 'ไม่มีการจองในช่วงนี้'));
            row.append(info, track);
            return row;
        }
        return {
            get data() { return state.data; },
            set data(value) { state.data = value; },
            selected: null, hoverRoom: null, dragging: false, pointerId: null,
            pointerX: 0, pointerY: 0, busy: false, stale: false, message: '', version: 0,
            now: 0, loadedAt: 0,
            init() {
                this.beforeRequest = (event) => {
                    if (this.busy && event.detail.elt !== this.$root) event.preventDefault();
                    else if (!this.busy) { state.restore = null; this.cancel(); }
                };
                this.$root.addEventListener('htmx:beforeRequest', this.beforeRequest);
            },
            destroy() {
                this.stopDrag();
                clearTimeout(state.expiry);
                if (state.windowFrame !== null) cancelAnimationFrame(state.windowFrame);
                state.scroll?.removeEventListener?.('scroll', state.onScroll);
                this.$root.removeEventListener('htmx:beforeRequest', this.beforeRequest);
            },
            loadPanel(panel) {
                this.stopDrag();
                if (state.windowFrame !== null) cancelAnimationFrame(state.windowFrame);
                state.windowFrame = null;
                state.scroll?.removeEventListener?.('scroll', state.onScroll);
                const script = panel.querySelector('#tc-move-data');
                state.data = script ? JSON.parse(script.textContent) : {rooms: {}, stays: {}};
                this.now = Date.parse(state.data.serverNow) || Date.now();
                this.loadedAt = performance.now();
                this.selected = null;
                this.paintHover(null);
                this.stale = false;
                state.buttons = [...panel.querySelectorAll('.tc-movable')];
                state.targets = [...panel.querySelectorAll('[data-target-room]')];
                state.rows = new Map([...panel.querySelectorAll('[data-room-id]')].map(row => [row.dataset.roomId, row]));
                state.scroll = panel.querySelector('.tc-scroll');
                state.grid = panel.querySelector('.tc-grid');
                state.top = panel.querySelector('[data-spacer="top"]');
                state.bottom = panel.querySelector('[data-spacer="bottom"]');
                state.range = null;
                if (this.isVirtual()) {
                    state.onScroll = () => this.queueWindow();
                    state.scroll.addEventListener('scroll', state.onScroll, {passive: true});
                    state.scroll.scrollTop = state.restore?.top || 0;
                    state.scroll.scrollLeft = state.restore?.left || 0;
                    this.renderWindow();
                    const focusId = state.restore?.bookingRoomId;
                    if (focusId) this.focusBooking(focusId);
                }
                state.restore = null;
                state.reasons = {};
                this.syncControls();
                this.scheduleExpiry();
                this.version++;
            },
            isVirtual() { return !!state.data.order && state.grid?.dataset?.virtualRows === 'true'; },
            windowRange(top, height, total, rowHeight = 144, headerHeight = 64, overscan = 3) {
                const first = Math.max(0, Math.min(total, Math.floor(top / rowHeight)));
                const last = Math.max(first, Math.min(total, Math.ceil((top + Math.max(0, height - headerHeight)) / rowHeight)));
                return {start: Math.max(0, first - overscan), end: Math.min(total, last + overscan)};
            },
            queueWindow() {
                if (!this.isVirtual() || state.windowFrame !== null) return;
                state.windowFrame = requestAnimationFrame(() => {
                    state.windowFrame = null;
                    this.renderWindow();
                });
            },
            renderWindow() {
                if (!this.isVirtual()) return;
                const order = state.data.order, viewport = state.data.viewport, scroll = state.scroll;
                const range = this.windowRange(scroll.scrollTop, scroll.clientHeight, order.length,
                    viewport.rowHeight, viewport.headerHeight, viewport.overscan);
                if (state.range?.start === range.start && state.range?.end === range.end) return;
                const ids = order.slice(range.start, range.end), keep = new Set(ids);
                const active = document.activeElement;
                for (const [id, row] of state.rows) if (!keep.has(id)) {
                    if (active && row.contains(active)) {
                        state.focusIndex = order.indexOf(id);
                        scroll.focus({preventScroll: true});
                    }
                    row.remove(); state.rows.delete(id);
                }
                let anchor = state.top.nextElementSibling;
                for (const id of ids) {
                    let row = state.rows.get(id);
                    if (!row) { row = roomRow(id); state.rows.set(id, row); }
                    if (row !== anchor) state.grid.insertBefore(row, anchor);
                    anchor = row.nextElementSibling;
                }
                state.top.style.height = range.start * viewport.rowHeight + 'px';
                state.bottom.style.height = (order.length - range.end) * viewport.rowHeight + 'px';
                state.range = range;
                state.buttons = [...state.grid.querySelectorAll('.tc-movable')];
                state.targets = [...state.grid.querySelectorAll('[data-target-room]')];
                this.syncControls();
                this.paintHover(this.hoverRoom);
            },
            focusRoom(index, last = false) {
                const order = state.data.order, viewport = state.data.viewport, scroll = state.scroll;
                index = Math.max(0, Math.min(order.length - 1, index));
                if (index < 0) return;
                const top = index * viewport.rowHeight, bottom = top + viewport.rowHeight;
                const height = Math.max(1, scroll.clientHeight - viewport.headerHeight);
                if (top < scroll.scrollTop) scroll.scrollTop = top;
                else if (bottom > scroll.scrollTop + height) scroll.scrollTop = bottom - height;
                this.renderWindow();
                const row = state.rows.get(order[index]);
                const controls = [...row.querySelectorAll('button:not([disabled]):not([hidden]), [tabindex="0"]')];
                const control = last ? controls.at(-1) || row : controls[0] || row;
                control.focus({preventScroll: true});
                if (control.matches('.tc-bar')) {
                    const rect = control.getBoundingClientRect(), bounds = scroll.getBoundingClientRect();
                    if (rect.left < bounds.left + 200) scroll.scrollLeft = Math.max(0, scroll.scrollLeft + rect.left - bounds.left - 200);
                    else if (rect.right > bounds.right) scroll.scrollLeft += rect.right - bounds.right;
                }
                state.focusIndex = index;
            },
            focusBooking(id) {
                if (!this.isVirtual()) return;
                const row = state.rows.get(state.data.stays[id]?.roomId);
                const bar = [...(row?.querySelectorAll('[data-booking-room-id]') || [])]
                    .find(element => element.dataset.bookingRoomId === id);
                (bar && !bar.disabled ? bar : state.scroll).focus({preventScroll: true});
            },
            handleKey(event) {
                if (!this.isVirtual() || this.busy || event.altKey || event.ctrlKey || event.metaKey || !state.scroll.contains(event.target)) return;
                const row = event.target.closest('[data-room-id]');
                const index = row ? state.data.order.indexOf(row.dataset.roomId) : state.focusIndex;
                const page = Math.max(1, Math.floor((state.scroll.clientHeight - state.data.viewport.headerHeight) / state.data.viewport.rowHeight));
                const offsets = {ArrowUp: index - 1, ArrowDown: index + 1, Home: 0,
                    End: state.data.order.length - 1, PageUp: index - page, PageDown: index + page};
                if (event.key in offsets) {
                    event.preventDefault(); this.focusRoom(offsets[event.key]);
                } else if (event.key === 'Tab' && row) {
                    const controls = [...row.querySelectorAll('button:not([disabled]):not([hidden]), [tabindex="0"]')];
                    const boundary = event.shiftKey ? controls[0] : controls.at(-1);
                    const next = index + (event.shiftKey ? -1 : 1);
                    if ((event.target === boundary || event.target === row) && next >= 0 && next < state.data.order.length) {
                        event.preventDefault(); this.focusRoom(next, !!event.shiftKey);
                    }
                }
            },
            clock() { return this.now + performance.now() - this.loadedAt; },
            canMove(id) {
                const stay = state.data.stays[id];
                return !this.stale && !!stay && stay.canMove && (!stay.expiresAt || Date.parse(stay.expiresAt) > this.clock());
            },
            scheduleExpiry() {
                clearTimeout(state.expiry);
                const next = Math.min(...Object.values(state.data.stays)
                    .filter(stay => stay.canMove && stay.expiresAt && Date.parse(stay.expiresAt) > this.clock())
                    .map(stay => Date.parse(stay.expiresAt)));
                if (!Number.isFinite(next)) return;
                state.expiry = setTimeout(() => {
                    if (this.selected && !this.canMove(this.selected)) {
                        this.stopDrag();
                        this.paintHover(null);
                        this.message = 'การจองนี้หมดเวลาชำระเงินแล้ว กรุณาโหลดตารางใหม่';
                    }
                    this.cacheDestinations();
                    this.syncControls();
                    this.scheduleExpiry();
                }, Math.min(2147483647, Math.max(1, next - this.clock())));
            },
            syncControls() {
                const now = this.clock(), disabled = this.busy || this.stale;
                for (const button of state.buttons) {
                    const stay = state.data.stays[button.dataset.bookingRoomId];
                    button.disabled = disabled || !stay?.canMove || (!!stay.expiresAt && Date.parse(stay.expiresAt) <= now);
                }
                this.syncTargets();
            },
            syncTargets() {
                for (const button of state.targets) {
                    button.hidden = !this.selected;
                    button.disabled = this.busy || !this.selected || !!state.reasons[button.dataset.targetRoom];
                    button.title = state.reasons[button.dataset.targetRoom] || '';
                }
            },
            cacheDestinations() {
                state.reasons = {};
                if (this.selected) for (const id of Object.keys(state.data.rooms)) state.reasons[id] = this.reason(id);
                this.syncTargets();
            },
            select(id) {
                if (this.busy || !this.canMove(id)) return;
                this.paintHover(null);
                this.selected = id;
                this.cacheDestinations();
                this.message = 'เลือกห้องปลายทาง หรือกด Escape เพื่อยกเลิก';
            },
            reason(roomId) {
                const stay = state.data.stays[this.selected];
                const room = state.data.rooms[roomId];
                if (!stay || !this.canMove(this.selected)) return 'การจองนี้ย้ายไม่ได้หรือหมดเวลาชำระเงินแล้ว';
                if (!room || !room.active) return 'ห้องปลายทางปิดใช้งานอยู่';
                if (room.typeId !== stay.typeId) return 'ต้องเลือกห้องประเภทเดียวกัน';
                if (roomId === stay.roomId) return 'การจองอยู่ในห้องนี้แล้ว';
                if (room.stays.some(other => other.id !== stay.id && other.start < stay.end && stay.start < other.end)) {
                    return 'ห้องปลายทางไม่ว่างตลอดช่วงเข้าพัก';
                }
                return '';
            },
            handlePointer(event) {
                const bar = event.target.closest('.tc-movable');
                if (bar && this.$root.contains(bar) && !bar.disabled) {
                    event.preventDefault();
                    bar.focus({preventScroll: true});
                    this.beginDrag(event, bar.dataset.bookingRoomId);
                }
            },
            handleClick(event) {
                const target = event.target.closest('[data-target-room]');
                if (target && this.$root.contains(target) && !target.disabled) return this.submitMove(target.dataset.targetRoom);
                const bar = event.target.closest('.tc-movable');
                if (event.detail === 0 && bar && this.$root.contains(bar) && !bar.disabled) this.select(bar.dataset.bookingRoomId);
            },
            beginDrag(event, id) {
                if (!event.isPrimary || event.button !== 0 || this.busy || this.dragging || !this.canMove(id)) return;
                this.select(id);
                this.pointerId = event.pointerId;
                this.pointerX = event.clientX;
                this.pointerY = event.clientY;
                this.dragging = true;
            },
            paintHover(id) {
                const previous = state.rows.get(this.hoverRoom);
                previous?.classList.remove('tc-drop-valid', 'tc-drop-invalid');
                this.hoverRoom = id;
                state.rows.get(id)?.classList.add(state.reasons[id] ? 'tc-drop-invalid' : 'tc-drop-valid');
            },
            hitRoom(x, y) {
                this.renderWindow();
                const row = document.elementFromPoint(x, y)?.closest('[data-room-id]');
                return row && this.$root.contains(row) ? row.dataset.roomId : null;
            },
            queueFrame() {
                if (state.frame !== null) return;
                state.frame = requestAnimationFrame(() => {
                    state.frame = null;
                    if (!this.dragging || !state.point) return;
                    const {x, y} = state.point;
                    this.pointerX = x;
                    this.pointerY = y;
                    const id = this.hitRoom(x, y);
                    if (id !== this.hoverRoom) {
                        this.paintHover(id);
                        this.message = id ? (state.reasons[id] || 'ปล่อยเพื่อย้ายไปห้อง ' + state.data.rooms[id].number) : 'ลากไปยังแถวห้องปลายทาง';
                    }
                    // Continue scrolling at the edge even when the pointer is stationary.
                    const scroll = state.scroll;
                    if (scroll) {
                        const rect = scroll.getBoundingClientRect();
                        if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
                            const before = scroll.scrollTop;
                            if (y > rect.bottom - 32) scroll.scrollTop += 16;
                            else if (y < rect.top + 32) scroll.scrollTop -= 16;
                            if (scroll.scrollTop !== before) { this.renderWindow(); this.queueFrame(); }
                        }
                    }
                });
            },
            dragOver(event) {
                if (!this.dragging || event.pointerId !== this.pointerId) return;
                state.point = {x: event.clientX, y: event.clientY};
                this.queueFrame();
            },
            stopDrag() {
                if (state.frame !== null) cancelAnimationFrame(state.frame);
                state.frame = null;
                state.point = null;
                this.dragging = false;
                this.pointerId = null;
            },
            drop(event) {
                if (!this.dragging || event.pointerId !== this.pointerId) return;
                // Release can arrive before the queued frame; use the final coordinates.
                const target = this.hitRoom(event.clientX, event.clientY);
                this.stopDrag();
                this.paintHover(null);
                if (target) return this.submitMove(target);
                this.message = 'ยังไม่ได้ย้ายห้อง กรุณาเลือกห้องปลายทาง';
            },
            cancel() {
                if (this.busy) return;
                this.stopDrag();
                this.selected = null;
                this.paintHover(null);
                state.reasons = {};
                this.syncTargets();
                this.message = '';
            },
            async refresh() {
                if (this.isVirtual()) state.restore = {
                    top: state.scroll.scrollTop, left: state.scroll.scrollLeft, bookingRoomId: this.selected,
                };
                const version = this.version;
                await htmx.ajax('GET', window.location.pathname + window.location.search, {
                    source: this.$root, target: '#tc-panel', select: '#tc-panel', swap: 'outerHTML',
                });
                await this.$nextTick();
                if (this.version === version) throw new Error('Chart was not refreshed');
            },
            async submitMove(roomId) {
                if (this.busy) return;
                const reason = this.reason(roomId);
                if (reason) { this.message = reason; return; }
                const stay = state.data.stays[this.selected];
                const csrf = this.$root.querySelector('[name=csrfmiddlewaretoken]').value;
                this.busy = true;
                this.stopDrag();
                this.syncControls();
                this.message = 'กำลังย้ายห้อง…';
                let resultMessage;
                const controller = new AbortController();
                const timeout = setTimeout(() => controller.abort(), 15000);
                try {
                    const response = await fetch(stay.url, {
                        method: 'POST', credentials: 'same-origin', signal: controller.signal,
                        headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
                        body: new URLSearchParams({target_room_id: roomId}),
                    });
                    if (response.redirected) resultMessage = 'กรุณาเข้าสู่ระบบใหม่ แล้วโหลดตารางอีกครั้ง';
                    else if (response.status === 403) resultMessage = 'ไม่มีสิทธิ์ย้ายห้อง หรือเซสชันหมดอายุ กรุณาโหลดหน้าใหม่';
                    else if (response.status === 404) resultMessage = 'ไม่พบการจองหรือห้องที่เข้าถึงได้ กรุณาโหลดตารางใหม่';
                    else if (response.ok || response.status === 400) {
                        const result = await response.json();
                        resultMessage = response.ok ? result.message : Object.values(result.errors || {}).flat().join('\n');
                    } else resultMessage = 'ไม่สามารถย้ายห้องได้ กรุณาลองใหม่';
                } catch (_) {
                    resultMessage = 'ไม่สามารถยืนยันผลการย้ายห้องได้ กรุณาตรวจตำแหน่งในตารางล่าสุดก่อนลองใหม่';
                } finally {
                    clearTimeout(timeout);
                }
                // Keep the original bar until a fresh server render; no optimistic move to undo.
                try {
                    await this.refresh();
                } catch (_) {
                    this.stale = true;
                    resultMessage += '\nโหลดตารางล่าสุดไม่สำเร็จ กรุณาโหลดหน้าใหม่ก่อนย้ายต่อ';
                } finally {
                    this.busy = false;
                    this.selected = null;
                    this.paintHover(null);
                    this.syncControls();
                    this.focusBooking(stay.id);
                    this.message = resultMessage || 'กรุณาโหลดตารางใหม่';
                }
            },
        };
    }
    document.addEventListener('alpine:init', () => Alpine.data('tapeChart', tapeChart));
})();
