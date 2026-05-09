import json
from monitor import build_html, build_users_html, build_report_html, check_report_results, HTML_FILE, USERS_FILE

with open("data/flagged.json", encoding="utf-8") as f:
    data = json.load(f)

tracking = check_report_results()

with open(HTML_FILE, "w", encoding="utf-8") as f:
    f.write(build_html(data))
with open(USERS_FILE, "w", encoding="utf-8") as f:
    f.write(build_users_html(data))
with open("report_status.html", "w", encoding="utf-8") as f:
    f.write(build_report_html(tracking))

n = len(data["comments"])
print(f"HTML generated, {n} comments, {len(tracking)} reports")

for c in data["comments"]:
    if "wangzi" in c.get("user", "").lower():
        print("WARNING: still present!")
        break
else:
    print("Confirmed: no wangzi")
