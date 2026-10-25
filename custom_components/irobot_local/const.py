"""Constants for the iRobot Local integration."""

DOMAIN = "irobot_local"

CONF_BLID = "blid"
CONF_PMAP_ID = "pmap_id"
CONF_USER_PMAPV_ID = "user_pmapv_id"
CONF_ROOMS = "rooms"

STATE_WAIT_TIMEOUT = 10

# cleanMissionStatus.error codes, after roombapy's ERROR_MESSAGES.
ERROR_MESSAGES = {
    0: "None",
    1: "Left wheel off floor",
    2: "Main brushes stuck",
    3: "Right wheel off floor",
    4: "Left wheel stuck",
    5: "Right wheel stuck",
    6: "Stuck near a cliff",
    7: "Left wheel error",
    8: "Bin error",
    9: "Bumper stuck",
    10: "Right wheel error",
    11: "Bin error",
    12: "Cliff sensor issue",
    13: "Both wheels off floor",
    14: "Bin missing",
    15: "Reboot required",
    16: "Bumped unexpectedly",
    17: "Path blocked",
    18: "Docking issue",
    19: "Undocking issue",
    20: "Docking issue",
    21: "Navigation problem",
    22: "Navigation problem",
    23: "Battery issue",
    24: "Navigation problem",
    25: "Reboot required",
    26: "Vacuum problem",
    27: "Vacuum problem",
    29: "Software update needed",
    30: "Vacuum problem",
    31: "Reboot required",
    32: "Smart map problem",
    33: "Path blocked",
    34: "Reboot required",
    35: "Unrecognised cleaning pad",
    36: "Bin full",
    37: "Tank needed refilling",
    38: "Vacuum problem",
    39: "Reboot required",
    40: "Navigation problem",
    41: "Timed out",
    42: "Localization problem",
    43: "Navigation problem",
    44: "Pump issue",
    45: "Lid open",
    46: "Low battery",
    47: "Reboot required",
    48: "Path blocked",
    52: "Pad required attention",
    65: "Hardware problem detected",
    66: "Low memory",
    68: "Hardware problem detected",
    73: "Pad type changed",
    74: "Max area reached",
    75: "Navigation problem",
    76: "Hardware problem detected",
}


def signal_update(blid: str) -> str:
    """Dispatcher signal fired when the robot state changes."""
    return f"{DOMAIN}_update_{blid}"
