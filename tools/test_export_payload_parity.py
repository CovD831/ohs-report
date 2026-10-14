#!/usr/bin/env python3
"""导出 payload 同源性门禁 (防"CLI 假绿")

背景: tools/export_e2e.py 手抄了 app.py /export 的 project payload。
曾发生 e2e 漏 ppe/public_works/investment/area/facilities/staffing/protection/
emergency → 类比PPE表/项目组成表/经费表 静默 0 行, 本地测试全绿而线上产物退化。
本测试用 AST 比对两处 dict 字面量的键集, 谁漂移就直接红。

另两处**已知且刻意**的取值差异在此登记, 漂移即红:
  - name:  app 用 DB 行 p["name"], e2e 用 data["name"]
  - report_no: app 导出前先 ensure_report_no(pid) 生成并落库, e2e 不生成
用法: .venv/bin/python tools/test_export_payload_parity.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


def _keys_from_assign(path, func, var):
    """取 func 体内 `var = {...}` 字面量键列表"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign):
                    names = [t.id for t in sub.targets if isinstance(t, ast.Name)]
                    if var in names and isinstance(sub.value, ast.Dict):
                        return [k.value for k in sub.value.keys if isinstance(k, ast.Constant)]
    return None


def _keys_from_return(path, func):
    """取 func 直接 `return {...}` 字面量键列表"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            for sub in node.body:
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    return [k.value for k in sub.value.keys if isinstance(k, ast.Constant)]
    return None


APP = ROOT / "web" / "app.py"
E2E = ROOT / "tools" / "export_e2e.py"

app_keys = _keys_from_assign(APP, "api_export", "project")
e2e_keys = _keys_from_return(E2E, "build_project")

print("### 导出 payload 键集同源性")
check("app.py api_export 内找到 project dict 字面量", app_keys is not None, True)
check("export_e2e.py build_project 内找到 return dict 字面量", e2e_keys is not None, True)
if app_keys and e2e_keys:
    missing = [k for k in app_keys if k not in e2e_keys]
    extra = [k for k in e2e_keys if k not in app_keys]
    check(f"e2e 未漏字段 (app {len(app_keys)} 键)", missing, [])
    check(f"e2e 无多余字段", extra, [])
    print(f"      键数: app={len(app_keys)} e2e={len(e2e_keys)}")

print("\n### 已知取值差异登记 (变更需同步本文件)")
src_app = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
src_e2e = (ROOT / "tools" / "export_e2e.py").read_text(encoding="utf-8")
check("app 导出前调 ensure_report_no", "ensure_report_no(pid)" in src_app, True)
check("e2e 未调 ensure_report_no (刻意)", "ensure_report_no" in src_e2e, False)
check("app 的 name 取 DB 行 p[\"name\"]", '"name": p["name"]' in src_app, True)

print(f"\n通过 {n_pass} / 失败 {n_fail}")
sys.exit(1 if n_fail else 0)
