"""快进到指定页，保存 cursor"""
import json, os, sys, time, requests
from pathlib import Path

AID = "114568202297147"
BVID = "BV1WQjuz4EzZ"
DATA_DIR = Path("data")

# 加载 e nv
env_file = Path(".env")
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": f"https://www.bilibili.com/video/{BVID}",
}

# Cookie pool
pair = {}
for k, v in os.environ.items():
    if k.startswith("BILIBILI_SESSDATA"):
        idx = k.replace("BILIBILI_SESSDATA", "")
        pair.setdefault(idx, {})["sess"] = v
    elif k.startswith("BILIBILI_JCT"):
        idx = k.replace("BILIBILI_JCT", "")
        pair.setdefault(idx, {})["jct"] = v
pool = []
for idx in sorted(pair.keys(), key=lambda x: (x == "", x)):
    p = pair[idx]
    if p.get("sess") and p.get("jct"):
        pool.append((p["sess"], p["jct"]))

pool_idx = 0
def next_cookie():
    global pool_idx
    c = pool[pool_idx % len(pool)]
    pool_idx = (pool_idx + 1) % len(pool)
    return c

# 读取当前状态
state_file = DATA_DIR / ".pipeline_state.json"
state = json.load(open(state_file))
cursor = state.get("next_offset", "64810")
start_page = state.get("last_page", 40)
target_page = int(sys.argv[1]) if len(sys.argv) > 1 else 350

print(f"快进: page {start_page} -> {target_page}, cursor={cursor}")
print(f"Cookie池: {len(pool)} 组")

fetched_total = 0
current_page = start_page

while current_page < target_page:
    sess, _ = next_cookie()
    params = {"oid": AID, "type": "1", "mode": "2", "ps": "20", "next": str(cursor), "sort": "1"}
    try:
        resp = requests.get(
            "https://api.bilibili.com/x/v2/reply/main",
            params=params,
            cookies={"SESSDATA": sess},
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code == 412:
            continue  # rotate cookie
        data = resp.json()
        if data.get("code") != 0:
            print(f"  API error code={data.get('code')} at page {current_page}, retry...")
            time.sleep(1)
            continue
        page_data = data.get("data", {})
        replies = page_data.get("replies", []) or []
        new_cursor = page_data.get("cursor", {}).get("next", 0)
        is_end = page_data.get("cursor", {}).get("is_end", False)

        if is_end:
            print(f"  Page {current_page}: END, 停止")
            break

        current_page += 1
        count = len(replies)
        fetched_total += count
        old_cursor = cursor
        cursor = new_cursor

        if current_page % 10 == 0:
            print(f"  page {current_page}: {count}条, cursor {old_cursor} -> {cursor}, total={fetched_total}")

    except Exception as e:
        print(f"  Page {current_page} error: {e}, retry...")
        time.sleep(1)

# 保存状态
state["last_page"] = current_page
state["next_offset"] = str(cursor)
state["total_fetched"] = current_page * 20  # 粗略
json.dump(state, open(state_file, "w"), ensure_ascii=False, indent=2)
print(f"\n完成! last_page={current_page}, next_offset={cursor}, fetched={fetched_total}")
