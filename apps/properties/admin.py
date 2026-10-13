from django.contrib import admin

from .models import Membership, Property


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ['user']


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'email', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name']
    inlines = [MembershipInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ['user', 'property', 'role']
    list_filter = ['property', 'role']
    list_select_related = ['user', 'property']
    search_fields = ['user__username', 'property__name']
