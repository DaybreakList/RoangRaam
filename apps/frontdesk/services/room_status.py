"""
Housekeeping room status board (TASK-46).

Rooms become Dirty when their guests check out (services.check_in_out.check_out).
Housekeeping marks them Clean again here; there is no manual Clean -> Dirty.
"""

from dataclasses import dataclass

from django.db import transaction
from django.shortcuts import get_object_or_404

from apps.rooms.models import Room

DIRTY = Room.HousekeepingStatus.DIRTY
CLEAN = Room.HousekeepingStatus.CLEAN


@dataclass(frozen=True)
class RoomStatusBoard:
    rooms: list
    dirty_count: int
    clean_count: int


def room_status_board(property, *, floor=None, status=None):
    """
    Active rooms of `property`, Dirty first, then by floor and number. One query.
    The counts are of the rooms listed, after the filters.
    """
    rooms = Room.scoped.for_property(property).filter(is_active=True).select_related('room_type')
    if floor is not None:
        rooms = rooms.filter(floor=floor)
    if status:
        rooms = rooms.filter(hk_status=status)
    # 'DIRTY' sorts after 'CLEAN', so descending puts the rooms to clean first
    rooms = list(rooms.order_by('-hk_status', 'floor', 'number', 'pk'))
    dirty_count = sum(1 for room in rooms if room.hk_status == DIRTY)
    return RoomStatusBoard(rooms=rooms, dirty_count=dirty_count, clean_count=len(rooms) - dirty_count)


def mark_room_clean(property, room_id):
    """
    Mark a room of `property` Clean. Returns (room, changed); a room that is
    already Clean is left as it is, so a double tap is harmless. A room of
    another property is 404.
    """
    with transaction.atomic():
        room = get_object_or_404(Room.scoped.for_property(property).select_for_update(), pk=room_id)
        if room.hk_status == CLEAN:
            return room, False
        room.hk_status = CLEAN
        # Saved on the instance: RoomDataQuerySet refuses update() to keep validation
        room.save(update_fields=['hk_status'])
    return room, True
