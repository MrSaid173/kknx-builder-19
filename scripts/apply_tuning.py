#!/usr/bin/env python3
"""KKNX builder: параметры GPU-частот и CPU-напряжения для kernel_xiaomi_sdm439 (1topaz).

Параметры берутся из переменных окружения (их выставляет workflow):

  GPU_FREQS        "904,560,510,400,320"  частоты GPU в МГц, сверху вниз (5 уровней)
  GPU_TOP_PLL_MHZ  ""                     (опц.) PLL верхнего уровня, МГц. Пусто = 2 x частота (честная).
                                          1808 + метка 800 = ровно как в KKNX-800Mhz_v2 (реально 904 МГц)
  GPU_CORNER_FMAX  "320,400,510,560,921"  потолки МГц для corner'ов LOWER,LOW,NOMINAL,NOM_PLUS,HIGH
  CPU_UV_MV        "0,0,0,0,0,0"          смещение напряжения CPU (мВ) по virtual corner 1..6
  CPU_CEIL_MV      ""                     (опц.) потолок напряжения, мВ, по corner 1..6
  CPU_QUOT_PER_MV  "2.2"                  единиц quotient на 1 мВ (из комментариев в исходнике)

CPU-частоты скрипт НЕ трогает, и это проверяется контрольной суммой перед/после.
Скрипт идемпотентен относительно чистого дерева (запускать на свежем checkout).
"""
import hashlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
DTS = ROOT / "arch/arm64/boot/dts/qcom"
CLK = ROOT / "drivers/clk/msm/clock-gcc-8952.c"

GPU_DTSI = [DTS / "sdm439-olive.dtsi"]
REG_DTSI = [DTS / "sdm439-regulator-olive.dtsi", DTS / "sdm439-regulator.dtsi"]

# ---- пределы безопасности (можно ослабить, но это осознанное решение) ----
GPLL3_MAX_OUT_MHZ = 904        # fmax gpll3 = 1808 МГц, делитель /2 -> 904 МГц на выходе
GPLL3_MIN_OUT_MHZ = 350        # VCO min 700 МГц
GPU_MAX_ACTIVE_LEVELS = 9        # KGSL_MAX_PWRLEVELS = 10, один уровень занят под XO (19,2 МГц)
GPLL3_LOW_MIN_OUT_MHZ = 175    # ниже 350: PLL = 4 x частота (делитель 2), VCO min 700 МГц
CPU_UV_MIN, CPU_UV_MAX = -150, 75
CEIL_MIN_MV, CEIL_MAX_MV = 600, 1050
CPR_FLOORS_UV = [560000, 670000, 740000, 740000, 740000, 740000]  # floor по corner-map <1 2 3 3 3 3>
CORNER_NAMES = ["LOWER", "LOW", "NOMINAL", "NOM_PLUS", "HIGH"]


def die(msg):
    print(f"[ОШИБКА] {msg}", file=sys.stderr)
    sys.exit(1)


def info(msg):
    print(f"[tuning] {msg}")


def env_list(name, default, cast=int):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return [cast(x.strip()) for x in raw.split(",") if x.strip() != ""]
    except ValueError:
        die(f"{name}={raw!r}: ожидается список чисел через запятую")


def cpu_freq_fingerprint():
    """Хэш всех CPU-таблиц частот и карт частота->corner во всех DTSI."""
    h = hashlib.sha256()
    pat = re.compile(r"(qcom,cpufreq-table-\d+\s*=.*?;|qcom,speed\d+-bin-v\d+-\w+\s*=.*?;"
                     r"|qcom,cpr-corner-frequency-map\s*=.*?;)", re.S)
    for f in sorted(DTS.glob("*.dtsi")):
        for m in pat.finditer(f.read_text(errors="ignore")):
            h.update(f.name.encode() + re.sub(r"\s+", " ", m.group(0)).encode())
    return h.hexdigest()


# ======================================================================
#  GPU
# ======================================================================
F_SLEW_RE = re.compile(
    r"^(?P<indent>\s*)F_SLEW\(\s*(?P<f>\d+),\s*(?P<src>FIXED_CLK_SRC|\d+),\s*(?P<pll>\w+),"
    r"\s*(?P<div>[\d.]+),\s*(?P<m>\d+),\s*(?P<n>\d+)\),\s*$")


def _interp(points, x):
    """Линейная интерполяция по точкам (x, y); за краями продолжаем крайний отрезок."""
    pts = sorted(points)
    if x >= pts[-1][0]:
        return pts[-1][1]
    if x <= pts[0][0]:
        (x0, y0), (x1, y1) = pts[0], pts[1]
    else:
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= x <= x1:
                break
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def rebuild_levels(node, freqs):
    """Узел qcom,gpu-pwrlevels-N: вместо 5 рабочих уровней делает len(freqs).
    Значения шины памяти (bus-freq/min/max) берутся интерполяцией по исходным 5 уровням."""
    lv = list(re.finditer(r"qcom,gpu-pwrlevel@(\d+) \{(.*?)\n[ \t]*\};", node, re.S))
    first = re.search(r"\n([ \t]*)(?:/\*[^\n]*\*/\n[ \t]*)?qcom,gpu-pwrlevel@0 \{", node)
    if not lv or not first:
        die("pwrlevels: не удалось разобрать уровни GPU")
    ind = first.group(1)
    levels = []
    for m in lv:
        body = m.group(2)

        def g(k, body=body):
            return int(re.search(r"qcom,%s = <(\d+)>;" % k, body)[1])
        levels.append(dict(hz=g("gpu-freq"), bus=g("bus-freq"), bmin=g("bus-min"), bmax=g("bus-max")))
    act = [l for l in levels if l["hz"] > 100_000_000]
    idle = [l for l in levels if l["hz"] <= 100_000_000]
    if len(act) != 5:
        die("pwrlevels: ожидалось 5 рабочих уровней в исходном узле")
    if len(freqs) + len(idle) > 10:
        die("pwrlevels: уровней больше KGSL_MAX_PWRLEVELS (10)")

    xs = [l["hz"] / 1e6 for l in act]
    xs[0] = max(xs[0], freqs[0])        # верхняя метка в исходнике может быть ниже новой (650/870 -> 904)

    def pick(key, x, hi):
        v = _interp([(xx, l[key]) for xx, l in zip(xs, act)], x)
        return max(1, min(hi, int(v + 0.5 + 1e-9)))
    top = {k: max(l[k] for l in act) for k in ("bus", "bmin", "bmax")}
    rows = []
    for f in freqs:
        b = pick("bus", f, top["bus"])
        bn = pick("bmin", f, top["bmin"])
        bx = pick("bmax", f, top["bmax"])
        rows.append((f, b, min(bn, b), max(bx, b)))

    def lvl(i, comment, hz, b, bn, bx):
        return (f"{ind}/* {comment} */\n{ind}qcom,gpu-pwrlevel@{i} {{\n{ind}\treg = <{i}>;\n"
                f"{ind}\tqcom,gpu-freq = <{hz}>;\n{ind}\tqcom,bus-freq = <{b}>;\n"
                f"{ind}\tqcom,bus-min = <{bn}>;\n{ind}\tqcom,bus-max = <{bx}>;\n{ind}}};")
    parts = [lvl(i, f"{f} MHz", f * 1_000_000, b, bn, bx) for i, (f, b, bn, bx) in enumerate(rows)]
    for k, l in enumerate(idle):
        parts.append(lvl(len(rows) + k, "XO" if l["hz"] < 50_000_000 else "idle",
                         l["hz"], l["bus"], l["bmin"], l["bmax"]))
    block = "\n\n".join(parts)

    new = node[:first.start() + 1] + block + node[lv[-1].end():]

    # начальный уровень: ближайший по частоте к исходному
    mi = re.search(r"qcom,initial-pwrlevel = <(\d+)>;", node)
    if mi:
        f0 = levels[int(mi.group(1))]["hz"] / 1e6
        j = min(range(len(freqs)), key=lambda t: (abs(freqs[t] - f0), t))
        new = new.replace(mi.group(0), f"qcom,initial-pwrlevel = <{j}>;", 1)
    info("GPU уровни (МГц: bus/min/max): " + ", ".join(f"{f}: {b}/{bn}/{bx}" for f, b, bn, bx in rows))
    return new


def tune_gpu():
    freqs = env_list("GPU_FREQS", [904, 560, 510, 400, 320])
    fmax = env_list("GPU_CORNER_FMAX", [280, 369, 510, 637, 921])
    top_pll = env_list("GPU_TOP_PLL_MHZ", [])
    if top_pll and not (700 <= top_pll[0] <= 1808):
        die(f"GPU_TOP_PLL_MHZ={top_pll[0]}: допустимо 700..1808 МГц (VCO PLL и fmax gpll3)")

    if not (5 <= len(freqs) <= GPU_MAX_ACTIVE_LEVELS):
        die(f"GPU_FREQS: нужно от 5 до {GPU_MAX_ACTIVE_LEVELS} значений (МГц), например 900,560,510,400,320")
    if any(a <= b for a, b in zip(freqs, freqs[1:])):
        die("GPU_FREQS: значения должны строго убывать")
    if len(fmax) != 5 or any(a >= b for a, b in zip(fmax, fmax[1:])):
        die("GPU_CORNER_FMAX: нужно 5 строго возрастающих значений (LOWER,LOW,NOMINAL,NOM_PLUS,HIGH)")
    if fmax[-1] < freqs[0]:
        die(f"GPU_CORNER_FMAX: верхний потолок HIGH={fmax[-1]} ниже максимальной частоты {freqs[0]}")

    src = CLK.read_text()
    m = re.search(r"(static struct clk_freq_tbl ftbl_gcc_oxili_gfx3d_clk_sdm439\[\] = \{\n)(.*?)(\tF_END\n\};)",
                  src, re.S)
    if not m:
        die("не найдена таблица ftbl_gcc_oxili_gfx3d_clk_sdm439 в clock-gcc-8952.c")
    head, body, tail = m.group(1), m.group(2), m.group(3)

    entries = []          # (freq_hz, строка)
    for line in body.splitlines():
        fm = F_SLEW_RE.match(line)
        if not fm:
            die(f"неожиданная строка в таблице GPU: {line!r}")
        entries.append((int(fm["f"]), line, fm["pll"]))
    have = {e[0] for e in entries}

    def synth(mhz, pll_mhz=None):
        hz = mhz * 1_000_000
        if mhz < GPLL3_MIN_OUT_MHZ and not pll_mhz:
            # частоты 175..349 МГц: PLL = 4 x частота, делитель 2 (например 288 -> PLL 1152)
            return (hz, f"\tF_SLEW( {hz}, {4 * mhz * 1_000_000},\t  gpll3,\t2,\t0,\t0),", "gpll3")
        pll = (pll_mhz if pll_mhz else 2 * mhz) * 1_000_000
        return (hz, f"\tF_SLEW( {hz}, {pll},\t  gpll3,\t1,\t0,\t0),", "gpll3")

    # Старую «нестандартную» запись 870 МГц (PLL 1808 -> реально 904) убираем:
    # честные частоты получаются только при PLL = 2 x частота.
    entries = [e for e in entries if not (e[0] == 870_000_000 and e[2] == "gpll3")]
    have = {e[0] for e in entries}

    for idx, mhz in enumerate(freqs):
        hz = mhz * 1_000_000
        use_pll = top_pll[0] if (idx == 0 and top_pll) else None
        if use_pll and hz in have:                      # метка уже есть в таблице: заменяем её PLL
            entries = [e for e in entries if e[0] != hz]
            have.discard(hz)
        if hz in have:
            continue
        if not (GPLL3_LOW_MIN_OUT_MHZ <= mhz <= GPLL3_MAX_OUT_MHZ):
            die(f"GPU {mhz} МГц: нет в таблице и вне диапазона gpll3 "
                f"({GPLL3_LOW_MIN_OUT_MHZ}..{GPLL3_MAX_OUT_MHZ} МГц). Допустимы также уже имеющиеся "
                f"в таблице: {sorted(x // 1_000_000 for x in have if x >= 200_000_000)}")
        entries.append(synth(mhz, use_pll))
        have.add(hz)
        info(f"GPU: добавлена запись {mhz} МГц (gpll3, PLL={use_pll or (4 * mhz if mhz < GPLL3_MIN_OUT_MHZ else 2 * mhz)} МГц)")

    entries.sort(key=lambda e: e[0])
    new_body = "\n".join(e[1] for e in entries) + "\n"
    src = src[:m.start()] + head + new_body + tail + src[m.end():]

    # fmax по corner'ам (только строки для sdm439)
    for name, val in zip(CORNER_NAMES, fmax):
        pat = re.compile(r"(gfx3d_clk_src\.c\.fmax\[VDD_DIG_%s\] = )\d+(;)" % name)
        # блок sdm439 идёт сразу после ftbl_gcc_oxili_gfx3d_clk_sdm439 (единственный, где есть HIGH=921000000)
        sm = re.search(r"ftbl_gcc_oxili_gfx3d_clk_sdm439;\n\n((?:\t+gfx3d_clk_src\.c\.fmax\[[^\n]*\n)+)", src)
        if not sm:
            die("не найден блок gfx3d fmax для sdm439")
        blk = sm.group(1)
        nblk, n = pat.subn(lambda mm: f"{mm.group(1)}{val * 1_000_000}{mm.group(2)}", blk)
        if n != 1:
            die(f"не удалось заменить fmax[{name}]")
        src = src.replace(blk, nblk, 1)
    CLK.write_text(src)
    info(f"GPU corner fmax (МГц): {dict(zip(CORNER_NAMES, fmax))}")

    # --- DTS: pwrlevels ---
    for path in GPU_DTSI:
        text = path.read_text()
        node_re = re.compile(r"(qcom,gpu-pwrlevels-\d+ \{.*?\n\t\t\};)", re.S)
        cnt = 0

        def fix_node(mm):
            nonlocal cnt
            node = mm.group(1)
            fr = re.findall(r"qcom,gpu-freq = <(\d+)>;", node)
            active = [i for i, v in enumerate(fr) if int(v) > 100_000_000]   # без 19.2 МГц (idle)
            # Трогаем только «полный» узел (speed-bin 0, 5 рабочих уровней). Узлы для
            # худших bin'ов (4/5/10) остаются как есть: там кристаллы хуже по качеству.
            if len(active) != 5:
                return node
            if len(freqs) != 5:
                cnt += 1
                return rebuild_levels(node, freqs)
            wanted = freqs
            it = iter(wanted)
            idx = {"i": -1}

            def rep(fm):
                idx["i"] += 1
                if idx["i"] in active:
                    return f"qcom,gpu-freq = <{next(it) * 1_000_000}>;"
                return fm.group(0)

            cnt += 1
            return re.sub(r"qcom,gpu-freq = <(\d+)>;", rep, node)

        new = node_re.sub(fix_node, text)
        if cnt == 0:
            die(f"{path.name}: не найдены узлы qcom,gpu-pwrlevels-*")
        path.write_text(new)
        info(f"GPU DTS {path.name}: обновлено узлов pwrlevels: {cnt}")
    if top_pll:
        info(f"GPU частоты (МГц): {freqs}; верхний уровень: метка {freqs[0]}, PLL {top_pll[0]} "
             f"-> реально {top_pll[0] / 2:g} МГц")
    else:
        info(f"GPU частоты (МГц): {freqs}  (честные: PLL = 2 x частота)")


# ======================================================================
#  CPU: напряжение через CPR
# ======================================================================
def parse_int(tok):
    tok = tok.strip()
    if tok.startswith("(") and tok.endswith(")"):
        tok = tok[1:-1]
    return int(tok, 0)


def fmt_int(v):
    return f"(-{abs(v)})" if v < 0 else str(v)


def tune_cpu():
    uv = env_list("CPU_UV_MV", [0] * 6)
    ceil = env_list("CPU_CEIL_MV", [])
    qpm = float(os.environ.get("CPU_QUOT_PER_MV", "2.2") or "2.2")

    if len(uv) != 6:
        die("CPU_UV_MV: нужно 6 значений (corner 1..6), например -10,-10,-15,-15,-20,-20")
    for i, v in enumerate(uv, 1):
        if not (CPU_UV_MIN <= v <= CPU_UV_MAX):
            die(f"CPU_UV_MV corner {i}: {v} мВ вне безопасного диапазона {CPU_UV_MIN}..{CPU_UV_MAX}")
    if ceil:
        if len(ceil) != 6:
            die("CPU_CEIL_MV: нужно 6 значений или пусто")
        for i, v in enumerate(ceil):
            if not (CEIL_MIN_MV <= v <= CEIL_MAX_MV):
                die(f"CPU_CEIL_MV corner {i + 1}: {v} мВ вне диапазона {CEIL_MIN_MV}..{CEIL_MAX_MV}")
            if v * 1000 < CPR_FLOORS_UV[i]:
                die(f"CPU_CEIL_MV corner {i + 1}: {v} мВ ниже floor {CPR_FLOORS_UV[i] // 1000} мВ, "
                    f"CPR не запустится (floor > ceiling)")

    if not any(uv) and not ceil:
        info("CPU: без изменений (CPU_UV_MV все нули, CPU_CEIL_MV пуст)")
        return

    delta = [int(round(v * qpm)) for v in uv]

    for path in REG_DTSI:
        if not path.exists():
            continue
        text = path.read_text()

        # --- quotient-adjustment по virtual corner ---
        pm = re.search(r"(qcom,cpr-virtual-corner-quotient-adjustment =\s*)((?:<[^>]*>,?[^\n]*\n?\s*)+?);", text, re.S)
        if not pm:
            die(f"{path.name}: не найден qcom,cpr-virtual-corner-quotient-adjustment")
        rows = re.findall(r"<([^>]*)>", pm.group(2))
        parsed = []
        for r in rows:
            vals = [parse_int(t) for t in re.findall(r"\(-?\d+\)|-?\d+", r)]
            if len(vals) != 6:
                die(f"{path.name}: строка quotient-adjustment должна иметь 6 значений: {r!r}")
            parsed.append(vals)
        if len(parsed) != 3:
            die(f"{path.name}: ожидалось 3 строки (по cpr-fuse-version-map), найдено {len(parsed)}")
        # добавляем смещение ко всем 3 строкам: какая строка сработает, зависит от fuse-ревизии
        new_rows = [[a + d for a, d in zip(row, delta)] for row in parsed]
        block = "\n".join(
            "\t\t\t<" + " ".join(fmt_int(v) for v in row) + ">" + ("," if i < 2 else ";")
            for i, row in enumerate(new_rows))
        comment = ("\t\t/* KKNX builder: смещение по corner 1..6 (мВ): "
                   + ",".join(str(v) for v in uv) + f"; {qpm} quot/мВ */\n")
        text = text[:pm.start()] + comment + "\t\t" + pm.group(1).strip() + "\n" + block + text[pm.end():]

        # --- потолки напряжения ---
        if ceil:
            cm = re.search(r"(qcom,cpr-voltage-ceiling-override =\s*)<\(-1\) \(-1\)[^>]*>;", text)
            if not cm:
                die(f"{path.name}: не найден qcom,cpr-voltage-ceiling-override")
            vals = " ".join(str(v * 1000) for v in ceil)
            text = text[:cm.start()] + cm.group(1) + f"<(-1) (-1) {vals}>;" + text[cm.end():]
        path.write_text(text)
        info(f"CPU CPR {path.name}: delta quotient по corner 1..6 = {delta}"
             + (f", потолки мВ = {ceil}" if ceil else ""))


def main():
    for p in (CLK, *GPU_DTSI, *REG_DTSI[:1]):
        if not p.exists():
            die(f"нет файла {p} (это точно kernel_xiaomi_sdm439, ветка 1topaz?)")
    before = cpu_freq_fingerprint()
    tune_gpu()
    tune_cpu()
    after = cpu_freq_fingerprint()
    if before != after:
        die("КОНТРОЛЬ: таблицы CPU-частот изменились! Это не должно происходить. Прерываю.")
    info("контроль: таблицы CPU-частот не изменены")


if __name__ == "__main__":
    main()
