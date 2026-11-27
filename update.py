import os
import re
import sys
import time
import gzip
import urllib.request
import concurrent.futures
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

USER_AGENT = "Mozilla/5.0"
TIMEOUT = 10
MAX_STREAMS_PER_CHANNEL = 3

SOURCE_FILES = [
    "s1_k9m2.m3u",
    "s2_p4x8.m3u",
    "s3_v7n1.m3u",
    "s5_1964n.m3u",
    "s4_z5q3.m3u",
]
OUTPUT_PLAYLIST = "pl_9x8k2m.m3u"

ALIASES = {
    "megogo спорт": ["megogo sport"],
    "окко прайм спорт": ["okko prime sport", "окко спорт прайм", "okko sport prime"],
    "окко спорт": ["okko sport"],
    "box sportcast": ["sportcast", "box sportcast 1", "бокс спорткаст"],
    "9-1-1": ["911", "911 служба спасения", "служба спасения", "служба спасения 911"],
    "history 2": ["viju history 2", "viju+ history 2", "viasat history 2", "h2", "хистори 2"],
    "viju tv1000": ["viju tv 1000", "tv1000", "tv 1000"],
    "viju tv1000 action": ["viju tv 1000 action", "tv1000 action", "tv 1000 action"],
    "viju tv1000 русское": ["viju tv 1000 русское", "tv1000 русское", "tv 1000 русское"],
    "cineman air crash": ["cineman aircrash", "cineman авиакатастрофы"],
    "z!russian": ["z русское", "z кинозал русское", "кинозал z russian"],
    "z!comedy": ["z комедия", "z кинозал комедия", "кинозал z comedy"],
    "z!thriller": ["z триллер", "z кинозал триллер", "кинозал z thriller"],
    "z!action": ["z боевик", "z кинозал боевик", "кинозал z action"],
    "z!adventure": ["z приключения", "z кинозал приключения", "кинозал z adventure"],
    "z!detective": ["z детектив", "z кинозал детектив", "кинозал z detective"],
    "z!fantastic": ["z фантастика", "z кинозал фантастика", "кинозал z fantastic"],
    "z!science": ["z science", "z наука", "z! наука", "z кинозал наука", "кинозал z science"],
    "z!travel": ["z путешествия", "z кинозал путешествия", "кинозал z travel"],
    "euronews россия": ["euronews", "euronews russia", "euronews по-русски", "евроньюс", "евроньюс россия"],
    "центральное телевидение": ["центральное тв", "цтв"],
    "инфоканал": ["инфо канал", "инфоканал триколор", "нтв+ инфоканал", "промо тв", "promo"],
    "bcu action": ["bcu боевик", "bcu экшн"],
    "bcu catastrophe": ["bcu катастрофы", "bcu катастрофа"],
    "box kosmo": ["box cosmo", "box космос", "бокс космо"],
    "box history": ["box история", "бокс хистори"],
    "travelxp": ["travel xp", "travelxp europe", "travelxp de"],
}


def read_grouped_channels(filename):
    channels = []
    current_group = "Общие"
    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_group = line.strip("[]").strip()
            else:
                channels.append((line.lower(), current_group))
    return channels


def has_timezone_shift(name):
    return bool(re.search(r'[\s\(\[][\+\-]\d{1,2}[\)\]]?\b', name))


POZN_BLOCKED_RE = re.compile(
    r'(?<![a-zа-яё0-9])('
    r'4k\s*hdr|4k|uhd|ultra\s*hd|'
    r'fhd\s*(?:orig|oric|ориг|ариг|aric)|full\s*hd\s*(?:orig|oric|ориг|ариг|aric)|'
    r'fhd|full\s*hd|'
    r'uk|pl|es|md|bg|az|pt|de|kz'
    r')(?![a-zа-яё0-9])',
    re.IGNORECASE,
)


def is_blocked_pozn(name):
  return bool(POZN_BLOCKED_RE.search(name))


def is_unsupported_format(name, allow_all_formats=False, is_history2=False):
  if allow_all_formats:
    return False
  n = name.lower()

  if is_history2:
    if re.search(
        r'\b(full\s*hd|fhd|4k|uhd|ultra\s*hd|50\s*fps|60\s*fps|hdr)\b', n
    ):
      return True
    return not bool(re.search(r'\bhd(?:\s*(?:orig|oric|ориг|ариг|aric))?\b', n))

  return bool(
      re.search(
          r'\b(full\s*hd|fhd|orig|oric|ориг|ариг|aric|4k|uhd|ultra\s*hd|50\s*fps|60\s*fps|hdr)\b',
          n,
      )
  )


def clean_title(name, allow_all_formats=False):
  name = name.lower().strip()
  name = re.sub(r'[\(\[].*?[\)\]]', ' ', name)
  if allow_all_formats:
    name = re.sub(
        r'\b(full\s*hd|fhd|orig|oric|ориг|ариг|aric|4k|uhd|ultra\s*hd|50\s*fps|hd|sd|1080p?|720p?|russia|rus|ru|by|ua)\b',
        ' ',
        name,
    )
  else:
    name = re.sub(
        r'\b(hd|sd|orig|oric|ориг|ариг|aric|1080p?|720p?|60\s*fps|hdr|russia|rus|ru|by|ua)\b',
        ' ',
        name,
    )
  name = re.sub(r'[^\w\sа-яёa-z0-9]', ' ', name)
  name = re.sub(r'\btv\b', 'тв', name)
  return re.sub(r'\s+', ' ', name).strip()



def is_channel_match(wanted, channel_name, allow_all_formats=False):
    c_clean = clean_title(channel_name, allow_all_formats)
    if not c_clean:
        return False
    for variant in [wanted] + ALIASES.get(wanted, []):
        w_clean = clean_title(variant, allow_all_formats)
        if w_clean and (w_clean == c_clean or c_clean == f"{w_clean} тв"):
            return True
    return False


def read_pinned_streams(filename, wanted_list):
    pinned = {}
    if not os.path.exists(filename):
        return pinned
    with open(filename, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#EXTM3U")]
    current_info = ""
    for line in lines:
        if line.startswith("#EXTINF"):
            current_info = line
        elif line.startswith(("http://", "https://")) and current_info:
            raw_name = current_info.rsplit(",", 1).pop().strip().lstrip("★").strip()
            name_lower = raw_name.lower()
            for idx, (w, grp) in enumerate(wanted_list):
                if is_channel_match(w, name_lower, allow_all_formats=True):
                    pinned.setdefault(idx, []).append((grp, current_info, line))
                    break
            current_info = ""
    return pinned
def normalize_name(name):
    name = name.lower().strip()
    name = re.sub(r'[\(\[].*?[\)\]]', '', name)
    name = re.sub(r'[!\.\-\+:]', ' ', name)
    name = re.sub(r'\b(full\s*hd|fhd|hd|sd|4k|uhd|1080p?|720p?|50\s*fps|60\s*fps|hdr|orig|russia|rus|тв|tv)\b', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def load_epg_data(epg_url, retries=3, delay=5):
    epg_map = {}
    for attempt in range(1, retries + 1):
        try:
            print(f"Загрузка базы EPG (попытка {attempt}/{retries})...")
            req = urllib.request.Request(epg_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as resp:
                xml_data = gzip.decompress(resp.read())
            root = ET.fromstring(xml_data)
            for ch in root.findall("channel"):
                ch_id = (ch.get("id") or "").strip()
                icon_el = ch.find("icon")
                icon_src = (icon_el.get("src") or "").strip() if icon_el is not None else ""
                for dn in ch.findall("display-name"):
                    if dn.text:
                        key = normalize_name(dn.text)
                        if key:
                            old_id, old_logo = epg_map.get(key, ("", ""))
                            if not old_logo and icon_src:
                                epg_map[key] = (ch_id or old_id, icon_src)
                            elif key not in epg_map:
                                epg_map[key] = (ch_id, icon_src)
            print(f"База EPG загружена: {len(epg_map)} каналов с иконками.")
            return epg_map
        except Exception as e:
            print(f"Ошибка загрузки EPG (попытка {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(delay)
    print("Не удалось загрузить иконки из EPG после 3 попыток.")
    return epg_map


def get_quality_score(name):
    name_low = name.lower()
    if re.search(r'\b(1080p?|720p?|hd)\b', name_low):
        return 1
    if re.search(r'\bsd\b', name_low):
        return 3
    return 2


def has_archive(info):
    info_low = info.lower()
    return any(tag in info_low for tag in ('catchup=', 'catchup-days=', 'timeshift='))


def stream_sort_key(item):
    grp, info, url, ping = item
    title = info.rsplit(",", 1).pop().strip()
    archive_priority = 0 if has_archive(info) else 1
    return (archive_priority, get_quality_score(title), ping)


def is_valid_ts_data(data):
    if not data or len(data) < 1880:
        return False
    head = data.split(b"\n", 1).pop(0).lstrip().lower()
    if head.startswith((b"<!doctype", b"<html", b"404", b"403", b"error", b"no signal")):
        return False
    sync_pos = data.find(0x47)
    if sync_pos == -1:
        return False
    mv = memoryview(data)
    packets = 0
    for pos in range(sync_pos, len(data) - 4, 188):
        if mv.__getitem__(pos) != 0x47:
            return False
        pid = ((mv.__getitem__(pos + 1) & 0x1F) << 8) | mv.__getitem__(pos + 2)
        scrambling = (mv.__getitem__(pos + 3) & 0xC0) >> 6
        has_payload = (mv.__getitem__(pos + 3) & 0x10) != 0
        if 0x0020 <= pid < 0x1FFF and scrambling == 0 and has_payload:
            packets += 1
            if packets >= 3:
                return True
    return False


def fetch_bytes(url, max_bytes=16384, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            return b""
        return resp.read(max_bytes)


def check_stream(item):
    src_name, idx, key, group, info, url = item
    start = time.time()
    try:
        data = fetch_bytes(url, max_bytes=8192)
        if not data:
            return None

        if data.lstrip().startswith(b"#EXTM3U"):
            current_url = url
            text = data.decode("utf-8", errors="ignore")

            for _ in range(2):
                sub_links = [
                    line.strip() for line in text.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
                if not sub_links:
                    return None
                next_url = urljoin(current_url, sub_links.pop(0))
                if ".m3u8" in next_url.lower() and next_url != current_url:
                    sub_data = fetch_bytes(next_url, max_bytes=8192)
                    if not sub_data or not sub_data.lstrip().startswith(b"#EXTM3U"):
                        return None
                    current_url = next_url
                    text = sub_data.decode("utf-8", errors="ignore")
                else:
                    seg_data = fetch_bytes(next_url, max_bytes=16384)
                    if is_valid_ts_data(seg_data):
                        return (src_name, idx, key, group, info, url, time.time() - start)
                    return None
            return None

        if is_valid_ts_data(data):
            return (src_name, idx, key, group, info, url, time.time() - start)
    except Exception:
        pass
    return None

def classify_sport_stream(name):
    n = name.lower()
    # 1. Явные пометки 720 -> строго в 5-й слот
    if '720' in n:
        return "slot5_720"
    # 2. Full HD, HD 50 (50/60 fps), Aric, Orig -> строго в 1-й слот
    if re.search(r'\b(full\s*hd|fhd|1080p?|50\s*fps|60\s*fps|hd\s*50|orig|ориг|ариг|aric)\b', n):
        return "slot1_only"
    # 3. Чистый HD (25 fps) -> в 2-4 слоты (или в 1-й, если нет Full HD / HD 50 / Aric)
    if re.search(r'\bhd\b', n):
        return "hd_general"
    # 4. Лёгкий поток без приставок (например, просто "Матч Премьер") -> в 5-й слот
    return "slot5_plain"

def select_channel_streams(streams, group_name=""):
    streams.sort(key=stream_sort_key)
    unique = []
    seen_urls = set()
    for s in streams:
        grp_val, info_val, url_val, ping_val = s
        if url_val not in seen_urls:
            seen_urls.add(url_val)
            unique.append(s)

    grp_low = re.sub(r'^[^\wа-яёa-z0-9]+', '', group_name.lower()).strip()

    # Логика для спортивных каналов (5 слотов)
    if grp_low == "спортивные":
        slot1_only = []
        hd_general = []
        slot5_720 = []
        slot5_plain = []

        for s in unique:
            title_val = s[1].rsplit(",", 1).pop().strip()
            cat = classify_sport_stream(title_val)
            if cat == "slot1_only":
                slot1_only.append(s)
            elif cat == "hd_general":
                hd_general.append(s)
            elif cat == "slot5_720":
                slot5_720.append(s)
            else:
                slot5_plain.append(s)

        first = None
        if slot1_only:
            first = slot1_only.pop(0)
        elif hd_general:
            first = hd_general.pop(0)

        middle = hd_general[:3]

        fifth = None
        if slot5_plain:
            fifth = slot5_plain.pop(0)
        elif slot5_720:
            fifth = slot5_720.pop(0)

        result = []
        if first:
            result.append(first)
        result.extend(middle)
        if fifth:
            result.append(fifth)

        if not result and unique:
            result.append(unique[0])

        return result[:5]

    # Логика для группы "Эфирные" / "Эфирная" (до 3 HD + 1 лёгкий, но минимум 3 потока при наличии)
    if grp_low in ("эфирные", "эфирная"):
        hd_streams = []
        light_720 = []
        light_plain = []

        for s in unique:
            title_val = s[1].rsplit(",", 1).pop().strip().lower()
            if '720' in title_val:
                light_720.append(s)
            elif re.search(r'\b(1080p?|hd)\b', title_val):
                hd_streams.append(s)
            else:
                light_plain.append(s)

        result = hd_streams[:3]

        # 4-й слот: лёгкий поток (сначала обычный без HD, если нет — 720p)
        if light_plain:
            result.append(light_plain.pop(0))
        elif light_720:
            result.append(light_720.pop(0))

        # Если потоков меньше 3 (например, нет HD как у "Оружия" или только 1 HD как у "Санкт-Петербурга"),
        # добиваем до 3 потоков из оставшихся рабочих (сначала обычные, затем 720p, затем оставшиеся HD)
        fallback_pool = light_plain + light_720 + hd_streams[3:]
        for s in fallback_pool:
            if len(result) >= 3:
                break
            result.append(s)

        return result[:4]

    # Все остальные группы — как раньше (3 потока)
    return unique[:MAX_STREAMS_PER_CHANNEL]


def main():
    epg_host = "raw.githubusercontent.com"
    epg_path = "Lorax121/epg_v2/main/data/epg2.xml.gz"
    epg_url = f"https://{epg_host}/{epg_path}"

    epg_map = load_epg_data(epg_url, retries=3, delay=5)
    wanted_list = read_grouped_channels("channels.txt")
    pinned_by_idx = read_pinned_streams("pinned.m3u", wanted_list)

    candidates = []
    blocked_by_format = {}
    tested_per_source = {src: 0 for src in SOURCE_FILES}
    alive_per_source = {src: 0 for src in SOURCE_FILES}

    for src in SOURCE_FILES:
        if not os.path.exists(src):
            print(f"⚠️ Файл {src} не найден в репозитории, пропускаем.")
            continue
        with open(src, "r", encoding="utf-8", errors="ignore") as f:
            lines = [line.strip() for line in f if line.strip()]
        current_info = ""
        for line in lines:
            if line.startswith("#EXTINF"):
                current_info = line
            elif line.startswith(("http://", "https://")) and current_info:
                raw_name = current_info.rsplit(",", 1).pop().strip()
                if not has_timezone_shift(raw_name):
                    name_lower = raw_name.lower()
                    for idx, (w, grp) in enumerate(wanted_list):
                      grp_low = re.sub(r'^[\W_]+', '', grp.lower())
                      is_kz = grp_low == "кинозалы"
                      is_sport = grp_low == "спортивные"
                      is_pozn = grp_low == "познавательные"
                      is_history2 = w == "history 2"

                      allow_all = (
                          is_kz
                          or is_sport
                          or w.startswith(("z!", "cineman", "bcu ", "box "))
                          or w == "travelxp"
                      )

                      if is_channel_match(w, name_lower, allow_all):
                        if is_pozn and is_blocked_pozn(raw_name):
                          blocked_by_format.setdefault(idx, set()).add(raw_name)
                        elif not is_unsupported_format(
                            raw_name,
                            allow_all_formats=allow_all,
                            is_history2=is_history2,
                        ):
                          candidates.append(
                              (src, idx, w, grp, current_info, line)
                          )
                          tested_per_source[src] += 1
                        else:
                          blocked_by_format.setdefault(idx, set()).add(raw_name)
                        break

                current_info = ""

    valid_by_idx = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        for res in executor.map(check_stream, candidates):
            if res:
                src_name, idx, key, grp, info, url, ping = res
                alive_per_source[src_name] += 1
                valid_by_idx.setdefault(idx, []).append((grp, info, url, ping))

    for idx in valid_by_idx:
        _, grp_name = wanted_list[idx]
        valid_by_idx[idx] = select_channel_streams(valid_by_idx[idx], grp_name)


    seen_names = {}
    total_streams = 0
    found_indices = set()

    with open(OUTPUT_PLAYLIST, "w", encoding="utf-8") as out:
        out.write(f'#EXTM3U url-tvg="{epg_url}" catchup="shift" catchup-days="5"\n')

        for idx, (w, grp) in enumerate(wanted_list):
            p_streams = pinned_by_idx.get(idx, [])
            a_streams = valid_by_idx.get(idx, [])
            if not p_streams and not a_streams:
                continue

            found_indices.add(idx)
            pinned_urls = {u for _, _, u in p_streams}
            combined = [(g, i, u, True) for g, i, u in p_streams]
            for g, i, u, _ in a_streams:
                if u not in pinned_urls:
                    combined.append((g, i, u, False))

            best_id = ""
            best_logo = ""
            for _, s_info, _, _ in combined:
                if not best_id:
                    m_id = re.search(r'tvg-id="([^"]+)"', s_info)
                    if m_id:
                        best_id = m_id.group(1)
                if not best_logo:
                    m_logo = re.search(r'tvg-logo="([^"]+)"', s_info)
                    if m_logo:
                        best_logo = m_logo.group(1)

            epg_id, epg_logo = epg_map.get(normalize_name(w), ("", ""))
            if not epg_id or not epg_logo:
                for _, s_info, _, _ in combined:
                    s_raw = s_info.rsplit(",", 1).pop().strip().lstrip("★").strip()
                    s_id, s_logo = epg_map.get(normalize_name(s_raw), ("", ""))
                    if not epg_id and s_id:
                        epg_id = s_id
                    if not epg_logo and s_logo:
                        epg_logo = s_logo

            final_id = epg_id or best_id
            final_logo = epg_logo or best_logo

            for group_name, info, url, is_pinned in combined:
                total_streams += 1
                raw_name = info.rsplit(",", 1).pop().strip().lstrip("★").strip()
                base_name = f"★ {raw_name}" if is_pinned else raw_name
                name_key = base_name.lower()
                seen_names[name_key] = seen_names.get(name_key, 0) + 1
                final_name = f"{base_name} ({seen_names[name_key]})" if seen_names[name_key] > 1 else base_name

                catchup_tags = ""
                for tag in ("catchup", "catchup-days", "catchup-source", "timeshift"):
                    m_tag = re.search(rf'{tag}="([^"]+)"', info)
                    if m_tag:
                        catchup_tags += f' {tag}="{m_tag.group(1)}"'

                id_part = f' tvg-id="{final_id}"' if final_id else ""
                logo_part = f' tvg-logo="{final_logo}"' if final_logo else ""
                clean_info = f'#EXTINF:-1{id_part}{logo_part}{catchup_tags} group-title="{group_name}",{final_name}'
                out.write(f"{clean_info}\n#EXTGRP:{group_name}\n{url}\n")

    print("\n" + "=" * 45)
    print("🩺 ЗДОРОВЬЕ ИСТОЧНИКОВ iLook")
    print("=" * 45)
    dead_sources = []
    for src in SOURCE_FILES:
        tested = tested_per_source[src]
        alive = alive_per_source[src]
        if tested == 0:
            print(f"⚪ {src} — файл пуст или не загружен")
        elif alive == 0:
            print(f"❌ {src} — УПАЛ! (0 из {tested} потоков — пора менять лист или ключ!)")
            dead_sources.append(src)
        else:
            print(f"✅ {src} — ЖИВОЙ (рабочих потоков: {alive} из {tested})")

    missing = []
    for idx, (w, grp) in enumerate(wanted_list):
        if idx not in found_indices:
            reason = ""
            if idx in blocked_by_format:
                reason = f" — отсеян фильтром формата ({', '.join(list(blocked_by_format[idx])[:2])})"
            missing.append(f"  - {w} (группа: {grp}){reason}")

    print("\n" + "=" * 45)
    print("📊 ОТЧЁТ О СБОРКЕ ПЛЕЙЛИСТА")
    print("=" * 45)
    print(f"✅ Найдено каналов: {len(found_indices)} из {len(wanted_list)}")
    print(f"📌 Закреплено потоков: {sum(len(v) for v in pinned_by_idx.values())}")
    print(f"📺 Всего рабочих потоков: {total_streams}")
    if missing:
        print(f"❌ Не найдено каналов ({len(missing)}):")
        for m in missing:
            print(m)
    else:
        print("🎉 Отлично! Найдены абсолютно все каналы из списка!")
    print("=" * 45 + "\n")



if __name__ == "__main__":
    main()

