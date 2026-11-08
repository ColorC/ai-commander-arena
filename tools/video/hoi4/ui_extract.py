"""HOI4 HUD skin asset extractor (fan / personal use, user-authorised).

按下方 ENTRIES 清单把 HOI4 原始 DDS/TGA 贴图用 ffmpeg 转成 PNG，并写 manifest.json。
只读素材根目录（视为不可信数据，不在里面写文件、不执行任何东西）；可重跑，输出覆盖。

用法（务必隔离模式）:
    python -I -X utf8 tools/video/hoi4/ui_extract.py \
        --root $ARENA_DATA/video/hoi4-assets/x \
        --out  $ARENA_DATA/video/hoi4-assets/ui \
        [--ffmpeg ffmpeg]

依赖: Python 标准库 + ffmpeg（DXT1/DXT5/BGRA DDS 与 TGA 均可解）。
不依赖 numpy / Pillow。PNG 尺寸直接读 IHDR。
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 清单: (sub, role, src 相对 root, sprite 名, frames, border(x,y)|None, note)
# frames = .gfx 的 noOfFrames（横向等宽条带）；border = corneredTileSpriteType 的 borderSize
# ---------------------------------------------------------------------------
E = []


def add(sub, role, src, sprite, frames=1, border=None, note=""):
    E.append(dict(sub=sub, role=role, src=src, sprite=sprite, frames=frames,
                  border=border, note=note))


# 1. 顶栏 ------------------------------------------------------------------
add("topbar", "topbar_background", "gfx/interface/topbar/background_extended.dds",
    "GFX_topbar_background_extended", note="主界面顶栏整条底图（topbar.gui 实际使用的版本，左端含国旗/资源区，右端含日期区）。按 UPPER_LEFT 放 x=-4,y=-1，宽超屏时右侧裁掉")
add("topbar", "topbar_background_plain", "gfx/interface/topbar/background.dds",
    "GFX_topbar_background", note="顶栏底图的非 extended 版本，同尺寸，备用")
add("topbar", "topbar_date_overlay", "gfx/interface/topbar/topbar_date_overlay.dds",
    "GFX_topbar_date_overlay", note="日期区装饰压层（topbar.gui 里已注释掉，原位 x=-357 右对齐），可选")
add("topbar", "time_control_bg", "gfx/interface/date_pause_button_bg.dds",
    "GFX_date_pause_button_bg", note="日期/暂停按钮底框（time_control_window 内 x=-197 右对齐），DateText 就写在它上面")
add("topbar", "time_control_paused", "gfx/interface/date_pause_button.dds",
    "GFX_date_pause_button", frames=2, note="暂停态日期框，2 帧 1fps 交替闪烁（frameAnimatedSpriteType, blendframes）")
add("topbar", "speed_step", "gfx/interface/topbar/speed_step.dds",
    "GFX_speed_step", frames=3, note="速度档位小格，5 个排在 x=94/119/144/169/194,y=27；3 帧=未激活/激活/悬停（buttonstate）")
add("topbar", "speed_up", "gfx/interface/topbar/zoom_in.dds",
    "GFX_zoom_in", note="加速按钮（+），time_control_window x=233,y=1；.gfx 写 .tga，实际文件为同名 .dds")
add("topbar", "speed_down", "gfx/interface/topbar/zoom_out.dds",
    "GFX_zoom_out", note="减速按钮（-），x=58,y=1")
add("topbar", "value_box_small", "gfx/interface/generic_box_smallest.dds",
    "GFX_generic_box_smallest", note="顶栏数值框底（政治点/稳定度/战争支持度/工厂/燃料/船运），70x26，同时用于右下角时钟时间框")
add("topbar", "value_box_96", "gfx/interface/generic_box_96.dds",
    "GFX_generic_box_96", note="顶栏较宽数值框底（人力）")
add("topbar", "value_box_tiled", "gfx/interface/tiles/tiled_generic_bg_1.dds",
    "GFX_tiled_generic_bg_1", border=(14, 14), note="可拉伸数值框（陆/海/空经验、指挥点 56~62x28），9 宫格，中心平铺")
add("topbar", "clock_bg", "gfx/interface/ingameclock_bg.dds",
    "GFX_ingameclock_bg", note="右下角游戏内时钟窗底（clock_window 228x42, LOWER_RIGHT x=-210,y=-40）")

# 2. 面板 ------------------------------------------------------------------
add("panel", "window_bg_1b_border", "gfx/interface/tiles/tiled_window_1b_border.dds",
    "GFX_tiled_window_1b_border", border=(64, 64), note="标准深色窗口底框（单金边），9 宫格中心平铺；战术列表等窗口使用。.gfx size=192 而贴图 190")
add("panel", "window_bg", "gfx/interface/tiles/tiled_window.dds",
    "GFX_tiled_window", border=(64, 64), note="通用窗口底框（tiled_window），9 宫格")
add("panel", "window_bg_2b_border", "gfx/interface/tiles/tiled_window_2b_border.dds",
    "GFX_tiled_window_2b_border", border=(64, 64), note="双边窗口底框，9 宫格")
add("panel", "mini_tooltip", "gfx/interface/tiles/tiled_mini_dialog.dds",
    "GFX_mini_tooltip", border=(12, 12), note="小型浮框/提示框底（核武器数等），9 宫格；也作 GFX_mini_bg")
add("panel", "title_strip", "gfx/interface/header_bg.dds",
    "GFX_tiled_header", border=(61, 21), note="窗口标题条（GFX_tiled_header, bordersize 61x21 横向拉伸）")
add("panel", "header_small_tiled", "gfx/interface/tiles/tiled_header_1.dds",
    "GFX_tiled_header_1", border=(8, 8), note="小标题/分组头底，9 宫格")
add("panel", "divider", "gfx/interface/divider.dds",
    "GFX_divider", note="水平分隔线 514x9")
add("panel", "card_event_news", "gfx/interface/event_news_bg.dds",
    "GFX_event_news_bg", note="新闻事件窗口整张底图（报纸风），卡片底")
add("panel", "card_decision_item", "gfx/interface/decisionview/decision_item_bg.dds",
    "GFX_decision_item_bg", note="决议列表条目底（1536x40 长条，按需裁宽）")

# 3. 战斗窗口 --------------------------------------------------------------
add("combat", "land_battle_window_bg", "gfx/interface/land_battle_bg.dds",
    "GFX_land_battle_bg", note="陆战窗口整张底图 567x617（landcombatview）：左上交叉刀剑=进攻方徽，右上盾牌=防守方徽，中部对比条位，下部战场地图")
add("combat", "battle_progress_attacker", "gfx/interface/landcombat_attacker_progress.dds",
    "GFX_landcombat_progress(textureFile1)", note="进攻方/防守方对比条的进攻方填充（progressbartype size 179x14，位于 x=185,y=116）")
add("combat", "battle_progress_defender", "gfx/interface/landcombat_defender_progress.dds",
    "GFX_landcombat_progress(textureFile2)", note="对比条的防守方底色（textureFile2 = 底/剩余部分）")
add("combat", "battle_progress_frame", "gfx/interface/land_battle_progress_frame.dds",
    "GFX_land_battle_progress_frame", note="对比条外框 185x20，放 x=183,y=114")
add("combat", "battle_heading_indicator", "gfx/interface/land_battle_heading_indicator_strip.dds",
    "GFX_land_battle_heading_indicator_strip", frames=5, note="对比条上的胜负倾向指示，5 帧（按优势程度切帧）")
add("combat", "progressbar_tiled", "gfx/interface/tiles/tiled_progress_bar.dds",
    "GFX_tiled_progress_bar", border=(14, 19), note="通用可拉伸进度条框（190x27），9 宫格")
add("combat", "battle_map_icon", "gfx/interface/mapicons/mapicon_land_combat.dds",
    "GFX_mapicon_land_combat", note="地图上的陆战图标圆底（铜色圆钮，land_combat_mapicon 35x34 的 bg_btn），上面叠 frame 与进度%文字")
add("combat", "battle_map_icon_frame", "gfx/interface/mapicons/mapicon_land_combat_frame.dds",
    "GFX_mapicon_land_combat_frame", frames=2, note="陆战图标外框压层，2 帧（左=进攻剑形/右=防守旗形，按我方攻守切帧）")
add("combat", "battle_anim_strip", "gfx/interface/landbattle_strip.dds",
    "GFX_landbattle_strip", frames=20, note="地图陆战进行中的爆炸火光动画条带 20 帧 x 51px，10fps 循环（frameAnimatedSpriteType）")
add("combat", "battle_direction_indicator", "gfx/interface/battle_indicator_direction.dds",
    "GFX_battle_indicator_direction", note="地图战斗图标下的进攻方向指示（按方向旋转）")
add("combat", "combat_unit_row_bg", "gfx/interface/land_battle_unit_bg.dds",
    "GFX_land_battle_unit_bg", note="战斗窗口内参战师一行的底")
add("combat", "attacker_damage_icon", "gfx/interface/attacker_damage_icon.dds",
    "GFX_attacker_damage_icon", note="进攻方伤害图标")
add("combat", "defender_damage_icon", "gfx/interface/defender_damage_icon.dds",
    "GFX_defender_damage_icon", note="防守方伤害图标")
add("combat", "unit_in_combat_icon", "gfx/interface/unitview_entry_combat_icon.dds",
    "GFX_land_unit_combat_icon", frames=2, note="单位列表里的“战斗进行中”图标，2 帧 x 42x32：第 1 帧=红剑（进攻中），第 2 帧=蓝盾（防守中）")
add("combat", "theatre_battle_indicator", "gfx/interface/theatre_battle_indicator.dds",
    "GFX_theatre_battle_indicator", frames=2, note="战区栏的战斗指示，2 帧 x 34x34：剑（进攻）/盾（防守），白色单色版")

# 4. 按钮 ------------------------------------------------------------------
add("button", "button_standard", "gfx/interface/button_type_1.dds",
    "button_type_1", note="标准按钮 145x32，单帧；悬停/按下靠 buttonstate.lua 着色器变亮/压暗，不是多帧")
add("button", "button_148x34", "gfx/interface/button_148x34.dds",
    "GFX_button_148x34", note="中号按钮（贴图 148x36），单帧 + buttonstate")
add("button", "button_88", "gfx/interface/standard_button_88.dds",
    "GFX_standard_button_88", note="小号标准按钮 88x32")
add("button", "button_sort_3frames", "gfx/interface/sort_button_202x29_3_frames.dds",
    "GFX_sort_button_202x29_3_frames", frames=3, note="多帧条带按钮示例：606x29 = 3 帧 x 202（常态/悬停/选中）")
add("button", "topbar_toolbar_button", "gfx/interface/topbar/toolbar/production_button.dds",
    "GFX_topbar_production", frames=2, note="顶栏左侧功能按钮（生产），2 帧（常态/激活）")
add("button", "close_button", "gfx/interface/closebutton.dds",
    "GFX_closebutton", note="窗口右上角关闭按钮")

# 5. 地图单位标牌 counter -------------------------------------------------
add("counter", "unit_counter_bg", "gfx/interface/onmap_unit_counter.dds",
    "GFX_onmap_unit_counter", frames=3, note="地图师标牌底框（unit_counter 76x24 内 count_bg），198x27 = 3 帧 x 66：按友/敌/中立等关系换帧")
add("counter", "unit_counter_selected", "gfx/interface/onmap_unit_counter_selected.dds",
    "GFX_onmap_unit_counter_selected", note="标牌选中描边（count_selected, x=0,y=0）")
add("counter", "unit_counter_overlay", "gfx/interface/onmap_unit_counter_overlay.dds",
    "GFX_onmap_unit_counter_overlay", note="标牌左侧兵种图标区压层（x=3,y=3）")
add("counter", "unit_counter_half", "gfx/interface/onmap_unit_counter_half.dds",
    "GFX_onmap_unit_counter_half", note="缩小/半宽标牌底")
add("counter", "unit_counter_ideology", "gfx/interface/onmap_unit_counter_ideology.dds",
    "GFX_onmap_unit_counter_ideology", frames=4, note="标牌右段意识形态色块，4 帧（x=37,y=0）")
add("counter", "bar_org_sprite", "gfx/interface/mapicons/mapicon_bar_org_sprite.dds",
    "GFX_mapicon_unit_bar_org", note="标牌组织度条（3x17 竖贴图被代码拉成横条，bar_org x=14,y=8）")
add("counter", "bar_str_sprite", "gfx/interface/mapicons/mapicon_bar_str_sprite.dds",
    "GFX_mapicon_unit_bar_str", note="标牌兵力条（bar_str x=14,y=12）")
add("counter", "bar_bg", "gfx/interface/mapicons/mapicon_bar_bg.dds",
    "GFX_combat_unit_bar_*(textureFile2)", note="小条底 21x7（战斗窗口/标牌 str/org 进度条背景）")
add("counter", "bar_org", "gfx/interface/mapicons/mapicon_bar_org.dds",
    "GFX_combat_unit_bar_org(textureFile1)", note="组织度条填充 21x7")
add("counter", "bar_str", "gfx/interface/mapicons/mapicon_bar_str.dds",
    "GFX_combat_unit_bar_str(textureFile1)", note="兵力条填充 21x7")
add("counter", "unit_type_strip", "gfx/interface/mapicons/mapicon_unit_type_strip.dds",
    "GFX_mapicon_unit_types_strip", frames=12, note="地图堆叠图标的兵种小图条带 12 帧 x 30x12")
add("counter", "tiny_unit_counter", "gfx/interface/mapicons/mapicon_tiny_unit_counter.dds",
    "GFX_mapicon_tiny_unit_counter", note="缩远时的极小标牌")

# 6. 兵种图标（北约式，gfx/interface/counters）---------------------------
# large = 师模板/面板用 152x42（2 帧 x 76x42）；small = 地图标牌里用的白色版 60x12（2 帧 x 30x12）
ICONS = [
    ("infantry", "unit_infantry_icon", "onmap_unit_infantry_icon", "GFX_unit_infantry_icon_medium", "步兵"),
    ("armor", "unit_medium_tank_icon", "onmap_unit_medium_tank_icon", "GFX_unit_medium_armor_icon_medium", "装甲（中型坦克）"),
    ("mechanized", "unit_mechanized_icon", "onmap_unit_mechanized_icon", "GFX_unit_mechanized_icon_medium", "机械化步兵"),
    ("motorized", "unit_motorized_icon", "onmap_unit_motorized_icon", "GFX_unit_motorized_icon_medium", "摩托化步兵"),
    ("recon", "unit_recon_icon", "onmap_unit_recon_icon", "GFX_unit_recon_icon_medium", "侦察"),
    ("artillery", "unit_art_icon", "onmap_unit_art_icon", "GFX_unit_artillery_brigade_icon_medium", "炮兵"),
    ("sp_artillery", "unit_medium_tank_artillery_icon", "onmap_unit_medium_spart_icon", "GFX_unit_medium_sp_artillery_brigade_icon_medium", "自行火炮"),
    ("anti_tank", "unit_at_icon", "onmap_unit_at_icon", "GFX_unit_anti_tank_brigade_icon_medium", "反坦克"),
    ("anti_air", "unit_anti_air_icon", "onmap_unit_anti_air_icon", "GFX_unit_anti_air_brigade_icon_medium", "防空"),
    ("engineer", "unit_engineer_icon", "onmap_unit_engineer_icon", "GFX_unit_engineer_icon_medium", "工兵"),
    ("hq", "support_unit_hq_icon", "onmap_unit_hq_icon", "GFX_unit_hq_support_company_icon_medium", "指挥部（HQ 支援连）"),
    ("helicopter", "unit_helicopter_brigade_icon", "onmap_helicopter_brigade_icon", "GFX_unit_helicopter_brigade_icon_medium", "直升机旅"),
    ("paratrooper", "unit_paratroop_icon", "onmap_unit_paratroop_icon", "GFX_unit_paratrooper_icon_medium", "伞兵"),
]
for key, large, small, sprite, zh in ICONS:
    add("army_icons", f"unit_{key}_large", f"gfx/interface/counters/divisions_large/{large}.dds",
        sprite, frames=2, note=f"{zh}（面板/师设计器用），2 帧：第 1 帧=写实小图，第 2 帧=北约兵种符号（按图标显示设置切换）")
    add("army_icons", f"unit_{key}_small", f"gfx/interface/counters/divisions_small/{small}.dds",
        sprite + "_white", frames=2, note=f"{zh}，地图标牌用白色 counter 小图，2 帧 x 30x12：第 1 帧=写实剪影，第 2 帧=北约符号")
for key, f, zh in [("fighter", "onmap_fighter", "战斗机"), ("cas", "onmap_cas", "近距支援机"),
                   ("tac_bomber", "onmap_tac_bomber", "战术轰炸机")]:
    add("army_icons", f"air_{key}_small", f"gfx/interface/counters/air_small/{f}.dds",
        "(code-referenced air counter)", note=f"{zh}，地图空军联队标牌小图（60x12，无 .gfx noOfFrames，按 1 帧记）")
add("army_icons", "army_insignia_strip", "gfx/army_icons/army_icons.dds",
    "GFX_theatre_army_shield", frames=81,
    note="gfx/army_icons 里只有军/集团军徽章条带（army_icons.txt 按帧索引定义，32px 方块），不是北约兵种图标；仅作参考")

# 7. 地图箭头 ---------------------------------------------------------------
add("maparrows", "plan_attack_arrow_mask", "gfx/maparrows/blitz_mask.dds",
    "arrowType blitz/withdraw (texture_masks)", note="作战计划进攻箭头（带箭头 MapArrowDefault）。一张图同时含箭身(左)与箭头(右)；绿通道=内填色区，红通道=外描边区，shader 按国家色上色；箭身沿路径拉伸，head_length=30")
add("maparrows", "plan_attack_arrow_pattern", "gfx/maparrows/blitz_pattern.dds",
    "arrowType blitz (texture_patterns)", note="进攻箭头的灰度花纹层，与 mask 同尺寸，沿箭身方向滚动（texture_anim_speed）")
add("maparrows", "move_arrow_mask", "gfx/maparrows/default_mask.dds",
    "arrowType move_thick (texture_masks)", note="粗移动箭头 mask（同上：绿内红外，左箭身右箭头，body_uv_scale 0.4，head_length 19.5，body_width 20.5→6 收窄）")
add("maparrows", "move_arrow_pattern", "gfx/maparrows/default_pattern.dds",
    "arrowType move_thick (texture_patterns)", note="粗移动箭头的灰度花纹")
add("maparrows", "offensive_line", "gfx/maparrows/offensive_line.dds",
    "arrowType offence_line (texture_patterns)", note="进攻发起线（无箭头 MapArrowNoHead），沿线 texture_loop=2 平铺")
add("maparrows", "offensive_line_mask", "gfx/maparrows/offensive_line_mask.dds",
    "arrowType offence_line (texture_masks)", note="进攻线 mask（绿内红外）")
add("maparrows", "defensive_line", "gfx/maparrows/defensive_line.dds",
    "arrowType defence_line (texture_patterns)", note="防线（锯齿/前线）花纹，沿线平铺")
add("maparrows", "unit_attack_arrow", "gfx/mapitems/movearrow_attack.dds",
    "arrowType attackArrow (interface/arrows.gfx)", note="单位下令进攻时的箭头贴图（mapitems，配 movearrow_normals/spec）：上下两条完整箭（箭身左、箭头右，黑描边），上=当前色、下=淡色版；箭身沿路径拉伸")
add("maparrows", "unit_move_arrow", "gfx/mapitems/movearrow_move.dds",
    "arrowType moveArrow (interface/arrows.gfx)", note="单位移动 3D 箭头贴图")


# ---------------------------------------------------------------------------
# 附带信息: 字体与布局摘要（从 interface/*.gui 摘出，供参考，不是贴图）
# ---------------------------------------------------------------------------
FONTS = {
    "note": "素材里没有 gfx/fonts（无 .fnt / 字体贴图）；interface/fonts.gfx 只引用 gfx/fonts/garamond_12（调试字体），文件不在本次解包内。下列为 .gui 引用的字体名，像素大小按命名中的数字推断。",
    "topbar": [
        {"font": "hoi_18mbs", "px_guess": 18, "use": "顶栏全部数值、日期 DateText、威胁度、时钟 time_text"},
        {"font": "hoi_16mbs", "px_guess": 16, "use": "顶栏决议/提醒计数、重开大厅标签"},
        {"font": "Main_14_black", "px_guess": 14, "use": "菜单/帮助按钮 buttonFont（文字为空）"},
        {"font": "vic_22", "px_guess": 22, "use": "暂停横幅（已注释）"},
    ],
    "landcombat": [
        {"font": "hoi_30header", "px_guess": 30, "use": "陆战窗口标题 window_title (x=55,y=2)"},
        {"font": "hoi_20bs", "px_guess": 20, "use": "双方大数值/名称"},
        {"font": "hoi_20b", "px_guess": 20, "use": "粗体数值"},
        {"font": "hoi_18mbs", "px_guess": 18, "use": "常规数值"},
        {"font": "hoi_16mbs", "px_guess": 16, "use": "战术/伤害数值、地图战斗进度%"},
        {"font": "Arial12", "px_guess": 12, "use": "小注释文字"},
    ],
    "counter": [{"font": "Main_14", "px_guess": 14, "use": "地图标牌师数 count_txt (x=36,y=6)"}],
    "maparrows": [{"font": "hoi_arrow_font", "px_guess": None, "use": "作战箭头上的文字"}],
}

LAYOUT = {
    "topbar": {
        "root": "topbar 窗口 position x=-4,y=-1, 宽 100%；底图 GFX_topbar_background_extended 2346x87 左上对齐",
        "flag": "player_flag (GFX_shield_medium) x=12,y=14",
        "resources_row_y": 5,
        "resources": "政治点 bg x=100 (70x26) / 稳定度 x=173 / 战争支持 x=246 / 人力 x=319 (96x26) / 工厂 x=418 / 燃料 x=491 / 船运 x=637 / 指挥点 x=732 (62x28) / 陆海空经验 x=796/854/912 (56x28) / 核弹 x=970；数值文字 y=4~8，hoi_18mbs",
        "ratio_bars": "工厂/燃料/船运下方 GFX_topbar_fuel_bar 45x8 于 y=20",
        "time_control_window": "UPPER_RIGHT x=-378,y=7, 253x30；日期框 x=-197；DateText x=-175,y=4 宽150 右对齐；减速 x=58,y=1；5 个速度格 x=94..194 步距 25 y=27；加速 x=233,y=1",
        "menu_button": "UPPER_RIGHT x=-35,y=7",
        "clock_window": "LOWER_RIGHT x=-210,y=-40, 228x42；时间框 x=40,y=10",
    },
    "landcombatview": {
        "root": "567x617, position x=-512,y=-256, 底图 GFX_land_battle_bg",
        "title": "window_title x=55,y=2 hoi_30header；关闭 UPPER_RIGHT x=-34,y=5",
        "terrain_picture": "x=77,y=37",
        "progress": "GFX_landcombat_progress 179x14 于 x=185,y=116；外框 185x20 于 x=183,y=114；倾向指示条带同位",
    },
    "unit_counter": {
        "root": "76x24；底 GFX_onmap_unit_counter 帧 66x27",
        "parts": "兵种图标区 x=3,y=3；组织度条 x=14,y=8；兵力条 x=14,y=12；国旗条 x=5,y=15；师数文字 x=36,y=6 Main_14；意识形态色块 x=37；选中描边 x=0,y=0",
    },
}


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a png: " + path)
    return struct.unpack(">II", head[16:24])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="解包素材根（含 gfx/ interface/）")
    ap.add_argument("--out", required=True, help="PNG 与 manifest.json 输出目录")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root).replace("\\", "/")
    out = os.path.abspath(a.out).replace("\\", "/")
    if os.path.normcase(out).startswith(os.path.normcase(root + "/")):
        sys.exit("refuse: --out must not be inside --root (source is read-only)")

    items, missing, failed = [], [], []
    for e in E:
        src = f"{root}/{e['src']}"
        if not os.path.isfile(src):
            missing.append(e["src"])
            continue
        d = f"{out}/{e['sub']}"
        os.makedirs(d, exist_ok=True)
        png = f"{d}/{os.path.splitext(os.path.basename(e['src']))[0]}.png"
        r = subprocess.run([a.ffmpeg, "-v", "error", "-y", "-i", src, png],
                           capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(png):
            failed.append({"src": src, "err": (r.stderr or "").strip()[-400:]})
            continue
        w, h = png_size(png)
        frames = e["frames"]
        if frames is None:  # army insignia: square frames
            frames = w // h if h else 1
        b = e["border"]
        items.append({
            "role": e["role"], "category": e["sub"], "src": src, "png": png,
            "w": w, "h": h, "frames": frames,
            "frame_w": w // frames if frames else w,
            "border": {"x": b[0], "y": b[1]} if b else None,
            "sprite": e["sprite"], "note": e["note"],
        })
        print(f"ok  {e['sub']:<10} {e['role']:<30} {w}x{h} f={frames}")

    manifest = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": os.path.abspath(__file__).replace("\\", "/"),
        "source_root": root,
        "usage": "HOI4 UI 素材，粉丝向自用 HUD 皮肤；frames>1 为横向等宽条带(frame_w=w/frames)；border 为 9 宫格边宽(px)",
        "items": items,
        "fonts": FONTS,
        "layout": LAYOUT,
        "missing": missing,
        "failed": failed,
    }
    os.makedirs(out, exist_ok=True)
    mp = f"{out}/manifest.json"
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"items={len(items)} missing={len(missing)} failed={len(failed)}\nmanifest: {mp}")
    for m in missing:
        print("MISSING", m)
    for x in failed:
        print("FAILED", x["src"], x["err"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
