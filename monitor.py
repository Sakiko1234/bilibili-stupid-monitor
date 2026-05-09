"""
ECS Cron 自动监测脚本
每5分钟拉取 B站评论 → DeepSeek AI判定 → 生成 HTML → 自动发布
"""
import asyncio, json, os, time, sys, urllib.parse
from datetime import datetime, timezone, timedelta
CST = timezone(timedelta(hours=8))  # 中国时区
import requests
import random
import re
from bilibili_api import bvid2aid  # 仅用于 get_comment_url

BVID = "BV1WQjuz4EzZ"
# 预计算 aid，避免每次调 API 都转换
try:
    AID = str(bvid2aid(BVID))
except Exception:
    AID = "114568202297147"  # 硬编码兜底

DATA_FILE = "data/flagged.json"
CHECKED_FILE = "data/.checked.json"
REPORTED_FILE = "data/.reported.json"
AUTO_REPORT = os.environ.get("AUTO_REPORT", "false").lower() in ("1", "true", "yes")
AVATAR_FILE = "data/avatars.json"
REPORT_TRACKING_FILE = "data/report_tracking.json"
FAILED_REPORT_FILE = "data/.failed_reports.json"
HTML_FILE = "index.html"
USERS_FILE = "users.html"

DEEPSEEK_KEY = os.environ["DEEPSEEK_API_KEY"]
_COOKIE_STATE_FILE = "data/.cookie_state.json"

def _build_cookie_pool():
    """构建 SESSDATA-JCT 配对池，确保顺序对应"""
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

def _load_cookie_state():
    import json
    if os.path.exists(_COOKIE_STATE_FILE):
        with open(_COOKIE_STATE_FILE) as f:
            return json.load(f)
    return {"idx": 0}

def _save_cookie_state():
    import json
    os.makedirs(os.path.dirname(_COOKIE_STATE_FILE), exist_ok=True)
    with open(_COOKIE_STATE_FILE, "w") as f:
        json.dump({"idx": _COOKIE_INDEX}, f)

_cs = _load_cookie_state()
_COOKIE_INDEX = _cs.get("idx", 0) % len(_COOKIE_POOL) if _COOKIE_POOL else 0

def _get_cookie():
    if not _COOKIE_POOL:
        return "", ""
    sess, jct = _COOKIE_POOL[_COOKIE_INDEX % len(_COOKIE_POOL)]
    return sess, jct

def _get_sessdata():
    return _get_cookie()[0]

def _get_jct():
    return _get_cookie()[1]

def _rotate_cookie():
    global _COOKIE_INDEX
    if _COOKIE_POOL:
        _COOKIE_INDEX = (_COOKIE_INDEX + 1) % len(_COOKIE_POOL)
        _save_cookie_state()
    return _get_cookie()

def _rotate_sessdata():
    _rotate_cookie()
    return _get_sessdata()

def _rotate_jct():
    return _get_jct()

BILIBILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": f"https://www.bilibili.com/video/{BVID}",
}

AI_PROMPT = """你是原神社区的内容审核员，负责识别「米家内斗」类低质引战评论。

## 背景
这个评论区聚集了大量原神极端粉丝。他们热爱原神，但对米哈游旗下其他游戏（崩坏：星穹铁道、绝区零）及其玩家群体充满敌意。你的任务是识别他们攻击米家其他游戏的言论，抓出社区内耗的典型样本。

## 标记标准（满足任意一条即标「是」）

### 1. 游戏间拉踩引战
在米哈游游戏之间制造对立、贬低一方抬高另一方：
- 「XX制作组产能不足才要XX帮忙擦屁股」
- 「XX给原神提鞋都不配」
- 用原神的成功去否定崩铁/绝区零，或用崩铁/绝区零的缺点反衬原神
- **用运营数据/商业数据做武器贬低米家其他游戏**：
  - 「铁的拉新差到宣发费不如砍了」「XX的流水才这点」→ 在原神视频下用数据踩崩铁/绝区零＝拉踩引战
  - 用「XX不如原神」「原神比XX强多了」等比较句式，即使是「理性讨论」口吻
  - **关键**：在原神评论区讨论米家其他游戏的商业表现，天然就是「用原神标准审判其他游戏」——标
- 号召玩家站队、挑动「你站哪边」式对立

### 2. 牵强附会 / IP殖民
用毫无根据的臆测强行将原神与崩坏系列绑定，借机踩原神：
- 「冰神就是布洛妮娅」、「原神是崩坏的世界泡」、「三月女神就是三月七」
- 「原神的成功全靠崩坏」、「没有崩坏就没有原神」
- 「崩坏宇宙殖民原神」这类将正常IP关系扭曲为附庸关系的言论

### 3. 阴阳怪气

**7. 模拟对方口吻进行讽刺**：假装站在对方立场说话，实质在挖苦对方玩家群体：
- 用「鸟为什么会飞，生命的第一因，多么哲学」等简化方式讽刺崩铁剧情的深度感
- 用「烧鸡养出了」「烧鸡真是养出了XX群体」将崩铁编剧（烧鸡）等同于游戏整体，贬低其玩家
- 「赛博嘉豪」"赛博XX"等借特定KOL/主播之名贬低整个玩家群体
- 任何用【】框起来模拟对方发言、实则贬损的话术

用假装夸奖、反问句、emoji堆砌等方式暗讽米家其他游戏：
- 「孩子们XX的恩情还不完😭😭」
- 「这下分不清开拓和殖民了」
- 「又幻想了…😭😍🤩😤」系列复读
- **伪理性引战话术**：用「基于事实」「管好自己」等看似理性的措辞包装，实则立稻草人攻击对方玩家群体：
  - **数据伪装式踩一捧一**：用「XX的拉新/流水/营收」等数据开头，后接「不如原神」「宣发费不如砍了」「XX不行」等结论——表面讨论数据、实际是在原神评论区踩米家其他游戏。只要是在用数据贬低崩铁/绝区零就标
  - 「是因为跟原比显得差？」这类表面提问、实际踩一捧一的反问句 → 标
  - 「多坑管好自己就行了，把手伸到别人身上」→ 表面中立、实际煽动对立
  - 「不允许基于事实批评XX」「XX千娇万贵不允许别人批评」→ 立稻草人，把正常的社区讨论歪曲为「不让批评」
  - 「XX玩家双标」「XX玩家就是护主」→ 攻击玩家群体而非讨论游戏
  - **关键判断**：评论是否在用「理性」「事实」做幌子攻击另一个玩家群体？如果是 → 标记

### 4. 人身攻击 / 扣帽子
针对米家特定玩家群体的侮辱，使用社群黑称：
- 「铁孝子」「原批」「txg」「铁÷」「原婴」「绝批」等针对米家任一游戏玩家群体的黑称
- 「txg」是「铁孝子/铁学长」的缩写，专门用于攻击崩铁玩家，见到即标
- 「脑子呢」「你游玩家就这」等群体侮辱

### 5. 恶意KY
在无关场景下反复刷其他游戏的内容，制造社区噪音：
- 原神视频下刷「爱门」「嗨想我了吗」
- 崩铁内容下反复粘贴「摄像头」「散兵」等节奏话术

### 6. 外部游戏拉踩米家（特殊规则）
- 吐槽鸣潮本身 → **不标记**（哪怕是同时夸原神，比如「鸣潮地图花里胡哨，打开原神舒服了」。注意：「呜哇」「wuwa」「兀蛙」都是鸣潮的代称/黑称，吐槽这些同样不标记）
- **用外部游戏当武器攻击崩铁/绝区零** → **标记**。不仅是鸣潮，包括方舟/粥、fgo、母鸡卡等任何外部游戏，只要通过对比/拉踩来贬低崩铁或绝区零，就标记。例如：「鸣潮都比崩铁强」「粥的剧情我都看得下去，老铁怎么味这么大」「连XX都不如」——这是在米家内部引战

### 7. 对崩铁/绝区零的恶意诋毁
**⚠️ 核心原则：只要是在骂崩铁/绝区零，就标记。宁可多标不可漏标。AI 不需要判断「是不是正常批评」，贬低/嘲讽/阴阳/玩梗嘲讽崩铁的一律标记。**
对崩坏：星穹铁道、绝区零本身的谩骂和侮辱性攻击，即使不涉及与其他游戏的比较：
- **任何贬低崩铁/绝区零的言论**：包括但不限于嘲讽游戏品质、玩梗阴阳、用崩铁黑称/梗（如「铁友」「琥珀王」「一片焦土」等）进行讽刺、假装客观实则贬损——全部标记
- **角色设计/建模攻击**：用「性饥渴」「恶心」「难看」「配不上脸」「卖肉」「擦边恶心」等侮辱性措辞贬低角色外观。这不是正常批评，是恶意攻击
- **游戏品质的无脑贬低**：「XX就是一坨」「XX垃圾游戏」「XX根本不配叫二游」等纯情绪化输出
- **玩家群体的地图炮**：直接攻击某个游戏的全体玩家（如「铁÷都是XX」「绝批没脑子」）
- **⚠️ 假装夸奖的反讽**：用「为XX点赞」「支持XX」「感谢XX」等正面句式，后面紧接着全是负面吐槽/嘲讽。这不是正常的游戏讨论，是恶意阴阳怪气（例：「崩铁角色塑造真好，过了剧情就查无此人，为崩坏星穹铁道点赞」→ 前面全在骂、最后假装点赞）
- **❌ 不标记：针对特定魔怔角色厨群体的吐槽**：「白解」「白姐」是崩铁白露的魔怔角色厨群体，单纯骂/嘲讽白解这个群体的评论不属于米家内斗（不涉及米游之间的拉踩引战），不标记

- **恶意攻击崩铁编剧/制作人员**：烧鸡（崩铁编剧/制作人）作为公众人物，针对其个人的侮辱、谩骂、人身攻击（如「烧鸡XX」「烧鸡就是个XX」等），或者用挖苦/讽刺方式贬低其人格或能力，均纳入标记范围。注意：正常批评编剧水平（如「烧鸡这次剧情写得不好」「崩铁剧情不如以前」）不标记，只有带侮辱性措辞或纯情绪化人身攻击才标记

- **吹毛求疵 / 小题大做式抱怨**：用夸张到失真的措辞将游戏正常体验描绘成灾难，本质是带情绪带节奏而非反馈问题：
  - 「玩10多次XX已经开始生理性恶心了」→ 标记
  - 「XX就是一个XXX的纯黑子」→ 标记
  - 「代肝也挺命苦哈」这种假装同情实则贬低的阴阳话术 → 标记
  - 通篇只有情绪化吐槽、没有任何实质性讨论的 → 标记
  - **关键判断**：看评论是在「表达不满但尚在讨论游戏」（不标记）还是「用极端措辞把游戏描绘成一无是处」（标记）

### 8. 以臆测事实为论据贬低崩铁/绝区零
用毫无根据的猜测和臆想包装成「事实」，作为攻击崩铁/绝区零的论据。表面上看起来「有理有据」，实际核心论据全凭主观臆测——评论区常见话术：
- 臆测项目组意图/动机来贬低：「项目组就准备做1小时不到的演唱会才只留了一个月准备时间」「制作组故意敷衍玩家」
- 臆测开发内幕：「崩铁编剧写剧情全靠抄XX」
- 用「我发现了XX的差距」「说白了就是XX」等话术开头，后接纯主观臆断
- **关键判断**：看评论者是否将「自己的猜测」当成「既定事实」来攻击——如果是，标记。如果评论者承认是主观感受（如「我觉得」「个人认为」）且无恶意措辞，不标记

**如何区分正常批评和恶意诋毁**：
- 正常批评：「绝区零走格子玩法有点无聊」、「崩铁数值膨胀太快了」→ 不标记（基于可验证的游戏内容）
- 恶意诋毁：「绝区零角色设计极度性饥渴，恶心死了」「崩铁这垃圾游戏」→ 标记（纯情绪化）
- **⚠️ 以臆测充当事实的贬低**：「崩铁项目组就准备做1小时的演唱会才只留一个月」→ 标记（核心论据是臆测项目组意图，不是事实）

## 不标记的情况
- 对原神本身的内容/剧情/机制的理性批评
- 正常讨论原神角色、剧情、设定（即使语气轻松或玩梗，比如「法尔伽是蒲公英酒，钟离是茅台」这种趣味对比——**任何将角色比作物品/酒/食物等的轻松比喻都不标记**）
- 单纯吐槽鸣潮/呜哇/wuwa/异环等外部竞品（不涉及拉踩米家其他游戏）
- 对米家其他游戏的正常负面评价（有理有据、不使用侮辱性措辞、不人身攻击。例如「绝区零走格子有点无聊」正常，「绝区零角色极度性饥渴恶心」是攻击 → 标记）
- 讨论非米家游戏（方舟、母鸡卡、fgo等），只要不涉及米家拉踩（⚠️ 注意：用外部游戏对比贬低崩铁/绝区零，如「粥的XX都比铁强」「连XX都不如」，属于用外部游戏当武器拉踩，应标记）
- **攻击外部游戏以维护原神/崩铁不是米家内斗**：对外部游戏（方舟/鸣潮/库洛等）及其玩家的批评、嘲讽、黑称（如「皱友」「粥批」），只要评论者立场是维护原神/崩铁、而非攻击米家其他游戏，就不标记。米家内斗必须是社区内部互掐（原 vs 铁 vs 绝），不是「一致对外」
- **日常游戏咨询**：抽卡建议（抽一命还是专武）、装备选择、配队求助、角色培养等纯游戏内容
- **来源解释**：解释某个物品/奖励/称号的来源或获取方式（如「在官号底下拿的」「从活动领的」）
- **游戏进度/体验分享**：晒练度、报进度、分享游戏感受等无引战意图的日常内容
- **角色数据/PV播放量讨论**：对比角色PV播放量、人气、强度排名等纯社区数据讨论，没有攻击米家其他游戏就不标记
- **极度简短/意义不明**：只有一个字或无法判断意图的极短评论（如「可…」「成功5阶」等），默认不标记

## 输出格式（严格遵守）
- 如果不标记 → 输出「否」
- 如果标记 → 输出「是|举报理由」，举报理由为10-20字简短说明，例如：
  「是|使用黑称攻击崩铁玩家」
  「是|用鸣潮当武器拉踩崩铁」
  「是|恶意诋毁绝区零角色设计」

不要输出其他内容。

评论内容："""

AI_REVIEW_PROMPT = """以下评论已被判定为「米家内斗」引战。请判断：这是否属于明显误判？

⚠️ 重要：即使评论中包含了大量游戏细节讨论、bug分析、剧情考据等内容，只要末尾或中间存在米家游戏间对比拉踩（如「对比原神XX」「再看看XX」「反观XX」「XX就比XX好」「再一对比你铁」），就不属于误判，应输出「否」维持标记。

如果评论属于以下任一 → 输出「是」（确实是误判）：
- 纯个人游戏体验分享（抽卡/专武/练度/词条/过关/满花/凹分/结算截图相关）
- 评论者明确声明「不涉及拉踩」「没有引战」「只是讨论游戏」等自证表述
- 讨论非米哈游游戏（但未拉踩米家）
- 纯角色期待或正常讨论
- 纯链接/纯表情/极度简短意义不明
- 纯游戏日程讨论
- 纯游戏玩法说明（解释某个模式/机制的规则和特点，无攻击意图）

如果不属于以上误判 → 输出「否」（不是误判，维持标记）
只输出「是」或「否」。
"""

# ── 图片审核 Prompt ──
AI_IMAGE_PROMPT = """你负责识别B站评论区中用户通过图片表达的「米家内斗」引战内容。

## 背景
原神极端粉丝会截图、拍照、P图来攻击米哈游旗下其他游戏（崩坏：星穹铁道、绝区零）及其玩家群体。
你的任务是：只看这张图片，判断图片中是否包含引战/拉踩/攻击/阴阳怪气米家其他游戏的内容。

## 标记标准（图片中出现以下任一即输出「是」）

1. 截图了崩铁/绝区零的游戏画面，并在图片上添加了贬低/嘲讽/对比的文字
2. 截图了其他用户的评论进行挂人/嘲讽（常见于补档被举报的评论），尤其涉及米家游戏间拉踩
3. P图/表情包用于贬低崩铁/绝区零角色或玩家
4. 图片中包含针对米家特定游戏的侮辱性文字
5. 图片通过对比（原神 vs 崩铁/绝区零）贬低一方
6. 截图了排行榜/数据/流水，并用于贬低崩铁/绝区零
7. 图片中用文字或图像表达「XX不如原神」「XX垃圾」等含义

## 不标记的情况
- 纯游戏截图无引战文字
- 普通的二创/同人图
- 日常聊天/生活截图与米家游戏无关
- 只是展示自己游戏练度/成就的截图

## 输出格式
- 如果图片不违规 → 输出「否」
- 如果图片违规 → 输出「是|违规理由」，理由简短10-20字

只输出结果，不要说其他话。"""

# ── 图片审核函数 ──

def _ocr_image(url):
    """下载图片并用 Tesseract OCR 提取文字"""
    try:
        from PIL import Image, UnidentifiedImageError
        import io
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com",
        }, timeout=15)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        # 转换为 RGB 以兼容所有格式
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        import pytesseract
        text = pytesseract.image_to_string(img, lang="chi_sim")
        return text.strip()
    except Exception as e:
        print(f"  [image] OCR failed: {e}")
        return ""

def _analyze_image_comment(text, pictures):
    """图片评论审核路径：OCR提取文字 -> v4-flash 双判"""
    if not pictures:
        return False, "无图片", None
    print(f"  [image] 检测到 {len(pictures)} 张图片，启动OCR...")
    ocr_texts = []
    for i, pic_url in enumerate(pictures):
        if i >= 3:  # 最多处理3张
            break
        txt = _ocr_image(pic_url)
        if txt:
            ocr_texts.append(txt)
    if not ocr_texts:
        return False, "图片无文字", None
    # 拼接图片文字和评论文本
    full_text = " ".join(ocr_texts)
    if text and text.strip():
        full_text = text + " [图片文字: " + full_text + "]"
    print(f"  [image] OCR提取: {full_text[:100]}...")
    # 走现有文字审核管线
    try:
        is_flag, reason, report_content = check_comment(full_text)
    except RecursionError:
        # 防止 check_comment 再次进入图片路径（pictures=None 走文字路径）
        return False, "OCR后审核异常", None

    if not is_flag:
        return False, reason, report_content

    # 图片评论强制加入「挂人」+ 图片文字描述
    ocr_summary = " ".join(ocr_texts)[:120].replace("\n", " ")
    if report_content:
        report_content = f"挂人，图片：{ocr_summary}，{report_content}"
    else:
        report_content = f"挂人，图片：{ocr_summary}"
    print(f"  [image] 举报词重写: {report_content}")
    return True, reason, report_content

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="referrer" content="no-referrer">
<script>var bgs=['bg.jpg','bg2.jpg','bg3.jpg','bg4.jpg'];document.documentElement.style.setProperty('--bg-image','url('+bgs[Math.floor(Math.random()*bgs.length)]+')');</script>
<title>bilibili</title>
<style>
  :root {{
    --bili-pink: #fb7299; --bili-blue: #00a1d6;
    --bg: #f1f2f3; --card: #ffffff; --border: #e3e5e7;
    --text: #18191c; --dim: #9499a0; --accent: var(--bili-pink);
    --accent-dim: rgba(251,114,153,0.10);
    --overlay: linear-gradient(rgba(241,242,243,0.65),rgba(241,242,243,0.65));
    --topbar-bg: #ffffff; --topbar-text: #18191c;
    --card-bg: #ffffff; --input-bg: #f1f2f5;
    --bg-image: url('bg.jpg');
  }}
  [data-theme="dark"] {{
    --bili-pink: #fb7299; --bili-blue: #00a1d6;
    --bg: #0f0f15; --card: #1a1a24; --border: #2a2a3a;
    --text: #e8e8ed; --dim: #8b8b9e; --accent: var(--bili-pink);
    --accent-dim: rgba(251,114,153,0.12);
    --overlay: linear-gradient(rgba(15,15,21,0.55),rgba(15,15,21,0.55));
    --topbar-bg: #1a1a24; --topbar-text: #e8e8ed;
    --card-bg: rgba(26,26,36,0.85); --input-bg: #1a1a24;
    --bg-image: url('bg.jpg');
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    font-family: -apple-system,BlinkMacSystemFont,"Helvetica Neue",Helvetica,Arial,"PingFang SC","Microsoft YaHei",sans-serif;
    background: var(--overlay), var(--bg-image) fixed center/cover;
    color: var(--text); min-height: 100vh;
  }}
  .topbar {{
    position: sticky; top: 0; z-index: 100;
    background: var(--topbar-bg); backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--border);
    padding: 0 20px; height: 48px;
    display: flex; align-items: center; justify-content: space-between;
  }}
  .topbar .logo {{ display: flex; align-items: center; gap: 8px; color: var(--bili-pink); font-size: 16px; font-weight: 700; text-decoration: none; }}
  .topbar .theme-toggle {{ background: transparent; border: 1px solid var(--border); border-radius: 16px; padding: 4px 12px; cursor: pointer; font-size: 15px; color: var(--dim); transition: all 0.2s; }}
  .topbar .theme-toggle:hover {{ border-color: var(--bili-pink); color: var(--bili-pink); }}
  .main {{ max-width: 650px; margin: 0 auto; padding: 16px 12px 24px; }}
  .nav {{ display: flex; gap: 8px; margin-bottom: 14px; }}
  .nav a {{ background: var(--card-bg); color: var(--text); text-decoration: none; font-size: 13px; padding: 6px 16px; border: 1px solid var(--border); border-radius: 6px; transition: all 0.2s; font-weight: 500; }}
  .nav a:hover {{ border-color: var(--bili-pink); color: var(--bili-pink); }}
  .nav a.active {{ background: var(--bili-pink); color: #fff; border-color: var(--bili-pink); }}
  h1 {{ font-size: 18px; margin-bottom: 4px; color: var(--text); }}
  h1 span {{ font-size: 12px; color: var(--dim); font-weight: 400; }}
  .stats {{ display: flex; gap: 24px; margin: 8px 0 16px; font-size: 13px; color: var(--dim); background: var(--card-bg); padding: 10px 16px; border-radius: 8px; }}
  .stats b {{ color: var(--bili-pink); }}
  .ranking {{ background: var(--card-bg); border: 1px solid rgba(251,114,153,0.15); border-radius: 8px; padding: 14px 18px; margin-bottom: 16px; }}
  .ranking h3 {{ font-size: 14px; color: var(--bili-pink); margin-bottom: 10px; display: flex; align-items: center; gap: 6px; }}
  .ranking h3 small {{ font-size: 11px; color: var(--dim); font-weight: 400; }}
  .ranking-row {{ display: flex; align-items: center; gap: 8px; padding: 5px 0; font-size: 13px; border-bottom: 1px solid rgba(128,128,128,0.06); }}
  .ranking-row:last-child {{ border-bottom: none; }}
  .ranking-row .medal {{ font-size: 18px; min-width: 24px; text-align: center; }}
  .ranking-row .user {{ color: var(--bili-pink); font-weight: 600; min-width: 90px; }}
  .ranking-row .count {{ color: var(--bili-pink); font-weight: 700; margin-right: 6px; }}
  .summary {{ background: var(--card-bg); border: 1px solid rgba(251,114,153,0.15); border-radius: 8px; padding: 12px 18px; margin-bottom: 14px; font-size: 13px; line-height: 1.7; }}
  .summary .icon {{ font-size: 16px; margin-right: 4px; }}
  .summary .text {{ color: var(--bili-pink); }}
  .toolbar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; font-size: 13px; color: var(--dim); }}
  .toolbar select {{ background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 28px 6px 10px; font-size: 13px; cursor: pointer; appearance: none; }}
  .search-box {{ background: var(--input-bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 13px; width: 180px; outline: none; transition: border-color 0.2s; }}
  .search-box:focus {{ border-color: var(--bili-pink); }}
  .search-box::placeholder {{ color: var(--dim); }}
  .search-result {{ color: var(--dim); font-size: 12px; }}
  /* B站评论区卡片 */
  .comment-item {{
    display: flex; background: var(--card-bg); padding: 14px 16px;
    border-radius: 0; border-bottom: 1px solid var(--border);
    transition: background 0.15s;
  }}
  .comment-item:hover {{ background: rgba(128,128,128,0.03); }}
  .comment-item:first-child {{ border-radius: 8px 8px 0 0; }}
  .comment-item:last-child {{ border-radius: 0 0 8px 8px; border-bottom: none; }}
  .comment-item:first-child:last-child {{ border-radius: 8px; }}
  .avatar {{ width: 40px; height: 40px; border-radius: 50%; margin-right: 12px; flex-shrink: 0; object-fit: cover; }}
  .comment-content {{ flex: 1; display: flex; flex-direction: column; min-width: 0; }}
  .comment-user-row {{ display: flex; align-items: center; margin-bottom: 6px; font-size: 13px; gap: 6px; flex-wrap: wrap; }}
  .comment-username {{ color: var(--dim); font-weight: 500; text-decoration: none; }}
  .comment-username:hover {{ color: var(--bili-pink); }}
  .lv-tag {{ background: var(--bili-pink); color: #fff; font-size: 10px; padding: 0 4px; border-radius: 2px; line-height: 15px; font-weight: 700; flex-shrink: 0; }}
  .reason-tag {{ background: var(--accent-dim); color: var(--bili-pink); font-size: 11px; padding: 1px 6px; border-radius: 8px; font-weight: 500; margin-left: auto; }}
  .comment-text {{ font-size: 15px; color: var(--text); line-height: 1.6; margin-bottom: 8px; white-space: pre-wrap; word-break: break-word; }}
  .comment-footer {{ display: flex; align-items: center; gap: 16px; color: var(--dim); font-size: 13px; flex-wrap: wrap; }}
  .comment-time {{ color: var(--dim); }}
  .comment-like {{ display: flex; align-items: center; gap: 4px; cursor: default; }}
  .comment-link {{ font-size: 12px; color: var(--dim); text-decoration: none; cursor: pointer; transition: color 0.15s; }}
  .comment-link:hover {{ color: var(--bili-blue); }}
  .empty {{ text-align: center; padding: 80px 0; color: var(--dim); font-size: 15px; }}
  .pager {{ display: flex; justify-content: center; align-items: center; gap: 6px; margin-top: 24px; font-size: 14px; flex-wrap: wrap; }}
  .pager button {{ background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 7px 14px; cursor: pointer; font-size: 13px; min-width: 38px; text-align: center; transition: all 0.2s; }}
  .pager button:hover {{ border-color: var(--bili-pink); color: var(--bili-pink); }}
  .pager button:disabled {{ opacity: 0.3; cursor: default; }}
  .pager button.active {{ background: var(--bili-pink); color: #fff; border-color: var(--bili-pink); font-weight: 600; }}
  .pager .ellipsis {{ color: var(--dim); padding: 0 4px; }}
  footer {{ text-align: center; padding: 32px 0 16px; font-size: 12px; color: var(--dim); }}
  @media (max-width: 640px) {{
    body {{ padding: 0; }}
    .main {{ padding: 10px 8px; }}
    .topbar {{ padding: 0 12px; height: 44px; }}
    .topbar .logo {{ font-size: 14px; }}
    h1 {{ font-size: 16px; }}
    .stats {{ flex-direction: column; gap: 4px; margin: 6px 0 12px; font-size: 12px; padding: 8px 12px; }}
    .nav {{ margin-bottom: 10px; }}
    .nav a {{ font-size: 13px; padding: 6px 12px; }}
    .toolbar {{ flex-direction: column; gap: 8px; align-items: flex-start; }}
    .search-box {{ width: 100% !important; }}
    .comment-item {{ padding: 12px; }}
    .comment-text {{ font-size: 14px; }}
    .avatar {{ width: 34px; height: 34px; margin-right: 10px; }}
    .comment-footer {{ gap: 10px; font-size: 12px; }}
    .pager button {{ padding: 6px 10px; font-size: 12px; min-width: 32px; }}
    .ranking-row {{ flex-wrap: wrap; gap: 4px; font-size: 12px; }}
    .ranking-row .user {{ min-width: auto; }}
    footer {{ padding: 20px 0 12px; font-size: 11px; }}
  }}
</style>
</head>
<body>
<div class="topbar">
  <div class="logo">🚩 聊天室优质文案合集</div>
  <button class="theme-toggle" onclick="toggleTheme()" title="切换主题">🌓</button>
</div>
<div class="main">
<div class="nav"><a href="./" class="active">评论</a><a href="users.html">名人堂</a></div>
<h1><span>AI 自动识别 · 实时更新</span></h1>
<div class="stats">
  <div>累计标记 <b id="totalCount">{total}</b> 条</div>
  <div>监控视频 <b>{videos}</b> 个</div>
  <div>更新于 <b>{updated}</b></div>
</div>
{summary_html}
{ranking_html}
<div class="toolbar">
  <div style="display:flex;align-items:center;gap:10px;">
    <select id="sortSelect" onchange="render()"><option value="newest">最新</option><option value="oldest">最早</option></select>
    <input type="text" id="searchInput" class="search-box" placeholder="搜索..." oninput="search()">
  </div>
  <div><span id="pageInfo">1/1</span><span id="searchInfo" class="search-result" style="margin-left:10px;display:none;"></span></div>
</div>
<div id="commentsContainer">{placeholder}</div>
<div class="pager" id="pager"></div>
<footer>DeepSeek AI · B站评论区数据</footer>
</div>
<script>
(function() {{
  var saved = localStorage.getItem('theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
}})();
function toggleTheme() {{
  var current = document.documentElement.getAttribute('data-theme');
  var next = current === 'dark' ? '' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
}}
var ALL_COMMENTS = {all_comments_json};
const PER_PAGE = 20;
let currentSort = 'newest';
let currentPage = 1;
let currentKeyword = '';
function getSorted() {{
  let list = [...ALL_COMMENTS];
  list.sort((a, b) => (a.time || '').localeCompare(b.time || ''));
  if (currentSort === 'newest') list.reverse();
  return list;
}}
function getFiltered() {{
  const sorted = getSorted();
  if (!currentKeyword) return sorted;
  const kw = currentKeyword.toLowerCase();
  return sorted.filter(c =>
    (c.user || '').toLowerCase().includes(kw) ||
    (c.content || '').toLowerCase().includes(kw) ||
    (c.ai_reason || '').toLowerCase().includes(kw)
  );
}}
function search() {{
  currentKeyword = document.getElementById('searchInput').value.trim();
  currentPage = 1;
  render();
}}
function render() {{
  currentSort = document.getElementById('sortSelect').value;
  const filtered = getFiltered();
  const totalPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  if (currentPage > totalPages) currentPage = totalPages;
  if (currentPage < 1) currentPage = 1;
  const start = (currentPage - 1) * PER_PAGE;
  const pageItems = filtered.slice(start, start + PER_PAGE);
  let html = '';
  if (pageItems.length === 0) {{ html = '<div class="empty">' + (currentKeyword ? '没有匹配的结果' : '暂无标记评论，等待检测结果...') + '</div>'; }}
  for (const c of pageItems) {{
    var ava = c.avatar ? '<img class="avatar" src="' + esc(c.avatar) + '" loading="lazy">' : '<div class="avatar" style="background:var(--border);flex-shrink:0;"></div>';
    var spaceUrl = 'https://space.bilibili.com/' + esc(c.mid);
        var lvTag = c.level && c.level > 0 ? '<span class="lv-tag">LV' + c.level + '</span>' : '';
    html += '<div class="comment-item">' + ava + '<div class="comment-content"><div class="comment-user-row"><a class="comment-username" href="' + spaceUrl + '" target="_blank">' + esc(c.user) + '</a>' + lvTag + '</div><div class="comment-text">' + esc(c.content) + '</div><div class="comment-footer"><span class="comment-time">' + esc(c.time) + '</span><span class="comment-like">👍 ' + esc(c.like) + '</span><a class="comment-link" href="' + esc(c.comment_url) + '" target="_blank">💬 查看</a><a class="comment-link" href="' + esc(c.anchor_url) + '" target="_blank">🎯 直达</a><span class="reason-tag">🤖 ' + esc(c.ai_reason || '?') + '</span></div></div></div>';
  }}
  document.getElementById('commentsContainer').innerHTML = html;
  document.getElementById('pageInfo').textContent = currentPage + '/' + totalPages;
  var pagerHtml = '';
  pagerHtml += '<button onclick="goPage(' + (currentPage - 1) + ')"' + (currentPage <= 1 ? ' disabled' : '') + '>← 上一页</button>';
  var pages = [];
  if (totalPages <= 7) {{ for (var i = 1; i <= totalPages; i++) pages.push(i); }}
  else if (currentPage <= 4) {{ pages = [1, 2, 3, 4, 5, '...', totalPages]; }}
  else if (currentPage >= totalPages - 3) {{ pages = [1, '...', totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages]; }}
  else {{ pages = [1, '...', currentPage - 1, currentPage, currentPage + 1, '...', totalPages]; }}
  for (var i = 0; i < pages.length; i++) {{
    if (pages[i] === '...') {{ pagerHtml += '<span class="ellipsis">...</span>'; }}
    else {{ pagerHtml += '<button onclick="goPage(' + pages[i] + ')"' + (pages[i] === currentPage ? ' class="active"' : '') + '>' + pages[i] + '</button>'; }}
  }}
  pagerHtml += '<button onclick="goPage(' + (currentPage + 1) + ')"' + (currentPage >= totalPages ? ' disabled' : '') + '>下一页 →</button>';
  document.getElementById('pager').innerHTML = pagerHtml;
  var si = document.getElementById('searchInfo');
  if (currentKeyword) {{ si.textContent = '找到 ' + filtered.length + ' 条'; si.style.display = 'inline'; }}
  else {{ si.style.display = 'none'; }}
}}
function goPage(p) {{
  const filtered = getFiltered();
  const totalPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  if (p < 1) p = 1; if (p > totalPages) p = totalPages;
  currentPage = p; render();
}}
function esc(s) {{ if (!s) return ''; s = String(s); return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }}
render();
</script>
</body>
</html>"""


def get_comment_url(bvid, rpid):
    try:
        aid = bvid2aid(bvid)
        return f"https://www.bilibili.com/h5/comment/sub?oid={aid}&pageType=1&root={rpid}"
    except Exception:
        return f"https://www.bilibili.com/video/{bvid}?reply={rpid}"

def load_avatars():
    """加载头像缓存 {mid: url}"""
    if os.path.exists(AVATAR_FILE):
        with open(AVATAR_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def fetch_missing_avatars(mids):
    """按需拉取缺失的头像 URL"""
    cache = load_avatars()
    missing = [m for m in mids if str(m) not in cache]
    if not missing:
        return cache

    cookies = {"SESSDATA": _get_sessdata()} if _get_sessdata() else None
    for mid in missing:
        try:
            resp = requests.get(
                "https://api.bilibili.com/x/space/acc/info",
                params={"mid": str(mid)},
                cookies=cookies,
                headers=BILIBILI_HEADERS,
                timeout=5,
            )
            if resp.status_code == 200 and resp.json().get("code") == 0:
                face = resp.json()["data"].get("face", "")
                if face:
                    cache[str(mid)] = face
        except Exception:
            pass
        # 不放 sleep，B站头像 API 限流不严

    os.makedirs(os.path.dirname(AVATAR_FILE), exist_ok=True)
    with open(AVATAR_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)

    return cache

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"videos": {}, "comments": []}

def save_data(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_checked():
    """加载已检查的评论 ID（独立文件，不提交到 git）"""
    if os.path.exists(CHECKED_FILE):
        with open(CHECKED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_checked(data):
    os.makedirs(os.path.dirname(CHECKED_FILE), exist_ok=True)
    with open(CHECKED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)

def load_reported():
    if os.path.exists(REPORTED_FILE):
        try:
            with open(REPORTED_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_reported(data):
    os.makedirs(os.path.dirname(REPORTED_FILE), exist_ok=True)
    with open(REPORTED_FILE, "w", encoding="utf-8") as f:
        json.dump(list(data), f)

def load_report_tracking():
    if os.path.exists(REPORT_TRACKING_FILE):
        with open(REPORT_TRACKING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def load_failed_reports():
    if os.path.exists(FAILED_REPORT_FILE):
        try:
            with open(FAILED_REPORT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_failed_reports(data):
    os.makedirs(os.path.dirname(FAILED_REPORT_FILE), exist_ok=True)
    with open(FAILED_REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_report_tracking(data):
    os.makedirs(os.path.dirname(REPORT_TRACKING_FILE), exist_ok=True)
    with open(REPORT_TRACKING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def check_report_results():
    tracking = load_report_tracking()
    now = datetime.now(CST)
    checked = 0
    for rpid_str, info in list(tracking.items()):
        if info.get("result") != "pending":
            continue
        reported_at = datetime.fromisoformat(info["reported_at"])
        if (now - reported_at).total_seconds() < 600:
            continue
        oid = info.get("oid", AID)
        try:
            resp = requests.get(
                "https://api.bilibili.com/x/v2/reply/detail",
                params={"oid": oid, "type": 1, "rpid": int(rpid_str), "root": int(rpid_str)},
                headers=BILIBILI_HEADERS,
                timeout=10,
            )
            if resp.status_code == 200:
                code = resp.json().get("code", -1)
                if code == 0:
                    root = resp.json().get("data", {}).get("root")
                    if root and root.get("rpid"):
                        info["result"] = "still_there"
                    else:
                        info["result"] = "removed"
                elif code in (12006, 12089):
                    info["result"] = "removed"
                else:
                    info["result"] = "check_failed"
            else:
                info["result"] = "check_failed"
        except Exception as e:
            info["result"] = f"error: {str(e)[:50]}"
        info["checked_at"] = now.isoformat()
        checked += 1
        time.sleep(0.5)
    if checked:
        save_report_tracking(tracking)
        print(f"  [tracker] {checked} reports checked")
    return tracking

def build_report_html(tracking):
    items = []
    for rpid_str, info in sorted(tracking.items(), key=lambda x: x[1].get("reported_at", ""), reverse=True):
        result = info.get("result", "pending")
        icon = {"removed": "&#x2705;", "still_there": "&#x274C;", "pending": "&#x23F3;", "check_failed": "&#x26A0;&#xFE0F;"}.get(result, "&#x2753;")
        label = {"removed": "deleted", "still_there": "not processed", "pending": "pending", "check_failed": "retry later"}.get(result, result)
        ct = info.get("content", "?")[:60].replace("<", "&lt;").replace(">", "&gt;")
        rs = info.get("reason", "?")[:30].replace("<", "&lt;").replace(">", "&gt;")
        pt = (info.get("comment_time") or "")[:16]
        items.append(f"<tr><td>{icon}</td><td>{(info.get('reported_at') or '?')[:16]}</td><td>{pt}</td><td>{info.get('user', '?')}</td><td style='max-width:260px;overflow:hidden'>{ct}</td><td>{rs}</td><td>{label}</td><td>{(info.get('checked_at') or '-')[:16]}</td></tr>")
    pending = sum(1 for i in tracking.values() if i.get("result") == "pending")
    failed = sum(1 for i in tracking.values() if i.get("result") == "check_failed")
    removed = sum(1 for i in tracking.values() if i.get("result") == "removed")
    still = sum(1 for i in tracking.values() if i.get("result") == "still_there")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>report feedback</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:#1a1a24;color:#e8e8ed;padding:20px;max-width:1000px;margin:0 auto}}
h1{{color:#fb7299;margin-bottom:4px;font-size:18px}}
h1 span{{font-size:12px;color:#8b8b9e;font-weight:400}}
.stats{{display:flex;gap:20px;margin:12px 0 18px;font-size:13px;color:#8b8b9e}}
.stats b{{color:#fb7299}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px 10px;border-bottom:1px solid #2a2a3a;color:#8b8b9e;font-size:12px}}
td{{padding:8px 10px;border-bottom:1px solid #1f1f2e}}
tr:hover{{background:rgba(251,114,153,0.05)}}
footer{{text-align:center;padding:24px;color:#8b8b9e;font-size:11px}}
</style></head>
<body>
<h1>report feedback <span>auto check 10min delay</span></h1>
<div class="stats">
  <div>total <b>{len(tracking)}</b></div>
  <div>pending <b>{pending}</b></div>
  <div>deleted <b>{removed}</b></div>
  <div>not processed <b>{still}</b></div>
</div>
<table>
<tr><th></th><th>time</th><th>user</th><th>content</th><th>reason</th><th>result</th><th>checked</th></tr>
{"".join(items)}
</table>
<footer>every 5min auto update</footer>
</body></html>"""
async def auto_report_comments(new_flagged_comments):
    if not AUTO_REPORT:
        return 0
    global _COOKIE_INDEX
    sessdata = _get_sessdata()
    jct = _get_jct()
    if not sessdata or not jct:
        print("  [report] no SESSDATA or bili_jct, skip")
        return 0

    import requests
    reported = load_reported()
    count = 0
    max_per_run = 5
    report_interval = 90  # 每次举报间隔秒数（基础值，实际会加随机扰动）
    report_url = "https://api.bilibili.com/x/v2/reply/report"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://www.bilibili.com/video/{BVID}",
    }
    cookies = {"SESSDATA": sessdata}

    failed_list = load_failed_reports()
    if failed_list:
        new_flagged_comments = failed_list + new_flagged_comments
        print(f"  [report] retrying {len(failed_list)} previously failed")

    if not new_flagged_comments:
        return 0
    
    for c in new_flagged_comments:
        if count >= max_per_run:
            break
        rpid = c.get("rpid")
        if not rpid or rpid in reported:
            continue
        try:
            rc = c.get("report_content") or ""
            data = {
                "oid": int(AID),
                "type": 1,  # VIDEO
                "rpid": rpid,
                "reason": 4,  # 引战
                "content": rc if len(rc) >= 2 else "引战拉踩攻击米家其他游戏",
                "csrf": jct,
            }
            r = requests.post(report_url, data=data, cookies=cookies, headers=headers, timeout=10)
            result = r.json()
            code = result.get("code", -1)
            if code == 0:
                reported.add(rpid)
                count += 1
                print(f"  [report] OK #{count} rpid={rpid} user={c.get('user','?')}")
                tracking = load_report_tracking()
                tracking[str(rpid)] = {
                    "user": c.get("user", "?"),
                    "content": c.get("content", "")[:100],
                    "reason": c.get("report_content", c.get("ai_reason", "?"))[:60],
                    "oid": AID,
                    "reported_at": datetime.now(CST).isoformat(),
                    "checked_at": None,
                    "result": "pending"
                }
                save_report_tracking(tracking)
                await asyncio.sleep(report_interval + random.randint(5, 20))
            elif code == 12008:
                reported.add(rpid)
                print(f"  [report] already reported rpid={rpid}")
            else:
                msg = result.get("message", "unknown")
                print(f"  [report] FAIL rpid={rpid} code={code} msg={msg}")
                if code == -101:
                    print("  [report] not logged in, stop")
                    break
                if code in (12019, -352):
                    print(f"  [report] too frequent / risk control (code={code}), rotate cookie")
                    retry_ok = False
                    for attempt in range(len(_COOKIE_POOL)):
                        new_sess = _rotate_sessdata()
                        new_jct = _rotate_jct()
                        if not new_sess or not new_jct:
                            continue
                        await asyncio.sleep(60 + random.randint(10, 30))
                        r2 = requests.post(report_url, data={**data, "csrf": new_jct}, cookies={"SESSDATA": new_sess}, headers=headers, timeout=10)
                        result2 = r2.json()
                        code2 = result2.get("code", -1)
                        if code2 == 0:
                            reported.add(rpid)
                            count += 1
                            retry_ok = True
                            print(f"  [report] retry OK #{count} rpid={rpid} user={c.get('user','?')} (rotated)")
                            tracking = load_report_tracking()
                            tracking[str(rpid)] = {
                                "user": c.get("user", "?"),
                                "content": c.get("content", "")[:100],
                                "reason": c.get("report_content", c.get("ai_reason", "?"))[:60],
                                "oid": AID,
                                "reported_at": datetime.now(CST).isoformat(),
                                "checked_at": None,
                                "result": "pending"
                            }
                            save_report_tracking(tracking)
                            break
                        elif code2 in (12019, -352):
                            msg2 = result2.get('message','?')
                            print(f"  [report] rotate {attempt+1}/{len(_COOKIE_POOL)} code={code2} ({msg2}) -> next")
                        else:
                            print(f"  [report] rotate FAIL rpid={rpid} code={code2} msg={result2.get('message','?')}")
                            break
                    if not retry_ok:
                        print(f"  [report] all cookies exhausted rpid={rpid}")
                        failed_list.append(c)
                        save_failed_reports(failed_list)
            sessdata = _rotate_sessdata()
            jct = _rotate_jct()
            cookies = {"SESSDATA": sessdata}
        except Exception as e:
            print(f"  [report] error rpid={rpid}: {str(e)[:100]}")
            sessdata = _rotate_sessdata()
            jct = _rotate_jct()
            cookies = {"SESSDATA": sessdata}
    
    remaining = [x for x in failed_list if x.get("rpid") not in reported]
    save_failed_reports(remaining)
    
    if reported - load_reported():
        save_reported(reported)
    
    # 本轮未处理的（因 max_per_run 跳过）加入重试队列
    unprocessed = [c for c in new_flagged_comments if c.get("rpid") and c.get("rpid") not in reported]
    failed_list = load_failed_reports()
    for c in unprocessed:
        if not any(x.get("rpid") == c.get("rpid") for x in failed_list):
            failed_list.append(c)
    save_failed_reports(failed_list)
    if unprocessed:
        print(f"  [report] queued {len(unprocessed)} for next cycle")
    
    return count

def fetch_comments(bvid, max_pages=3):
    """直接用 requests 调 B站 API，自动切换 cookie 绕过 412 风控"""
    all_comments = []
    next_offset = 0

    def _try_fetch_page(page, sess):
        params = {"oid": AID, "type": "1", "mode": "2", "ps": "20", "next": str(next_offset)}
        cookies = {"SESSDATA": sess} if sess else None
        resp = requests.get("https://api.bilibili.com/x/v2/reply/main",
            params=params, cookies=cookies, headers=BILIBILI_HEADERS, timeout=15)
        return resp

    for page in range(max_pages):
        try:
            resp = _try_fetch_page(page, _get_sessdata())
            # 412 风控 → 换 cookie 重试
            if resp.status_code == 412:
                new_sess = _rotate_sessdata()
                if new_sess:
                    print(f"  第{page+1}页 cookie 切换重试...")
                    resp = _try_fetch_page(page, new_sess)

            if resp.status_code != 200:
                print(f"  第{page+1}页失败: HTTP {resp.status_code}")
                break
            data = resp.json()
            if data.get("code") != 0:
                print(f"  第{page+1}页失败: code={data.get('code')} {data.get('message', '')}")
                if data.get("code") == -101:
                    _rotate_sessdata()
                    print(f"  自动切换 cookie...")
                break

            replies = data.get("data", {}).get("replies") or []
            if not replies:
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
            if cursor.get("is_end"):
                break

            print(f"  第{page+1}页: {len(replies)} 条, 累计 {len(all_comments)} 条")
            time.sleep(0.6)
        except Exception as e:
            print(f"  第{page+1}页失败: {e}")
            break

    return all_comments

def _call_ai(model, system_prompt, user_text, max_tokens=400):
    """调用 DeepSeek API，统一处理 content/reasoning_content"""
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
            # 优先匹配明确输出模式（如「输出是」「因此是」「应该是误判」）
            m = re.search(r'(?:输出|因此|应该|属于|标记)\s*(?:是|否|为)?\s*[：:]*\s*([是否])[\s。]*', reasoning[::-1])
            if not m:
                m = re.search(r'(?:输出|因此|应该|属于|标记)\s*(?:是|否|为)?\s*[：:]*\s*([是否])[\s。]*', reasoning)
            if m:
                answer = m.group(1)
            else:
                # 兜底：反向扫描最后出现的 是/否
                for i in range(len(reasoning) - 1, -1, -1):
                    ch = reasoning[i]
                    if ch == '否':
                        answer = '否'; break
                    if ch == '是' and (i == 0 or reasoning[i-1] not in '不否'):
                        answer = '是'; break
    return answer.strip()

def check_comment(text, pictures=None):
    """双判机制。有图片走多模态路径，无图片走纯文本路径"""
    # ── 图片路径 ──
    if pictures:
        return _analyze_image_comment(text, pictures)

    try:
        # 第一判：v4-flash 高召回
        answer = _call_ai("deepseek-v4-flash", AI_PROMPT, text, max_tokens=400)
        # 解析「是|理由」或「否」
        report_content = None
        if "|" in answer:
            parts = answer.split("|", 1)
            verdict = parts[0].strip()
            report_content = parts[1].strip() if len(parts) > 1 else None
        else:
            verdict = answer.strip()

        if verdict in ('否',) or verdict.startswith("否"):
            reason_text = report_content or "无"
            return False, f"否|{reason_text}", None

        if not verdict.startswith("是"):
            return False, verdict[:20], None
        # 硬规则检查：含黑称关键词直接跳过复审
        HARD_RULE_TERMS = ['txg', '铁孝子', '原批', '铁÷', '原婴', '绝批', '你游玩家就这']
        if any(t in text for t in HARD_RULE_TERMS):
            return True, "硬规则", report_content
        # 第二判：v4-flash 判断是否误判（「是」=误判驳回，「否」=维持）
        review = _call_ai("deepseek-v4-flash", AI_REVIEW_PROMPT, text, max_tokens=200)
        if review in ('是',) or review.startswith("是"):
            return False, "复审驳回", None
        else:
            return True, "双判通过", report_content
    except Exception as e:
        print(f"  AI调用失败: {e}")
        return False, str(e), None

USERS_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="referrer" content="no-referrer">
<script>var bgs=['bg.jpg','bg2.jpg','bg3.jpg','bg4.jpg'];document.documentElement.style.setProperty('--bg-image','url('+bgs[Math.floor(Math.random()*bgs.length)]+')');</script>
<title>弱智名人堂</title>
<style>
  :root {{
    --bili-pink: #fb7299; --bili-blue: #00a1d6;
    --bg: #f1f2f3; --card: #ffffff; --border: #e3e5e7;
    --text: #18191c; --dim: #9499a0; --accent: var(--bili-pink);
    --accent-dim: rgba(251,114,153,0.10);
    --overlay: linear-gradient(rgba(241,242,243,0.65),rgba(241,242,243,0.65));
    --topbar-bg: #ffffff; --topbar-text: #18191c;
    --card-bg: #ffffff; --input-bg: #f1f2f5;
    --bg-image: url('bg.jpg');
  }}
  [data-theme="dark"] {{
    --bili-pink: #fb7299; --bili-blue: #00a1d6;
    --bg: #0f0f15; --card: #1a1a24; --border: #2a2a3a;
    --text: #e8e8ed; --dim: #8b8b9e; --accent: var(--bili-pink);
    --accent-dim: rgba(251,114,153,0.12);
    --overlay: linear-gradient(rgba(15,15,21,0.55),rgba(15,15,21,0.55));
    --topbar-bg: #1a1a24; --topbar-text: #e8e8ed;
    --card-bg: rgba(26,26,36,0.85); --input-bg: #1a1a24;
    --bg-image: url('bg.jpg');
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    font-family: -apple-system,BlinkMacSystemFont,"Helvetica Neue",Helvetica,Arial,"PingFang SC","Microsoft YaHei",sans-serif;
    background: var(--overlay), var(--bg-image) fixed center/cover;
    color: var(--text); min-height: 100vh;
  }}
  .topbar {{
    position: sticky; top: 0; z-index: 100;
    background: var(--topbar-bg); backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--border);
    padding: 0 20px; height: 48px;
    display: flex; align-items: center; justify-content: space-between;
  }}
  .topbar .logo {{ display: flex; align-items: center; gap: 8px; color: var(--bili-pink); font-size: 16px; font-weight: 700; text-decoration: none; }}
  .topbar .theme-toggle {{ background: transparent; border: 1px solid var(--border); border-radius: 16px; padding: 4px 12px; cursor: pointer; font-size: 15px; color: var(--dim); transition: all 0.2s; }}
  .topbar .theme-toggle:hover {{ border-color: var(--bili-pink); color: var(--bili-pink); }}
  .main {{ max-width: 650px; margin: 0 auto; padding: 16px 12px 24px; }}
  .nav {{ display: flex; gap: 8px; margin-bottom: 14px; }}
  .nav a {{ background: var(--card-bg); color: var(--text); text-decoration: none; font-size: 13px; padding: 6px 16px; border: 1px solid var(--border); border-radius: 6px; transition: all 0.2s; font-weight: 500; }}
  .nav a:hover {{ border-color: var(--bili-pink); color: var(--bili-pink); }}
  .nav a.active {{ background: var(--bili-pink); color: #fff; border-color: var(--bili-pink); }}
  h1 {{ font-size: 18px; margin-bottom: 4px; color: var(--text); }}
  .stats {{ display: flex; gap: 24px; margin: 8px 0 16px; font-size: 13px; color: var(--dim); background: var(--card-bg); padding: 10px 16px; border-radius: 8px; }}
  .stats b {{ color: var(--bili-pink); }}
  .search-box {{ background: var(--input-bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 7px 12px; font-size: 13px; width: 200px; outline: none; transition: border-color 0.2s; }}
  .search-box:focus {{ border-color: var(--bili-pink); }}
  .search-box::placeholder {{ color: var(--dim); }}
  .toolbar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; font-size: 13px; color: var(--dim); flex-wrap: wrap; gap: 8px; }}
  .search-result {{ color: var(--dim); font-size: 12px; }}
  /* 用户卡片 */
  .user-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 0; border-bottom: 1px solid var(--border); padding: 14px 16px; cursor: pointer; transition: background 0.15s; }}
  .user-card:hover {{ background: rgba(128,128,128,0.03); }}
  .user-card:first-child {{ border-radius: 8px 8px 0 0; }}
  .user-card:last-child {{ border-radius: 0 0 8px 8px; border-bottom: none; }}
  .user-card:first-child:last-child {{ border-radius: 8px; }}
  .user-card.open {{ border-color: var(--bili-pink); }}
  .user-header {{ display: flex; align-items: center; gap: 10px; }}
  .user-header .rank {{ font-size: 18px; min-width: 28px; text-align: center; }}
  .avatar {{ width: 36px; height: 36px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }}
  .user-header .name {{ color: var(--bili-pink); font-weight: 600; font-size: 14px; text-decoration: none; }}
  .user-header .name:hover {{ text-decoration: underline; }}
  .user-header .uid {{ color: var(--dim); font-size: 11px; }}
  .user-header .count {{ margin-left: auto; background: var(--accent-dim); color: var(--bili-pink); padding: 2px 10px; border-radius: 10px; font-size: 12px; font-weight: 600; }}
  .user-comments {{ display: none; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }}
  .user-card.open .user-comments {{ display: block; }}
  .user-comment-item {{ padding: 10px 0; border-bottom: 1px solid rgba(128,128,128,0.06); }}
  .user-comment-item:last-child {{ border-bottom: none; }}
  .user-comment-item .meta {{ font-size: 12px; color: var(--dim); margin-bottom: 4px; }}
  .user-comment-item .content {{ font-size: 14px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }}
  .user-comment-item .reason {{ font-size: 11px; color: var(--bili-pink); margin-top: 4px; }}
  .empty {{ text-align: center; padding: 80px 0; color: var(--dim); font-size: 15px; }}
  .no-result {{ text-align: center; padding: 30px 0; color: var(--dim); font-size: 14px; display: none; }}
  .pager {{ display: flex; justify-content: center; align-items: center; gap: 6px; margin-top: 24px; font-size: 14px; flex-wrap: wrap; }}
  .pager button {{ background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 7px 14px; cursor: pointer; font-size: 13px; min-width: 38px; text-align: center; transition: all 0.2s; }}
  .pager button:hover {{ border-color: var(--bili-pink); color: var(--bili-pink); }}
  .pager button:disabled {{ opacity: 0.3; cursor: default; }}
  .pager button.active {{ background: var(--bili-pink); color: #fff; border-color: var(--bili-pink); font-weight: 600; }}
  .pager .ellipsis {{ color: var(--dim); padding: 0 4px; }}
  footer {{ text-align: center; padding: 32px 0 16px; font-size: 12px; color: var(--dim); }}
  @media (max-width: 640px) {{
    body {{ padding: 0; }}
    .main {{ padding: 10px 8px; }}
    .topbar {{ padding: 0 12px; height: 44px; }}
    .topbar .logo {{ font-size: 14px; }}
    h1 {{ font-size: 16px; }}
    .stats {{ flex-direction: column; gap: 4px; margin: 6px 0 12px; font-size: 12px; padding: 8px 12px; }}
    .nav {{ margin-bottom: 10px; gap: 6px; }}
    .nav a {{ font-size: 12px; padding: 5px 12px; }}
    .toolbar {{ flex-direction: column; gap: 8px; align-items: flex-start; }}
    .search-box {{ width: 100% !important; }}
    .user-card {{ padding: 12px; }}
    .user-header {{ gap: 8px; }}
    .user-header .rank {{ font-size: 16px; min-width: 24px; }}
    .user-header .name {{ font-size: 13px; }}
    .avatar {{ width: 30px; height: 30px; }}
    .user-comment-item .content {{ font-size: 13px; }}
    .pager button {{ padding: 6px 10px; font-size: 12px; min-width: 32px; }}
    footer {{ padding: 20px 0 12px; font-size: 11px; }}
  }}
</style>
</head>
<body>
<div class="topbar">
  <div class="logo">👤 弱智名人堂</div>
  <button class="theme-toggle" onclick="toggleTheme()" title="切换主题">🌓</button>
</div>
<div class="main">
<div class="nav">
  <a href="./">← 评论列表</a>
  <a href="users.html" class="active">🏆 名人堂</a>
</div>
<h1>🏆 弱智名人堂</h1>
<div class="stats">
  <div>收录用户 <b>{user_count}</b> 人</div>
  <div>累计语录 <b>{total}</b> 条</div>
  <div>更新于 <b>{updated}</b></div>
</div>
<div class="toolbar">
  <div style="display:flex;align-items:center;gap:10px;">
    <input type="text" id="searchBox" class="search-box" placeholder="搜索用户名..." oninput="search()">
  </div>
  <div><span id="pageInfo">1/1</span><span class="search-result" id="searchInfo" style="margin-left:10px;display:none;"></span></div>
</div>
<div class="no-result" id="noResult">没有匹配的用户</div>
<div id="container">{placeholder}</div>
<div class="pager" id="pager"></div>
<footer>DeepSeek AI · B站评论区数据</footer>
</div>
<script>
(function() {{
  var saved = localStorage.getItem('theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
}})();
function toggleTheme() {{
  var current = document.documentElement.getAttribute('data-theme');
  var next = current === 'dark' ? '' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
}}
var allUsers = {all_users_json};
var PER_PAGE = 15;
var currentPage = 1;
var currentKeyword = '';
function esc(s) {{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }}
function getSorted() {{
  var list = allUsers.slice();
  list.sort(function(a, b) {{ return b.count - a.count; }});
  return list;
}}
function getFiltered() {{
  var sorted = getSorted();
  if (!currentKeyword) return sorted;
  var kw = currentKeyword.toLowerCase();
  return sorted.filter(function(u) {{ return (u.user || '').toLowerCase().includes(kw); }});
}}
function search() {{
  currentKeyword = document.getElementById('searchBox').value.trim();
  currentPage = 1;
  render();
}}
function toggleUser(el) {{
  el.classList.toggle('open');
}}
function goPage(p) {{
  var filtered = getFiltered();
  var totalPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  if (p < 1) p = 1;
  if (p > totalPages) p = totalPages;
  currentPage = p;
  render();
}}
function render() {{
  var filtered = getFiltered();
  var totalPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  if (currentPage > totalPages) currentPage = totalPages;
  var start = (currentPage - 1) * PER_PAGE;
  var pageItems = filtered.slice(start, start + PER_PAGE);
  var html = '';
  if (pageItems.length === 0) {{
    html = '<div class="empty">' + (currentKeyword ? '没有匹配' : '暂无标记用户') + '</div>';
  }}
  pageItems.forEach(function(u, i) {{
    var rank;
    if (currentPage === 1 && currentKeyword === '') {{
      rank = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : (i + 1);
    }} else {{
      rank = start + i + 1;
    }}
    html += '<div class="user-card" onclick="toggleUser(this)">';
    html += '<div class="user-header"><span class="rank">' + rank + '</span>';
    var ava = u.avatar ? '<img class="avatar" src="' + esc(u.avatar) + '" loading="lazy">' : '';
    html += ava + '<a class="name" href="https://space.bilibili.com/' + esc(u.mid) + '" target="_blank" onclick="event.stopPropagation()">' + esc(u.user) + '</a>';
    html += '<span class="uid">UID:' + esc(u.mid) + '</span>';
    html += '<span class="count">' + u.count + ' 条</span></div>';
    html += '<div class="user-comments">';
    u.comments.forEach(function(c) {{
      html += '<div class="user-comment-item"><div class="meta">' + esc(c.time) + ' · 👍 ' + c.like + '</div>';
      html += '<div class="content">' + esc(c.content) + '</div>';
      html += '<div class="reason">🤖 ' + esc(c.ai_reason) + '</div></div>';
    }});
    html += '</div></div>';
  }});
  document.getElementById('container').innerHTML = html;
  document.getElementById('pageInfo').textContent = 'Page ' + currentPage + '/' + totalPages;
  var pagerHtml = '';
  pagerHtml += '<button onclick="goPage(' + (currentPage - 1) + ')"' + (currentPage <= 1 ? ' disabled' : '') + '>prev</button>';
  var pages = [];
  if (totalPages <= 7) {{ for (var j = 1; j <= totalPages; j++) pages.push(j); }}
  else if (currentPage <= 4) {{ pages = [1, 2, 3, 4, 5, '...', totalPages]; }}
  else if (currentPage >= totalPages - 3) {{ pages = [1, '...', totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages]; }}
  else {{ pages = [1, '...', currentPage - 1, currentPage, currentPage + 1, '...', totalPages]; }}
  for (var i = 0; i < pages.length; i++) {{
    if (pages[i] === '...') {{ pagerHtml += '<span class="ellipsis">...</span>'; }}
    else {{ pagerHtml += '<button onclick="goPage(' + pages[i] + ')"' + (pages[i] === currentPage ? ' class="active"' : '') + '>' + pages[i] + '</button>'; }}
  }}
  pagerHtml += '<button onclick="goPage(' + (currentPage + 1) + ')"' + (currentPage >= totalPages ? ' disabled' : '') + '>next</button>';
  document.getElementById('pager').innerHTML = pagerHtml;
  var si = document.getElementById('searchInfo');
  if (currentKeyword) {{ si.textContent = 'Found ' + filtered.length; si.style.display = 'inline'; }}
  else {{ si.style.display = 'none'; }}
  document.getElementById('noResult').style.display = (pageItems.length === 0) ? '' : 'none';
}}
render();
</script>
</body>
</html>"""

def build_users_html(data):
    """生成弱智名人堂页面"""
    comments = data.get("comments", [])

    # 按UID分组（无mid的老数据用用户名兜底）
    from collections import defaultdict
    users = defaultdict(list)
    name_map = {}  # key -> (display_name, latest_detected_at)
    for c in comments:
        mid = c.get("mid") or c.get("user", "?")
        key = str(mid)
        users[key].append({
            "content": c.get("content", ""),
            "time": c.get("time", ""),
            "like": c.get("like", 0),
            "ai_reason": c.get("ai_reason", "")[:60],
        })
        detected = c.get("detected_at", "")
        if key not in name_map or detected > name_map[key][1]:
            name_map[key] = (c.get("user", "?"), detected)

    # 按标记数排序
    user_list = []
    for key, cmts in sorted(users.items(), key=lambda x: -len(x[1])):
        display_name = name_map.get(key, (key, ""))[0]
        is_uid = key.isdigit()
        user_list.append({
            "user": display_name,
            "mid": key if is_uid else "-",
            "count": len(cmts),
            "comments": cmts,
            "avatar": "",
        })

    # 按需拉取头像
    mids = [u["mid"] for u in user_list if u["mid"] != "—"]
    if mids:
        avatars = fetch_missing_avatars(mids)
        for u in user_list:
            if u["mid"] in avatars:
                u["avatar"] = avatars[u["mid"]]

    updated = data.get("last_run", "暂无数据")
    placeholder = '' if user_list else '<div class="empty">暂无标记评论</div>'

    return USERS_PAGE.format(
        user_count=len(user_list),
        total=len(comments),
        updated=updated,
        placeholder=placeholder,
        all_users_json=json.dumps(user_list, ensure_ascii=False),
    )

def build_html(data):
    comments = data.get("comments", [])
    videos = data.get("videos", {})

    seen = set()
    unique = []
    for c in comments:
        rpid = c.get("rpid")
        if rpid in seen:
            continue
        seen.add(rpid)
        unique.append(c)

    comments_clean = []
    for c in unique:
        avatar = c.get("avatar", "")
        if not avatar and c.get("mid"):
            # fallback 到缓存
            cache = load_avatars()
            avatar = cache.get(str(c["mid"]), "")
        comments_clean.append({
            "user": c.get("user", "?"),
            "mid": c.get("mid", ""),
            "avatar": avatar,
            "time": c.get("time", "?"),
            "like": c.get("like", 0),
            "content": c.get("content", ""),
            "level": c.get("level", 0),
            "ai_reason": c.get("ai_reason", "?")[:80],
            "comment_url": get_comment_url(c.get("bvid", ""), c.get("rpid", "")),
            "anchor_url": f"https://www.bilibili.com/video/{c.get('bvid', '')}#reply{c.get('rpid', '')}",
        })

    updated = data.get("last_run", "暂无数据")
    placeholder = ('<div class="empty">暂无标记评论，等待检测结果...</div>'
                   if not comments_clean else '')

    # 生成排行榜 HTML
    ranking_html = ""
    summary_html = ""
    ranking_file = os.path.join(os.path.dirname(DATA_FILE), "ranking.json")
    if os.path.exists(ranking_file):
        try:
            with open(ranking_file, "r", encoding="utf-8") as f:
                ranking = json.load(f)
            # AI 锐评总结
            summary = ranking.get("summary", "")
            if summary:
                summary_html = f'<div class="summary"><span class="icon">📝</span><span class="text">AI锐评：{summary}</span></div>'
            rows = ranking.get("ranking", [])
            if rows:
                parts = [f'<div class="ranking"><h3>🏆 今日弱智榜 <small>{ranking.get("date", "")}</small></h3>']
                for r in rows:
                    parts.append(
                        f'<div class="ranking-row">'
                        f'<span class="medal">{r["medal"]}</span>'
                        f'<span class="user">{r["user"]}</span>'
                        f'<span class="count">x{r["count"]}</span>'
                        f'</div>'
                    )
                parts.append('</div>')
                ranking_html = "\n".join(parts)
        except Exception:
            pass

    return HTML.format(
        total=len(comments_clean),
        videos=len(videos),
        updated=updated,
        placeholder=placeholder,
        summary_html=summary_html,
        ranking_html=ranking_html,
        all_comments_json=json.dumps(comments_clean, ensure_ascii=False),
    )

async def main():
    print(f"[{datetime.now(CST).strftime('%H:%M:%S')}] 开始监测 {BVID}")

    comments = fetch_comments(BVID)
    print(f"  拉取 {len(comments)} 条")

    if not comments:
        print("  无新评论，跳过")
        return

    data = load_data()

    # 从最新评论提取 mid→昵称 映射，自动更新改名的用户
    name_map = {}
    for c in comments:
        if c.get("mid"):
            name_map[str(c["mid"])] = c["user"]

    renamed = 0
    filled_avatars = 0
    for c in data.get("comments", []):
        uid = str(c.get("mid", ""))
        if uid and uid in name_map:
            if c.get("user") != name_map[uid]:
                c["user"] = name_map[uid]
                renamed += 1
            # 回填头像（评论 API 自带）
            if not c.get("avatar"):
                for fc in comments:
                    if str(fc.get("mid")) == uid:
                        c["avatar"] = fc["avatar"]
                        filled_avatars += 1
                        break
    if renamed:
        print(f"  🔄 {renamed} 个用户昵称已更新")
    if filled_avatars:
        print(f"  🖼️ {filled_avatars} 个头像已回填")

    if BVID not in data["videos"]:
        data["videos"][BVID] = {
            "title": BVID,
            "first_check": datetime.now(CST).isoformat(),
            "total_checked": 0,
        }

    checked = load_checked()
    checked_ids = checked.setdefault(BVID, [])
    new_flagged = 0

    for c in comments:
        if c["rpid"] in checked_ids:
            continue
        if any(x.get("rpid") == c["rpid"] for x in data["comments"]):
            checked_ids.append(c["rpid"])
            continue

        # 带图片评论直接跳过（举报成功率0%）
        if c.get("pictures"):
            checked_ids.append(c["rpid"])
            print(f"     [{c['user']}] {c['content'][:60]}... → ⬜ 图片跳过 [rpid={c['rpid']}]")
            continue

        is_flag, reason, report_content = check_comment(c["content"])
        checked_ids.append(c["rpid"])

        if is_flag:
            print(f"  🚩 [{c['user']}] {c['content'][:60]}... → {reason} [rpid={c['rpid']}]")
            if report_content:
                print(f"      举报理由: {report_content}")
        elif reason and reason.startswith("否|"):
            print(f"     [{c['user']}] {c['content'][:60]}... → ⬜ {reason[3:]} [rpid={c['rpid']}]")
        elif reason and reason not in ("否", "是"):
            print(f"     [{c['user']}] {c['content'][:60]}... → AI异常: {reason} [rpid={c['rpid']}]")
        else:
            print(f"     [{c['user']}] {c['content'][:60]}... → ⬜ [rpid={c['rpid']}]")

        if is_flag:
            entry = {
                **c, "bvid": BVID, "ai_reason": reason,
                "report_content": report_content or "引战拉踩攻击米家其他游戏",
                "detected_at": datetime.now(CST).isoformat(),
            }
            data["comments"].insert(0, entry)
            new_flagged += 1

    data["videos"][BVID]["total_checked"] += len(comments)
    data["videos"][BVID]["last_check"] = datetime.now(CST).isoformat()
    data["last_run"] = datetime.now(CST).strftime("%m-%d %H:%M")

    # checked_ids 存独立文件（不提交 git 减轻仓库体积）
    save_checked(checked)

    if new_flagged > 0:
        save_data(data)
        print(f"  新标记 {new_flagged} 条，累计 {len(data['comments'])} 条")
        if AUTO_REPORT:
            newly = [c for c in data["comments"] if c.get("detected_at", "").startswith(datetime.now(CST).strftime("%Y-%m-%d"))][:new_flagged]
            await auto_report_comments(newly)
    else:
        print(f"  无新标记（共 {len(comments)} 条已检查）")
        if AUTO_REPORT:
            await auto_report_comments([])

    # HTML 始终重新生成（时间戳和统计需要刷新到网页）
    tracking = check_report_results()
    report_html = build_report_html(tracking)
    with open("report_status.html", "w", encoding="utf-8") as f:
        f.write(report_html)
    print(f"  report_status.html generated ({len(report_html)} bytes)")

    html = build_html(data)
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  HTML 已生成 ({len(html)} bytes)")

    users_html = build_users_html(data)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        f.write(users_html)
    print(f"  用户页已生成 ({len(users_html)} bytes)")

if __name__ == "__main__":
    asyncio.run(main())
