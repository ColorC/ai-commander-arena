"""Every number of the exhibition rules (materials/10 section 2.x) in one place.

Change a value here only, and record the change in section 8 of materials/10. Names follow the rule text:
wake (2.6), clock (2.5), sandbox limits (2.8), prematch (2.9), faults (2.11). The attention energy of 2.6 is gone
since 2026-11-04 (referee/thinking.py only counts thinking time).
"""

# --- 2.6 wake ---
WAKE_MERGE_SEC = 30        # events inside this many game seconds of the first one share a wake
COMMANDER_TURN_SEC = 8 * 60      # a backstop for a stuck harness; no season 2 turn came near it
SUBAGENT_TASK_SEC = 10 * 60
MAX_SUBAGENTS = 2
SCRIPT_ALARM_GAP_SEC = 5 * 60
TIMER_MIN_MINUTES = 10
NEW_CONTACT_MERGE_SEC = 10 * 60   # default subscription: later new contacts inside this window do not wake again
CONTACT_REGAIN_SEC = 10 * 60      # a contact lost this long and seen again counts as new
DEFAULT_DROP_POINTS = 5
SUMMARY_MAX_TOKENS = 6000
CHARS_PER_TOKEN = 3        # JSON text, pessimistic

DEFAULT_SUBSCRIPTIONS = [
    {"type": "vp_change"},
    {"type": "unit_out"},
    {"type": "strength_drop", "points": DEFAULT_DROP_POINTS},
    {"type": "new_contact", "merge_sec": NEW_CONTACT_MERGE_SEC},
    {"type": "timer", "every_min": 60},
    {"type": "script_alarm"},
]
SUB_TYPES = {"orders_phase", "new_contact", "combat", "unit_out", "strength_drop", "vp_change", "order_done",
             "script_alarm", "timer", "contact_near"}
MAX_SUBSCRIPTIONS = 24

# --- 2.11 faults ---
ILLEGAL_STREAK = 3         # consecutive illegal commands -> violation, later illegal ones are dropped

# --- 2.8 scripts and compute ---
MAX_SCRIPTS = 32         # our own cap (materials 10 §2.8), not the game's; the real limit is SCRIPT_MINUTE_INSTR
SCRIPT_CALL_INSTR = 2_000_000
SCRIPT_CALL_MS = 200
SCRIPT_MINUTE_INSTR = 10_000_000   # per side per game minute
SCRIPT_MEM_MB = 64
SCRIPT_PERSIST_BYTES = 1 << 20
SCRIPT_SOURCE_BYTES = 64 * 1024
SCRIPT_STRIKES = 3
COMPUTE_JOBS = 60
COMPUTE_CPU_S = 10
COMPUTE_MEM_MB = 256
PREMATCH_COMPUTE_CPU_S = 2 * 3600

# --- 2.9 prematch ---
PREMATCH_REAL_SEC = 120 * 60
PREMATCH_PHASES = ("Pregame", "Setup")

# --- facility ---
# the relay syncs every 0.3 s whatever the game does; silent this long after its first sync = the relay is gone
# (P4 training p1-7 10-31: the relay died at 09:45 and the exercise waited out its 90-minute cap)
RELAY_SILENT_SEC = 180

# --- 2.10 exercises, P4 training ---
TURN_ORDERS_SEC = 8 * 60          # turn pacing: an own Orders phase waits this long at most for the commander (a backstop the players are not told)
EXERCISE_PREMATCH_SEC = 10 * 60   # deployment time of an exercise (the director passes it as --prematch-sec)
EXERCISE_SCENARIO = "eyes-ears-and-teeth"   # exercises use the match scenario only (materials/10 section 8; season 2: materials/19 section 4, re-chosen 11-03)
EXERCISE_FULL_MAX = 4             # full exercises per player
EXERCISE_FULL_WALL_MIN = 120      # real minutes per full exercise, then it ends as it stands (The Armored Fist 11-01: 71 and 74)
EXERCISE_SMALL_UNITS = 10         # own units a small experiment may use; the others hold
EXERCISE_SMALL_GAME_MIN = 60      # game minutes per small experiment
EXERCISE_SMALL_WALL_SEC = 3 * 3600   # real seconds of all small experiments of a player together
EXERCISE_SMALL_MIN_LEFT_SEC = 15 * 60   # less small-experiment time left than this: no new one (load + deployment)
TRAINING_TURN_SEC = 20 * 60       # one training turn (wake) of a player
TRAINING_REAL_SEC = 12 * 3600     # the training period; the director stops it, the referee only records it

# --- queue / release (referee-internal choices) ---
MAX_QUEUE = 200
MAX_ORDERS_PER_CMD = 8
RELEASE_RETRY_SEC = 10     # resend an unanswered release batch (same batch_id) after this many real seconds
F9_RETRY_SEC = 15         # while the game still reports the same Orders/setup phase, F9 again after this long
F9_MAX = 8                # (the relay skips an F9 unless the game's banner shows it waiting); then log it once
ORDERS_SIDE_GRACE_SEC = 3  # Orders phase without a known side: let the game continue after this long
NOTE_MAX_BYTES = 256 * 1024
NOTES_TOTAL_BYTES = 8 * 1024 * 1024
HEX_QUERY_MAX = 400

# order names seen in recorded games (rec.jsonl of Alamo at Soest) and the manual, plus the ones the game-machine probe of 10-31
# read back as themselves (Counter Battery; Hunt for helicopters). SetUnitOrders returns true for a name it does not know
# and turns the order into Screen (zzz, Airstrike, Disembark, Embark On, Improve Positions...), so only names read back
# unchanged go here. Air Strike, Land On, Improve Position, Lay/Lift Mines, the obstacle and bridge orders and Unload
# were refused as written; extend here when a probe shows how they are written.
ORDER_TYPES = {"Hold", "Screen", "Assault", "Move Deliberate", "Move Hasty", "On Call", "Withdraw", "Scoot",
               "Barrage", "Resupply", "Reorganize", "Counter Battery", "Hunt"}
ORDER_FIELDS = {"orders", "destination_location", "target_location", "target_unit", "target_contact", "start_time", "end_time",
                "barrage_area_fire", "barrage_duration_mins", "barrage_rounds", "barrage_timing", "barrage_type",
                "reorg_duration", "reorg_readiness_target_pct", "reorg_resupply_target_pct"}
# Barrage as the game takes it (game machine probe 10-31, 4 guns of both sides): an aim point (target_location, or a contact
# that becomes target_unit) and these three fields, rounds and minutes at least 1. barrage_type HE or ICM (any case);
# Smoke, DPICM, FASCAM, Illumination and others refused (smoke goes as an obscuration fire request). barrage_timing:
# the program knows asap and fixed; every other value, and barrage_first_round_time / start_time, read back as asap
# from the current minute, and fixed is untested, so only asap is open. barrage_area_fire is read only as the string.
BARRAGE_NEEDS = ("barrage_type", "barrage_rounds", "barrage_duration_mins")
BARRAGE_TYPES = ("HE", "ICM")
BARRAGE_TIMINGS = ("asap",)
ORDERS_NEED_DESTINATION = {"Hunt"}        # Hunt without destination_location is refused
# IssueFireSupportRequest keys, found in the game's program strings (arena/DESIGN.md 4.1). An agent names an enemy target by
# `contact` (number); the referee turns it into `enemy_unit` only when the batch is made and the contact is in view.
FIRE_SUPPORT_TYPES = {"static_target", "enemy_target", "obscuration", "cb_scan", "direct_support", "sead", "minefield"}
# target_type values the game takes (game machine probe 10-31, lower case only; the game logs them as Soft Target, Hard
# Target, Enemy HQ, Obscuration, Illumination). static_target without one is refused. projectile_type, priority,
# requester_unit, spotter_unit, duration_mins and delay are not checked by the game at all (any value returns true).
FIRE_SUPPORT_TARGET_TYPES = ("soft", "hard", "hq", "obscuration", "illumination")
FIRE_SUPPORT_NO_LOCATION = {"enemy_target", "direct_support"}
FIRE_SUPPORT_UNITS = ("requester_unit", "spotter_unit")      # own units on the map
FIRE_SUPPORT_STRINGS = ("target_type", "projectile_type", "priority")
FIRE_SUPPORT_INTS = ("duration_mins", "delay")
FIRE_SUPPORT_FIELDS = {"request_type", "location", "contact"} | set(FIRE_SUPPORT_UNITS) | set(FIRE_SUPPORT_STRINGS) \
    | set(FIRE_SUPPORT_INTS)
COMMAND_KINDS = ("orders", "doctrine", "emission", "fire_support", "deploy")

# --- season 2 (materials/18 2.1): lint and fire reports ---
# Blind-fire warning: an aim point with no contact of the side in view within this many hexes is a blind shot. A hex is
# 500 m (FM01 23.2); a contact's position is refreshed once a game minute and a vehicle moving hasty covers about a hex
# in that minute; observed fire may shift to the hex its target moved into (FM01 25.4.2). So a contact on the aim hex,
# its ring or the next ring (up to 1 km) still counts as observed; farther out the game usually drops the barrage
# (P5 game 1: three battalions ordered onto VP cells with nothing in view fired nothing, materials/17 3.2).
FIRE_NEAR_HEXES = 2
# a Barrage released for a unit that has not fired this many game seconds later: one fire_pending reminder (the order
# delay is 2 to 60 minutes, average 5 to 10, FM01 21.7; 15 minutes is past the usual delay)
FIRE_PENDING_SEC = 15 * 60
# the text of a command_changed event: an order the game took but read back without one of the sent orders
ORDERS_CHANGED_WHY = ("游戏收下了这条命令，但读回的命令里没有被去掉的那几条。常见原因：Rest and Resupply 只在不交战时进行"
                      "（FM01 21.7），交战中的 Resupply 会被跳过；部队压力过大时 Assault、Move、Resupply 变 Screen，"
                      "Hold、Screen 变 Scoot（FM01 21.8）；目的地就是所在格的 Move 也会被省掉。unit show 看单位现在的命令。")
# unit icons (GetUnitStatus icon_type) of indirect-fire units, matched as lower-case parts of the icon: Alamo at Soest
# has SPArty (howitzers, its mortar platoon too) and Arty (towed mortars); the mortar / rocket words cover the
# FM02 7.2.10 / 7.3.9 / 7.3.10 types of other scenarios (unverified icon spellings). A unit given a Barrage or Counter
# Battery order counts as indirect-fire whatever its icon.
ARTILLERY_ICON_WORDS = ("arty", "mtr", "mortar", "rkt", "rocket", "mrl")
INDIRECT_ORDERS = {"Barrage", "Counter Battery"}
HELO_ORDERS = {"Hunt"}                    # helicopter orders (Helo icon)
# kinds that may be released in the side's Orders phase; deploy only in the prematch phases
GAME_STATUS_ACTIVE = "Active"
DEAD_STATUS = "Dead"
SUB_OK = "Runner"
AIR_ICONS = {"Helo", "Air"}      # icon_type of units that cannot take a VP cell
