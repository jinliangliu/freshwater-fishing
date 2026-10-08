#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""淡水野钓：环境数据采集器。

只做一件事：把「地点 + 日期」转成一份确定性的环境数据 JSON，
交给模型去做水域/鱼种/安全的评估推理。

设计原则：
- 零第三方依赖，仅用标准库（urllib），保证在任何沙箱里都能跑。
- 缺数据就显式置 null 并写进 warnings，绝不编造（原技能靠模型心算，会静默幻觉）。
- 不做"适宜度打分"——评分涉及大量领域判断，留给模型；脚本只提供事实。

用法：
    python fishing_conditions.py --location 千岛湖 --date 2026-06-01
    python fishing_conditions.py --location "上海青浦" --days 3 --json
    python fishing_conditions.py --location 太湖 --date 明天 --no-network   # 仅算天文/季节
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# 农历 / 月相
# ---------------------------------------------------------------------------

# 1900-2100 年农历数据表（每年一个 32 位整数，标准 lunarInfo 表）
_LUNAR_INFO = [
    0x04bd8, 0x04ae0, 0x0a570, 0x054d5, 0x0d260, 0x0d950, 0x16554, 0x056a0, 0x09ad0, 0x055d2,
    0x04ae0, 0x0a5b6, 0x0a4d0, 0x0d250, 0x1d255, 0x0b540, 0x0d6a0, 0x0ada2, 0x095b0, 0x14977,
    0x04970, 0x0a4b0, 0x0b4b5, 0x06a50, 0x06d40, 0x1ab54, 0x02b60, 0x09570, 0x052f2, 0x04970,
    0x06566, 0x0d4a0, 0x0ea50, 0x06e95, 0x05ad0, 0x02b60, 0x186e3, 0x092e0, 0x1c8d7, 0x0c950,
    0x0d4a0, 0x1d8a6, 0x0b550, 0x056a0, 0x1a5b4, 0x025d0, 0x092d0, 0x0d2b2, 0x0a950, 0x0b557,
    0x06ca0, 0x0b550, 0x15355, 0x04da0, 0x0a5b0, 0x14573, 0x052b0, 0x0a9a8, 0x0e950, 0x06aa0,
    0x0aea6, 0x0ab50, 0x04b60, 0x0aae4, 0x0a570, 0x05260, 0x0f263, 0x0d950, 0x05b57, 0x056a0,
    0x096d0, 0x04dd5, 0x04ad0, 0x0a4d0, 0x0d4d4, 0x0d250, 0x0d558, 0x0b540, 0x0b6a0, 0x195a6,
    0x095b0, 0x049b0, 0x0a974, 0x0a4b0, 0x0b27a, 0x06a50, 0x06d40, 0x0af46, 0x0ab60, 0x09570,
    0x04af5, 0x04970, 0x064b0, 0x074a3, 0x0ea50, 0x06b58, 0x05ac0, 0x0ab60, 0x096d5, 0x092e0,
    0x0c960, 0x0d954, 0x0d4a0, 0x0da50, 0x07552, 0x056a0, 0x0abb7, 0x025d0, 0x092d0, 0x0cab5,
    0x0a950, 0x0b4a0, 0x0baa4, 0x0ad50, 0x055d9, 0x04ba0, 0x0a5b0, 0x15176, 0x052b0, 0x0a930,
    0x07954, 0x06aa0, 0x0ad50, 0x05b52, 0x04b60, 0x0a6e6, 0x0a4e0, 0x0d260, 0x0ea65, 0x0d530,
    0x05aa0, 0x076a3, 0x096d0, 0x04afb, 0x04ad0, 0x0a4d0, 0x1d0b6, 0x0d250, 0x0d520, 0x0dd45,
    0x0b5a0, 0x056d0, 0x055b2, 0x049b0, 0x0a577, 0x0a4b0, 0x0aa50, 0x1b255, 0x06d20, 0x0ada0,
    0x14b63, 0x09370, 0x049f8, 0x04970, 0x064b0, 0x168a6, 0x0ea50, 0x06b20, 0x1a6c4, 0x0aae0,
    0x0a2e0, 0x0d2e3, 0x0c960, 0x0d557, 0x0d4a0, 0x0da50, 0x05d55, 0x056a0, 0x0a6d0, 0x055d4,
    0x052d0, 0x0a9b8, 0x0a950, 0x0b4a0, 0x0b6a6, 0x0ad50, 0x055a0, 0x0aba4, 0x0a5b0, 0x052b0,
    0x0b273, 0x06930, 0x07337, 0x06aa0, 0x0ad50, 0x14b55, 0x04b60, 0x0a570, 0x054e4, 0x0d160,
    0x0e968, 0x0d520, 0x0daa0, 0x16aa6, 0x056d0, 0x04ae0, 0x0a9d4, 0x0a2d0, 0x0d150, 0x0f252,
    0x0d520,
]

_LUNAR_MONTH_CN = ["正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊"]
_LUNAR_DAY_CN = [
    "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
]


def _leap_month(year: int) -> int:
    """返回该农历年的闰月（0 表示无闰月）。"""
    return _LUNAR_INFO[year - 1900] & 0xF


def _leap_days(year: int) -> int:
    """闰月天数，无闰月返回 0。"""
    if _leap_month(year):
        return 30 if (_LUNAR_INFO[year - 1900] & 0x10000) else 29
    return 0


def _month_days(year: int, month: int) -> int:
    """农历某月天数（非闰月）。bit 15..4 依次对应正月..腊月，置 1 为 30 天。

    注意用掩码 (1 << (16 - month)) 而非右移，避免 month=1 时撞上 bit 16（闰月大小标志位）。
    """
    return 30 if (_LUNAR_INFO[year - 1900] & (1 << (16 - month))) else 29


def _year_days(year: int) -> int:
    """农历年总天数 = 12 个非闰月 + 可能的闰月。约 354/384 天。"""
    total = 0
    for i in range(1, 13):
        total += _month_days(year, i)
    return total + _leap_days(year)


def solar_to_lunar(date: _dt.date) -> dict:
    """公历 → 农历。范围 1900-01-31 ~ 2100-12-31。"""
    base = _dt.date(1900, 1, 31)
    offset = (date - base).days
    if offset < 0:
        raise ValueError("日期早于 1900-01-31，超出农历算法支持范围")

    year = 1900
    while year < 2101 and offset >= _year_days(year):
        offset -= _year_days(year)
        year += 1

    leap = _leap_month(year)
    is_leap = False
    month = 1
    while month <= 12:
        days = _leap_days(year) if (leap and month == leap + 1 and not is_leap) else _month_days(year, month)
        if leap and month == leap + 1 and not is_leap:
            is_leap = True
        else:
            is_leap = False
        if offset < days:
            break
        offset -= days
        month += 1

    day = offset + 1
    return {
        "year": year,
        "month": month,
        "day": day,
        "is_leap_month": is_leap,
        "month_cn": ("闰" if is_leap else "") + _LUNAR_MONTH_CN[month - 1] + "月",
        "day_cn": _LUNAR_DAY_CN[day - 1],
        "text": f"农历{_LUNAR_MONTH_CN[month - 1]}月{_LUNAR_DAY_CN[day - 1]}",
    }


def moon_phase(lunar: dict) -> dict:
    """由农历日期判定月相，并给出夜钓/昼钓修正与朔望间隔天数。"""
    d = lunar["day"]
    # 朔望月约 29.53 天，按农历日近似相位角
    illumination = (1 - math.cos(2 * math.pi * (d - 1) / 29.53059)) / 2 * 100

    if d <= 2 or d >= 30:
        name, key = "新月（朔）", "new_moon"
    elif d <= 6:
        name, key = "蛾眉月", "waxing_crescent"
    elif d <= 9:
        name, key = "上弦月", "first_quarter"
    elif d <= 13:
        name, key = "盈凸月", "waxing_gibbous"
    elif d <= 17:
        name, key = "满月（望）", "full_moon"
    elif d <= 21:
        name, key = "亏凸月", "waning_gibbous"
    elif d <= 24:
        name, key = "下弦月", "last_quarter"
    else:
        name, key = "残月", "waning_crescent"

    modifiers = {
        "new_moon": (10, 0),
        "waxing_crescent": (5, 0),
        "first_quarter": (0, 0),
        "waxing_gibbous": (-5, 0),
        "full_moon": (-10, 5),
        "waning_gibbous": (-5, 0),
        "last_quarter": (0, 0),
        "waning_crescent": (5, 0),
    }
    night_adj, day_adj = modifiers[key]
    return {
        "phase": name,
        "phase_key": key,
        "illumination_pct": round(illumination, 1),
        "night_fishing_adj": night_adj,
        "day_fishing_adj": day_adj,
        "note": "初七/初八与廿二/廿三属半月，前夜与后夜修正相反，脚本按整夜给出净修正 0",
    }


# ---------------------------------------------------------------------------
# 季节（节气法，与气象学/公众认知一致的近似）
# ---------------------------------------------------------------------------

# 各月节气分界日（近似，误差 ±1 天）：立春/立夏/立秋/立冬
_SEASON_BOUNDS = {
    2: (4, "春"), 3: (0, "春"), 4: (0, "春"), 5: (6, "夏"), 6: (0, "夏"),
    7: (0, "夏"), 8: (7, "秋"), 9: (0, "秋"), 10: (0, "秋"),
    11: (7, "冬"), 12: (0, "冬"), 1: (6, "冬"),
}


def season_of(date: _dt.date) -> dict:
    """按节气近似判定季节。与原技能「农历月份法」不同，见 references/season-debate.md。"""
    m, d = date.month, date.day
    bound, season = _SEASON_BOUNDS[m]
    if bound and d >= bound:
        # 跨入下一季
        order = ["春", "夏", "秋", "冬"]
        season = order[(order.index(season) + 1) % 4]
    names = {"春": "春季", "夏": "夏季", "秋": "秋季", "冬": "冬季"}
    return {
        "season": names[season],
        "basis": "节气近似（立春/立夏/立秋/立冬）",
        "month": date.month,
        "day": date.day,
    }


# ---------------------------------------------------------------------------
# 天气（wttr.in，无 API Key）
# ---------------------------------------------------------------------------

_WTTR_URL = "https://wttr.in/{location}?format=j1&lang=zh"

# wttr.in / WWO 天气代码 → 中文描述
_WWO_ZH = {
    "113": "晴", "116": "多云", "119": "阴", "122": "阴", "143": "薄雾",
    "176": "零星阵雨", "179": "零星阵雪", "182": "零星雨夹雪", "185": "零星冻雨",
    "200": "雷阵雨", "227": "吹雪", "230": "暴雪", "248": "雾", "260": "冻雾",
    "263": "零星毛毛雨", "266": "毛毛雨", "281": "冻毛毛雨", "284": "强冻毛毛雨",
    "293": "零星小雨", "296": "小雨", "299": "间歇中雨", "302": "中雨",
    "305": "间歇大雨", "308": "大雨", "311": "小雨夹雪", "314": "中雨夹雪",
    "317": "小雨夹雪", "320": "中雪", "323": "小雪", "326": "小雪",
    "329": "零星中雪", "332": "中雪", "335": "零星大雪", "338": "大雪",
    "350": "冻雨", "353": "小雨", "356": "中雨", "359": "暴雨",
    "362": "雨夹雪", "365": "雨夹雪", "368": "小雪", "371": "大雪",
    "374": "零星冻雨", "377": "冻雨", "386": "雷阵雨", "389": "强雷阵雨",
    "392": "雷雪", "395": "强降雪",
}


def _wind_force(kmph: float) -> int:
    """km/h → 蒲福风级。"""
    thresholds = [1, 6, 12, 20, 29, 39, 50, 62, 75, 89, 103, 118]
    for i, t in enumerate(thresholds):
        if kmph < t:
            return i
    return 12


def _fetch_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _parse_wttr_day(day: dict) -> dict:
    astro = (day.get("astronomy") or [{}])[0]
    hourly = day.get("hourly") or []

    hours = []
    for h in hourly:
        wind_kmph = float(h.get("windspeedKmph", 0) or 0)
        hours.append({
            "time": f"{int(h.get('time', 0)) // 100:02d}:00",
            "temp_c": _num(h.get("tempC")),
            "feels_like_c": _num(h.get("FeelsLikeC")),
            "humidity_pct": _num(h.get("humidity")),
            "pressure_hpa": _num(h.get("pressure")),
            "wind_kmph": wind_kmph,
            "wind_force": _wind_force(wind_kmph),
            "wind_dir": h.get("winddir16Point"),
            "cloud_cover_pct": _num(h.get("cloudcover")),
            "precip_mm": _num(h.get("precipMM")),
            "rain_chance_pct": _num(h.get("chanceofrain")),
            "thunder_chance_pct": _num(h.get("chanceofthunder")),
            "condition": _WWO_ZH.get(str(h.get("weatherCode", "")), None),
        })

    return {
        "date": day.get("date"),
        "temp_min_c": _num(day.get("mintempC")),
        "temp_max_c": _num(day.get("maxtempC")),
        "sunrise": astro.get("sunrise"),
        "sunset": astro.get("sunset"),
        "moonrise": astro.get("moonrise"),
        "moonset": astro.get("moonset"),
        "moon_phase_wttr": astro.get("moon_phase"),
        "moon_illumination_wttr": _num(astro.get("moon_illumination")),
        "hourly": hours,
    }


def _num(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return int(f) if f == int(f) else round(f, 1)
    except (TypeError, ValueError):
        return None


def fetch_weather(location: str, timeout: int = 20) -> dict:
    """拉取 wttr.in 结构化天气。返回 {'ok': bool, 'resolved': str|None, 'days': [...]}"""
    url = _WTTR_URL.format(location=urllib.parse.quote(location))
    try:
        data = _fetch_json(url, timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "url": url, "days": []}

    area = (data.get("nearest_area") or [{}])[0]
    resolved = None
    if area:
        name = (area.get("areaName") or [{}])[0].get("value")
        region = (area.get("region") or [{}])[0].get("value")
        country = (area.get("country") or [{}])[0].get("value")
        parts = [p for p in (country, region, name) if p]
        resolved = " / ".join(parts) if parts else None

    return {
        "ok": True,
        "query": location,
        "resolved": resolved,
        "url": url,
        "current": _parse_current(data.get("current_condition") or [{}]),
        "days": [_parse_wttr_day(d) for d in (data.get("weather") or [])],
    }


def _parse_current(cur_list) -> dict:
    if not cur_list:
        return {}
    c = cur_list[0]
    wind_kmph = float(c.get("windspeedKmph", 0) or 0)
    return {
        "time_local": c.get("localObsDateTime") or c.get("observation_time"),
        "temp_c": _num(c.get("temp_C")),
        "feels_like_c": _num(c.get("FeelsLikeC")),
        "humidity_pct": _num(c.get("humidity")),
        "pressure_hpa": _num(c.get("pressure")),
        "wind_kmph": wind_kmph,
        "wind_force": _wind_force(wind_kmph),
        "wind_dir": c.get("winddir16Point"),
        "cloud_cover_pct": _num(c.get("cloudcover")),
        "precip_mm": _num(c.get("precipMM")),
        "condition": _WWO_ZH.get(str(c.get("weatherCode", "")), None),
    }


# ---------------------------------------------------------------------------
# 停钓政策关键词预筛（不是法律判定，只用于提示需要人工核实）
# ---------------------------------------------------------------------------

_BAN_PATTERNS = [
    (re.compile(r"长江|干流|长江流域"), "长江流域重点水域十年禁渔（2021-01-01 起，十年）"),
    (re.compile(r"鄱阳湖|洞庭湖"), "长江流域重点水域十年禁渔（湖泊重点水域）"),
    (re.compile(r"赤水河"), "长江上游珍稀特有鱼类国家级自然保护区 / 十年禁渔"),
    (re.compile(r"黄河"), "黄河禁渔期制度（每年 4 月 1 日 - 7 月 31 日为禁渔期，以当地公告为准）"),
    (re.compile(r"珠江"), "珠江流域禁渔期制度（每年 3 月 1 日 - 6 月 30 日，以当地公告为准）"),
    (re.compile(r"水源|取水口|水库.*一级保护"), "饮用水水源一级保护区严禁垂钓"),
    (re.compile(r"自然保护区|保护区|禁渔区|水产种质资源保护区"), "自然保护区 / 禁渔区 / 种质资源保护区严禁垂钓"),
    (re.compile(r"禁钓|禁渔"), "用户已提示禁钓/禁渔字样"),
]


def ban_screening(location: str, water_type: str | None = None) -> dict:
    text = f"{location or ''} {water_type or ''}"
    hits = [{"pattern": p.pattern, "note": note} for p, note in _BAN_PATTERNS if p.search(text)]
    return {
        "hit": bool(hits),
        "hits": hits,
        "disclaimer": (
            "以上为关键词预筛，不能替代法律判定。"
            "真实禁钓范围请以「农业农村部 / 省级农业农村厅公告」和当地渔政答复为准，"
            "出钓前请主动向当地渔政核实（农业农村部渔业渔政管理局 010-59192988 或当地 12345）。"
        ),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

_DATE_ALIAS = {
    "今天": 0, "今日": 0, "today": 0,
    "明天": 1, "明日": 1, "tomorrow": 1,
    "后天": 2,
    "大后天": 3,
}


def parse_date(raw: str | None, today: _dt.date | None = None) -> _dt.date:
    today = today or _dt.date.today()
    if not raw:
        return today + _dt.timedelta(days=1)
    key = raw.strip().lower()
    if key in _DATE_ALIAS:
        return today + _dt.timedelta(days=_DATE_ALIAS[key])
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", key)
    if m:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})$", key)
    if m:
        return _dt.date(today.year, int(m.group(1)), int(m.group(2)))
    raise ValueError(
        f"无法解析日期 {raw!r}。支持：今天/明天/后天/大后天、YYYY-MM-DD、MM-DD。"
        "「周六」这类相对星期请先自行换算成具体日期再传入。"
    )


def build_report(location: str, date_str: str | None, days: int = 3,
                 water_type: str | None = None, offline: bool = False) -> dict:
    today = _dt.date.today()
    target = parse_date(date_str, today)
    warnings: list[str] = []

    lunar = solar_to_lunar(target)
    phase = moon_phase(lunar)
    season = season_of(target)

    report = {
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "location_query": location,
        "target_date": target.isoformat(),
        "weekday_cn": "周" + "一二三四五六日"[target.weekday()],
        "days_ahead": (target - today).days,
        "lunar": lunar,
        "moon": phase,
        "season": season,
        "ban_screening": ban_screening(location, water_type),
        "weather": None,
        "warnings": warnings,
    }

    if offline:
        warnings.append("已指定 --no-network，未获取天气数据。请要求用户提供气温/气压/风力/降水。")
        return report

    wx = fetch_weather(location)
    if not wx["ok"]:
        warnings.append(
            f"天气数据获取失败（{wx['error']}）。已降级："
            "不再向用户索要全量气象数据，改为要求用户描述「所在地 + 天气 + 气温 + 风力」后继续评估。"
        )
        report["weather"] = {"ok": False, "error": wx["error"], "url": wx["url"]}
        return report

    report["location_resolved"] = wx["resolved"]
    if not wx["resolved"] or wx["resolved"] == location:
        warnings.append(f"地点「{location}」可能未被精确解析，请向用户确认到「市/区/县」级别。")

    # 挑出目标日期及其后 days-1 天
    day_map = {d["date"]: d for d in wx["days"]}
    target_key = target.isoformat()
    forecast_days = [d for d in wx["days"] if d["date"] and d["date"] >= target_key][:days]

    if not forecast_days:
        warnings.append(
            f"wttr.in 仅提供未来 3 天预报，目标日期 {target_key} 超出预报范围。"
            "请改用气候平均值 + 用户描述，或改问近 3 天内的日期。"
        )

    report["weather"] = {
        "ok": True,
        "source": "wttr.in (World Weather Online)",
        "resolved": wx["resolved"],
        "current": wx["current"],
        "forecast_days": forecast_days,
        "forecast_coverage": [d["date"] for d in wx["days"]],
    }

    # 交叉校验：wttr 自带月相 vs 本地农历推算
    if forecast_days:
        w_illum = forecast_days[0].get("moon_illumination_wttr")
        if isinstance(w_illum, (int, float)) and abs(w_illum - phase["illumination_pct"]) > 25:
            warnings.append(
                f"月相交叉校验不一致：本地农历推算照度 {phase['illumination_pct']}%，"
                f"wttr.in 给出 {w_illum}%。两者取一使用，并在输出中说明来源。"
            )

    # 安全预扫描：直接给出确定性否决项，避免模型漏判
    flags = []
    for d in forecast_days:
        for h in d["hourly"]:
            if (h.get("wind_force") or 0) >= 6:
                flags.append(f"{d['date']} {h['time']} 风力达 {h['wind_force']} 级（≥6 级，安全否决）")
            if (h.get("thunder_chance_pct") or 0) >= 40:
                flags.append(f"{d['date']} {h['time']} 雷暴概率 {h['thunder_chance_pct']}%（强对流风险）")
    report["safety_preflags"] = flags[:12]

    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="淡水野钓环境数据采集器")
    ap.add_argument("--location", "-l", required=True, help="地点/水域名称，如 千岛湖")
    ap.add_argument("--date", "-d", default=None, help="目标日期：今天/明天/后天/YYYY-MM-DD/MM-DD")
    ap.add_argument("--days", "-n", type=int, default=3, help="输出预报天数，默认 3（wttr.in 上限）")
    ap.add_argument("--water-type", "-w", default=None, help="水域类型，用于禁钓关键词预筛")
    ap.add_argument("--no-network", action="store_true", help="跳过网络请求，仅输出天文/季节/法规预筛")
    ap.add_argument("--json", action="store_true", help="仅输出 JSON（默认也是 JSON，此参数保留兼容）")
    args = ap.parse_args(argv)

    try:
        report = build_report(args.location, args.date, args.days, args.water_type, args.no_network)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
