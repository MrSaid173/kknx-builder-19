#!/usr/bin/env python3
"""Эксперимент wbt: накладывает backports/wbt/wbt-backport.patch на дерево ядра (блок writeback throttling из 4.10).

Идемпотентен (если block/blk-wbt.c уже есть, ничего не делает). Любой отказ патча = ошибка сборки,
чтобы эксперимент не прошёл молча без фикса. Конфиг включает apply_tweaks.py (experiments содержит wbt).
"""
import subprocess, sys, pathlib

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
patch = pathlib.Path(__file__).resolve().parent.parent / "backports/wbt/wbt-backport.patch"

if (root / "block/blk-wbt.c").exists():
    print("backport_wbt: уже применён")
    sys.exit(0)
if not patch.exists():
    print("backport_wbt: ОШИБКА: нет", patch)
    sys.exit(1)

r = subprocess.run(["patch", "-p1", "--forward", "--batch", "-d", str(root), "-i", str(patch)],
                   capture_output=True, text=True)
print(r.stdout[-1500:])
rejects = list(root.rglob("*.rej"))
if r.returncode != 0 or rejects:
    print(r.stderr[-1500:])
    print("backport_wbt: ОШИБКА: патч не лёг чисто:", [str(x) for x in rejects][:5])
    sys.exit(1)
print("backport_wbt: применён (blk-wbt из 4.10 для legacy-очередей)")
