"""
Pipeline: 从新到旧遍历评论 → AI分析 → 自动举报
每轮拉取 20 页（cursor 推进），举报间隔 120s，-352 静默 300s
"""
import json, os, time, sys, random, re
from datetime import datetime, timezone, timedelta
CST = timezone(timedelta(hours=8))

import requests
from pathlib import Path

BVID = "BV1WQjuz4EzZ"
try:
    from bilibili_api import bvid2aid
    AID = str(bvid2aid(BVID))
except Exception:
    AID = "114568202297147"

DATA_DIR = Path("data")
PIPE_DIR = DATA_DIR / "pipeline"
DATA_FILE = DATA_DIR / "flagged.json"
CHECKED_FILE = DATA_DIR / ".checked.json"
REPORTED_FILE = PIPE_DIR / ".reported.json"
TRACKING_FILE = PIPE_DIR / "report_tracking.json"
FAILED_FILE = PIPE_DIR / ".failed_reports.json"
PIPELINE_STATE = DATA_DIR / ".pipeline_state.json"
HTML_FILE = "index.html"
USERS_FILE = "users.html"
AVATAR_CACHE = {}  # lazy init

DEEPSEEK_KEY = os.environ["DEEPSEEK_API_KEY"]
AUTO_REPORT = os.environ.get("AUTO_REPORT", "false").lower() in ("1", "true", "yes")

REPORT_INTERVAL = 120
COOLDOWN_352 = 300
API_DOMAINS = ["api.bilibili.com", "api.biliapi.net", "api.biliapi.com"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": f"https://www.bilibili.com/video/{BVID}",
}

# ── Cookie Pool ──

def _build_cookie_pool():
    pair = {}
    for k, v in os.environ.items():
        if k.startswith("BILIBILI_SESSDATA"):
            idx = k.replace("BILIBILI_SESSDATA", "")
            pair.setdefault(idx, {})
            pair[idx]["sess"] = v
        elif k.startswith("BILIBILI_JCT"):
            idx = k.replace("BILIBILI_JCT", "")
            pair.setdefault(idx, {})
            pair[idx]["jct"] = v
    pool = []
    for idx in sorted(pair.keys(), key=lambda x: (x == "", x)):
        p = pair[idx]
        if p.get("sess") and p.get("jct"):
            pool.append((p["sess"], p["jct"]))
    return pool

_COOKIE_POOL = _build_cookie_pool()
_COOKIE_IDX = 0

def _get_cookie():
    if not _COOKIE_POOL:
        return "", ""
    return _COOKIE_POOL[_COOKIE_IDX % len(_COOKIE_POOL)]

def _rotate_cookie():
    global _COOKIE_IDX
    if _COOKIE_POOL:
        _COOKIE_IDX = (_COOKIE_IDX + 1) % len(_COOKIE_POOL)
    return _get_cookie()

# ── Data I/O ──

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_flagged():
    return load_json(DATA_FILE, {"videos": {}, "comments": []})

def save_flagged(data):
    save_json(DATA_FILE, data)

def load_checked():
    c = load_json(CHECKED_FILE, {})
    return c.setdefault(BVID, [])

def save_checked(ids):
    c = load_json(CHECKED_FILE, {})
    c[BVID] = ids
    save_json(CHECKED_FILE, c)

def load_reported():
    d = load_json(REPORTED_FILE, [])
    return set(d) if isinstance(d, list) else set()

def save_reported(s):
    save_json(REPORTED_FILE, list(s))

def load_tracking():
    return load_json(TRACKING_FILE, {})

def save_tracking(t):
    save_json(TRACKING_FILE, t)

def load_failed():
    return load_json(FAILED_FILE, [])

def save_failed(f):
    save_json(FAILED_FILE, f)

def load_state():
    return load_json(PIPELINE_STATE, {"last_page": 0, "next_offset": "0", "total_fetched": 0})

def save_state(s):
    save_json(PIPELINE_STATE, s)

# ── AI Prompts ──

AI_PROMPT = """你是原神社区的内容审核员，负责识别「米家内斗」类低质引战评论。

## 背景
这个评论区聚集了大量原神极端粉丝。他们热爱原神，但对米哈游旗下其他游戏（崩坏：星穹铁道、绝区零）及其玩家群体充满敌意。你的任务是识别他们攻击米家其他游戏的言论，抓出社区内耗的典型样本。

## 标记标准（满足任意一条即标「是」）

### 1. 游戏间拉踩引战
在米哈游游戏之间制造对立、贬低一方抬高另一方：
- 「XX制作组产能不足才要XX帮忙擦屁股」
- 「XX给原神提鞋都不配」
- 用原神的成功去否定崩铁/绝区零，或用崩铁/绝区零的缺点反衬原神
- **用运营数据/商业数据做武器贬低米家其他游戏**
- 号召玩家站队、挑动「你站哪边」式对立

### 2. 牵强附会 / IP殖民
用毫无根据的臆测强行将原神与崩坏系列绑定，借机踩原神

### 3. 阴阳怪气
用假装夸奖、反问句、emoji堆砌等方式暗讽米家其他游戏
**伪理性引战话术**：用「基于事实」「管好自己」等看似理性的措辞包装，实则立稻草人攻击对方玩家群体

### 4. 人身攻击 / 扣帽子
针对米家特定玩家群体的侮辱，使用社群黑称：铁孝子、原批、txg、铁÷、原婴、绝批等

### 5. 恶意KY
在无关场景下反复刷其他游戏的内容

### 6. 外部游戏拉踩米家（特殊规则）
- 吐槽鸣潮本身 → **不标记**
- **用外部游戏当武器攻击崩铁/绝区零** → **标记**

### 7. 对崩铁/绝区零的恶意诋毁
**核心原则：只要是在骂崩铁/绝区零，就标记。宁可多标不可漏标。**
包括：角色设计攻击、游戏品质贬低、玩家地图炮、假装夸奖的反讽、恶意攻击编剧

### 8. 以臆测事实为论据贬低崩铁/绝区零

## 不标记的情况
- 对原神本身的理性批评
- 单纯吐槽鸣潮等外部竞品（不涉及拉踩米家其他游戏）
- 正常负面评价（有理有据、不侮辱）
- 攻击外部游戏以维护原神不是米家内斗
- 日常游戏咨询、进度分享、极度简短

## 输出格式（严格遵守）
- 不标记 → 输出「否」
- 标记 → 输出「是|举报理由」，举报理由10-20字

评论内容："""

AI_REVIEW_PROMPT = """以下评论已被判定为「米家内斗」引战。请判断：这是否属于明显误判？

如果评论属于以下任一 → 输出「是」（确实是误判）：
- 纯个人游戏体验分享
- 评论者明确声明「不涉及拉踩」
- 讨论非米哈游游戏（未拉踩米家）
- 纯角色期待或正常讨论
- 纯链接/纯表情/极度简短

如果不属于以上误判 → 输出「否」
只输出「是」或「否」。"""

# ── AI ──

def _call_ai(model, system_prompt, user_text, max_tokens=400):
    resp = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={
            "Authorization": f"Bearer {DEEPSEEK_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text[:500]},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        },
        timeout=30,
    )
    resp.raise_for_status()
    msg = resp.json()["choices"][0]["message"]
    answer = (msg.get("content") or "").strip()
    if not answer:
        reasoning = (msg.get("reasoning_content") or "").strip()
        if reasoning:
            for ch in reasoning[::-1]:
                if ch == '否':
                    answer = '否'; break
                if ch == '是' and reasoning[reasoning.index(ch)-1] not in '不否':
                    answer = '是'; break
    return answer.strip()

def check_comment(text):
    try:
        answer = _call_ai("deepseek-v4-flash", AI_PROMPT, text, max_tokens=400)
        report_content = None
        if "|" in answer:
            parts = answer.split("|", 1)
            verdict = parts[0].strip()
            report_content = parts[1].strip() if len(parts) > 1 else None
        else:
            verdict = answer.strip()

        if verdict.startswith("否") or verdict == "否":
            return False, f"否|{report_content or '无'}", None
        if not verdict.startswith("是"):
            return False, verdict[:20], None

        HARD_RULE_TERMS = ['txg', '铁孝子', '原批', '铁÷', '原婴', '绝批']
        if any(t in text for t in HARD_RULE_TERMS):
            return True, "硬规则", report_content

        review = _call_ai("deepseek-v4-flash", AI_REVIEW_PROMPT, text, max_tokens=200)
        if review.startswith("是"):
            return False, "复审驳回", None
        else:
            return True, "双判通过", report_content
    except Exception as e:
        print(f"  AI调用失败: {e}")
        return False, str(e), None

# ── Fetch ──

def fetch_pages(state, pages=20):
    all_comments = []
    next_offset = state.get("next_offset", "0")
    fetched_pages = 0

    for _ in range(pages):
        sess, _ = _get_cookie()
        params = {"oid": AID, "type": "1", "mode": "2", "ps": "20", "next": str(next_offset)}
        try:
            resp = requests.get(
                "https://api.bilibili.com/x/v2/reply/main",
                params=params,
                cookies={"SESSDATA": sess},
                headers=HEADERS,
                timeout=15,
            )
            if resp.status_code == 412:
                sess, _ = _rotate_cookie()
                resp = requests.get(
                    "https://api.bilibili.com/x/v2/reply/main",
                    params=params,
                    cookies={"SESSDATA": sess},
                    headers=HEADERS,
                    timeout=15,
                )
            if resp.status_code != 200:
                print(f"  page {state['last_page']+1}: HTTP {resp.status_code}, stop")
                break
            data = resp.json()
            if data.get("code") != 0:
                print(f"  page {state['last_page']+1}: code={data.get('code')}, stop")
                break

            replies = data.get("data", {}).get("replies") or []
            if not replies:
                print(f"  page {state['last_page']+1}: empty, stop")
                break

            for r in replies:
                all_comments.append({
                    "rpid": r["rpid"],
                    "mid": r["member"]["mid"],
                    "avatar": r["member"]["avatar"],
                    "content": r["content"]["message"],
                    "pictures": [p["img_src"] for p in r.get("content", {}).get("pictures", [])],
                    "user": r["member"]["uname"],
                    "level": r["member"].get("level_info", {}).get("current_level", 0),
                    "like": r["like"],
                    "time": datetime.fromtimestamp(r["ctime"], tz=CST).strftime("%Y-%m-%d %H:%M"),
                })

            cursor = data.get("data", {}).get("cursor", {})
            next_offset = cursor.get("next", 0)
            state["last_page"] += 1
            fetched_pages += 1

            if state["last_page"] % 10 == 0:
                print(f"  page {state['last_page']}: {len(replies)}条, cursor={next_offset}, total_fetched={state['total_fetched']+len(all_comments)}")

            if cursor.get("is_end"):
                print(f"  page {state['last_page']}: END reached")
                break

            time.sleep(0.6)
        except Exception as e:
            print(f"  page {state['last_page']+1} error: {e}")
            break

    state["next_offset"] = str(next_offset)
    state["total_fetched"] += len(all_comments)
    return all_comments

# ── Report ──

MAX_QUEUE_SIZE = 5  # 队列达到此数量时只举报不爬取

def do_report(rpid, reason_text):
    sess, jct = _get_cookie()
    if not sess or not jct:
        print("  [report] no cookie, skip")
        return None

    data = {
        "oid": int(AID), "type": 1, "rpid": rpid,
        "reason": 4, "content": reason_text, "csrf": jct,
    }

    for domain_idx in range(len(API_DOMAINS)):
        try:
            r = requests.post(
                f"https://{API_DOMAINS[domain_idx]}{'/x/v2/reply/report'}",
                data=data,
                cookies={"SESSDATA": sess},
                headers=HEADERS,
                timeout=10,
            )
            result = r.json()
            code = result.get("code", -1)
            if code == 0:
                return 0
            elif code == 12022:
                return 12022
            elif code == 12008:
                return 12008
            elif code in (12019, -352):
                continue
            else:
                return code
        except Exception as e:
            print(f"  [report] error: {e}")
            continue

    return -352

def process_queue(state):
    queue = state.get("queue", [])
    if not queue:
        return 0

    print(f"  [queue] {len(queue)} items pending, processing...")
    reported = load_reported()
    removed = 0
    for item in list(queue):
        rpid = item.get("rpid")
        if rpid in reported:
            queue.remove(item)
            removed += 1
            continue

        reason_text = item.get("report_content") or "引战拉踩攻击米家其他游戏"
        code = do_report(rpid, reason_text)
        if code == 0:
            reported.add(rpid)
            queue.remove(item)
            removed += 1
            tracking = load_tracking()
            tracking[str(rpid)] = {
                "user": item.get("user", "?"),
                "content": item.get("content", "")[:100],
                "reason": reason_text[:60],
                "oid": AID,
                "reported_at": datetime.now(CST).isoformat(),
                "checked_at": None,
                "result": "pending",
            }
            save_tracking(tracking)
            print(f"  [queue] OK rpid={rpid} user={item.get('user','?')}, {len(queue)} left")
            _rotate_cookie()
            time.sleep(REPORT_INTERVAL + random.randint(5, 20))
        elif code in (12022, 12008):
            reported.add(rpid)
            queue.remove(item)
            removed += 1
            print(f"  [queue] skip rpid={rpid} (already {'deleted' if code==12022 else 'reported'})")
        elif code == -352:
            print(f"  [queue] -352, cooldown {COOLDOWN_352}s...")
            time.sleep(COOLDOWN_352)
            break  # stop processing queue this run
        else:
            print(f"  [queue] FAIL rpid={rpid} code={code}, keep in queue")
            _rotate_cookie()

    save_reported(reported)
    state["queue"] = queue
    save_state(state)
    return removed

# ── Main ──

MAX_LOOP_REPORTS = 20  # 每轮循环最多举报数，达到后也继续下一轮

def report_batch(flagged_list, state):
    """举报一批标记评论，返回(成功数, -352是否触发)"""
    queue = state.get("queue", [])
    reported = load_reported()
    ok_count = 0
    hit_352 = False

    for c in flagged_list:
        if c["rpid"] in reported:
            continue
        reason_text = c.get("report_content") or "引战拉踩攻击米家其他游戏"
        if len(reason_text) < 2:
            reason_text = "引战拉踩攻击米家其他游戏"

        code = do_report(c["rpid"], reason_text)
        if code == 0:
            reported.add(c["rpid"])
            ok_count += 1
            print(f"  [report] OK rpid={c['rpid']} user={c['user']}")
            tracking = load_tracking()
            tracking[str(c["rpid"])] = {
                "user": c["user"],
                "content": c.get("content", "")[:100],
                "reason": reason_text[:60],
                "oid": AID,
                "reported_at": datetime.now(CST).isoformat(),
                "checked_at": None,
                "result": "pending",
            }
            save_tracking(tracking)
            _rotate_cookie()
            time.sleep(REPORT_INTERVAL + random.randint(5, 20))
        elif code in (12022, 12008):
            reported.add(c["rpid"])
            print(f"  [report] skip rpid={c['rpid']} (already {'deleted' if code==12022 else 'reported'})")
        elif code == -352:
            print(f"  [report] -352, cooldown {COOLDOWN_352}s...")
            time.sleep(COOLDOWN_352)
            hit_352 = True
            break
        else:
            print(f"  [report] FAIL rpid={c['rpid']} code={code}")
            _rotate_cookie()

    # 未上报成功的推入队列
    for c in reversed(flagged_list):
        if c["rpid"] in reported:
            break
        if not any(x.get("rpid") == c["rpid"] for x in queue):
            queue.append(c)

    save_reported(reported)
    state["queue"] = queue
    save_state(state)
    return ok_count, hit_352

def scan_and_flag(state):
    """爬取 20 页 → AI分析 → 返回标记列表"""
    print(f"  Resume: page={state['last_page']}, offset={state['next_offset']}, fetched={state['total_fetched']}")
    comments = fetch_pages(state)

    if not comments:
        print("  No comments, done")
        save_state(state)
        return None

    data = load_flagged()
    if BVID not in data["videos"]:
        data["videos"][BVID] = {
            "title": BVID,
            "first_check": datetime.now(CST).isoformat(),
            "total_checked": 0,
        }

    checked_ids = load_checked()
    new_flagged = []

    for c in comments:
        if len(new_flagged) >= MAX_QUEUE_SIZE:
            print(f"  [limit] {MAX_QUEUE_SIZE} flagged, stop analyzing")
            break

        if c["rpid"] in checked_ids:
            continue
        if any(x.get("rpid") == c["rpid"] for x in data["comments"]):
            checked_ids.append(c["rpid"])
            continue

        if c.get("pictures"):
            checked_ids.append(c["rpid"])
            print(f"  [{c['user']}] {c['content'][:60]}... → ⬜ 图片跳过 [rpid={c['rpid']}]")
            continue

        is_flag, reason, report_content = check_comment(c["content"])
        checked_ids.append(c["rpid"])

        if is_flag:
            print(f"  🚩 [{c['user']}] {c['content'][:60]}... → {reason} [rpid={c['rpid']}]")
            if report_content:
                print(f"      举报理由: {report_content}")
        elif reason and reason.startswith("否|"):
            print(f"  [{c['user']}] {c['content'][:60]}... → ⬜ {reason[3:]} [rpid={c['rpid']}]")
        else:
            print(f"  [{c['user']}] {c['content'][:60]}... → ⬜ [rpid={c['rpid']}]")

        if is_flag:
            entry = {
                **c, "bvid": BVID, "ai_reason": reason,
                "report_content": report_content or "引战拉踩攻击米家其他游戏",
                "detected_at": datetime.now(CST).isoformat(),
            }
            data["comments"].insert(0, entry)
            new_flagged.append(entry)

    data["videos"][BVID]["total_checked"] += len(comments)
    data["videos"][BVID]["last_check"] = datetime.now(CST).isoformat()
    data["last_run"] = datetime.now(CST).strftime("%m-%d %H:%M")

    save_checked(checked_ids)
    save_state(state)

    if new_flagged:
        save_flagged(data)
        print(f"  New flagged: {len(new_flagged)}, total: {len(data['comments'])}")

    return new_flagged

def main():
    print(f"[{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}] Pipeline start (continuous loop), cookie pool: {len(_COOKIE_POOL)}")
    state = load_state()
    state.setdefault("queue", [])
    consecutive_352 = 0

    while True:
        queue = state.get("queue", [])

        # 1. 清空举报队列
        if queue:
            print(f"  [loop] processing {len(queue)} queued reports")
            ok, hit_352 = report_batch(list(queue), state)
            queue = state.get("queue", [])
            if hit_352:
                consecutive_352 += 1
                if consecutive_352 >= 2:
                    print("  [loop] too many -352, save state and exit")
                    save_state(state)
                    return
                continue  # wait cooldown done, retry queue
            else:
                consecutive_352 = 0
            # 如果队列还是没清完（比如只有部分被举报了），继续循环
            if queue:
                continue

        # 2. 队列清空 → 爬取新评论
        flagged = scan_and_flag(state)
        if flagged is None:
            # 没有新评论了
            print(f"  [loop] all comments scanned, page={state['last_page']}, done")
            save_state(state)
            return

        if not flagged:
            # 本轮没标记到 → 继续下一轮爬取
            continue

        # 3. 举报刚标记的
        if AUTO_REPORT:
            ok, hit_352 = report_batch(flagged, state)
            if hit_352:
                consecutive_352 += 1
            else:
                consecutive_352 = 0
        else:
            for c in flagged:
                if not any(x.get("rpid") == c["rpid"] for x in queue):
                    queue.append(c)
            state["queue"] = queue
            save_state(state)

    # unreachable, but safety
    save_state(state)

if __name__ == "__main__":
    main()
