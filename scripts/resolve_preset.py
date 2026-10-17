#!/usr/bin/env python3
"""Разворачивает preset в итоговые значения твиков и отдаёт их следующим шагам workflow.

Зачем: у workflow_dispatch лимит 25 полей, а полей про твики было много, и смешивать десяток
непроверенных изменений в одной сборке легко. Пресет задаёт безопасный набор одним выбором.

Вход (переменные окружения): PRESET, IO_SCHED, DEFAULT_GOV, CPU_BOOST, ZRAM_COMP, NET_CC, LMK,
RUNTIME_TUNE, EXPERIMENTS. Выход: строки T_<ИМЯ>=значение в файл $GITHUB_ENV (и сводка в
$GITHUB_STEP_SUMMARY, если он задан). Без GITHUB_ENV просто печатает результат (удобно для проверки).

Пресеты:
  custom    поля формы берутся как введены (по умолчанию: поведение прежнее)
  baseline  «как раньше»: ни одного твика, init.kknx.rc не создаётся
  stable    значения по умолчанию из workflow (cfq, schedutil, cpu_boost, lz4, io/vm/sched/boost)
  balanced  stable + experiments iowait, lz4, ramoops (малый риск, плюс журнал после зависания)
  perf      balanced + inline, perf, crypto, swap150 (то, что по замерам и анализу даёт выигрыш; без lto/preempt)
  lab       всё экспериментальное разом, чтобы проверить «собирается и грузится». Не для повседневной
            работы: при вылете виновника не найти. honestfreq сюда не входит (драйвер отклоняет частоты).

Если выбран не custom, поля io_sched ... runtime_tune игнорируются (скрипт предупредит, если их
меняли), а поле experiments ДОБАВЛЯЕТСЯ к набору: например balanced + wbt4000.
"""
import os
import sys

DEFAULTS = {
    "IO_SCHED": "cfq",
    "DEFAULT_GOV": "schedutil",
    "CPU_BOOST": "true",
    "ZRAM_COMP": "lz4",
    "NET_CC": "keep",
    "LMK": "kernel",
    "RUNTIME_TUNE": "io,vm,sched,boost",
}

PRESETS = {
    "baseline": dict(IO_SCHED="keep", DEFAULT_GOV="keep", CPU_BOOST="false", ZRAM_COMP="keep",
                     NET_CC="keep", LMK="kernel", RUNTIME_TUNE="", EXPERIMENTS=""),
    "stable": dict(DEFAULTS, EXPERIMENTS=""),
    "balanced": dict(DEFAULTS, EXPERIMENTS="iowait,lz4,ramoops"),
    "perf": dict(DEFAULTS, EXPERIMENTS="iowait,lz4,ramoops,inline,perf,crypto,swap150"),
    "lab": dict(DEFAULTS, ZRAM_COMP="zstd", NET_CC="bbr",
                EXPERIMENTS="iowait,lz4,zstd1,pelt16,wbt,ramoops,spf,inline,lzorle,tcppace,utilest,swapmax,wsprot,f2fs54,samefill,syncswap,schedtun,perf,preempt,crypto,ra128"),
}


def die(msg):
    print(f"[ОШИБКА] {msg}", file=sys.stderr)
    sys.exit(1)


def split_exp(raw):
    return [g.strip().lower() for g in raw.replace(";", ",").replace(" ", ",").split(",") if g.strip()]


def main():
    preset = (os.environ.get("PRESET") or "custom").strip().lower()
    if preset != "custom" and preset not in PRESETS:
        die(f"PRESET={preset!r}: допустимо custom, {', '.join(PRESETS)}")

    form = {k: (os.environ.get(k) if os.environ.get(k) is not None else DEFAULTS.get(k, ""))
            for k in list(DEFAULTS) + ["EXPERIMENTS"]}
    form["EXPERIMENTS"] = os.environ.get("EXPERIMENTS", "")

    if preset == "custom":
        eff = dict(form)
    else:
        eff = dict(PRESETS[preset])
        for k, dflt in DEFAULTS.items():
            if form[k].strip().lower() != dflt.lower():
                print(f"[preset] предупреждение: поле {k.lower()}={form[k]!r} игнорируется, "
                      f"потому что preset={preset} (выберите custom, если нужны свои значения)")
        extra = split_exp(form["EXPERIMENTS"])
        base = split_exp(eff["EXPERIMENTS"])
        eff["EXPERIMENTS"] = ",".join(base + [g for g in extra if g not in base])

    # зависимость: zram_comp=lzo-rle невозможен без бэкпорта lzorle
    if eff.get("ZRAM_COMP", "").strip().lower() == "lzo-rle" and "lzorle" not in split_exp(eff["EXPERIMENTS"]):
        eff["EXPERIMENTS"] = ",".join(split_exp(eff["EXPERIMENTS"]) + ["lzorle"])
        print("[preset] zram_comp=lzo-rle: добавлен experiments=lzorle (бэкпорт нужен для этого алгоритма)")

    eff["PRESET"] = preset
    lines = [f"T_{k}={v}" for k, v in eff.items()]
    for ln in lines:
        if "\n" in ln:
            die("в значениях полей не должно быть переводов строк")
        print(f"[preset] {ln}")

    env_file = os.environ.get("GITHUB_ENV")
    if env_file:
        with open(env_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Пресет: `{preset}`\n\n")
            f.write("| Поле | Итоговое значение |\n|---|---|\n")
            for k, v in eff.items():
                if k != "PRESET":
                    f.write(f"| {k.lower()} | `{v or '(пусто)'}` |\n")
            f.write("\n")


if __name__ == "__main__":
    main()
