from enum import Enum


class EOrderSide(str, Enum):
    # Note: a trailing comma makes the value a 1-tuple ("buy",) rather
    # than the bare string "buy", which then breaks valueOf() lookups
    # because they compare against the literal venue string.
    BUY = "buy"
    SELL = "sell"

    @classmethod
    def valueOf(cls, value):
        for k, v in cls.__members__.items():
            if v == value:
                return v
        else:
            raise ValueError(f"'{cls.__name__}' enum not found for '{value}'")

    def __str__(self):
        return self.value


class EOrderStatus(str, Enum):
    # Note: a trailing comma makes the value a 1-tuple ("closed",) rather
    # than the bare string "closed", which then breaks valueOf() lookups.
    CLOSED = "closed"
    PARTIALLY_FILLED = "partially-filled"
    FILLED = "filled"
    REJECTED = "rejected"
    OPEN = "open"
    CANCELED = "canceled"
    REDUCE_ONLY_CANCELED = "reduceOnlyCanceled"

    @classmethod
    def valueOf(cls, value):
        for k, v in cls.__members__.items():
            if v == value:
                return v
        else:
            raise ValueError(f"'{cls.__name__}' enum not found for '{value}'")

    def __str__(self):
        return self.value

class EOrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"

    @classmethod
    def valueOf(cls, value):
        for k, v in cls.__members__.items():
            if v == value:
                return v
        else:
            raise ValueError(f"'{cls.__name__}' enum not found for '{value}'")

    def __str__(self):
        return self.value
