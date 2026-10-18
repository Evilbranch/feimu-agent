"""联网搜索 + 天气 - 独立工具（Tavily 后端 + 缓存）"""
import os
import json
import time
import hashlib

import requests

from core.network import is_online
from core.constants import (TAVILY_API_KEY, SEARCH_CACHE_FILE,
    SEARCH_CACHE_TTL, SEARCH_MAX_RESULTS)


# ==================== 缓存 ====================
def _cache_key(query):
    return hashlib.md5(query.encode("utf-8")).hexdigest()[:16]


def _load_cache():
    if not os.path.exists(SEARCH_CACHE_FILE):
        return {}
    try:
        with open(SEARCH_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    try:
        os.makedirs(os.path.dirname(SEARCH_CACHE_FILE), exist_ok=True)
        with open(SEARCH_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[搜索缓存] 写失败: {e}")


def _cache_get(query):
    entry = _load_cache().get(_cache_key(query))
    if entry and time.time() - entry.get("ts", 0) < SEARCH_CACHE_TTL:
        return entry.get("result")
    return None


def _cache_set(query, result):
    cache = _load_cache()
    now = time.time()
    cache = {k: v for k, v in cache.items()
             if now - v.get("ts", 0) < SEARCH_CACHE_TTL}
    cache[_cache_key(query)] = {"ts": now, "query": query, "result": result}
    if len(cache) > 200:
        cache = dict(sorted(cache.items(),
                             key=lambda x: -x[1].get("ts", 0))[:200])
    _save_cache(cache)


# ==================== Tavily 搜索 ====================
def _tavily_search(query):
    if not TAVILY_API_KEY or "填入" in TAVILY_API_KEY:
        print("[搜索] Tavily API Key 未配置")
        return None
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "max_results": SEARCH_MAX_RESULTS,
                "include_answer": True,
                "include_raw_content": False,
            },
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[搜索] Tavily HTTP {r.status_code}: {r.text[:100]}")
            return None
        data = r.json()
    except Exception as e:
        print(f"[搜索] Tavily 异常: {e}")
        return None

    answer = (data.get("answer") or "").strip()
    results = data.get("results", [])
    if not answer and not results:
        return None

    lines = []
    if answer:
        lines.append(answer)
    for r in results[:SEARCH_MAX_RESULTS]:
        title = (r.get("title") or "").strip()
        content = (r.get("content") or "").strip()
        if not title and not content:
            continue
        if len(content) > 120:
            content = content[:120] + "…"
        if title and content:
            lines.append(f"{title}：{content}")
        elif title:
            lines.append(title)

    return "\n".join(lines) if lines else None


# ==================== 对外接口 ====================
def web_search(query):
    """联网搜索。带缓存。返回字符串或 None。"""
    if not query:
        return None
    if not is_online():
        return None

    cached = _cache_get(query)
    if cached:
        print(f"[搜索] 命中缓存: {query}")
        return cached

    print(f"[搜索] {query}")
    t0 = time.time()
    result = _tavily_search(query)
    if result:
        _cache_set(query, result)
        print(f"[搜索] 完成 耗时 {time.time()-t0:.2f}s")
    else:
        print(f"[搜索] 无结果 耗时 {time.time()-t0:.2f}s")
    return result


# ==================== 天气（wttr.in） ====================
WEATHER_ZH = {
    "Sunny": "晴", "Clear": "晴", "Partly cloudy": "局部多云",
    "Cloudy": "多云", "Overcast": "阴", "Mist": "薄雾",
    "Fog": "雾", "Freezing fog": "冻雾", "Patchy rain possible": "可能有雨",
    "Patchy rain nearby": "附近有雨", "Light rain": "小雨",
    "Moderate rain": "中雨", "Heavy rain": "大雨",
    "Light drizzle": "毛毛雨", "Patchy light drizzle": "零星毛毛雨",
    "Light rain shower": "阵雨", "Moderate or heavy rain shower": "大阵雨",
    "Thundery outbreaks possible": "可能雷雨", "Thundery outbreaks nearby": "附近雷雨",
    "Light snow": "小雪", "Moderate snow": "中雪", "Heavy snow": "大雪",
    "Blizzard": "暴雪", "Blowing snow": "风雪",
    "Smoky haze": "烟霾", "Haze": "霾", "Smoke": "烟",
    "Dust": "浮尘", "Sand": "沙尘", "Sandstorm": "沙尘暴",
    "Snow": "雪", "Sleet": "雨夹雪", "Ice pellets": "冰粒",
    "Patchy snow possible": "可能有雪", "Light freezing rain": "冻雨",
    "Patchy light rain with thunder": "雷阵雨",
    "Moderate or heavy rain with thunder": "强雷雨",
}

WIND_DIR_ZH = {
    "N": "北", "NNE": "东北偏北", "NE": "东北", "ENE": "东北偏东",
    "E": "东", "ESE": "东南偏东", "SE": "东南", "SSE": "东南偏南",
    "S": "南", "SSW": "西南偏南", "SW": "西南", "WSW": "西南偏西",
    "W": "西", "WNW": "西北偏西", "NW": "西北", "NNW": "西北偏北",
    "C": "无", "Calm": "无",
}


def get_weather(city="杭州"):
    """查询天气。返回一句中文描述或 None。"""
    if not is_online():
        return None
    if not city:
        city = "杭州"

    cache_key = f"weather::{city}"
    cached = _cache_get(cache_key)
    if cached:
        print(f"[天气] 命中缓存: {city}")
        return cached

    try:
        url = f"https://wttr.in/{requests.utils.quote(city)}?format=j1"
        r = requests.get(url, timeout=15, headers={"User-Agent": "curl"})
        if r.status_code != 200:
            print(f"[天气] HTTP {r.status_code}")
            return None
        data = r.json()
        cur = data["current_condition"][0]

        desc_zh = ""
        if cur.get("lang_zh"):
            desc_zh = cur["lang_zh"][0].get("value", "")
        if not desc_zh:
            desc_en = cur.get("weatherDesc", [{}])[0].get("value", "").strip()
            desc_zh = WEATHER_ZH.get(desc_en, desc_en)

        temp_c = cur.get("temp_C", "?")
        feels = cur.get("FeelsLikeC", "?")
        humidity = cur.get("humidity", "?")
        wind_kmph = cur.get("windspeedKmph", "?")
        wind_dir_en = cur.get("winddir16Point", "")
        wind_dir = WIND_DIR_ZH.get(wind_dir_en, wind_dir_en)

        today = data.get("weather", [{}])[0]
        max_t = today.get("maxtempC", "?")
        min_t = today.get("mintempC", "?")

        result = (
            f"{city}当前{desc_zh}，温度{temp_c}℃（体感{feels}℃），"
            f"今日{min_t}~{max_t}℃，湿度{humidity}%，{wind_dir}风{wind_kmph}km/h。"
        )
        print(f"[天气] {result}")
        _cache_set(cache_key, result)
        return result
    except Exception as e:
        print(f"[天气] 异常: {e}")
        return None