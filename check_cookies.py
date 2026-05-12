import os, requests

os.chdir(os.path.dirname(os.path.abspath(__file__)))

pair = {}
for line in open(".env"):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
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
        pool.append((idx or "1", p["sess"], p["jct"]))

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

print("Cookie 状态检查:")
for label, sess, jct in pool:
    try:
        r = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            cookies={"SESSDATA": sess},
            headers=headers, timeout=10,
        )
        code = r.json().get("code", -1)
        if code == 0:
            uname = r.json()["data"].get("uname", "?")
            mid = r.json()["data"].get("mid", "?")
            print(f"  #{label}  有效  用户={uname}  UID={mid}")
        elif code == -101:
            print(f"  #{label}  过期  (-101)")
        elif code == -352:
            print(f"  #{label}  风控  (-352)")
        else:
            print(f"  #{label}  code={code}  {r.json().get('message','?')[:30]}")
    except Exception as e:
        print(f"  #{label}  错误: {str(e)[:40]}")
