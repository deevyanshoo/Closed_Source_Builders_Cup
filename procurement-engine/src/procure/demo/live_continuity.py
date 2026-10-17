"""Small rolling usage budgets for a live socket. Times are monotonic seconds."""
from collections import deque
from hashlib import sha256
import hmac


def call_key(socket_secret: bytes, call_id: str) -> str:
    """Stable only inside one accepted browser socket, even across providers."""
    return 'live-' + hmac.new(socket_secret, call_id.encode('utf-8'), sha256).hexdigest()[:48]


class RollingBudget:
    def __init__(self, limit: int, seconds: float, clock):
        self.limit = limit
        self.seconds = seconds
        self.clock = clock
        self.events = deque()
        self.total = 0

    def allow(self, amount: int = 1) -> bool:
        now = self.clock()
        while self.events and self.events[0][0] <= now - self.seconds:
            _, expired = self.events.popleft()
            self.total -= expired
        if amount < 0 or self.total + amount > self.limit:
            return False
        self.events.append((now, amount))
        self.total += amount
        return True


class ReplayGuard:
    """Bounded, fail-closed memory of tool IDs after cached results retire.

    False positives can deny an old-looking call; they cannot re-execute one.
    """
    def __init__(self):
        self.bits = bytearray(8192)

    def _positions(self, call_id: str):
        digest = sha256(call_id.encode('utf-8')).digest()
        for offset in (0, 4, 8, 12):
            yield int.from_bytes(digest[offset:offset + 4], 'big') % (len(self.bits) * 8)

    def remember(self, call_id: str) -> None:
        for position in self._positions(call_id):
            self.bits[position // 8] |= 1 << (position % 8)

    def seen(self, call_id: str) -> bool:
        return all(self.bits[position // 8] & (1 << (position % 8))
                   for position in self._positions(call_id))
