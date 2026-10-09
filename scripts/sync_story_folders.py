# -*- coding: utf-8 -*-
"""按 data/character.json 的 stories 同步重命名各角色 profile/story/ 下的文件夹。

- 数据源：data/character.json 每条 [id, name_en, name_zh, path, stories]
- 目标：data/story/character/{id}/profile/story/ 下的文件夹名与 stories 一致
- 幂等：已一致则不动，可反复运行

映射规则：
1. 固定项 character_details、character_story_1..5 按名匹配；
2. 可变项（如 name_1/name_2 对应的 slug）优先按「同名」直接匹配；
3. 其余「已改名」的：当前剩余文件夹（按名称排序）与 stories 中剩余目标名（按列表顺序）一一对应；
   若两侧数量不一致则跳过并告警。
"""
import json
import os
import sys

from tqdm import tqdm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAR_JSON = os.path.join(ROOT, "data", "character.json")
FIXED = ["character_details"] + ["character_story_%d" % i for i in range(1, 6)]


def main():
    with open(CHAR_JSON, encoding="utf-8") as f:
        chars = json.load(f)

    renames = 0
    warnings = []

    for c in tqdm(chars, desc="同步文件夹", unit="角色", dynamic_ncols=True):
        sid = c["id"]
        target = list(c.get("stories") or [])
        sdir = os.path.join(ROOT, "data", "story", "character", sid, "profile", "story")

        if not os.path.isdir(sdir):
            if target:
                warnings.append("[%s] 缺少目录 %s" % (sid, os.path.relpath(sdir, ROOT).replace(os.sep, "/")))
            continue

        current = sorted(n for n in os.listdir(sdir) if os.path.isdir(os.path.join(sdir, n)))

        for fx in FIXED:
            if fx in target and fx not in current:
                warnings.append("[%s] 缺少固定文件夹 %s" % (sid, fx))

        cur_set = set(current)
        tgt_set = set(target)
        if cur_set == tgt_set:
            continue  # 已一致，幂等

        leftover_cur = sorted(n for n in current if n not in tgt_set)
        leftover_tgt = [n for n in target if n not in cur_set]

        if len(leftover_cur) != len(leftover_tgt):
            warnings.append("[%s] 无法映射：当前多余 %s，目标多余 %s" % (sid, leftover_cur, leftover_tgt))
            continue

        for old, new in zip(leftover_cur, leftover_tgt):
            src = os.path.join(sdir, old)
            dst = os.path.join(sdir, new)
            if os.path.exists(dst):
                warnings.append("[%s] 目标已存在，跳过：%s -> %s" % (sid, old, new))
                continue
            os.rename(src, dst)
            renames += 1
            tqdm.write("[%s] %s -> %s" % (sid, old, new))

    print("\n重命名 %d 个文件夹。" % renames)
    if warnings:
        print("告警 %d 条：" % len(warnings))
        for w in warnings:
            print("  " + w)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
