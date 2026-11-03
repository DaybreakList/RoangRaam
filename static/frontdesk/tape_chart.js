/* The server is authoritative. This component only previews allowed destinations. */
(function () {
    function tapeChart() {
        return {
            data: {rooms: {}, stays: {}}, selected: null, hoverRoom: null,
            dragging: false, pointerId: null, pointerX: 0, pointerY: 0,
            busy: false, stale: false, message: '', version: 0, now: 0,
            init() {
                this.timer = setInterval(() => { this.now = this.serverTime + performance.now() - this.loadedAt; }, 1000);
                this.beforeRequest = (event) => {
                    if (this.busy && event.detail.elt !== this.$root) event.preventDefault();
                    else if (!this.busy) this.cancel();
                };
                this.$root.addEventListener('htmx:beforeRequest', this.beforeRequest);
            },
            destroy() {
                clearInterval(this.timer);
                this.$root.removeEventListener('htmx:beforeRequest', this.beforeRequest);
            },
            loadPanel(panel) {
                const script = panel.querySelector('#tc-move-data');
                this.data = script ? JSON.parse(script.textContent) : {rooms: {}, stays: {}};
                this.serverTime = Date.parse(this.data.serverNow) || Date.now();
                this.loadedAt = performance.now();
                this.now = this.serverTime;
                this.selected = null;
                this.hoverRoom = null;
                this.dragging = false;
                this.pointerId = null;
                this.stale = false;
                this.version++;
            },
            canMove(id) {
                const stay = this.data.stays[id];
                return !this.stale && !!stay && stay.canMove && (!stay.expiresAt || Date.parse(stay.expiresAt) > this.now);
            },
            select(id) {
                if (this.busy || !this.canMove(id)) return;
                this.selected = id;
                this.message = 'เลือกห้องปลายทาง หรือกด Escape เพื่อยกเลิก';
            },
            reason(roomId) {
                const stay = this.data.stays[this.selected];
                const room = this.data.rooms[roomId];
                if (!stay || !this.canMove(this.selected)) return 'การจองนี้ย้ายไม่ได้หรือหมดเวลาชำระเงินแล้ว';
                if (!room || !room.active) return 'ห้องปลายทางปิดใช้งานอยู่';
                if (room.typeId !== stay.typeId) return 'ต้องเลือกห้องประเภทเดียวกัน';
                if (roomId === stay.roomId) return 'การจองอยู่ในห้องนี้แล้ว';
                if (room.stays.some(other => other.id !== stay.id && other.start < stay.end && stay.start < other.end)) {
                    return 'ห้องปลายทางไม่ว่างตลอดช่วงเข้าพัก';
                }
                return '';
            },
            beginDrag(event, id) {
                if (!event.isPrimary || event.button !== 0 || this.busy || this.dragging || !this.canMove(id)) return;
                this.select(id);
                this.pointerId = event.pointerId;
                this.pointerX = event.clientX;
                this.pointerY = event.clientY;
                this.dragging = true;
                // Do not capture: elementFromPoint must find the destination row.
            },
            dragOver(event) {
                if (!this.dragging || event.pointerId !== this.pointerId) return;
                this.pointerX = event.clientX;
                this.pointerY = event.clientY;
                const row = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-room-id]');
                this.hoverRoom = row && this.$root.contains(row) ? row.dataset.roomId : null;
                this.message = this.hoverRoom ? (this.reason(this.hoverRoom) || 'ปล่อยเพื่อย้ายไปห้อง ' + this.data.rooms[this.hoverRoom].number) : 'ลากไปยังแถวห้องปลายทาง';
                const scroll = this.$root.querySelector('.tc-scroll');
                if (scroll) {
                    const rect = scroll.getBoundingClientRect();
                    if (event.clientY > rect.bottom - 32) scroll.scrollTop += 16;
                    else if (event.clientY < rect.top + 32) scroll.scrollTop -= 16;
                }
            },
            drop(event) {
                if (!this.dragging || event.pointerId !== this.pointerId) return;
                this.dragOver(event);
                this.dragging = false;
                this.pointerId = null;
                const target = this.hoverRoom;
                this.hoverRoom = null;
                if (target) return this.submitMove(target);
                this.message = 'ยังไม่ได้ย้ายห้อง กรุณาเลือกห้องปลายทาง';
            },
            cancel() {
                if (this.busy) return;
                this.selected = null;
                this.hoverRoom = null;
                this.dragging = false;
                this.pointerId = null;
                this.message = '';
            },
            async refresh() {
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
                const stay = this.data.stays[this.selected];
                const csrf = this.$root.querySelector('[name=csrfmiddlewaretoken]').value;
                this.busy = true;
                this.dragging = false;
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
                    this.hoverRoom = null;
                    this.message = resultMessage || 'กรุณาโหลดตารางใหม่';
                }
            },
        };
    }
    document.addEventListener('alpine:init', () => Alpine.data('tapeChart', tapeChart));
})();
