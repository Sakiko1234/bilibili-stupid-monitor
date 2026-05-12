"""单 Cookie -352 检测
用法: python3.11 test_cookie.py SESSDATA JCT
      python3.11 test_cookie.py "aaaa%2C1794051446%2Cb495d%2A52Cj..."

# 从 .env 加载所有，逐组检测举报接口
用法: python3.11 test_cookie.py --all
"""
import sys, requests

AID = "114568202297147"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com/video/BV1WQjuz4EzZ",
}

def check_login(sess):
    """返回 (ok, uname) 或 (False, 错误描述)"""
    r = requests.get("https://api.bilibili.com/x/web-interface/nav",
        cookies={"SESSDATA": sess}, headers=HEADERS, timeout=10)
    code = r.json().get("code", -1)
    if code == 0:
        return True, r.json()["data"]["uname"]
    elif code == -101:
        return False, "过期 (-101)"
    elif code == -352:
        return False, "登录接口被风控 (-352)"
    else:
        return False, f"code={code}"


def check_report(sess, jct):
    """返回 (ok, 描述)。用已删除的 rpid 探测接口状态，不会产生真实举报"""
    r = requests.post("https://api.bilibili.com/x/v2/reply/report",
        data={
            "oid": int(AID), "type": 1,
            "rpid": 299109337105,  # 已删除的评论，只会返回 12022
            "reason": 4, "content": "test",
            "csrf": jct,
        },
        cookies={"SESSDATA": sess},
        headers=HEADERS, timeout=10,
    )
    code = r.json().get("code", -1)
    msg = r.json().get("message", "")[:50]
    if code == 12022:
        return True, "正常 (评论已删除，接口通路)"
    elif code == -352:
        return False, "-352 风控中"
    elif code == 12019:
        return False, f"12019 频率过快 ({msg})"
    elif code == -101:
        return False, "-101 未登录"
    else:
        return False, f"code={code} {msg}"


def test_one(label, sess, jct):
    ok, info = check_login(sess)
    if not ok:
        print(f"  #{label} 登录: {info}")
        return
    uname = info
    ok2, info2 = check_report(sess, jct)
    status = "✅" if ok2 else "⚠️"
    print(f"  #{label} {status} 用户={uname} | 举报: {info2}")


if __name__ == "__main__":
    if "--all" in sys.argv:
        import os
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

        print(f"Cookie 池: {len(pool)} 组\n")
        for label, sess, jct in pool:
            test_one(label, sess, jct)

    elif len(sys.argv) == 3:
        sess = sys.argv[1]
        jct = sys.argv[2]
        test_one("?", sess, jct)

    elif len(sys.argv) == 2:
        sess = sys.argv[1]
        ok, info = check_login(sess)
        print(f"登录: {'✅ ' + info if ok else info}")
        print("(未传 JCT，跳过举报接口检测)")

    else:
        print("用法: python3.11 test_cookie.py SESSDATA JCT")
        print("      python3.11 test_cookie.py SESSDATA  (仅检查登录)")
        print("      python3.11 test_cookie.py --all      (检测 .env 全部)")
