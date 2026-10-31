from django import template

from apps.dashboard.views import HotelDashboardView
from apps.frontdesk.daily_ops_views import DailyOpsView
from apps.frontdesk.room_status_views import RoomStatusView
from apps.frontdesk.tape_chart_views import TapeChartView

register = template.Library()

# Navbar page name -> view class. Each view's `allowed_roles` is the only source of
# truth for who sees the menu item; add one line here to expose another page.
NAV_PAGES = {
    'daily_ops': DailyOpsView,
    'tape_chart': TapeChartView,
    'room_status': RoomStatusView,
    'dashboard': HotelDashboardView,
}


@register.filter
def can_view_page(membership, page):
    """True if `membership` (request.membership, may be None) may open the named page."""
    view_class = NAV_PAGES.get(page)
    if membership is None or view_class is None:
        return False
    return membership.role in view_class.allowed_roles
