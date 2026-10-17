#!/usr/bin/env python3
"""Эксперимент ramoops: узел ramoops в /reserved-memory (журнал ядра после зависания или паники).

Запуск: add_ramoops.py <корень ядра>

Что делает: в msm8937.dtsi и msm8937-olive.dtsi (те же два файла, что правит порт LOLZ) вставляет
в блок reserved-memory узел

    ramoops@9ff00000 { compatible = "ramoops"; reg = <0 0x9ff00000 0 0x100000>; ... };

1 МБ: console 256 КБ, pmsg 128 КБ, остальное под записи oops/panic по 128 КБ.

Почему этот адрес: тот же 0x9ff00000 / 1 МБ используют mainline-DT близких Xiaomi-плат семейства
msm8953 (tissot, vince) с такой же раскладкой low-RAM (other_ext на 0x84a00000, modem, adsp, wcnss).
Узел ramoops внутри /reserved-memory этим деревом подхватывается: drivers/of/platform.c ищет
compatible "ramoops" именно там, fs/pstore/ram.c читает reg и размеры из DT.

Строго: скрипт падает, если (а) не нашёл блок reserved-memory или строку ranges;, (б) новая область
пересекается с любой reg из этого блока (дерево изменилось). Идемпотентен.
Это НЕ проверено на железе: после прошивки смотрите /sys/fs/pstore и tools/kknx-pstore.sh.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
DTS = ROOT / "arch/arm64/boot/dts/qcom"
FILES = ["msm8937.dtsi", "msm8937-olive.dtsi"]

ADDR = 0x9FF00000
SIZE = 0x100000
NODE = (
    "\n"
    "\t\t/* KKNX: журнал ядра после зависания/паники (pstore/ramoops), см. scripts/add_ramoops.py */\n"
    "\t\tramoops@9ff00000 {\n"
    '\t\t\tcompatible = "ramoops";\n'
    "\t\t\treg = <0x0 0x9ff00000 0x0 0x100000>;\n"
    "\t\t\trecord-size = <0x20000>;\n"
    "\t\t\tconsole-size = <0x40000>;\n"
    "\t\t\tpmsg-size = <0x20000>;\n"
    "\t\t};\n"
)
HDR_RE = re.compile(r"^\treserved_memory: reserved-memory \{", re.M)
REG_RE = re.compile(r"\breg\s*=\s*<\s*(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s*>")


def die(msg):
    print(f"[ОШИБКА] {msg}", file=sys.stderr)
    sys.exit(1)


def block_span(text, m):
    i = text.index("{", m.start())
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return i, j
    die("не закрыта скобка блока reserved-memory")


def patch(path):
    t = path.read_text()
    if "ramoops@" in t:
        print(f"[ramoops] {path.name}: уже")
        return
    m = HDR_RE.search(t)
    if not m:
        die(f"{path.name}: нет блока 'reserved_memory: reserved-memory {{' (дерево изменилось)")
    start, end = block_span(t, m)
    block = t[start:end]
    for a_hi, a_lo, s_hi, s_lo in REG_RE.findall(block):
        base = (int(a_hi, 16) << 32) | int(a_lo, 16)
        size = (int(s_hi, 16) << 32) | int(s_lo, 16)
        if base < ADDR + SIZE and ADDR < base + size:
            die(f"{path.name}: область ramoops 0x{ADDR:x}+0x{SIZE:x} пересекается с reg 0x{base:x}+0x{size:x}")
    r = re.search(r"^\t\tranges;\n", block, re.M)
    if not r:
        die(f"{path.name}: в reserved-memory нет строки 'ranges;'")
    pos = start + r.end()
    path.write_text(t[:pos] + NODE + t[pos:])
    print(f"[ramoops] {path.name}: узел ramoops@9ff00000 добавлен (1 МБ)")


def main():
    for name in FILES:
        p = DTS / name
        if not p.exists():
            die(f"нет файла {p}")
        patch(p)


if __name__ == "__main__":
    main()
