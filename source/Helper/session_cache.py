"""String-session credentials with bounded, replace-by-ID Telegram metadata."""
from collections import OrderedDict

from telethon import utils
from telethon.sessions import StringSession
from telethon.tl.types import PeerUser, PeerChat, PeerChannel


class BoundedStringSession(StringSession):
    """Keep StringSession's wire format, but not MemorySession's growing set.

    Telethon preprocesses entities even while our message filter is paused.
    Public peer lookups still work; evicted peers can be resolved by Telegram.
    """
    def __init__(self, string=None, *, entity_limit=4096):
        super().__init__(string)
        if entity_limit < 1:
            raise ValueError("Entity limit must be positive")
        self.entity_limit = entity_limit
        self._entity_rows = OrderedDict()

    def process_entities(self, tlo):
        for row in self._entities_to_rows(tlo):
            self._entity_rows[row[0]] = row
            self._entity_rows.move_to_end(row[0])
            if len(self._entity_rows) > self.entity_limit:
                self._entity_rows.popitem(last=False)

    def _find(self, column, value):
        for identifier, row in reversed(self._entity_rows.items()):
            if row[column] == value:
                self._entity_rows.move_to_end(identifier)
                return row[:2]

    def get_entity_rows_by_phone(self, phone):
        return self._find(3, phone)

    def get_entity_rows_by_username(self, username):
        return self._find(2, username)

    def get_entity_rows_by_name(self, name):
        return self._find(4, name)

    def get_entity_rows_by_id(self, identifier, exact=True):
        identifiers = [identifier] if exact else [utils.get_peer_id(peer(identifier))
                                                for peer in (PeerUser, PeerChat, PeerChannel)]
        for key in identifiers:
            row = self._entity_rows.get(key)
            if row is not None:
                self._entity_rows.move_to_end(key)
                return row[:2]
