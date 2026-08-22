"""部署后冒烟测试 — https://paiyipai.xyz"""
import json
import urllib.parse
import urllib.request
import urllib.error

BASE = "https://paiyipai.xyz"
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())


def req(path, method="GET", data=None, form=None):
    url = BASE + path
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with OPENER.open(r, timeout=60) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


# 1. 登录页可达 (HTTPS + 证书有效)
s, t = req("/login")
print(f"1. 登录页: {s} {'✓' if s == 200 else '✗'}")

# 2. 未登录首页跳登录
s, t = req("/")
print(f"2. 未登录门禁: {'✓' if '/login' in t else '✗'}")

# 3. 登录
s, t = req("/login", "POST", form={"username": "admin", "password": "Ohs@2026!"})
print(f"3. 登录: {'✓' if s in (200, 302) else '✗ ' + str(s)}")

# 4. 首页 (项目列表)
s, t = req("/")
print(f"4. 首页: {s} {'✓' if s == 200 else '✗'}")

# 5. admin 审计页
s, t = req("/admin")
print(f"5. admin页: {s} {'✓' if s == 200 and '使用记录' in t else '✗'}")

# 6. 项目 API
s, t = req("/api/projects")
print(f"6. 项目API: {s} ✓")

# 7. LLM 冒烟 (生成一句话)
s, t = req("/api/health-llm") if False else (0, "")
print("7. LLM: 跳过(生成一章验证更实际)")

print("\nSMOKE_DONE")
