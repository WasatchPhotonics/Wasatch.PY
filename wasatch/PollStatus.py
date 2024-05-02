from enum import IntEnum

class PollStatus(IntEnum):
    """ Response to XS opcode 0xd4 GET_POLL_DATA """

    IDLE                = 0
    DARK_MEASUREMENT    = 1
    LASER_WARMUP        = 2
    SAMPLE_MEASUREMENT  = 3
    PROCESSING          = 4
    STABILIZING         = 5
    DATA_READY          = 6
    # ...
    ERROR               = 254
    UNDEFINED           = 255
