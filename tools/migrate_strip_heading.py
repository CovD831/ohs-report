"""迁移: 清洗 section_states 正文首行的节标题 (脏数据)
背景: llm_draft 曾把「1.1 项目背景」写进正文首行; 导出侧 word_export.py:871 会剥离,
      但预览 API / 其他消费方不会 → 源头清洗 (单一合并点)。
幂等: 只剥首行的编号标题/#标题, 重复跑无副作用。
用法: python tools/migrate_strip_heading.py [--apply]
"""
import sys, json, re, sqlite3, shutil, datetime
sys.stdout.reconfigure(encoding='utf-8')

DB = 'data/ohs.db'
APPLY = '--apply' in sys.argv

# 与 llm_draft._strip_md_tables ③ 完全同规则 (单一产地: 复制常量, 不各自实现)
def strip_heading(text: str) -> str:
    lines = text.split('\n')
    k = 0
    while k < len(lines):
        s = lines[k].strip()
        if not s:
            k += 1
            continue
        if re.match(r'^#{1,6}\s', s) or re.match(r'^第[一二三四五六七八九十\d]+章\s', s):
            k += 1
            continue
        if re.match(r'^\d+(\.\d+)*\s+\S', s) and len(s) < 40:
            k += 1
            continue
        break
    return '\n'.join(lines[k:]).lstrip('\n') if k else text

def main():
    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT id, data FROM project").fetchall()
    if APPLY:
        bak = f'/tmp/ohs.db.bak_before_strip_{datetime.datetime.now():%H%M%S}'
        shutil.copy(DB, bak)
        print(f"备份 → {bak}")
    total = 0
    for pid, data in row:
        d = json.loads(data)
        ss = d.get('section_states') or {}
        n = 0
        for sn, meta in ss.items():
            t = meta.get('text') or ''
            nt = strip_heading(t)
            if nt != t:
                n += 1
                if APPLY:
                    meta['text'] = nt
        if n:
            print(f"  {pid}: 清洗 {n} 节 ({len(t)}→{len(nt)})")
            total += n
            if APPLY:
                conn.execute("UPDATE project SET data=? WHERE id=?", (json.dumps(d, ensure_ascii=False), pid))
    if APPLY:
        conn.commit()
        print(f"\n✅ 已清洗 {total} 节并落库")
    else:
        print(f"\n[DRY-RUN] 待清洗 {total} 节; 加 --apply 生效")

main()
