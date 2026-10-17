#!/usr/bin/env python3
"""KKNX builder: твики производительности. Запуск: apply_tweaks.py <корень ядра>

Каждый твик включается и выключается отдельным полем workflow (значения приходят через переменные окружения).
Принцип отбора: только то, что не режет энергосбережение (никакого governor performance, никаких
фиксированных частот и отключения сна CPU). CPU/GPU-частоты, напряжения и таблицы шины скрипт не трогает.

Переменные окружения (значение по умолчанию = то, что выставляет workflow):

  IO_SCHED      cfq | deadline | keep      планировщик I/O: собрать и сделать планировщиком по умолчанию (keep = noop, как было)
  DEFAULT_GOV   schedutil | keep           governor по умолчанию (keep = performance, как в defconfig)
  CPU_BOOST     true | false               драйвер cpu_boost: короткий подъём частоты при касании экрана
  ZRAM_COMP     lz4 | zstd | lzo | lzo-rle | keep   алгоритм сжатия zram по умолчанию (keep = как после порта LOLZ; lzo-rle требует experiments=lzorle)
  NET_CC        bbr | keep                 TCP BBR по умолчанию (+ fq как qdisc по умолчанию через runtime)
  LMK           kernel | psi               psi = PSI вместо встроенного LMK (под lmkd; зависит от прошивки)
  CC_OPT        inline | keep              inline = убрать -inline-threshold=1 / -unroll-threshold=1 (штатные пороги clang)
  SPF           true | false               Speculative Page Fault (в defconfig автора выключен)
  EXPERIMENTS   список через запятую | ""  исходные правки для A/B-сравнения, каждая включается отдельно (пусто = ничего):
                  iowait   iowait boost schedutil нарастает ступенями (backport из 4.13); в дереве любое ожидание I/O
                           сразу поднимает частоту ядра до максимума политики
                  lz4      библиотека LZ4 из 4.19.325 вместо старой 2011-2012 (zram с lz4, распаковка быстрее)
                  zstd     kernel-ZSTD 1.5.7 (Linux 6.15+); zstd1/zstd2 = то же + уровень 1/2 по умолчанию вместо 1.3.1 эпохи 4.14; принудительно CRYPTO_ZSTD=y; алгоритм zram отдельно через ZRAM_COMP
                  pelt16   полураспад PELT 16 мс вместо 8 в defconfig (медленнее разгон, меньше скачков частоты)
                  pelt32   то же, 32 мс
                  wbt      бэкпорт blk-wbt из 4.10 (троттлинг фоновой записи ради чтения), цель задержки по умолчанию 2000 мкс;
                           wbt<мкс> (например wbt4000, wbt6000) то же, но с целью задержки в мкс, вшитой в ядро (допустимо 100-1000000)
                  honestfreq  верхние частоты CPU в DT заменяются на реальные (0-3: 2616->2304 МГц, 4-7: 2568->1613 МГц); сам патч делает
                           шаг workflow через scripts/honest_cpu_freq.py до этого скрипта, здесь значение только допускается и пишется в отчёт
                  ramoops  PSTORE/CONSOLE/PMSG/RAM в .config; узел ramoops в DT добавляет шаг workflow через scripts/add_ramoops.py
                  spf      Speculative Page Fault (бывшее поле spf; переменная SPF=true работает как прежде)
                  inline   штатные пороги inline/unroll clang (бывшее поле cc_opt=inline; переменная CC_OPT=inline работает как прежде)
                  lzorle   бэкпорт lzo-rle для zram (Linux 5.1): патч накладывает шаг workflow через scripts/apply_backports.py; здесь CRYPTO_LZO=y и проверка, что патч наложен
                  tcppace  бэкпорт TCP internal pacing (Linux 4.13): BBR без fq; патч накладывает apply_backports.py, здесь только проверка
                  utilest  бэкпорт util_est (Linux 4.17) в путь выбора частоты schedutil; на лету kernel.sched_util_est 0/1;
                           utilest0 / utilest1 = то же + значение после загрузки (0 пишет группа sched; голый utilest = 1, как в патче)
                  perf     убрать лишнюю работу из горячих путей: RMNET_DATA_DEBUG_PKT (отладка каждого пакета rmnet), KASLR
                           (RANDOMIZE_BASE + RANDOMIZE_MODULE_REGION_FULL). SUSFS не затрагивается
                  preempt  PREEMPT_VOLUNTARY вместо полного PREEMPT: чуть выше пропускная способность, хуже хвосты задержек
                  lto      ThinLTO (CONFIG_LTO_CLANG + THINLTO) через ld.lld; правит Makefile под lld (убирает -plugin LLVMgold.so,
                           чинит $(nproc --all)); workflow передаёт LLVM_AR/LLVM_DIS. Сборка заметно дольше
                  crypto   ARM64 crypto (NEON bit-slice AES всегда, CE-варианты AES/SHA1/SHA2/GHASH подхватываются, только если CPU их умеет)
                  ra128    read_ahead_kb 128 вместо 256 в группе io
                  swapmax / swap<N>   vm.swappiness до 200; swap150 = то же + значение 150 в группе vm
                  wsprot / wsprot<МБ> защита файлового кэша (vm.file_protect_kbytes); wsprot200 = то же + порог 200 МБ в группе vm
                  f2fs54   обновление fs/f2fs с 5.3 до 5.4 (без verity, casefold, fscrypt v2); патч накладывает apply_backports.py, здесь проверка и маркер образа
                  schedtun CONFIG_SCHED_DEBUG=y (+DEBUG_KERNEL, DEBUG_PREEMPT принудительно выключен): появляются sched_latency_ns и др.; значения пишет группа cfs
  RUNTIME_TUNE  io,vm,sched,boost | ""     группы значений в init.kknx.rc (пусто = файла нет, кроме NET_CC=bbr: одна строка про fq)

Что делает скрипт:
  1. правит исходники (Makefile для CC_OPT, zram_drv.c и lib/zstd/Makefile для ZRAM_COMP, cpufreq_schedutil.c для
     SUGOV_IOWAIT, библиотеки LZ4/ZSTD для backport-экспериментов);
  2. пишет в $TWEAKS_OUT (по умолчанию каталог выше ядра):
       tweaks.fragment  строки .config для merge_config.sh
       tweaks.need      опции, которые обязаны остаться в .config после olddefconfig
       tweaks.markers   строки, которые должны найтись в готовом образе (verify_image.py)
       tweaks-info.txt  что именно включено (попадает в build-info.txt)
       init.kknx.rc     runtime-значения (если RUNTIME_TUNE не пуст или нужен fq для BBR)

Строгий и идемпотентный: если ожидаемого места в исходниках нет, падает (дерево изменилось, нужна проверка).
"""
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
OUT = Path(os.environ.get("TWEAKS_OUT") or ROOT.parent).resolve()


def die(msg):
    print(f"[ОШИБКА] {msg}", file=sys.stderr)
    sys.exit(1)


def info(msg):
    print(f"[tweaks] {msg}")


def env_choice(name, default, allowed):
    v = (os.environ.get(name) or default).strip().lower()
    if v not in allowed:
        die(f"{name}={v!r}: допустимо {', '.join(allowed)}")
    return v


def env_bool(name, default):
    v = (os.environ.get(name) or default).strip().lower()
    if v not in ("true", "false", "1", "0", "yes", "no"):
        die(f"{name}={v!r}: ожидается true/false")
    return v in ("true", "1", "yes")


IO_SCHED = env_choice("IO_SCHED", "cfq", ("cfq", "deadline", "keep"))
DEFAULT_GOV = env_choice("DEFAULT_GOV", "schedutil", ("schedutil", "keep"))
CPU_BOOST = env_bool("CPU_BOOST", "true")
ZRAM_COMP = env_choice("ZRAM_COMP", "lz4", ("lz4", "zstd", "lzo", "lzo-rle", "keep"))
NET_CC = env_choice("NET_CC", "keep", ("bbr", "keep"))
LMK = env_choice("LMK", "kernel", ("kernel", "psi"))
CC_OPT = env_choice("CC_OPT", "keep", ("inline", "keep"))
SPF = env_bool("SPF", "false")
EXP_ALL = ("iowait", "lz4", "zstd", "pelt16", "pelt32", "honestfreq", "wbt", "ramoops", "spf", "inline", "lzorle", "tcppace",
           "utilest", "swapmax", "wsprot", "f2fs54", "samefill", "syncswap", "schedtun", "perf", "preempt", "lto", "crypto", "ra128")
EXP_RAW = os.environ.get("EXPERIMENTS", "")
EXPERIMENTS = [g.strip().lower() for g in EXP_RAW.replace(";", ",").replace(" ", ",").split(",") if g.strip()]
# wbt<мкс> (например wbt4000) = wbt + своя цель задержки по умолчанию в мкс. Дальше в списке остаётся обычное "wbt",
# число лежит в WBT_LAT_USEC (None = штатные 2000 мкс из патча).
WBT_LAT_MIN, WBT_LAT_MAX = 100, 1000000
WBT_LAT_USEC = None
_exp = []
for g in EXPERIMENTS:
    m = re.fullmatch(r"wbt[=:_-]?([0-9]{1,9})", g)
    if m:
        v = int(m.group(1))
        if not WBT_LAT_MIN <= v <= WBT_LAT_MAX:
            die(f"EXPERIMENTS: {g!r}: задержка wbt должна быть от {WBT_LAT_MIN} до {WBT_LAT_MAX} мкс "
                f"(например wbt4000); для выключения не указывайте wbt вообще")
        if WBT_LAT_USEC is not None and WBT_LAT_USEC != v:
            die(f"EXPERIMENTS: указано два разных значения wbt ({WBT_LAT_USEC} и {v} мкс), оставьте одно")
        WBT_LAT_USEC = v
        g = "wbt"
    _exp.append(g)
EXPERIMENTS = _exp
# zstd1 / zstd2 = zstd (обновление библиотеки) + уровень сжатия по умолчанию в crypto/zstd.c (для zram).
# Просто "zstd" оставляет штатный уровень (3).
ZSTD_LEVEL = None
_exp = []
for g in EXPERIMENTS:
    m = re.fullmatch(r"zstd[=:_-]?([12])", g)
    if m:
        v = int(m.group(1))
        if ZSTD_LEVEL is not None and ZSTD_LEVEL != v:
            die("EXPERIMENTS: zstd1 и zstd2 одновременно нельзя, выберите одно")
        ZSTD_LEVEL = v
        g = "zstd"
    _exp.append(g)
EXPERIMENTS = _exp
# swap<N> (101-200) = swapmax + значение swappiness N для группы vm; wsprot<МБ> = wsprot + порог защиты файлового кэша.
SWAPPINESS = None
WSPROT_MB = None
UTILEST_DEF = None   # utilest<0|1>: значение kernel.sched_util_est после загрузки (голый utilest = как в патче, 1)
_exp = []
for g in EXPERIMENTS:
    m = re.fullmatch(r"swap[=:_-]?([0-9]{1,3})", g)
    if m:
        v = int(m.group(1))
        if not 101 <= v <= 200:
            die(f"EXPERIMENTS: {g!r}: swappiness выше 100 и не больше 200 (например swap150); 100 и меньше и так ставит группа vm")
        if SWAPPINESS is not None and SWAPPINESS != v:
            die(f"EXPERIMENTS: указано два разных swappiness ({SWAPPINESS} и {v}), оставьте одно")
        SWAPPINESS = v
        g = "swapmax"
    m = re.fullmatch(r"wsprot[=:_-]?([0-9]{1,4})", g)
    if m:
        v = int(m.group(1))
        if not 16 <= v <= 2048:
            die(f"EXPERIMENTS: {g!r}: порог защиты файлового кэша от 16 до 2048 МБ (например wsprot200)")
        if WSPROT_MB is not None and WSPROT_MB != v:
            die(f"EXPERIMENTS: указано два разных порога wsprot ({WSPROT_MB} и {v} МБ), оставьте одно")
        WSPROT_MB = v
        g = "wsprot"
    m = re.fullmatch(r"utilest[=:_-]?([01])", g)
    if m:
        v = int(m.group(1))
        if UTILEST_DEF is not None and UTILEST_DEF != v:
            die(f"EXPERIMENTS: указано два разных значения utilest ({UTILEST_DEF} и {v}), оставьте одно")
        UTILEST_DEF = v
        g = "utilest"
    _exp.append(g)
EXPERIMENTS = _exp
for g in EXPERIMENTS:
    if g not in EXP_ALL:
        hint = "; число пишите слитно с wbt, например wbt4000" if g.isdigit() else ""
        die(f"EXPERIMENTS: неизвестное значение {g!r}, допустимо: {', '.join(EXP_ALL)} и wbt<мкс> (wbt4000 = 4000 мкс), zstd1, zstd2, swap<101-200>, wsprot<МБ>, utilest<0|1>{hint}")
if "pelt16" in EXPERIMENTS and "pelt32" in EXPERIMENTS:
    die("EXPERIMENTS: pelt16 и pelt32 одновременно нельзя, выберите одно")
PELT = "16" if "pelt16" in EXPERIMENTS else "32" if "pelt32" in EXPERIMENTS else "keep"
SUGOV_IOWAIT = "ramp" if "iowait" in EXPERIMENTS else "keep"
# spf, inline (бывший cc_opt=inline) и ramoops задаются в experiments; переменные SPF / CC_OPT остаются для обратной совместимости
if "spf" in EXPERIMENTS:
    SPF = True
if "inline" in EXPERIMENTS:
    CC_OPT = "inline"
RAMOOPS = "ramoops" in EXPERIMENTS
LZORLE = "lzorle" in EXPERIMENTS
TCPPACE = "tcppace" in EXPERIMENTS
UTILEST = "utilest" in EXPERIMENTS
SWAPMAX = "swapmax" in EXPERIMENTS
SAMEFILL = "samefill" in EXPERIMENTS
SYNCSWAP = "syncswap" in EXPERIMENTS
WSPROT = "wsprot" in EXPERIMENTS
F2FS54 = "f2fs54" in EXPERIMENTS
PERF = "perf" in EXPERIMENTS
PREEMPT_VOL = "preempt" in EXPERIMENTS
LTO = "lto" in EXPERIMENTS
CRYPTO_CE = "crypto" in EXPERIMENTS
RA_KB = 128 if "ra128" in EXPERIMENTS else 256
SCHEDTUN = "schedtun" in EXPERIMENTS
LZ4_UPDATE = "lz4" in EXPERIMENTS
ZSTD_UPDATE = "zstd" in EXPERIMENTS
BACKPORTS = Path(__file__).resolve().parent.parent / "backports"

RT_ALL = ("io", "vm", "sched", "boost", "cfs", "cpuset", "mem2")
RT_RAW = os.environ.get("RUNTIME_TUNE", "io,vm,sched,boost")
RUNTIME = [g.strip().lower() for g in RT_RAW.split(",") if g.strip()]
for g in RUNTIME:
    if g not in RT_ALL:
        die(f"RUNTIME_TUNE: неизвестная группа {g!r}, допустимо: {', '.join(RT_ALL)}")
if "cfs" in RUNTIME and not SCHEDTUN:
    die("RUNTIME_TUNE: группа cfs пишет sched_latency_ns и др., они есть только с experiments=schedtun (CONFIG_SCHED_DEBUG)")
if UTILEST_DEF == 0 and "sched" not in RUNTIME:
    die("EXPERIMENTS: utilest0 записывается группой sched из runtime_tune, добавьте sched (или используйте utilest и пишите sched_util_est сами)")
if (SWAPPINESS or WSPROT_MB) and "vm" not in RUNTIME:
    die("EXPERIMENTS: swap<N> и wsprot<МБ> записываются группой vm из runtime_tune, добавьте vm (или используйте голые swapmax / wsprot "
        "и задайте vm.swappiness / vm.file_protect_kbytes сами)")

frag = []        # строки .config
need = []        # что обязано быть в .config после olddefconfig (точные строки)
markers = []     # что должно быть в образе
notes = []       # для build-info


def opt_on(name):
    frag.append(f"CONFIG_{name}=y")
    need.append(f"CONFIG_{name}=y")


def opt_off(name):
    frag.append(f"# CONFIG_{name} is not set")
    need.append(f"# CONFIG_{name} is not set")


# ======================================================================
#  Исходники
# ======================================================================
def patch_inline():
    """Makefile: для clang стоят -inline-threshold=1 / -inlinehint-threshold=1 / -unroll-threshold=1."""
    p = ROOT / "Makefile"
    s = p.read_text()
    lines = [
        "KBUILD_CFLAGS\t+= -mllvm -inline-threshold=1\n",
        "KBUILD_CFLAGS\t+= -mllvm -inlinehint-threshold=1\n",
        "KBUILD_CFLAGS   += -mllvm -unroll-threshold=1\n",
    ]
    block = "".join(lines)
    stub = "# KKNX tweaks: пороги inline/unroll не занижаются (штатные значения clang)\n"
    if stub in s:
        info("Makefile: пороги inline/unroll уже сняты")
        return
    if s.count(block) != 1:
        die("Makefile: не нашёл три строки с -inline-threshold=1 / -inlinehint-threshold=1 / -unroll-threshold=1 "
            "подряд (дерево изменилось)")
    p.write_text(s.replace(block, stub))
    info("Makefile: убраны -inline-threshold=1, -inlinehint-threshold=1, -unroll-threshold=1")


ZRAM_RE = re.compile(
    r'static const char \*default_compressor =\s*'
    r'(?:#if IS_ENABLED\(CONFIG_CRYPTO_ZSTD\)\s*"zstd";\s*#else\s*"lzo";\s*#endif\s*|"[a-z0-9]+";\s*)')


def patch_zram(alg):
    p = ROOT / "drivers/block/zram/zram_drv.c"
    s = p.read_text()
    want = f'static const char *default_compressor = "{alg}";\n'
    if want in s:
        info(f"zram_drv.c: компрессор по умолчанию уже {alg}")
        return
    m = list(ZRAM_RE.finditer(s))
    if len(m) != 1:
        die("zram_drv.c: не нашёл определение default_compressor (ни исходное, ни после порта LOLZ full)")
    p.write_text(s[:m[0].start()] + want + s[m[0].end():])
    info(f"zram_drv.c: компрессор по умолчанию {alg}")


def patch_zstd_makefile():
    """Убирает дубли общих объектов, когда compressor+decompressor встроены (=y).

    Работает и со старым плоским Makefile (4.14/5.10), и с новым (5.16+, объекты вида common/x.o):
    общие для zstd_compress-y и zstd_decompress-y объекты выносятся в zstd_shared.o.
    """
    mk = ROOT / "lib/zstd/Makefile"
    m = mk.read_text()
    if "zstd_shared" in m:
        info("lib/zstd/Makefile: общие объекты уже вынесены в zstd_shared")
        return

    pat = re.compile(r'(?m)^(zstd_(?:compress|decompress)-y)[ \t]*:=((?:[^\n]*\\\n)*[^\n]*)\n?')
    found = list(pat.finditer(m))
    byname = {x.group(1): x for x in found}
    if len(found) != 2 or set(byname) != {"zstd_compress-y", "zstd_decompress-y"}:
        # Некоторые версии уже линкуют общие объекты один раз (отдельный zstd_common)
        info("lib/zstd/Makefile: duplicate-symbol схема не обнаружена")
        return

    def objs(match):
        return [t for t in match.group(2).replace("\\\n", " ").split() if t != "\\"]

    c_objs = objs(byname["zstd_compress-y"])
    d_objs = objs(byname["zstd_decompress-y"])
    common = [x for x in c_objs if x in d_objs]
    if not common:
        info("lib/zstd/Makefile: общих объектов в compress/decompress нет")
        return

    def fmt(var, values):
        if not values:
            return f"{var} :=\n"
        return f"{var} := \\\n" + " \\\n".join("\t\t" + v for v in values) + "\n"

    block = (
        "ifeq ($(CONFIG_ZSTD_COMPRESS)$(CONFIG_ZSTD_DECOMPRESS),yy)\n"
        "# KKNX: compressor+decompressor built-in -> shared ZSTD objects linked once\n"
        "obj-y += zstd_shared.o\n"
        + fmt("zstd_shared-y", common)
        + fmt("zstd_compress-y", [x for x in c_objs if x not in common])
        + fmt("zstd_decompress-y", [x for x in d_objs if x not in common])
        + "else\n"
        + fmt("zstd_compress-y", c_objs)
        + fmt("zstd_decompress-y", d_objs)
        + "endif\n"
    )
    first, second = sorted(found, key=lambda x: x.start())
    mk.write_text(m[:first.start()] + block + m[first.end():second.start()] + m[second.end():])
    info("lib/zstd/Makefile: общие объекты zstd линкуются один раз")


# ======================================================================
#  Конфиг
# ======================================================================
def replace_once(path, old, new, what):
    """Точная замена одного вхождения; иначе падаем (дерево изменилось)."""
    s = path.read_text()
    n = s.count(old)
    if n != 1:
        die(f"{path.relative_to(ROOT)}: {what}: ожидал ровно одно вхождение, нашёл {n} (дерево изменилось, нужна проверка)")
    path.write_text(s.replace(old, new))


def patch_sugov_iowait():
    """cpufreq_schedutil.c: iowait boost как в Linux 4.13 (commit "cpufreq: schedutil: Make iowait boost more energy efficient").

    Было: каждое пробуждение из ожидания I/O ставит boost = максимальная частота (2.6 ГГц на этом дереве), дальше он
    уменьшается вдвое при каждой оценке. Стало: первое ожидание даёт минимальную частоту политики, каждое следующее подряд
    удваивает boost до максимума, без новых ожиданий boost убывает вдвое. Одиночное чтение с диска больше не будит ядро
    на полной частоте, а длинная серия запросов всё равно доходит до максимума за 2-3 шага.
    """
    p = ROOT / "kernel/sched/cpufreq_schedutil.c"
    s = p.read_text()
    if "iowait_boost_pending" in s:
        info("cpufreq_schedutil.c: iowait boost уже ступенчатый")
        return

    replace_once(p, "\tunsigned long iowait_boost;\n\tunsigned long iowait_boost_max;\n",
                 "\tbool iowait_boost_pending;\n\tunsigned long iowait_boost;\n\tunsigned long iowait_boost_max;\n",
                 "поле iowait_boost_pending")

    replace_once(p,
        "\tif (flags & SCHED_CPUFREQ_IOWAIT) {\n"
        "\t\tsg_cpu->iowait_boost = sg_cpu->iowait_boost_max;\n"
        "\t} else if (sg_cpu->iowait_boost) {\n"
        "\t\ts64 delta_ns = time - sg_cpu->last_update;\n"
        "\n"
        "\t\t/* Clear iowait_boost if the CPU apprears to have been idle. */\n"
        "\t\tif (delta_ns > TICK_NSEC)\n"
        "\t\t\tsg_cpu->iowait_boost = 0;\n"
        "\t}\n",
        "\tif (flags & SCHED_CPUFREQ_IOWAIT) {\n"
        "\t\tif (sg_cpu->iowait_boost_pending)\n"
        "\t\t\treturn;\n"
        "\n"
        "\t\tsg_cpu->iowait_boost_pending = true;\n"
        "\n"
        "\t\tif (sg_cpu->iowait_boost) {\n"
        "\t\t\tsg_cpu->iowait_boost <<= 1;\n"
        "\t\t\tif (sg_cpu->iowait_boost > sg_cpu->iowait_boost_max)\n"
        "\t\t\t\tsg_cpu->iowait_boost = sg_cpu->iowait_boost_max;\n"
        "\t\t} else {\n"
        "\t\t\tsg_cpu->iowait_boost = sg_policy->policy->min;\n"
        "\t\t}\n"
        "\t} else if (sg_cpu->iowait_boost) {\n"
        "\t\ts64 delta_ns = time - sg_cpu->last_update;\n"
        "\n"
        "\t\t/* Clear iowait_boost if the CPU apprears to have been idle. */\n"
        "\t\tif (delta_ns > TICK_NSEC) {\n"
        "\t\t\tsg_cpu->iowait_boost = 0;\n"
        "\t\t\tsg_cpu->iowait_boost_pending = false;\n"
        "\t\t}\n"
        "\t}\n",
        "sugov_set_iowait_boost")

    replace_once(p,
        "\tunsigned long boost_util = sg_cpu->iowait_boost;\n"
        "\tunsigned long boost_max = sg_cpu->iowait_boost_max;\n"
        "\n"
        "\tif (!boost_util)\n"
        "\t\treturn;\n"
        "\n"
        "\tif (*util * boost_max < *max * boost_util) {\n"
        "\t\t*util = boost_util;\n"
        "\t\t*max = boost_max;\n"
        "\t}\n"
        "\tsg_cpu->iowait_boost >>= 1;\n"
        "}\n",
        "\tunsigned long boost_util, boost_max;\n"
        "\n"
        "\tif (!sg_cpu->iowait_boost)\n"
        "\t\treturn;\n"
        "\n"
        "\tif (sg_cpu->iowait_boost_pending) {\n"
        "\t\tsg_cpu->iowait_boost_pending = false;\n"
        "\t} else {\n"
        "\t\tsg_cpu->iowait_boost >>= 1;\n"
        "\t\tif (sg_cpu->iowait_boost < sg_cpu->sg_policy->policy->min) {\n"
        "\t\t\tsg_cpu->iowait_boost = 0;\n"
        "\t\t\treturn;\n"
        "\t\t}\n"
        "\t}\n"
        "\n"
        "\tboost_util = sg_cpu->iowait_boost;\n"
        "\tboost_max = sg_cpu->iowait_boost_max;\n"
        "\n"
        "\tif (*util * boost_max < *max * boost_util) {\n"
        "\t\t*util = boost_util;\n"
        "\t\t*max = boost_max;\n"
        "\t}\n"
        "}\n",
        "sugov_iowait_boost")

    replace_once(p,
        "\t\t\tj_sg_cpu->iowait_boost = 0;\n\t\t\tcontinue;\n",
        "\t\t\tj_sg_cpu->iowait_boost = 0;\n\t\t\tj_sg_cpu->iowait_boost_pending = false;\n\t\t\tcontinue;\n",
        "сброс boost у простаивающего ядра")
    info("cpufreq_schedutil.c: iowait boost ступенчатый (backport 4.13)")


LZ4_FILES = (
    "lib/lz4/Makefile", "lib/lz4/lz4_compress.c", "lib/lz4/lz4_decompress.c", "lib/lz4/lz4hc_compress.c",
    "lib/lz4/lz4defs.h", "include/linux/lz4.h", "lib/decompress_unlz4.c", "fs/squashfs/lz4_wrapper.c",
)


ZSTD_BACKPORT_MARKER = "# KKNX-ZSTD-BACKPORT"
# kernel-style API zstd 1.5.7 (Linux 6.15+) вместо старого ZSTD_*
ZSTD_OLD_API_RE = re.compile(r'\bZSTD_(?!error_)(?=\w*[a-z])\w+')


def patch_zstd_kconfig(mk_text):
    """Если новый Makefile собирает общий код отдельным модулем (CONFIG_ZSTD_COMMON), а в lib/Kconfig его нет."""
    if "CONFIG_ZSTD_COMMON" not in mk_text:
        return
    kc = ROOT / "lib/Kconfig"
    s = kc.read_text()
    if "config ZSTD_COMMON" in s:
        return
    s2, n = re.subn(r'(?m)^(config ZSTD_(?:COMPRESS|DECOMPRESS)\n)', r'\1\tselect ZSTD_COMMON\n', s)
    if n != 2:
        die("lib/Kconfig: не нашёл config ZSTD_COMPRESS и ZSTD_DECOMPRESS для добавления ZSTD_COMMON")
    s2 = s2.replace("config ZSTD_COMPRESS\n", "config ZSTD_COMMON\n\ttristate\n\nconfig ZSTD_COMPRESS\n", 1)
    kc.write_text(s2)
    info("lib/Kconfig: добавлен ZSTD_COMMON")


def ensure_fallthrough():
    """В 4.9 нет макроса fallthrough (появился в 5.4), а kernel-zstd 1.5.7 его использует.

    Глобальный #define нельзя: в дереве есть код, где fallthrough это метка goto (net/sctp). Поэтому заменяем слово
    только внутри lib/zstd на атрибут компилятора (clang и gcc 7+).
    """
    used = re.compile(r'(?m)^(?!\s*(?:/\*|\*|//)).*\bfallthrough\b(?!\s*\*/).*$')
    attr = "__attribute__((__fallthrough__))"
    n = 0
    for fp in (ROOT / "lib/zstd").rglob("*"):
        if fp.suffix not in (".c", ".h"):
            continue
        t = fp.read_text(errors="ignore")
        t2 = used.sub(lambda m: re.sub(r'\bfallthrough\b', attr, m.group(0)), t)
        if t2 != t:
            fp.write_text(t2)
            n += 1
    if n:
        info(f"lib/zstd: fallthrough заменён на {attr} в {n} файлах (глобальный макрос не добавляется)")


def fix_zstd_includes():
    """В 6.12+ заголовок переехал в linux/unaligned.h, в 4.9 он только asm/unaligned.h."""
    n = 0
    for fp in (ROOT / "lib/zstd").rglob("*"):
        if fp.suffix not in (".c", ".h"):
            continue
        t = fp.read_text(errors="ignore")
        t2 = t.replace("<linux/unaligned.h>", "<asm/unaligned.h>")
        if t2 != t:
            fp.write_text(t2)
            n += 1
    if n:
        info(f"lib/zstd: linux/unaligned.h -> asm/unaligned.h в {n} файлах")


def set_zstd_level():
    """zstd1/zstd2: уровень сжатия по умолчанию в crypto/zstd.c (его использует zram)."""
    if ZSTD_LEVEL is None:
        return
    f = ROOT / "crypto/zstd.c"
    s = f.read_text()
    s2, n = re.subn(r'(?m)^(\s*#\s*define\s+ZSTD_DEF_LEVEL\s+)\d+', rf'\g<1>{ZSTD_LEVEL}', s)
    if n != 1:
        die("crypto/zstd.c: не нашёл #define ZSTD_DEF_LEVEL для смены уровня zstd")
    f.write_text(s2)
    info(f"crypto/zstd.c: уровень zstd по умолчанию = {ZSTD_LEVEL}")


def port_zstd_callers():
    """crypto/zstd.c (через него zram получает компрессор zstd): старый API ZSTD_* -> kernel-style zstd_*."""
    f = ROOT / "crypto/zstd.c"
    if f.is_file():
        s = o = f.read_text()
        s = re.sub(r'\bZSTD_getParams\(([^,()]+),\s*0,\s*0\)', r'zstd_get_params(\1, 0)', s)
        s = re.sub(r'\bZSTD_CCtxWorkspaceBound\(\s*([\w.>-]+?)\s*\)', r'zstd_cctx_workspace_bound(&\1)', s)
        s = re.sub(r'\bZSTD_compressCCtx\(([^;]*?),\s*(\w+)\s*\)', r'zstd_compress_cctx(\1, &\2)', s)
        for old, new in (("ZSTD_CCtx", "zstd_cctx"), ("ZSTD_DCtx", "zstd_dctx"), ("ZSTD_parameters", "zstd_parameters"),
                         ("ZSTD_initCCtx", "zstd_init_cctx"), ("ZSTD_initDCtx", "zstd_init_dctx"),
                         ("ZSTD_DCtxWorkspaceBound", "zstd_dctx_workspace_bound"),
                         ("ZSTD_decompressDCtx", "zstd_decompress_dctx"), ("ZSTD_isError", "zstd_is_error")):
            s = re.sub(rf'\b{old}\b', new, s)
        if s != o:
            f.write_text(s)
            info("crypto/zstd.c: переведён на kernel-style API zstd_*")

    # старый API нигде остаться не должен (squashfs, btrfs и т.д. сами не переводим: падаем и показываем где)
    if (ROOT / "lib/decompress_unzstd.c").exists():
        die("lib/decompress_unzstd.c: в дереве есть распаковка ядра zstd, её раскладка включений под новый lib/zstd не переносилась")
    left = []
    for dp, dn, fn in os.walk(ROOT):
        rel = Path(dp).relative_to(ROOT)
        dn[:] = [d for d in dn if d not in (".git", "out", "Documentation")]
        if rel.parts[:2] == ("lib", "zstd"):
            dn[:] = []
            continue
        for name in fn:
            if not name.endswith((".c", ".h")) or (rel.parts[:2] == ("include", "linux") and name.startswith("zstd")):
                continue
            try:
                t = (Path(dp) / name).read_text(errors="ignore")
            except OSError:
                continue
            hit = sorted(set(ZSTD_OLD_API_RE.findall(t)))
            if hit:
                left.append(f"{(rel / name)}: {', '.join(hit[:4])}")
    if left:
        die("после замены ZSTD остались вызовы старого API (нужен ручной перевод):\n  " + "\n  ".join(left))


def zstd_backport_config():
    """Библиотека должна реально собираться: без CRYPTO_ZSTD (zram_comp=lz4 по умолчанию) lib/zstd не компилируется вовсе."""
    if "CONFIG_CRYPTO_ZSTD=y" not in frag:
        opt_on("CRYPTO_ZSTD")
    opt_on("ZSTD_COMPRESS")
    opt_on("ZSTD_DECOMPRESS")
    markers.append("zstd_init_cctx")  # имя есть только в новой библиотеке (kernel-style API 5.16+)


def patch_zstd():
    """kernel-ZSTD 1.5.7 из Linux 6.15+ вместо 1.3.1 (4.14, он же 5.10): библиотека + вызывающие, в стиле patch_lz4().

    В отличие от ZRAM_COMP=zstd это отдельный эксперимент: библиотека обновляется независимо от алгоритма zram.
    Исходники готовит prepare_zstd_backport.sh в backports/zstd.
    """
    hdr = ROOT / "include/linux/zstd.h"
    mk = ROOT / "lib/zstd/Makefile"
    if not hdr.is_file() or not mk.is_file():
        die("ZSTD: в дереве нет include/linux/zstd.h или lib/zstd/Makefile")
    if ZSTD_BACKPORT_MARKER in mk.read_text(errors="ignore")[:1024]:
        info("ZSTD: библиотека уже обновлена (backport)")
        set_zstd_level()
        zstd_backport_config()
        return
    src = BACKPORTS / "zstd"
    src_hdr = src / "include/linux/zstd.h"
    src_lib = src / "lib/zstd"
    need_files = (src_hdr, src / "include/linux/zstd_errors.h", src / "include/linux/zstd_lib.h", src_lib / "Makefile",
                  src_lib / "zstd_compress_module.c", src_lib / "zstd_decompress_module.c",
                  src_lib / "compress/zstd_compress.c", src_lib / "decompress/zstd_decompress.c")
    for fp in need_files:
        if not fp.is_file():
            die(f"нет {fp}: каталог backports/zstd не подготовлен или не 6.15+; запустите prepare_zstd_backport.sh")
    if "zstd_init_cctx" not in src_hdr.read_text(errors="ignore"):
        die("backports/zstd/include/linux/zstd.h: нет kernel-style API zstd_*, нужен Linux 6.15 или новее")
    if "ZSTD_" not in hdr.read_text(errors="ignore"):
        die("include/linux/zstd.h: не похоже на старый kernel-ZSTD")

    ref = "unknown"
    try:
        ref = (src / "SOURCE.txt").read_text().splitlines()[0].split()[-1]
    except (OSError, IndexError):
        pass

    shutil.rmtree(ROOT / "lib/zstd")
    shutil.copytree(src_lib, ROOT / "lib/zstd")
    for fp in sorted((src / "include/linux").glob("zstd*.h")):
        shutil.copyfile(fp, ROOT / "include/linux" / fp.name)

    new_mk = (ROOT / "lib/zstd/Makefile").read_text()
    patch_zstd_kconfig(new_mk)
    patch_zstd_makefile()
    m = (ROOT / "lib/zstd/Makefile").read_text()
    if "zstd_compress-y" not in m or "zstd_decompress-y" not in m:
        die("ZSTD: новый lib/zstd/Makefile не содержит zstd_compress-y/zstd_decompress-y")
    # в 6.x size_t/INT_MAX приходят через linux/limits.h, а в 4.9 он types.h не включает: форсируем только для lib/zstd
    inc = ("# KKNX: типы и лимиты, которые в 4.9 не тянет за собой linux/limits.h (size_t, INT_MAX и т.д.)\n"
           "ccflags-y += -include linux/types.h -include linux/kernel.h -include linux/string.h\n")
    (ROOT / "lib/zstd/Makefile").write_text(f"{ZSTD_BACKPORT_MARKER}: {ref}\n" + inc + m)
    ensure_fallthrough()
    fix_zstd_includes()
    port_zstd_callers()
    set_zstd_level()

    zstd_backport_config()
    n_c = sum(1 for _ in (ROOT / "lib/zstd").rglob("*.c"))
    info(f"ZSTD: импортирован kernel-ZSTD {ref} (1.5.7), {n_c} C-файлов; CRYPTO_ZSTD включён принудительно")


def patch_lz4():
    """Библиотека LZ4 из 4.19.325 (в 4.9 старая, API lz4_compress/lz4_decompress) + переход вызывающих на новый API."""
    hdr = ROOT / "include/linux/lz4.h"
    if "LZ4_compress_default" in hdr.read_text():
        info("LZ4: библиотека уже новая")
        return
    if "lz4_compress(" not in hdr.read_text():
        die("include/linux/lz4.h: не узнаю ни старый, ни новый API LZ4 (дерево изменилось)")
    src = BACKPORTS / "lz4"
    for rel in LZ4_FILES:
        if not (src / rel).is_file():
            die(f"нет {src / rel}: каталог backports/lz4 неполный")
    for rel in LZ4_FILES:
        shutil.copyfile(src / rel, ROOT / rel)

    # crypto/lz4.c и crypto/lz4hc.c: через них zram получает компрессор "lz4"
    for name, comp_old, comp_new, ctxf in (
        ("lz4", "lz4_compress(src, slen, dst, &tmp_len, ctx->lz4_comp_mem)",
         "LZ4_compress_default(src, dst,\n\t\tslen, *dlen, ctx->lz4_comp_mem)", "lz4"),
        ("lz4hc", "lz4hc_compress(src, slen, dst, &tmp_len, ctx->lz4hc_comp_mem)",
         "LZ4_compress_HC(src, dst, slen,\n\t\t*dlen, LZ4HC_DEFAULT_CLEVEL, ctx->lz4hc_comp_mem)", "lz4hc"),
    ):
        f = ROOT / f"crypto/{name}.c"
        replace_once(f,
            f"\tstruct {ctxf}_ctx *ctx = crypto_tfm_ctx(tfm);\n"
            "\tsize_t tmp_len = *dlen;\n"
            "\tint err;\n"
            "\n"
            f"\terr = {comp_old};\n"
            "\n"
            "\tif (err < 0)\n"
            "\t\treturn -EINVAL;\n"
            "\n"
            "\t*dlen = tmp_len;\n"
            "\treturn 0;\n"
            "}\n",
            f"\tstruct {ctxf}_ctx *ctx = crypto_tfm_ctx(tfm);\n"
            f"\tint out_len = {comp_new};\n"
            "\n"
            "\tif (!out_len)\n"
            "\t\treturn -EINVAL;\n"
            "\n"
            "\t*dlen = out_len;\n"
            "\treturn 0;\n"
            "}\n",
            f"{name}: сжатие")
        replace_once(f,
            "\tint err;\n"
            "\tsize_t tmp_len = *dlen;\n"
            "\tsize_t __slen = slen;\n"
            "\n"
            "\terr = lz4_decompress_unknownoutputsize(src, __slen, dst, &tmp_len);\n"
            "\tif (err < 0)\n"
            "\t\treturn -EINVAL;\n"
            "\n"
            "\t*dlen = tmp_len;\n"
            "\treturn err;\n"
            "}\n",
            "\tint out_len = LZ4_decompress_safe(src, dst, slen, *dlen);\n"
            "\n"
            "\tif (out_len < 0)\n"
            "\t\treturn -EINVAL;\n"
            "\n"
            "\t*dlen = out_len;\n"
            "\treturn 0;\n"
            "}\n",
            f"{name}: распаковка")

    # pstore (в defconfig выключен, но правим, чтобы включение опции не ломало сборку)
    f = ROOT / "fs/pstore/platform.c"
    replace_once(f,
        "\tret = lz4_compress(in, inlen, out, &outlen, workspace);\n"
        "\tif (ret) {\n"
        "\t\tpr_err(\"lz4_compress error, ret = %d!\\n\", ret);\n"
        "\t\treturn -EIO;\n"
        "\t}\n"
        "\n"
        "\treturn outlen;\n",
        "\tret = LZ4_compress_default(in, out, inlen, outlen, workspace);\n"
        "\tif (!ret) {\n"
        "\t\tpr_err(\"LZ4_compress_default error; compression failed!\\n\");\n"
        "\t\treturn -EIO;\n"
        "\t}\n"
        "\n"
        "\treturn ret;\n",
        "pstore: сжатие")
    replace_once(f,
        "\tret = lz4_decompress_unknownoutputsize(in, inlen, out, &outlen);\n"
        "\tif (ret) {\n"
        "\t\tpr_err(\"lz4_decompress error, ret = %d!\\n\", ret);\n"
        "\t\treturn -EIO;\n"
        "\t}\n"
        "\n"
        "\treturn outlen;\n",
        "\tret = LZ4_decompress_safe(in, out, inlen, outlen);\n"
        "\tif (ret < 0) {\n"
        "\t\tpr_err(\"LZ4_decompress_safe error, ret = %d!\\n\", ret);\n"
        "\t\treturn -EIO;\n"
        "\t}\n"
        "\n"
        "\treturn ret;\n",
        "pstore: распаковка")
    replace_once(f, "lz4_compressbound(psinfo->bufsize)", "LZ4_compressBound(psinfo->bufsize)", "pstore: compressBound")

    # ничего со старым API остаться не должно
    left = []
    for pat in ("lz4_compress(", "lz4hc_compress(", "lz4_decompress(", "lz4_decompress_unknownoutputsize(", "lz4_compressbound("):
        for ext in ("*.c", "*.h"):
            for fp in ROOT.rglob(ext):
                if ".git" in fp.parts or "out" in fp.parts[:2]:
                    continue
                try:
                    if pat in fp.read_text(errors="ignore"):
                        left.append(f"{fp.relative_to(ROOT)}: {pat}")
                except OSError:
                    pass
    if left:
        die("после обновления LZ4 остались вызовы старого API:\n  " + "\n  ".join(left))
    info("LZ4: библиотека из 4.19.325, crypto/lz4, crypto/lz4hc, pstore, squashfs и unlz4 переведены на новый API")


def tweak_io():
    if IO_SCHED == "keep":
        return
    # CFQ_GROUP_IOSCHED нужен Android: blkio-группы (фон/передний план) получают вес только с ним
    for o in ("IOSCHED_CFQ", "CFQ_GROUP_IOSCHED", "IOSCHED_DEADLINE"):
        opt_on(o)
    for o in ("DEFAULT_CFQ", "DEFAULT_DEADLINE", "DEFAULT_NOOP"):
        (opt_on if o.endswith(IO_SCHED.upper()) else opt_off)(o)
    need.append(f'CONFIG_DEFAULT_IOSCHED="{IO_SCHED}"')
    markers.append("slice_idle")
    notes.append(f"io_sched: {IO_SCHED} (cfq+deadline собраны, CFQ_GROUP_IOSCHED)")


def patch_wbt_latency():
    """block/blk-wbt.c: цель задержки по умолчанию (wbt_default_latency_nsec) = WBT_LAT_USEC вместо 2000/75000 мкс."""
    p = ROOT / "block/blk-wbt.c"
    if not p.exists():
        die("wbt: нет block/blk-wbt.c (патч backports/wbt не наложен; шаг workflow «Эксперимент wbt» идёт раньше этого скрипта)")
    s = p.read_text()
    new = f"return {WBT_LAT_USEC * 1000}ULL;"
    old_nonrot, old_rot = "return 2000000ULL;", "return 75000000ULL;"
    if s.count(old_nonrot) == 1 and s.count(old_rot) == 1:
        p.write_text(s.replace(old_nonrot, new).replace(old_rot, new))
        info(f"blk-wbt.c: цель задержки по умолчанию {WBT_LAT_USEC} мкс")
    elif s.count(new) == 2:
        info(f"blk-wbt.c: цель задержки {WBT_LAT_USEC} мкс уже стоит")
    else:
        die("wbt: в block/blk-wbt.c не нашёл wbt_default_latency_nsec с return 2000000ULL / 75000000ULL (патч изменился?)")


def tweak_wbt():
    # сам патч (blk-wbt из 4.10) накладывает шаг workflow через scripts/backport_wbt.py до этого скрипта
    opt_on("BLK_WBT")
    opt_on("BLK_WBT_SQ")
    opt_off("BLK_WBT_MQ")
    need.append("CONFIG_BLK_WBT=y")
    need.append("CONFIG_BLK_WBT_SQ=y")
    markers.append("wbt_lat_usec")
    if WBT_LAT_USEC is None:
        lat = "по умолчанию 2000 мкс"
    else:
        patch_wbt_latency()
        lat = f"по умолчанию {WBT_LAT_USEC} мкс (задано через wbt{WBT_LAT_USEC})"
    notes.append(f"wbt: троттлинг фоновой записи (blk-wbt из 4.10, только legacy-очередь eMMC); цель задержки {lat}; "
                 "менять на лету: /sys/block/mmcblk0/queue/wbt_lat_usec, 0 = выключить")


def tweak_gov():
    if DEFAULT_GOV == "keep":
        return
    opt_on("CPU_FREQ_DEFAULT_GOV_SCHEDUTIL")
    opt_off("CPU_FREQ_DEFAULT_GOV_PERFORMANCE")
    notes.append("default_gov: schedutil (вместо performance)")


def tweak_boost():
    if not CPU_BOOST:
        return
    opt_on("CPU_BOOST")
    markers.append("input_boost_freq")
    notes.append("cpu_boost: драйвер включён (значения: группа runtime 'boost')")


def tweak_net():
    if NET_CC == "keep":
        return
    for o in ("TCP_CONG_ADVANCED", "TCP_CONG_BBR", "DEFAULT_BBR", "NET_SCH_FQ"):
        opt_on(o)
    for o in ("DEFAULT_WESTWOOD", "DEFAULT_CUBIC", "DEFAULT_RENO"):
        opt_off(o)
    need.append('CONFIG_DEFAULT_TCP_CONG="bbr"')
    notes.append("net_cc: bbr; fq как qdisc по умолчанию ставит init.kknx.rc "
                 "(в этом дереве нет compile-time выбора qdisc)")


def tweak_lmk():
    if LMK == "kernel":
        return
    opt_on("PSI")
    opt_off("PSI_DEFAULT_DISABLED")
    opt_off("ANDROID_LOW_MEMORY_KILLER")
    notes.append("lmk: psi (встроенный LMK убран, нужен lmkd с PSI)")


def tweak_spf():
    if not SPF:
        return
    opt_on("SPECULATIVE_PAGE_FAULT")
    notes.append("spf: Speculative Page Fault включён")


def tweak_ramoops():
    """pstore + ramoops: журнал ядра переживает перезагрузку. Узел в DT добавляет scripts/add_ramoops.py."""
    for o in ("PSTORE", "PSTORE_CONSOLE", "PSTORE_PMSG", "PSTORE_RAM"):
        opt_on(o)
    markers.append("ramoops")
    notes.append("ramoops: PSTORE, CONSOLE, PMSG, RAM включены; узел ramoops@9ff00000 (1 МБ) в DT; "
                 "после зависания смотрите /sys/fs/pstore")


def tweak_pelt():
    if PELT == "keep":
        return
    for h in ("8", "16", "32"):
        (opt_on if h == PELT else opt_off)(f"PELT_UTIL_HALFLIFE_{h}")
    notes.append(f"pelt: полураспад {PELT} мс (в defconfig автора 8)")


def tweak_lzorle():
    """lzo-rle: патч backports/lzorle накладывает apply_backports.py; здесь страховка и CRYPTO_LZO."""
    if not (ROOT / "crypto/lzo-rle.c").exists():
        die("experiments=lzorle, но crypto/lzo-rle.c нет: шаг apply_backports.py не отработал")
    opt_on("CRYPTO_LZO")
    markers.append("lzo-rle")
    notes.append("lzorle: алгоритм lzo-rle доступен в zram (/sys/block/zram0/comp_algorithm)")


def tweak_tcppace():
    """TCP internal pacing: патч backports/tcppace накладывает apply_backports.py; здесь только страховка."""
    if "tcp_pace_kick" not in (ROOT / "net/ipv4/tcp_output.c").read_text(errors="replace"):
        die("experiments=tcppace, но tcp_pace_kick в net/ipv4/tcp_output.c нет: шаг apply_backports.py не отработал")
    notes.append("tcppace: BBR и SO_MAX_PACING_RATE пейсятся внутри TCP, если на интерфейсе нет fq (rmnet, VPN)")


def tweak_utilest():
    """util_est: патч backports/utilest накладывает apply_backports.py; здесь только страховка."""
    p = ROOT / "kernel/sched/fair.c"
    if "sysctl_sched_util_est" not in p.read_text(errors="replace"):
        die("experiments=utilest, но sysctl_sched_util_est в kernel/sched/fair.c нет: шаг apply_backports.py не отработал")
    markers.append("sched_util_est")
    notes.append("utilest: schedutil берёт частоту не ниже суммы оценок по прошлым активациям задач в очереди (Linux 4.17); "
                 "на лету: kernel.sched_util_est 0/1 (0 = как без патча)"
                 + (f"; после загрузки {UTILEST_DEF}" if UTILEST_DEF is not None else ""))


def tweak_samefill():
    """zram page_same_filled: ранний выход по последнему слову; патч накладывает apply_backports.py."""
    if "last_pos" not in (ROOT / "drivers/block/zram/zram_drv.c").read_text(errors="replace"):
        die("experiments=samefill, но last_pos в zram_drv.c нет: шаг apply_backports.py не отработал")
    notes.append("samefill: page_same_filled выходит по последнему слову страницы (как в 5.4)")


def tweak_syncswap():
    """swap-in без swap cache для zram (4.15): патч накладывает apply_backports.py."""
    if "SWP_SYNCHRONOUS_IO" not in (ROOT / "include/linux/swap.h").read_text(errors="replace"):
        die("experiments=syncswap, но SWP_SYNCHRONOUS_IO в swap.h нет: шаг apply_backports.py не отработал")
    notes.append("syncswap: swap-in из zram мимо swap cache (SWP_SYNCHRONOUS_IO, Linux 4.15)")


def tweak_swapmax():
    """swappiness до 200: патч backports/swapmax."""
    if "two_hundred" not in (ROOT / "kernel/sysctl.c").read_text(errors="replace"):
        die("experiments=swapmax, но two_hundred в kernel/sysctl.c нет: шаг apply_backports.py не отработал")
    notes.append("swapmax: vm.swappiness допускает до 200" + (f"; группа vm пишет {SWAPPINESS}" if SWAPPINESS else
                 "; значение по умолчанию остаётся 100, задайте сами"))


def tweak_wsprot():
    """защита файлового кэша: патч backports/wsprot."""
    if "sysctl_file_protect_kbytes" not in (ROOT / "mm/vmscan.c").read_text(errors="replace"):
        die("experiments=wsprot, но sysctl_file_protect_kbytes в mm/vmscan.c нет: шаг apply_backports.py не отработал")
    markers.append("file_protect_kbytes")
    notes.append("wsprot: vm.file_protect_kbytes " + (f"= {WSPROT_MB * 1024} КБ ({WSPROT_MB} МБ), пишет группа vm" if WSPROT_MB else
                 "= 0 (выключено), включать вручную"))


def tweak_f2fs54():
    """fs/f2fs уровня 5.4: патч backports/f2fs54 (накладывает apply_backports.py)."""
    if not (ROOT / "fs/f2fs/compat54.h").exists():
        die("experiments=f2fs54, но fs/f2fs/compat54.h нет: шаг apply_backports.py не отработал")
    # строка появилась в super.c только в 5.4 (проверка io_bits при ремонте); в старой версии её нет
    markers.append("switch io_bits option is not allowed")
    notes.append("f2fs54: fs/f2fs уровня Linux 5.4 (verity, casefold и fscrypt v2 выключены заглушками); не проверено на устройстве, "
                 "перед прошивкой сделайте резервную копию /data")


def tweak_schedtun():
    """CONFIG_SCHED_DEBUG: без него у CFS нет настраиваемых sched_latency_ns и др.; DEBUG_KERNEL тянет DEBUG_PREEMPT=y по умолчанию, гасим."""
    opt_on("DEBUG_KERNEL")
    opt_on("SCHED_DEBUG")
    opt_off("DEBUG_PREEMPT")
    markers.append("sched_min_granularity_ns")
    notes.append("schedtun: SCHED_DEBUG включён (sched_latency_ns, sched_min_granularity_ns, sched_wakeup_granularity_ns, "
                 "sched_migration_cost_ns, /sys/kernel/debug/sched_features); DEBUG_PREEMPT принудительно выключен")


def tweak_perf():
    # в defconfig уже нет hardening (stackprotector, TTBR0_PAN, KPTI, SLUB_DEBUG), остаётся отладка в горячих путях и KASLR
    opt_off("RMNET_DATA_DEBUG_PKT")
    opt_off("RANDOMIZE_BASE")
    opt_off("RANDOMIZE_MODULE_REGION_FULL")
    notes.append("perf: RMNET_DATA_DEBUG_PKT и KASLR выключены (SUSFS не тронут)")


def tweak_preempt():
    opt_on("PREEMPT_VOLUNTARY")
    opt_off("PREEMPT")
    notes.append("preempt: PREEMPT_VOLUNTARY вместо полного PREEMPT")


def tweak_crypto():
    opt_on("ARM64_CRYPTO")
    for o in ("CRYPTO_AES_ARM64_NEON_BLK", "CRYPTO_AES_ARM64_CE_BLK", "CRYPTO_AES_ARM64_CE",
              "CRYPTO_SHA1_ARM64_CE", "CRYPTO_SHA2_ARM64_CE", "CRYPTO_GHASH_ARM64_CE"):
        frag.append(f"CONFIG_{o}=y")   # без need: часть символов может отсутствовать в 4.9, olddefconfig их отбросит
    need.append("CONFIG_CRYPTO_AES_ARM64_NEON_BLK=y")
    notes.append("crypto: ARM64 NEON/CE крипто (CE-драйверы сами не загрузятся, если CPU без расширений)")


def patch_lto():
    mk = ROOT / "Makefile"
    s = mk.read_text()
    s, n = re.subn(r"^LDFLAGS[ \t]*\+= -plugin LLVMgold\.so\n", "# KKNX: lld не нужен LLVMgold\n", s, count=1, flags=re.M)
    if n != 1:
        die("Makefile: не нашёл строку LDFLAGS += -plugin LLVMgold.so (дерево изменилось, нужна проверка)")
    if "--thinlto-jobs=$(nproc --all)" in s:
        s = s.replace("--thinlto-jobs=$(nproc --all)", "--thinlto-jobs=$(shell nproc)", 1)
    mk.write_text(s)
    frag.append("# CONFIG_LTO_NONE is not set")
    opt_on("LTO_CLANG")
    opt_on("THINLTO")
    notes.append("lto: ThinLTO через ld.lld (Makefile: убран -plugin LLVMgold.so, thinlto-jobs=$(shell nproc))")


def tweak_zram():
    if ZRAM_COMP == "keep":
        return
    if ZRAM_COMP == "lzo-rle" and not LZORLE:
        die("zram_comp=lzo-rle требует experiments=lzorle (preset добавляет его сам)")
    if ZRAM_COMP == "zstd":
        opt_on("CRYPTO_ZSTD")
        patch_zstd_makefile()
    elif ZRAM_COMP == "lz4":
        opt_on("CRYPTO_LZ4")
    patch_zram(ZRAM_COMP)
    notes.append(f"zram_comp: {ZRAM_COMP}")


# ======================================================================
#  Runtime: init.kknx.rc (читается init после vendor.post_boot.parsed=1, как init.lolz.rc)
# ======================================================================
# Частоты input boost: значения есть в таблицах cpufreq обоих кластеров (sdm439-olive.dtsi).
BOOST_FREQS = {0: 1305600, 1: 1305600, 2: 1305600, 3: 1305600, 4: 1171200, 5: 1171200, 6: 1171200, 7: 1171200}


def build_rc():
    groups = list(RUNTIME)
    lines = []

    if "io" in groups:
        lines.append("    # --- I/O: eMMC (mmcblk0) ---")
        if IO_SCHED != "keep":
            lines.append(f"    write /sys/block/mmcblk0/queue/scheduler {IO_SCHED}")
        if IO_SCHED == "cfq":
            # без простоя между запросами одного процесса: на flash он только задерживает диспетчеризацию
            lines.append("    write /sys/block/mmcblk0/queue/iosched/slice_idle 0")
        lines.append(f"    write /sys/block/mmcblk0/queue/read_ahead_kb {RA_KB}")
        lines.append("    write /sys/block/mmcblk0/queue/add_random 0")

    late = []
    if "vm" in groups:
        vm_lines = [
            "    # --- память: значения под zram ---",
            f"    write /proc/sys/vm/swappiness {SWAPPINESS or 100}",
            "    write /proc/sys/vm/page-cluster 0",
            # На устройстве встречались dirty_ratio 99, dirty_background_ratio 50 и 10 минут до сброса на диск
            # (60000 сотых секунды): на eMMC это редкие огромные залпы записи и потеря данных при зависании.
            "    # --- writeback: вернуть разумные пороги (ядро/прошивка/приложения могли выставить крайние) ---",
            "    write /proc/sys/vm/dirty_background_ratio 5",
            "    write /proc/sys/vm/dirty_ratio 20",
            "    write /proc/sys/vm/dirty_expire_centisecs 3000",
            "    write /proc/sys/vm/dirty_writeback_centisecs 500",
            # KSM на телефоне с zram только жжёт CPU (README: отвергнут); CONFIG_KSM=y в defconfig, включить может прошивка
            "    # --- KSM: слияние страниц на A53 не окупается ---",
            "    write /sys/kernel/mm/ksm/run 0",
        ]
        if WSPROT_MB:
            vm_lines.insert(3, f"    write /proc/sys/vm/file_protect_kbytes {WSPROT_MB * 1024}")
        lines += vm_lines
        late = vm_lines

    if "sched" in groups:
        lines.append("    # --- schedutil: не прыгать по частотам каждый тик, быстрее подниматься при I/O ---")
        for cpu in (0, 4):
            base = f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/schedutil"
            lines.append(f"    write {base}/up_rate_limit_us 500")
            lines.append(f"    write {base}/down_rate_limit_us 20000")
            lines.append(f"    write {base}/iowait_boost_enable 1")

    if UTILEST_DEF == 0:
        lines.append("    # --- util_est: по умолчанию в патче 1, здесь выключен после загрузки (utilest0) ---")
        lines.append("    write /proc/sys/kernel/sched_util_est 0")

    if "cfs" in groups:
        # штатные значения при 8 ядрах: latency 24 мс, min_granularity 3 мс, wakeup_granularity 4 мс (x4 за счёт log2 ядер)
        lines.append("    # --- CFS: короче окно планирования (латентность интерфейса под нагрузкой), цифры не измерены ---")
        lines.append("    write /proc/sys/kernel/sched_latency_ns 12000000")
        lines.append("    write /proc/sys/kernel/sched_min_granularity_ns 1500000")
        lines.append("    write /proc/sys/kernel/sched_wakeup_granularity_ns 2000000")

    if "cpuset" in groups:
        lines.append("    # --- cpuset: фоновые группы на ядра 0-3, передний план и top-app остаются на 0-7 ---")
        lines.append("    write /dev/cpuset/background/cpus 0-3")
        lines.append("    write /dev/cpuset/system-background/cpus 0-3")

    if "mem2" in groups:
        lines.append("    # --- память: watermark_scale_factor 30; штатно в ядре 10, но прошивка может ставить больше (на Redmi 8 было 100), сверьтесь с cat; тест 10 против 30 эффекта не дал ---")
        lines.append("    write /proc/sys/vm/watermark_scale_factor 30")

    if "boost" in groups and CPU_BOOST:
        lines.append("    # --- cpu_boost: короткий подъём минимальной частоты при касании (по умолчанию 40 мс, ставим 80) ---")
        pairs = " ".join(f"{c}:{f}" for c, f in BOOST_FREQS.items())
        lines.append(f'    write /sys/module/cpu_boost/parameters/input_boost_freq "{pairs}"')
        lines.append("    write /sys/module/cpu_boost/parameters/input_boost_ms 80")
        # без SCHED_WALT sched_set_boost() только возвращает -EINVAL и пишет ошибку в лог при каждом нажатии питания
        lines.append("    write /sys/module/cpu_boost/parameters/sched_boost_on_powerkey_input N")

    if NET_CC == "bbr":
        lines.append("    # --- сеть: BBR в 4.9 без внутреннего pacing требует fq ---")
        lines.append("    write /proc/sys/net/core/default_qdisc fq")

    if not lines:
        return None
    head = [
        "# KKNX runtime tweaks (генерируется сборщиком scripts/apply_tweaks.py).",
        "# Выполняется после vendor.post_boot.parsed=1, то есть после скриптов прошивки. Недоступный узел sysfs/proc",
        "# init просто пропускает. Проверить применённое: cat по тем же путям.",
        "",
        "on property:vendor.post_boot.parsed=1",
    ]
    body = head + lines
    if late:
        body += [
            "",
            "# Повтор для памяти: perf HAL и скрипты прошивки иногда переписывают эти узлы уже после post_boot.",
            "on property:sys.boot_completed=1",
        ] + late
    return "\n".join(body) + "\n"


def main():
    if not (ROOT / "Makefile").exists() or not (ROOT / "drivers").is_dir():
        die(f"{ROOT}: это не корень дерева ядра")

    if CC_OPT == "inline":
        patch_inline()
        notes.append("cc_opt: inline (штатные пороги inline/unroll clang)")
    if SUGOV_IOWAIT == "ramp":
        patch_sugov_iowait()
        notes.append("sugov_iowait: ramp (ступенчатый iowait boost, как в Linux 4.13)")
    if LZ4_UPDATE:
        patch_lz4()
        notes.append("lz4_update: библиотека LZ4 из 4.19.325 (новый API во всех вызывающих)")
    if ZSTD_UPDATE:
        patch_zstd()
        notes.append("zstd_update: kernel-ZSTD 1.5.7 из Linux 6.15+ (kernel-style API, crypto/zstd.c переведён)"
                     + (f"; уровень zstd по умолчанию = {ZSTD_LEVEL}" if ZSTD_LEVEL else ""))
    if "wbt" in EXPERIMENTS:
        tweak_wbt()
    if "honestfreq" in EXPERIMENTS:
        notes.append("honestfreq: верхние частоты CPU в DT заменены на реальные (ядра 0-3 2304 МГц, ядра 4-7 1613 МГц)")
    tweak_pelt()
    tweak_zram()
    tweak_io()
    tweak_gov()
    tweak_boost()
    tweak_net()
    tweak_lmk()
    tweak_spf()
    if RAMOOPS:
        tweak_ramoops()
    if LZORLE:
        tweak_lzorle()
    if TCPPACE:
        tweak_tcppace()
    if UTILEST:
        tweak_utilest()
    if SWAPMAX:
        tweak_swapmax()
    if WSPROT:
        tweak_wsprot()
    if SAMEFILL:
        tweak_samefill()
    if SYNCSWAP:
        tweak_syncswap()
    if F2FS54:
        tweak_f2fs54()
    if SCHEDTUN:
        tweak_schedtun()
    if PERF:
        tweak_perf()
    if PREEMPT_VOL:
        tweak_preempt()
    if CRYPTO_CE:
        tweak_crypto()
    if LTO:
        patch_lto()

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tweaks.fragment").write_text("\n".join(frag) + ("\n" if frag else ""))
    (OUT / "tweaks.need").write_text("\n".join(need) + ("\n" if need else ""))
    (OUT / "tweaks.markers").write_text("\n".join(markers) + ("\n" if markers else ""))

    rc = build_rc()
    rc_path = OUT / "init.kknx.rc"
    if rc:
        rc_path.write_text(rc)
        notes.append(f"runtime: init.kknx.rc, группы: {','.join(RUNTIME) or '-'}"
                     + (" + fq для BBR" if NET_CC == "bbr" else ""))
    elif rc_path.exists():
        rc_path.unlink()
    (OUT / "tweaks-info.txt").write_text("\n".join(notes) + ("\n" if notes else ""))

    info(f"в .config: {len(frag)} строк, проверяется после olddefconfig: {len(need)}, маркеров образа: {len(markers)}")
    for n in notes:
        info(n)
    if not notes:
        info("все твики выключены: сборка идентична прежней")


if __name__ == "__main__":
    main()
