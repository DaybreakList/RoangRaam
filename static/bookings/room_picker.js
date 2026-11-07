// Quantity steppers and the running summary on the guest search page.
// The total here is display only: the server prices and validates the booking.
document.addEventListener('alpine:init', () => {
  Alpine.data('roomPicker', (maxRooms) => ({
    rooms: {},
    quantities: {},

    init() {
      this.$el.querySelectorAll('[data-room-qty]').forEach((input) => {
        const id = input.dataset.roomQty;
        this.rooms[id] = {
          name: input.dataset.roomName,
          satang: Math.round(Number(input.dataset.roomTotal) * 100),
          max: Number(input.max),
        };
        this.quantities[id] = 0;
      });
    },

    limitFor(id) {
      const others = this.count - (this.quantities[id] || 0);
      return Math.min(this.rooms[id].max, maxRooms - others);
    },

    set(id, value) {
      const wanted = Math.trunc(Number(value)) || 0;
      this.quantities[id] = Math.max(0, Math.min(wanted, this.limitFor(id)));
    },

    step(id, delta) {
      this.set(id, (this.quantities[id] || 0) + delta);
    },

    atMax(id) {
      return Boolean(this.rooms[id]) && (this.quantities[id] || 0) >= this.limitFor(id);
    },

    get count() {
      return Object.values(this.quantities).reduce((sum, qty) => sum + (Number(qty) || 0), 0);
    },

    get summary() {
      if (this.count === 0) {
        return 'ยังไม่ได้เลือกห้อง';
      }
      const names = [];
      let satang = 0;
      Object.keys(this.rooms).forEach((id) => {
        const qty = Number(this.quantities[id]) || 0;
        if (qty > 0) {
          names.push(qty > 1 ? `${this.rooms[id].name} × ${qty}` : this.rooms[id].name);
          satang += qty * this.rooms[id].satang;
        }
      });
      const total = (satang / 100).toLocaleString('en-US', { minimumFractionDigits: 2 });
      return `เลือกแล้ว ${this.count} ห้อง · ${names.join(', ')} · ฿${total}`;
    },
  }));
});
