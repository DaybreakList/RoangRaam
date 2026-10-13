from django.contrib import admin

from .models import Booking, BookingNightRate, BookingRoom, Guest


@admin.register(Guest)
class GuestAdmin(admin.ModelAdmin):
    list_display = ['first_name', 'last_name', 'email', 'phone', 'property']
    list_filter = ['property']
    list_select_related = ['property']
    search_fields = ['first_name', 'last_name', 'email', 'phone']


class BookingRoomInline(admin.TabularInline):
    model = BookingRoom
    extra = 0
    fields = ['room_type', 'room', 'stay_range', 'is_active', 'subtotal']
    readonly_fields = fields
    can_delete = False


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['code', 'guest', 'property', 'status', 'check_in_date', 'check_out_date', 'total_amount']
    list_filter = ['property', 'status']
    list_select_related = ['guest', 'property']
    search_fields = ['code', 'guest__first_name', 'guest__last_name', 'guest__email']
    readonly_fields = ['code', 'created_at', 'cancelled_at']
    inlines = [BookingRoomInline]


@admin.register(BookingNightRate)
class BookingNightRateAdmin(admin.ModelAdmin):
    list_display = ['booking_room', 'night', 'price', 'property']
    list_filter = ['property']
    list_select_related = ['booking_room__booking', 'booking_room__room', 'property']
