#!/system/bin/sh
# kknx-test.sh v3.8: диагностика и замеры ядра KKNX (Redmi 8/8A, mi439)
#
# Запуск от root. Termux:  su -c "sh /sdcard/Download/kknx-test.sh diag"
#                 adb:     adb shell, затем su, затем sh /sdcard/Download/kknx-test.sh diag
#
#   diag               снимок состояния: ядро, cpufreq, sched, память, I/O, GPU, шина, термо, скрипты прошивки
#   quick [метка]      быстрый замер (CPU на 1 и на всех ядрах, память, диск), около 1 минуты; строка идёт в results.csv
#   ui                 плавность: прокрутка списка приложений в Настройках + статистика кадров
#   mem [пакеты...]    удержание фоновых приложений: открывает N приложений и смотрит, сколько осталось в памяти
#   zram               скорость сжатия/распаковки zram (lz4, lzo, zstd, lzo-rle) на отдельном временном устройстве (v3: с настоящей записью в zram и проверкой)
#   freq               реальная частота каждого ядра, скорость по ядрам и то, на каких ядрах планировщик держит нагрузку (около 1 минуты)
#   freqtable          реальная частота на каждой ступени таблицы cpu0 и cpu4 (около 1-2 минут; потом пределы возвращаются)
#   upmig              где держится один тяжёлый поток при разных sched_upmigrate/downmigrate (около 40 секунд; потом значения возвращаются)
#   tune               эксперименты "на лету" (без пересборки): пол частоты шины DDR, hispeed/rate_limit у schedutil, boost и prefer_idle у top-app.
#                      Каждый эксперимент: применить, замерить (разгон, задержка пробуждения, память, где работает поток), вернуть. Около 3-4 минут.
#   ddr on|off         держать шину DDR на максимуме (min_freq = max_freq) или вернуть как было; удобно для сравнения `ui` с включённым и выключенным
#   memlog start|stop|report  пассивный журнал памяти: раз в 10 минут пишет счётчики (свободная память, swap, число живых приложений, refault, allocstall)
#                      и ничего не нагружает; нужен вместо mem, если mem слишком тяжёл. report считает показатели в час за весь период записи
#   wbt                только для сборки с experiments=wbt: подбор /sys/block/mmcblk0/queue/wbt_lat_usec (0 = выкл, 2000, 8000, 30000).
#                      Для каждого значения: скорость записи без нагрузки и скорость чтения под фоновой буферной записью (около 4-5 минут)
#   untune             аварийно вернуть всё, что изменил tune (если его прервали)
#   iomix              чтение с диска без фона и во время фоновой записи, для планировщиков noop/deadline/cfq (временно переключает планировщик, потом возвращает; около 3 минут)
#   sustain [минут]    нагрузка на все ядра (по умолчанию 6 мин): как падает скорость и растёт температура
#   all [метка]        всё по очереди (diag, quick, freq, zram, iomix, ui, mem, sustain)
#
# Результаты: /data/local/tmp/kknx/ (копия отчёта diag кладётся в /sdcard/Download).
# Скрипт ничего не меняет в системе, кроме временных файлов в /data/local/tmp/kknx и сброса кэшей (drop_caches).
# Серийные номера и MAC из отчёта вырезаны, но перед отправкой всё равно просмотрите файл.

TMP=/data/local/tmp/kknx
[ -d /data/local/tmp ] || TMP=${TMPDIR:-/tmp}/kknx
mkdir -p "$TMP" 2>/dev/null
TS=$(date +%Y%m%d-%H%M%S)
CSV="$TMP/results.csv"

say() { printf '%s\n' "$*"; }
sec() { printf '\n=== %s ===\n' "$*"; }
rd() { [ -r "$1" ] && head -n 1 "$1" 2>/dev/null; }
now_cs() { read _u _r < /proc/uptime; echo "${_u%.*}${_u#*.}"; }          # сантисекунды с загрузки
fmt2() { printf '%d.%02d' $(($1 / 100)) $(($1 % 100)); }                 # сантисекунды -> секунды
mbps() { [ "$2" -gt 0 ] 2>/dev/null || { echo "-"; return; }; v=$(($1 * 1000 / $2)); printf '%d.%d' $((v / 10)) $((v % 10)); }
per_s() { [ "$2" -gt 0 ] 2>/dev/null || { echo "-"; return; }; echo $(($1 * 100 / $2)); }

dumpdir() { # каталог [макс. файлов]: имя = значение для всех обычных файлов
  [ -d "$1" ] || { say "(нет $1)"; return; }
  n=0
  for f in "$1"/*; do
    [ -f "$f" ] || continue
    case "${f##*/}" in uevent|*serial*|cid|csd|*_cid) continue ;; esac
    n=$((n + 1)); [ "$n" -gt "${2:-60}" ] && { say "... (обрезано)"; break; }
    printf '%s = ' "${f##*/}"
    head -c 160 "$f" 2>/dev/null | tr '\n' ' '
    echo
  done
}

# ---------------------------------------------------------------------------
#  diag
# ---------------------------------------------------------------------------
d_ident() {
  sec "Версия скрипта и время"
  say "kknx-test v3.7, $(date)"; say "uptime: $(uptime 2>/dev/null)"
  sec "Ядро"
  uname -a
  # из cmdline вырезаем серийник, MAC и прочие идентификаторы
  sed -E 's/(androidboot\.(serialno|cpuid|wifimacaddr|btmacaddr|imei[0-9a-z_]*|mac[a-z]*|ssn|uid))=[^ ]*/\1=<скрыто>/g' /proc/cmdline 2>/dev/null
  sec "Сборка ядра из /proc/config.gz (ключевые опции)"
  if [ -r /proc/config.gz ]; then
    zcat /proc/config.gz 2>/dev/null | grep -E '^(# )?CONFIG_(LOCALVERSION=|HZ=|HZ_|PREEMPT|SCHED_(WALT|CASS|TUNE)|PELT_UTIL|CPU_FREQ_DEFAULT_GOV|CPU_FREQ_GOV_|CPU_BOOST|IOSCHED_|DEFAULT_IOSCHED|CFQ_GROUP|PSI|MEMCG|ZRAM|CRYPTO_(ZSTD|LZ4)=|ANDROID_LOW_MEMORY|SPECULATIVE_PAGE_FAULT|LTO|SDCARD_FS|F2FS_FS=|MODULES=|BPF_JIT|TCP_CONG|DEFAULT_TCP_CONG|KSU|NOMOUNT|RANDOMIZE_BASE|SECCOMP=|MMC_CQ_HCI|MMC_CLKGATE|KSM|CMA_SIZE_MBYTES|CC_OPTIMIZE|NO_HZ|RCU_NOCB)' | head -80
  else say "(нет /proc/config.gz)"; fi
  sec "Прошивка и устройство"
  getprop | grep -E '^\[(ro\.(product\.(model|device|name)|build\.(display\.id|version\.(release|sdk)|type)|board\.platform|hardware|soc\.model|vendor\.build\.(id|date)|sys\.sdcardfs|config\.[a-z_.]*|zram[a-z_.]*|lmk\.[a-z_.]*|vendor\.qti\.[a-z_.]*)|persist\.(sys\.(fuse|sdcardfs|zram|lmk|perf|ssd)[a-z_.]*|vendor\.qti\.[a-z_.]*)|external_storage\.[a-z_.]*|sys\.(use_fifo_ui|boot_completed)|dalvik\.vm\.(heap[a-z]*|usejit|dex2oat[a-z_.-]*)|debug\.(sf|hwui|renderengine)\.[a-z_.]*|ro\.lmk\.[a-z_.]*)\]' | head -80
}

d_cpu() {
  sec "CPU: cpuinfo (выжимка)"
  grep -E '^(Features|CPU part|CPU implementer|CPU revision|Hardware)' /proc/cpuinfo 2>/dev/null | sort | uniq -c | head -12
  sec "CPU: online / isolated"
  say "online=$(rd /sys/devices/system/cpu/online) possible=$(rd /sys/devices/system/cpu/possible) isolated=$(rd /sys/devices/system/cpu/isolated)"
  sec "CPU: частоты и governor по ядрам"
  for c in /sys/devices/system/cpu/cpu[0-9]*; do
    n=${c##*/}; p=$c/cpufreq
    [ -d "$p" ] || { say "$n: cpufreq недоступен (ядро офлайн?)"; continue; }
    say "$n: gov=$(rd $p/scaling_governor) cur=$(rd $p/scaling_cur_freq) min=$(rd $p/scaling_min_freq) max=$(rd $p/scaling_max_freq) hw_max=$(rd $p/cpuinfo_max_freq) related=$(rd $p/related_cpus) cap=$(rd $c/cpu_capacity)"
  done
  sec "CPU: доступные governor'ы, частоты и твики governor (cpu0 и cpu4)"
  for n in 0 4; do
    p=/sys/devices/system/cpu/cpu$n/cpufreq
    [ -d "$p" ] || continue
    say "cpu$n governors: $(rd $p/scaling_available_governors)"
    say "cpu$n freqs: $(rd $p/scaling_available_frequencies)"
    g=$(rd $p/scaling_governor)
    [ -n "$g" ] && { say "-- cpu$n/$g:"; dumpdir "$p/$g"; }
  done
  sec "CPU: время на частотах с момента загрузки (кГц, сотые доли секунды) и число переключений"
  for n in 0 4; do
    p=/sys/devices/system/cpu/cpu$n/cpufreq/stats
    [ -r $p/time_in_state ] || continue
    say "-- cpu$n: total_trans=$(rd $p/total_trans)"; cat $p/time_in_state
  done
  sec "CPU: cpu_boost, lpm_levels, cpuidle"
  say "-- /sys/module/cpu_boost/parameters"; dumpdir /sys/module/cpu_boost/parameters
  say "-- /sys/module/lpm_levels/parameters"; dumpdir /sys/module/lpm_levels/parameters
  for s in /sys/devices/system/cpu/cpu0/cpuidle/state*; do
    [ -d "$s" ] || continue
    say "cpu0 ${s##*/}: name=$(rd $s/name) latency=$(rd $s/latency) residency=$(rd $s/residency) usage=$(rd $s/usage) time_us=$(rd $s/time) disable=$(rd $s/disable)"
  done
}

d_sched() {
  sec "Планировщик: /proc/sys/kernel/sched_*"
  for f in /proc/sys/kernel/sched_*; do [ -f "$f" ] && printf '%s = %s\n' "${f##*/}" "$(rd $f)"; done
  sec "Планировщик: stune (boost / prefer_idle) и cpuset"
  for g in /dev/stune /dev/stune/*; do
    [ -d "$g" ] && [ -f "$g/schedtune.boost" ] && say "stune${g#/dev/stune}: boost=$(rd $g/schedtune.boost) prefer_idle=$(rd $g/schedtune.prefer_idle)"
  done
  for g in /dev/cpuset /dev/cpuset/*; do
    [ -d "$g" ] && [ -f "$g/cpus" ] && say "cpuset${g#/dev/cpuset}: cpus=$(rd $g/cpus)"
  done
  for g in /dev/blkio /dev/blkio/*; do
    [ -d "$g" ] && [ -f "$g/blkio.weight" ] && say "blkio${g#/dev/blkio}: weight=$(rd $g/blkio.weight)"
  done
}

d_mem() {
  sec "Память: meminfo"
  grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapCached|Active\(anon\)|Inactive\(anon\)|Active\(file\)|Inactive\(file\)|SwapTotal|SwapFree|Shmem|Slab|KernelStack|PageTables|CmaTotal|CmaFree|Mlocked)' /proc/meminfo
  sec "Память: /proc/sys/vm"
  dumpdir /proc/sys/vm 80
  sec "Память: zram"
  for z in /sys/block/zram*; do
    [ -d "$z" ] || continue
    say "-- ${z##*/}: disksize=$(rd $z/disksize) algo=$(rd $z/comp_algorithm) streams=$(rd $z/max_comp_streams)"
    say "mm_stat: $(rd $z/mm_stat)"
  done
  sec "Память: swap"; cat /proc/swaps 2>/dev/null
  sec "Память: LMK ядра и PSI"
  say "-- /sys/module/lowmemorykiller/parameters"; dumpdir /sys/module/lowmemorykiller/parameters
  for f in /proc/pressure/*; do [ -f "$f" ] && { say "-- ${f##*/}"; cat "$f"; }; done
  sec "Память: vmstat (счётчики)"
  grep -E '^(pgscan_|pgsteal_|pswp|allocstall|workingset_refault|workingset_activate|pgmajfault|kswapd_|compact_stall|oom_kill|nr_free_cma)' /proc/vmstat | tr '\n' ' '; echo
  sec "Память: watermark'и зон"
  grep -E '^Node|^  (min|low|high|managed) ' /proc/zoneinfo | head -24
}

d_io() {
  sec "Диск: mmcblk0/queue"
  dumpdir /sys/block/mmcblk0/queue 40
  say "-- iosched"; dumpdir /sys/block/mmcblk0/queue/iosched
  sec "Диск: eMMC (без серийника)"
  dumpdir /sys/block/mmcblk0/device 30
  for f in /sys/class/mmc_host/mmc0/mmc0:0001/life_time /sys/class/mmc_host/mmc0/mmc0:0001/pre_eol_info; do
    [ -r "$f" ] && say "${f##*/}=$(rd $f)"
  done
  say "-- debugfs mmc0"
  for f in clock ios; do [ -r /sys/kernel/debug/mmc0/$f ] && { printf '%s: ' $f; head -n 12 /sys/kernel/debug/mmc0/$f | tr '\n' ' '; echo; }; done
  sec "Диск: dm-* read_ahead и очереди"
  for d in /sys/block/dm-*; do [ -d "$d" ] && say "${d##*/}: ra=$(rd $d/queue/read_ahead_kb) sched=$(rd $d/queue/scheduler)"; done
  sec "Файловые системы и монтирование"
  grep -E 'sdcardfs|fuse|f2fs|ext4' /proc/filesystems | tr '\n' ' '; echo
  grep -E ' (/data|/system|/vendor|/cache|/mnt/runtime/default/emulated|/storage/emulated|/sdcard) ' /proc/mounts | cut -c1-190 | head -14
  for d in /sys/fs/f2fs/*; do [ -d "$d" ] && { say "-- f2fs ${d##*/}"; dumpdir "$d" 40; }; done
  sec "dmesg: mmc / cmdq"
  dmesg 2>/dev/null | grep -iE 'mmc0|cmdq|hs400|hs200|sdhci' | head -14
}

d_gpu() {
  sec "GPU (kgsl)"
  g=/sys/class/kgsl/kgsl-3d0
  for k in gpuclk max_gpuclk num_pwrlevels default_pwrlevel max_pwrlevel min_pwrlevel thermal_pwrlevel idle_timer force_clk_on force_bus_on force_rail_on force_no_nap gpu_busy_percentage gpu_available_frequencies; do
    [ -r $g/$k ] && printf '%s = %s\n' "$k" "$(rd $g/$k)"
  done
  say "-- devfreq"; dumpdir $g/devfreq 20
  say "-- реальная частота gfx3d (нужен debugfs)"
  rd /sys/kernel/debug/clk/gcc_oxili_gfx3d_clk/measure
  sec "devfreq: шина DDR и прочее"
  for d in /sys/class/devfreq/*; do
    [ -d "$d" ] || continue
    say "-- ${d##*/}: gov=$(rd $d/governor) cur=$(rd $d/cur_freq) min=$(rd $d/min_freq) max=$(rd $d/max_freq)"
    say "   freqs: $(rd $d/available_frequencies)"
    for h in $d/*hwmon* $d/bw_hwmon; do [ -d "$h" ] && { say "   [${h##*/}]"; dumpdir "$h" 30; }; done
  done
}

d_thermal() {
  sec "Термо: зоны"
  for z in /sys/class/thermal/thermal_zone*; do
    [ -d "$z" ] && say "${z##*/}: $(rd $z/type) = $(rd $z/temp)"
  done | head -40
  sec "Термо: батарея"
  for k in capacity temp status health current_now voltage_now; do
    [ -r /sys/class/power_supply/battery/$k ] && printf '%s = %s\n' $k "$(rd /sys/class/power_supply/battery/$k)"
  done
  sec "Термо: процессы thermal-engine"
  ps -A 2>/dev/null | grep -iE 'thermal|perf|mi_thermald|thermanager' | head -8
}

d_vendor() {
  sec "Скрипты прошивки, которые трогают CPU/память/диск"
  for f in /vendor/bin/init.qcom.post_boot.sh /vendor/bin/init.qcom.sh /system/vendor/bin/init.qcom.post_boot.sh; do
    [ -r "$f" ] || continue
    say "-- $f (строки про governor/sched/vm/devfreq/kgsl/queue/zram/lmk)"
    grep -nE 'scaling_governor|scaling_m(in|ax)_freq|schedutil|interactive|sched_|swappiness|extra_free|min_free|watermark|vfs_cache|dirty_|page-cluster|devfreq|kgsl|/queue/|read_ahead|zram|lowmemorykiller|minfree|cpu_boost|stune|cpuset|iowait|hispeed|rate_limit|hmp|core_ctl' "$f" | cut -c1-170 | head -150
    break
  done
  sec "init.rc: наши файлы и blkio"
  ls -l /vendor/etc/init/init.kknx.rc /vendor/etc/init/init.lolz.rc 2>&1 | cut -c1-150
  [ -r /vendor/etc/init/init.kknx.rc ] && { say "-- init.kknx.rc"; cat /vendor/etc/init/init.kknx.rc; }
  grep -rnE 'blkio|schedtune|cpuset|swappiness|extra_free' /system/etc/init /vendor/etc/init 2>/dev/null | cut -c1-170 | head -40
  sec "perf HAL / powerhint"
  ls /vendor/etc/perf /vendor/etc/powerhint.json /vendor/etc/powerhint* 2>/dev/null | head -20
}

d_memmap() {
  sec "Карта памяти (/proc/iomem: System RAM и зарезервированное)"
  grep -iE 'system ram|reserved|ramoops|kernel (code|data)|crash' /proc/iomem 2>/dev/null | head -40
  sec "pstore (журнал прошлой аварии, если он есть)"
  ls -l /sys/fs/pstore 2>&1 | head -12
  for f in /proc/last_kmsg /sys/fs/pstore/console-ramoops /sys/fs/pstore/console-ramoops-0; do
    [ -r "$f" ] && say "$f: $(wc -c < $f) байт"
  done
}

d_log() {
  sec "dmesg: ошибки и важное (до 120 строк)"
  dmesg 2>/dev/null | grep -iE 'kknx|cpu_boost|schedutil|lowmemory|lmk|oom|thermal|throttl|panic|oops|bug:|warn|zram|kgsl|cpr|fail|error' | grep -viE 'wlan|wifi|bluetooth' | tail -n 120 | cut -c1-200
  sec "logcat: lmkd (последние 40)"
  logcat -d -s lmkd 2>/dev/null | tail -n 40 | cut -c1-200
}


d_ident2() {
  sec "Что за ядро (сверка с билдером)"
  say "uname -r: $(uname -r)"
  say "uname -v: $(uname -v)"
  if [ -r /proc/config.gz ]; then
    cfgv() { zcat /proc/config.gz 2>/dev/null | sed -n "s/^CONFIG_$1=//p" | head -n 1; }
    say "config: HZ=$(cfgv HZ) LOCALVERSION=$(cfgv LOCALVERSION) BPF_JIT=$(cfgv BPF_JIT) DEFAULT_GOV=$(zcat /proc/config.gz 2>/dev/null | sed -n 's/^CONFIG_CPU_FREQ_DEFAULT_GOV_\([A-Z_]*\)=y/\1/p' | head -n 1)"
  fi
  case "$(uname -r)" in
    *KKNX*) say "метка KKNX в uname есть: ядро собрано kknx-builder" ;;
    *) say "метки KKNX в uname нет: либо ядро из прошивки/другой сборки, либо localversion в билдере переопределён" ;;
  esac
  say "Билдер с lolz_port=full всегда даёт HZ=300 и BPF_JIT=y; HZ=100 здесь значит другую сборку."
}

d_topo() {
  sec "Топология ядер и что считает планировщик (DT: efficiency / capacity-dmips-mhz)"
  for c in /sys/devices/system/cpu/cpu[0-9]*; do
    n=${c##*/}
    say "$n: package=$(rd $c/topology/physical_package_id) core_id=$(rd $c/topology/core_id) siblings=$(rd $c/topology/core_siblings_list) capacity=$(rd $c/cpu_capacity)"
  done
  for d in /proc/device-tree/cpus/cpu@*; do
    [ -d "$d" ] || continue
    line="${d##*/}:"
    for pn in reg efficiency capacity-dmips-mhz enable-method; do
      [ -r "$d/$pn" ] || continue
      line="$line $pn=$(od -An -tx1 "$d/$pn" 2>/dev/null | tr -d '\n' | tr -s ' ')"
    done
    say "$line"
  done
  say "-- WALT/HMP настройки планировщика:"
  for f in sched_upmigrate sched_downmigrate sched_group_upmigrate sched_group_downmigrate sched_init_task_load sched_small_task sched_spill_load sched_boost sched_prefer_sync_wakee_to_waker sched_window_stats_policy sched_ravg_hist_size; do
    [ -r /proc/sys/kernel/$f ] && say "$f = $(rd /proc/sys/kernel/$f)"
  done
}

d_dirty() {
  sec "Кто выставляет vm.dirty_* (поиск по скриптам прошивки и модулей)"
  say "сейчас: expire=$(rd /proc/sys/vm/dirty_expire_centisecs) writeback=$(rd /proc/sys/vm/dirty_writeback_centisecs) ratio=$(rd /proc/sys/vm/dirty_ratio) background=$(rd /proc/sys/vm/dirty_background_ratio)"
  lst="$TMP/dirty-files.lst"
  find /vendor /system/etc /system_ext/etc /product/etc /odm/etc /data/adb /data/local/tmp -maxdepth 7 -type f \( -name '*.rc' -o -name '*.sh' -o -name '*.xml' -o -name '*.conf' -o -name '*.json' -o -name '*.prop' -o -name 'service.d' \) 2>/dev/null > "$lst"
  if [ -s "$lst" ]; then
    xargs grep -n -E 'dirty_(expire|writeback|ratio|background)' < "$lst" 2>/dev/null | cut -c1-200 | head -30
  fi
  say "(пусто выше = в этих каталогах не нашлось; тогда значения задаёт ядро по умолчанию или init из boot-образа)"
  getprop 2>/dev/null | grep -iE 'dirty|vm\.' | head -10
}

d_pstore() {
  sec "pstore: содержимое журналов прошлой аварии (серийники скрыты)"
  [ -r /sys/fs/pstore/console-ramoops-0 ] && say "ядро, которое писало console-ramoops: $(grep -m 1 'Linux version' /sys/fs/pstore/console-ramoops-0 2>/dev/null | cut -c1-160)"
  for f in /sys/fs/pstore/dmesg-ramoops-*; do
    [ -r "$f" ] || continue
    say "--- $f ($(wc -c < "$f") байт)"
    head -c 14000 "$f" 2>/dev/null | sed -E 's/(androidboot\.(serialno|cpuid|wifimacaddr|btmacaddr|imei[0-9a-z_]*|mac[a-z]*|ssn|uid))=[^ ]*/\1=<скрыто>/g' | cut -c1-220
  done
  for f in /sys/fs/pstore/console-ramoops-0 /proc/last_kmsg; do
    [ -r "$f" ] || continue
    say "--- хвост $f (последние 120 строк)"
    tail -n 120 "$f" 2>/dev/null | sed -E 's/(androidboot\.(serialno|cpuid|wifimacaddr|btmacaddr|imei[0-9a-z_]*|mac[a-z]*|ssn|uid))=[^ ]*/\1=<скрыто>/g' | cut -c1-220
  done
  [ -e /sys/fs/pstore/dmesg-ramoops-0 ] || say "(dmesg-ramoops нет: аварий с записью не было)"
}

cmd_diag() {
  out="$TMP/diag-$TS.txt"
  say "Собираю отчёт в $out (около 10-20 секунд)..."
  { d_ident; d_ident2; d_cpu; d_topo; d_sched; d_mem; d_dirty; d_io; d_gpu; d_thermal; d_vendor; d_memmap; d_pstore; d_log; } > "$out" 2>&1
  for d in /sdcard/Download /storage/emulated/0/Download; do
    [ -d "$d" ] && cp "$out" "$d/" 2>/dev/null && { say "Копия: $d/diag-$TS.txt"; break; }
  done
  say "Готово: $out ($(wc -c < "$out") байт). Просмотрите файл и пришлите его мне."
}

# ---------------------------------------------------------------------------
#  бинарник замеров (kknx-bench лежит рядом со скриптом; копируется в /data/local/tmp/kknx, чтобы его можно было запускать)
# ---------------------------------------------------------------------------
HERE=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
BENCH="$TMP/kknx-bench"

find_bench() {
  for c in "$HERE/kknx-bench" /sdcard/Download/kknx-bench /storage/emulated/0/Download/kknx-bench "$BENCH"; do
    [ -f "$c" ] || continue
    [ "$c" = "$BENCH" ] || cp -f "$c" "$BENCH" 2>/dev/null
    chmod 755 "$BENCH" 2>/dev/null
    [ -x "$BENCH" ] && return 0
  done
  return 1
}

need_bench() {
  find_bench && return 0
  say "Не найден kknx-bench. Положите файл kknx-bench в ту же папку, что и скрипт (например Download), и запустите снова."
  return 1
}

stable_hint() {
  say "Условия для сравнимых цифр: экран включён, зарядка отключена, телефон 5 минут в покое, одинаковый заряд и температура, фоновые синхронизации выключены (лучше режим полёта)."
}

# ---------------------------------------------------------------------------
#  время на частотах за время теста (дельта time_in_state)
# ---------------------------------------------------------------------------
tis_snap() { # метка
  for n in 0 4; do
    cat /sys/devices/system/cpu/cpu$n/cpufreq/stats/time_in_state > "$TMP/tis$n.$1" 2>/dev/null
  done
}

tis_report() { # от метки до метки; печатает долю времени на каждой частоте
  for n in 0 4; do
    [ -s "$TMP/tis$n.$1" ] && [ -s "$TMP/tis$n.$2" ] || continue
    while read -r f t; do eval "b_$f=$t"; done < "$TMP/tis$n.$1"
    tot=0
    while read -r f t; do eval "b=\${b_$f:-0}"; tot=$((tot + t - b)); done < "$TMP/tis$n.$2"
    [ "$tot" -gt 0 ] || continue
    line="cpu$n:"
    while read -r f t; do
      eval "b=\${b_$f:-0}"; d=$((t - b))
      [ "$d" -gt 0 ] && line="$line $((f / 1000))МГц=$((d * 100 / tot))%"
    done < "$TMP/tis$n.$2"
    say "$line"
  done
}

# ---------------------------------------------------------------------------
#  zram: скорость сжатия и распаковки через настоящий путь ядра (отдельное устройство, swap не трогаем)
# ---------------------------------------------------------------------------
cmd_zram() {
  [ -w /sys/class/zram-control/hot_add ] || { say "Нет /sys/class/zram-control/hot_add (нужны root и zram с hot_add)."; return 1; }
  mb=${KKNX_ZMB:-288}
  data="$TMP/zdata.bin"
  # файл данных пересобирается, если запрошенный размер изменился (метка рядом с файлом)
  [ "$(cat "$data.mb" 2>/dev/null)" = "$mb" ] || rm -f "$data"
  if [ ! -s "$data" ]; then
    say "Готовлю тестовые данные ($mb МБ): склеиваю родные библиотеки из /system/lib64 (сжимаются примерно как код/куча приложений)..."
    : > "$data"
    for f in /system/lib64/*.so /system/lib64/*/*.so; do
      [ -f "$f" ] || continue
      cat "$f" >> "$data"
      [ "$(wc -c < "$data")" -ge $((mb * 1048576)) ] && break
    done
    head -c $((mb * 1048576)) "$data" > "$data.cut" && mv "$data.cut" "$data"
    echo "$mb" > "$data.mb"
  fi
  size=$(wc -c < "$data")
  pins=${KKNX_ZPINS:-"0 4"}
  if ! taskset 1 true >/dev/null 2>&1; then
    say "taskset с hex-маской не работает: прогоны без привязки к ядру"
    pins="-"
  fi
  say "Данные: $((size / 1048576)) МБ. Алгоритмы: ${KKNX_ZALGS:-lz4 lzo zstd lzo-rle}. Ядра для прогона: $pins (0 и 4 = разные кластеры)"
  say "v3.3: на время прогона min-частота ядра = max (отключить: KKNX_ZLOCK=0); степень сжатия считается без переполнения."
  say "v3: запись идёт напрямую в zram (O_DIRECT, иначе с sync), чтение после drop_caches; если в zram попало меньше 90% данных, замер помечается недействительным."
  out="$TMP/zram-$TS.txt"; : > "$out"
  smb=$((size / 1048576))
  for alg in ${KKNX_ZALGS:-lz4 lzo zstd lzo-rle}; do
    for cpu in $pins; do
      id=$(cat /sys/class/zram-control/hot_add 2>/dev/null)
      [ -n "$id" ] || { say "hot_add не сработал"; break 2; }
      z=/sys/block/zram$id
      if ! echo "$alg" > $z/comp_algorithm 2>/dev/null; then
        say "$alg: ядро не поддерживает"; echo "$id" > /sys/class/zram-control/hot_remove 2>/dev/null; break
      fi
      echo "$size" > $z/disksize
      dev=/dev/block/zram$id; [ -b "$dev" ] || dev=/dev/zram$id
      if [ ! -b "$dev" ]; then
        # ueventd мог не создать узел для hot_add-устройства: делаем сами из major:minor (иначе dd молча пишет ОБЫЧНЫЙ файл в /dev)
        mm=$(cat $z/dev 2>/dev/null)
        dev=/dev/kknx-zram$id; rm -f "$dev"
        [ -n "$mm" ] && mknod "$dev" b "${mm%%:*}" "${mm##*:}" 2>/dev/null
      fi
      if [ ! -b "$dev" ]; then
        say "$alg: нет блочного узла для zram$id (major:minor=${mm:-?}), замер невозможен"
        echo "$id" > /sys/class/zram-control/hot_remove 2>/dev/null; break 2
      fi
      pre=""; [ "$cpu" != "-" ] && pre="taskset $(printf '%x' $((1 << cpu)))"
      zl_p=""
      if [ "$cpu" != "-" ] && [ "${KKNX_ZLOCK:-1}" = 1 ]; then
        zl_p=/sys/devices/system/cpu/cpu$cpu/cpufreq
        zl_omin=$(rd $zl_p/scaling_min_freq)
        echo "$(rd $zl_p/scaling_max_freq)" > $zl_p/scaling_min_freq 2>/dev/null
      fi
      sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null
      tis_snap z0
      t0=$(now_cs)
      derr="$TMP/zdd.err"; : > "$derr"
      if ! $pre dd if="$data" of="$dev" bs=1048576 oflag=direct 2>"$derr"; then
        d1=$(head -n 1 "$derr")
        t0=$(now_cs)
        $pre dd if="$data" of="$dev" bs=1048576 2>"$derr"
        sync
        d2=$(head -n 1 "$derr")
      fi
      t1=$(now_cs)
      set -- $(cat $z/mm_stat)
      orig=${1:-0}; comp=${2:-0}; same=${6:-0}
      echo 3 > /proc/sys/vm/drop_caches 2>/dev/null
      t1b=$(now_cs)
      rmode="direct"
      if ! $pre dd if="$dev" of=/dev/null bs=1048576 iflag=direct 2>/dev/null; then
        rmode="через кэш (iflag=direct не сработал)"
        t1b=$(now_cs)
        $pre dd if="$dev" of=/dev/null bs=1048576 2>/dev/null
      fi
      t2=$(now_cs)
      tis_snap z1
      [ -n "$zl_p" ] && echo "$zl_omin" > $zl_p/scaling_min_freq 2>/dev/null
      if [ "$orig" -lt $((size * 9 / 10)) ]; then
        msg="$alg [ядро $cpu]: НЕДЕЙСТВИТЕЛЬНО: в zram записалось $((orig / 1048576)) МБ из $smb. dev=$dev ($(ls -l "$dev" 2>&1 | cut -c1-60)) disksize=$(cat $z/disksize) mm_stat=[$(cat $z/mm_stat)] pre=[$pre] dd(direct)=[$d1] dd(buffered)=[$d2]"
      else
        ratio=$([ "$((comp / 1024))" -gt 0 ] && echo "$(( (orig / 1024) * 100 / (comp / 1024) ))" || echo 0)
        msg="$alg [ядро $cpu]: сжатие+запись $(mbps $smb $((t1 - t0))) МБ/с, распаковка+чтение $(mbps $smb $((t2 - t1b))) МБ/с, степень сжатия $(printf '%d.%02d' $((ratio / 100)) $((ratio % 100))):1, одинаковых страниц $((same / 256)) МБ, чтение: ${rmode:-?}"
      fi
      say "$msg"; echo "$msg" >> "$out"
      tis_report z0 z1 | tee -a "$out"
      rm -f /dev/kknx-zram$id
      echo "$id" > /sys/class/zram-control/hot_remove 2>/dev/null
    done
  done
  say "Подробности: $out"
}

cmd_freq() {
  need_bench || return 1
  stable_hint
  say "Замер идёт около минуты."
  out="$TMP/freq-$TS.txt"
  { "$BENCH" freq; "$BENCH" where 1 5; "$BENCH" where 4 5; } | tee "$out"
  say "Файл: $out"
}

# iomix: диск без фона и во время фоновой записи (в группе blkio/background, если она есть), для каждого планировщика
# v3.5: фоновая запись ограничена по времени и убирается при любом выходе; планировщик всегда возвращается
cmd_iomix() {
  need_bench || return 1
  sf=/sys/block/mmcblk0/queue/scheduler
  [ -w "$sf" ] || { say "Нет доступа к $sf (нужен root и диск mmcblk0)."; return 1; }
  orig=$(sed 's/.*\[\(.*\)\].*/\1/' "$sf")
  list=${KKNX_SCHEDS:-"noop deadline cfq"}
  out="$TMP/iomix-$TS.txt"; : > "$out"
  TOUT=""; command -v timeout >/dev/null 2>&1 && TOUT=1
  iomix_clean() { pkill -f bgw.bin 2>/dev/null; rm -f "$TMP/bgw.bin"; echo "$orig" > "$sf" 2>/dev/null; }
  trap 'iomix_clean' EXIT INT TERM
  grp=""
  if [ -d /dev/blkio/background ]; then
    grp='echo $$ > /dev/blkio/background/tasks 2>/dev/null;'
    say "Группа blkio/background есть: фоновая запись идёт в неё (как у Android для фоновых задач)."
  else
    say "Группы blkio/background нет: фоновая запись идёт без группы."
  fi
  say "Исходный планировщик: $orig. Он переключается на время замера и вернётся сам, даже если прервать скрипт."
  for sch in $list; do
    grep -q "$sch" "$sf" || { say "$sch: нет в ядре"; continue; }
    echo "$sch" > "$sf"
    sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null
    if [ -n "$TOUT" ]; then r=$(timeout 90 "$BENCH" io "$TMP" 128 2>&1 | tail -n 1); else r=$("$BENCH" io "$TMP" 128 2>&1 | tail -n 1); fi
    msg="[$sch] без фона: ${r:-не уложился в 90 с}"; say "$msg"; echo "$msg" >> "$out"
    if [ -n "$TOUT" ]; then
      timeout 60 sh -c "$grp while :; do dd if=/dev/zero of='$TMP/bgw.bin' bs=1048576 count=64 conv=fsync 2>/dev/null; done" &
    else
      sh -c "$grp while :; do dd if=/dev/zero of='$TMP/bgw.bin' bs=1048576 count=64 conv=fsync 2>/dev/null; done" &
    fi
    wp=$!
    sleep 2
    if [ -n "$TOUT" ]; then r=$(timeout 90 "$BENCH" io "$TMP" 128 2>&1 | tail -n 1); else r=$("$BENCH" io "$TMP" 128 2>&1 | tail -n 1); fi
    kill $wp 2>/dev/null; pkill -f bgw.bin 2>/dev/null; sleep 1; rm -f "$TMP/bgw.bin"
    msg="[$sch] с фоновой записью: ${r:-не уложился в 90 с}"; say "$msg"; echo "$msg" >> "$out"
  done
  iomix_clean
  trap - EXIT INT TERM
  say "Планировщик возвращён: $(rd $sf)"
  say "Файл: $out"
}

# ---------------------------------------------------------------------------
#  quick: CPU / память / диск / задержка / разгон; строка идёт в results.csv
# ---------------------------------------------------------------------------
# freqtable: реальная частота на каждой ступени таблицы (временно фиксирует min=max, потом возвращает)
cmd_freqtable() {
  need_bench || return 1
  stable_hint
  out="$TMP/freqtable-$TS.txt"; : > "$out"
  for cpu in 0 4; do
    p=/sys/devices/system/cpu/cpu$cpu/cpufreq
    [ -w $p/scaling_max_freq ] || { say "cpu$cpu: нет доступа к cpufreq"; continue; }
    omin=$(rd $p/scaling_min_freq); omax=$(rd $p/scaling_max_freq)
    freqs=$(rd $p/scaling_available_frequencies)
    low=${freqs%% *}
    say "cpu$cpu: ступени: $freqs"
    for f in $freqs; do
      echo "$low" > $p/scaling_min_freq; echo "$f" > $p/scaling_max_freq; echo "$f" > $p/scaling_min_freq
      sleep 1
      cur=$(rd $p/scaling_cur_freq)
      m=$("$BENCH" mhz $cpu)
      msg="cpu$cpu: заявлено $((f / 1000)) МГц, scaling_cur $((cur / 1000)) МГц, реально $m"
      say "$msg"; echo "$msg" >> "$out"
    done
    echo "$omax" > $p/scaling_max_freq; echo "$omin" > $p/scaling_min_freq
    say "cpu$cpu: возвращено min=$(rd $p/scaling_min_freq) max=$(rd $p/scaling_max_freq)"
  done
  say "Файл: $out"
}

# upmig: где держится один тяжёлый поток при разных sched_upmigrate/downmigrate (потом значения возвращаются)
cmd_upmig() {
  need_bench || return 1
  u=/proc/sys/kernel/sched_upmigrate; d=/proc/sys/kernel/sched_downmigrate
  [ -w $u ] || { say "Нет $u (нужны root и HMP-планировщик)."; return 1; }
  ou=$(rd $u); od=$(rd $d)
  out="$TMP/upmig-$TS.txt"; : > "$out"
  for pair in "$ou $od" "80 65" "70 50" "60 40"; do
    set -- $pair
    echo "$2" > $d; echo "$1" > $u
    say "sched_upmigrate=$(rd $u) sched_downmigrate=$(rd $d):"
    { "$BENCH" where 1 4; "$BENCH" where 4 4; } | tee -a "$out"
  done
  echo "$ou" > $u; echo "$od" > $d
  say "Возвращено: up=$(rd $u) down=$(rd $d). Файл: $out"
}

# ---------- tune: эксперименты на лету ----------
TUNE_SAVE="$TMP/tune-save.txt"

tset() { # путь значение: запоминает старое значение и пишет новое
  [ -w "$1" ] || { say "  (нет доступа: $1)"; return 1; }
  echo "$1 $(head -n 1 "$1" 2>/dev/null)" >> "$TUNE_SAVE"
  echo "$2" > "$1" 2>/dev/null
}

trestore() { # вернуть всё в обратном порядке
  [ -s "$TUNE_SAVE" ] || return 0
  tac "$TUNE_SAVE" | while read -r p v; do echo "$v" > "$p" 2>/dev/null; done
  : > "$TUNE_SAVE"
}

tmeasure() { # имя: ставит строку с метриками
  tn=$1
  r=$("$BENCH" ramp -1 2>&1 | tail -n 1 | sed 's/.*первая пачка \([0-9.]*\) мс.*за \([0-9]*\) мс.*/\1 \2/')
  l=$("$BENCH" lat 3 2>&1 | tail -n 1 | sed 's/.*p50=\([0-9]*\) p90=[0-9]* p99=\([0-9]*\) max=\([0-9]*\).*/\1 \2 \3/')
  m=$("$BENCH" mem 64 2>&1 | tr '\n' ' ' | sed 's/.*: \([0-9]*\) МБ\/с.*: \([0-9.]*\) нс.*/\1 \2/')
  w=$("$BENCH" where 1 3 2>&1 | tail -n 1 | sed 's/.*ядра 4-7 = \([0-9]*\)%.*/\1/')
  set -- $r; rf=${1:--}; rs=${2:--}
  set -- $l; p50=${1:--}; p99=${2:--}; pmax=${3:--}
  set -- $m; mc=${1:--}; ch=${2:--}
  msg=$(printf '%-12s разгон %5s/%4s мс | пробуждение p50 %3s p99 %4s max %5s мкс | memcpy %4s МБ/с, доступ %5s нс | на ядрах 4-7: %s%%' "$tn" "$rf" "$rs" "$p50" "$p99" "$pmax" "$mc" "$ch" "$w")
  say "$msg"; echo "$msg" >> "$TUNE_OUT"
}

texp() { # имя путь1 знач1 путь2 знач2 ...
  nm=$1; shift
  while [ $# -ge 2 ]; do tset "$1" "$2"; shift 2; done
  sleep 1
  tmeasure "$nm"
  trestore
}

cmd_ddr() {
  DV1=/sys/class/devfreq/soc:qcom,cpubw; DV2=/sys/class/devfreq/soc:qcom,mincpubw
  DDR_SAVE="$TMP/ddr-save.txt"; TUNE_SAVE="$DDR_SAVE"
  case "$1" in
    on)
      if [ -s "$DDR_SAVE" ]; then say "Уже включено."; else
        mx=$(rd $DV1/max_freq)
        tset $DV1/min_freq "$mx"; tset $DV2/min_freq "$mx"
      fi ;;
    off) trestore ;;
    *) say "Использование: ddr on | ddr off" ;;
  esac
  say "cpubw: cur=$(rd $DV1/cur_freq) min=$(rd $DV1/min_freq) max=$(rd $DV1/max_freq) | mincpubw: cur=$(rd $DV2/cur_freq) min=$(rd $DV2/min_freq)"
  [ -s "$DDR_SAVE" ] && say "DDR зафиксирована на максимуме. Верните командой: ddr off" || say "DDR в обычном режиме."
}

cmd_untune() {
  TUNE_SAVE="$TMP/ddr-save.txt"; trestore
  TUNE_SAVE="$TMP/tune-save.txt"; trestore
  [ -d /dev/stune ] && echo $$ > /dev/stune/tasks 2>/dev/null
  say "Возвращено всё, что запомнил tune."
}

cmd_tune() {
  need_bench || return 1
  stable_hint
  TUNE_OUT="$TMP/tune-$TS.txt"; : > "$TUNE_OUT"; : > "$TUNE_SAVE"
  trap 'trestore; [ -d /dev/stune ] && echo $$ > /dev/stune/tasks 2>/dev/null; [ -d /dev/cpuset ] && echo $$ > /dev/cpuset/tasks 2>/dev/null' EXIT INT TERM
  say "Ядро: $(uname -r). Все замеры идут из группы top-app (как у приложения на экране)."
  [ -d /dev/stune/top-app ] && echo $$ > /dev/stune/top-app/tasks 2>/dev/null
  [ -d /dev/cpuset/top-app ] && echo $$ > /dev/cpuset/top-app/tasks 2>/dev/null

  S0=/sys/devices/system/cpu/cpu0/cpufreq/schedutil
  S4=/sys/devices/system/cpu/cpu4/cpufreq/schedutil
  ST=/dev/stune/top-app
  DV1=/sys/class/devfreq/soc:qcom,cpubw
  DV2=/sys/class/devfreq/soc:qcom,mincpubw
  # средняя и верхняя ступени шины DDR из таблицы устройства
  set -- $(rd $DV1/available_frequencies); nf=$#; half=$(((nf + 1) / 2)); i=0; ddr_mid=$1
  for f in "$@"; do i=$((i + 1)); [ $i -le $half ] && ddr_mid=$f; done
  ddr_max=$(rd $DV1/max_freq)
  say "DDR: ступеней $nf, средняя $ddr_mid, верхняя $ddr_max. Значения по умолчанию: schedutil hispeed_load=$(rd $S0/hispeed_load)/$(rd $S4/hispeed_load), hispeed_freq=$(rd $S0/hispeed_freq)/$(rd $S4/hispeed_freq), up_rate_limit=$(rd $S0/up_rate_limit_us), top-app boost=$(rd $ST/schedtune.boost) prefer_idle=$(rd $ST/schedtune.prefer_idle)"
  say "Шум замера: первая и последняя строки (base1, base2) должны быть близки. Различия меньше этого шума ничего не значат."

  texp base1
  texp ddr_mid  $DV1/min_freq $ddr_mid  $DV2/min_freq $ddr_mid
  texp ddr_max  $DV1/min_freq $ddr_max  $DV2/min_freq $ddr_max
  texp su_load60 $S0/hispeed_load 60 $S4/hispeed_load 60
  texp su_hfreq  $S0/hispeed_freq 1958400 $S4/hispeed_freq 1305600
  texp su_up0    $S0/up_rate_limit_us 0 $S4/up_rate_limit_us 0
  texp su_all    $S0/hispeed_load 60 $S4/hispeed_load 60 $S0/hispeed_freq 1958400 $S4/hispeed_freq 1305600 $S0/up_rate_limit_us 0 $S4/up_rate_limit_us 0
  texp st_b10    $ST/schedtune.boost 10
  texp st_b30    $ST/schedtune.boost 30
  texp st_pi     $ST/schedtune.prefer_idle 1
  texp st_b10pi  $ST/schedtune.boost 10 $ST/schedtune.prefer_idle 1
  texp base2

  trestore
  [ -d /dev/stune ] && echo $$ > /dev/stune/tasks 2>/dev/null
  [ -d /dev/cpuset ] && echo $$ > /dev/cpuset/tasks 2>/dev/null
  trap - EXIT INT TERM
  say "Готово, значения возвращены. Файл: $TUNE_OUT"
}

# ---------- memlog: пассивный журнал памяти ----------
memlog_sample() {
  ma=$(sed -n 's/^MemAvailable: *\([0-9]*\).*/\1/p' /proc/meminfo)
  mf=$(sed -n 's/^MemFree: *\([0-9]*\).*/\1/p' /proc/meminfo)
  sf=$(sed -n 's/^SwapFree: *\([0-9]*\).*/\1/p' /proc/meminfo)
  apps=$(ps -A -o USER 2>/dev/null | grep -c '^u0_a')
  pin=$(sed -n 's/^pswpin \([0-9]*\)/\1/p' /proc/vmstat)
  pout=$(sed -n 's/^pswpout \([0-9]*\)/\1/p' /proc/vmstat)
  ref=$(sed -n 's/^workingset_refault \([0-9]*\)/\1/p' /proc/vmstat)
  maj=$(sed -n 's/^pgmajfault \([0-9]*\)/\1/p' /proc/vmstat)
  pin=${pin:-0}; pout=${pout:-0}; ref=${ref:-0}; maj=${maj:-0}; apps=${apps:-0}
  st=0; for n in $(sed -n 's/^allocstall[a-z_0-9]* \([0-9]*\)/\1/p' /proc/vmstat); do st=$((st + n)); done
  echo "$(date +%s),$ma,$mf,$sf,$apps,$pin,$pout,$ref,$st,$maj" >> "$MEMLOG_CSV"
}

cmd_memlog() {
  MEMLOG_CSV="$TMP/memlog.csv"; pf="$TMP/memlog.pid"
  case "$1" in
    start)
      if [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null; then say "Уже запущен (pid $(cat "$pf"))."; return 0; fi
      [ -f "$MEMLOG_CSV" ] || echo "epoch,memavail_kb,memfree_kb,swapfree_kb,app_procs,pswpin,pswpout,refault,allocstall,pgmajfault" > "$MEMLOG_CSV"
      nohup sh "$0" memlog run >/dev/null 2>&1 &
      echo $! > "$pf"
      say "Запущен. Раз в $(( ${KKNX_MEMLOG_SEC:-600} / 60 )) мин пишет в $MEMLOG_CSV. Пользуйтесь телефоном как обычно; после перезагрузки запустите снова."
      say "Когда наберётся сутки: memlog report (потом можно memlog stop)."
      ;;
    run)
      while :; do memlog_sample; sleep "${KKNX_MEMLOG_SEC:-600}"; done ;;
    stop)
      [ -f "$pf" ] && kill "$(cat "$pf")" 2>/dev/null; rm -f "$pf"
      say "Остановлен. Файл: $MEMLOG_CSV (очистить: rm $MEMLOG_CSV)." ;;
    report)
      [ -f "$MEMLOG_CSV" ] || { say "Журнала нет: сначала memlog start."; return 1; }
      n=$(($(wc -l < "$MEMLOG_CSV") - 1))
      [ "$n" -ge 2 ] || { say "Мало данных ($n точек), подождите."; return 1; }
      first=$(sed -n 2p "$MEMLOG_CSV"); last=$(tail -n 1 "$MEMLOG_CSV")
      OLDIFS=$IFS; IFS=,
      set -- $first; e0=$1; pin0=$6; pout0=$7; ref0=$8; st0=$9; maj0=${10}
      set -- $last;  e1=$1; pin1=$6; pout1=$7; ref1=$8; st1=$9; maj1=${10}
      IFS=$OLDIFS
      mins=$(((e1 - e0) / 60)); [ "$mins" -ge 1 ] || mins=1
      sa=0; sap=0; ssw=0; minapps=9999; k=0
      tail -n +2 "$MEMLOG_CSV" | while IFS=, read -r e a f sw ap x1 x2 x3 x4 x5; do
        echo "$a $ap $sw"
      done > "$TMP/memlog.tmp"
      while read -r a ap sw; do
        sa=$((sa + a)); sap=$((sap + ap)); ssw=$((ssw + sw)); k=$((k + 1))
        [ "$ap" -lt "$minapps" ] && minapps=$ap
      done < "$TMP/memlog.tmp"
      say "Период: $((mins / 60)) ч $((mins % 60)) мин, точек: $k"
      say "Свободно для приложений в среднем (MemAvailable): $((sa / k / 1024)) МБ"
      say "Живых приложений: в среднем $((sap / k)), минимум $minapps"
      swt=$(sed -n 's/^SwapTotal: *\([0-9]*\).*/\1/p' /proc/meminfo); say "Занято в swap в среднем: $(( (swt - ssw / k) / 1024 )) МБ из $((swt / 1024))"
      say "В час: swap-out $(((pout1 - pout0) * 60 / mins)) стр, swap-in $(((pin1 - pin0) * 60 / mins)) стр, refault $(((ref1 - ref0) * 60 / mins)) стр, allocstall $(((st1 - st0) * 60 / mins)), major faults $(((maj1 - maj0) * 60 / mins))"
      ;;
    *) say "Использование: memlog start | stop | report" ;;
  esac
}

# wbt: как влияет wbt_lat_usec на чтение под фоновой буферной записью и на саму скорость записи
cmd_wbt() {
  need_bench || return 1
  lat=/sys/block/mmcblk0/queue/wbt_lat_usec
  [ -w "$lat" ] || { say "Нет $lat: в этом ядре нет wbt (нужна сборка с experiments=wbt)."; return 1; }
  list=${KKNX_WBT:-"0 2000 8000 30000"}
  out="$TMP/wbt-$TS.txt"; : > "$out"
  TUNE_SAVE="$TMP/wbt-save.txt"; : > "$TUNE_SAVE"
  wbt_clean() { pkill -f wbw.bin 2>/dev/null; rm -f "$TMP/wbw.bin"; trestore; }
  trap 'wbt_clean' EXIT INT TERM
  say "wbt_lat_usec сейчас: $(rd $lat). Всё временное вернётся само."
  # фоновый сброс начинается рано (32 МБ грязных страниц), чтобы работал именно фоновый writeback, а не sync
  tset /proc/sys/vm/dirty_background_ratio 5
  tset /proc/sys/vm/dirty_background_bytes 33554432
  tset /proc/sys/vm/dirty_ratio 20
  tset /proc/sys/vm/dirty_expire_centisecs 300
  tset /proc/sys/vm/dirty_writeback_centisecs 100
  TOUT=""; command -v timeout >/dev/null 2>&1 && TOUT=1
  for v in $list; do
    tset "$lat" "$v"
    sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null; sleep 2
    t0=$(now_cs)
    dd if=/dev/zero of="$TMP/wbw.bin" bs=1048576 count=192 conv=fdatasync 2>/dev/null
    t1=$(now_cs)
    w=$(mbps 192 $((t1 - t0)))
    rm -f "$TMP/wbw.bin"; sync
    if [ -n "$TOUT" ]; then
      timeout 80 sh -c "while :; do dd if=/dev/zero of='$TMP/wbw.bin' bs=1048576 count=256 2>/dev/null; done" &
    else
      sh -c "while :; do dd if=/dev/zero of='$TMP/wbw.bin' bs=1048576 count=256 2>/dev/null; done" &
    fi
    wp=$!
    sleep 3
    if [ -n "$TOUT" ]; then r=$(timeout 90 "$BENCH" io "$TMP" 128 2>&1 | tail -n 1); else r=$("$BENCH" io "$TMP" 128 2>&1 | tail -n 1); fi
    kill $wp 2>/dev/null; pkill -f wbw.bin 2>/dev/null; sleep 1; rm -f "$TMP/wbw.bin"; sync
    msg="[wbt_lat_usec=$v] запись без нагрузки: $w МБ/с | под фоновой записью: ${r:-не уложился в 90 с}"
    say "$msg"; echo "$msg" >> "$out"
  done
  wbt_clean
  trap - EXIT INT TERM
  say "Возвращено: wbt_lat_usec=$(rd $lat). Файл: $out"
}

cmd_quick() {
  need_bench || return 1
  label=$(printf '%s' "${1:-$(uname -r)}" | tr ', ' '__')
  stable_hint
  say "Замер идёт примерно 1 минуту. Не трогайте телефон."
  if [ -f "$CSV" ] && [ "$(head -n 1 "$CSV")" != "$("$BENCH" csvhead)" ]; then
    mv "$CSV" "$TMP/results-old-$TS.csv"; say "Заголовок results.csv изменился (v3): старый файл сохранён как results-old-$TS.csv"
  fi
  [ -f "$CSV" ] || "$BENCH" csvhead > "$CSV"
  out="$TMP/quick-$TS.txt"
  sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null
  tis_snap q0
  "$BENCH" quick "$TMP" "$label" | tee "$out"
  tis_snap q1
  say "Время на частотах за замер (вместе с простоем между тестами):"
  tis_report q0 q1 | tee -a "$out"
  grep '^CSV,' "$out" | sed 's/^CSV,//' >> "$CSV"
  say ""
  say "Строка добавлена в $CSV. Чтобы сравнить сборки, пришлите мне этот файл целиком (cat $CSV)."
}

# ---------------------------------------------------------------------------
#  sustain: долгая нагрузка на все ядра
# ---------------------------------------------------------------------------
cmd_sustain() {
  need_bench || return 1
  say "Нагрузка на все ядра, смотрим падение скорости и температуру. Телефон станет тёплым. Минут: ${1:-6}"
  tis_snap s0
  "$BENCH" sustain "${1:-6}" | tee "$TMP/sustain-$TS.txt"
  tis_snap s1
  say "Время на частотах за всю нагрузку:"
  tis_report s0 s1 | tee -a "$TMP/sustain-$TS.txt"
}

# ---------------------------------------------------------------------------
#  ui: плавность прокрутки (кадры из gfxinfo)
# ---------------------------------------------------------------------------
cmd_ui() {
  pkg=com.android.settings
  size=$(wm size 2>/dev/null | tail -n 1 | sed 's/.*: *//')
  w=${size%x*}; h=${size#*x}
  case "$w$h" in ''|*[!0-9]*) w=720; h=1520 ;; esac
  x=$((w / 2)); y1=$((h * 78 / 100)); y2=$((h * 22 / 100))
  say "Открываю список всех приложений в Настройках и листаю его 60 раз. Не трогайте экран."
  am force-stop $pkg >/dev/null 2>&1
  am start -a android.settings.MANAGE_ALL_APPLICATIONS_SETTINGS >/dev/null 2>&1
  sleep 4
  dumpsys gfxinfo $pkg reset >/dev/null 2>&1
  i=0
  while [ $i -lt 30 ]; do
    input swipe $x $y1 $x $y2 220
    input swipe $x $y2 $x $y1 220
    i=$((i + 1))
  done
  out="$TMP/ui-$TS.txt"
  dumpsys gfxinfo $pkg | grep -E 'Total frames rendered|Janky frames|percentile|Number (Missed Vsync|High input latency|Slow UI thread|Slow bitmap uploads|Slow issue draw commands|Frame deadline missed)' | tee "$out"
  input keyevent KEYCODE_HOME
  say "Файл: $out (меньше джанка и ниже перцентили = плавнее)"
}

# ---------------------------------------------------------------------------
#  mem: сколько приложений держится в фоне
# ---------------------------------------------------------------------------
vm_snap() { grep -E '^(pgscan_direct|pgscan_kswapd|pgsteal_direct|pgsteal_kswapd|pswpin|pswpout|allocstall|workingset_refault|pgmajfault|oom_kill) ' /proc/vmstat > "$1" 2>/dev/null; }

vm_delta() {
  while read -r name before; do
    after=$(grep "^$name " "$2" | head -n 1 | sed 's/^[^ ]* //')
    [ -n "$after" ] && printf '%s=%s ' "$name" "$((after - before))"
  done < "$1"
  echo
}

launch_state() { # компонент -> "СОСТОЯНИЕ время_мс"
  r=$(am start -W -n "$1" 2>/dev/null)
  st=$(printf '%s\n' "$r" | grep -m1 'LaunchState' | sed 's/.*: *//' | tr -d '\r ')
  tt=$(printf '%s\n' "$r" | grep -m1 'TotalTime' | sed 's/.*: *//' | tr -d '\r ')
  echo "${st:-?} ${tt:--}"
}

cmd_mem() {
  list="$TMP/mem-pkgs.txt"
  max=${KKNX_APPS:-14}
  if [ $# -gt 0 ]; then
    : > "$list"
    for p in "$@"; do echo "$p" >> "$list"; done
  elif [ ! -s "$list" ]; then
    say "Подбираю приложения (сторонние, у которых есть значок запуска)..."
    : > "$list"
    n=0
    for p in $(pm list packages -3 2>/dev/null | sed 's/^package://'); do
      c=$(cmd package resolve-activity --brief -c android.intent.category.LAUNCHER "$p" 2>/dev/null | tail -n 1)
      case "$c" in */*) echo "$p" >> "$list"; n=$((n + 1)) ;; esac
      [ "$n" -ge "$max" ] && break
    done
    say "Выбрано $n приложений, список сохранён в $list (при повторных запусках используется тот же список; удалите файл, чтобы выбрать заново)."
  fi
  comps="$TMP/mem-comps.txt"; : > "$comps"
  while read -r p; do
    [ -n "$p" ] || continue
    c=$(cmd package resolve-activity --brief -c android.intent.category.LAUNCHER "$p" 2>/dev/null | tail -n 1)
    case "$c" in */*) echo "$p $c" >> "$comps" ;; *) say "  пропуск $p (нет значка запуска)" ;; esac
  done < "$list"
  total=$(wc -l < "$comps")
  [ "$total" -ge 4 ] || { say "Нужно хотя бы 4 приложения со значком запуска (найдено $total). Укажите пакеты: kknx-test.sh mem пакет1 пакет2 ..."; return 1; }

  say "Проход 1: запускаю $total приложений подряд (по 3 секунды), потом проход 2: смотрю, какие пришлось запускать заново."
  am kill-all >/dev/null 2>&1; sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null; sleep 2
  vm_snap "$TMP/vm0"; mem0=$(grep -m1 MemAvailable /proc/meminfo | tr -s ' ' | cut -d' ' -f2)
  out="$TMP/mem-$TS.txt"; : > "$out"
  while read -r p c; do
    launch_state "$c" >/dev/null; sleep 3; input keyevent KEYCODE_HOME; sleep 1
  done < "$comps"
  vm_snap "$TMP/vm1"
  cold=0; sumw=0; nw=0; lost=""
  while read -r p c; do
    set -- $(launch_state "$c")
    st=$1; tt=$2
    printf '%s %s %s мс\n' "$p" "$st" "$tt" >> "$out"
    case "$st" in
      COLD) cold=$((cold + 1)); lost="$lost $p" ;;
      WARM|HOT) case "$tt" in ''|-|*[!0-9]*) ;; *) sumw=$((sumw + tt)); nw=$((nw + 1)) ;; esac ;;
    esac
    sleep 2; input keyevent KEYCODE_HOME; sleep 1
  done < "$comps"
  vm_snap "$TMP/vm2"
  kept=$((total - cold))
  say ""
  say "=== Удержание фона ==="
  say "Во втором проходе осталось в памяти: $kept из $total (холодных запусков: $cold)"
  [ -n "$lost" ] && say "Пришлось запускать заново:$lost"
  [ "$nw" -gt 0 ] && say "Среднее время тёплого/горячего запуска: $((sumw / nw)) мс"
  say "MemAvailable до: ${mem0} кБ, после: $(grep -m1 MemAvailable /proc/meminfo | tr -s ' ' | cut -d' ' -f2) кБ"
  say "Работа подсистемы памяти за проход 1: $(vm_delta "$TMP/vm0" "$TMP/vm1")"
  say "Работа подсистемы памяти за проход 2: $(vm_delta "$TMP/vm1" "$TMP/vm2")"
  [ -r /sys/block/zram0/mm_stat ] && say "zram mm_stat (исходный, сжатый, занято, лимит, макс, одинаковые страницы): $(cat /sys/block/zram0/mm_stat)"
  echo "kept=$kept total=$total cold=$cold avg_warm_ms=$([ "$nw" -gt 0 ] && echo $((sumw / nw)) || echo -)" >> "$out"
  say "Подробности: $out"
}

# ---------------------------------------------------------------------------
case "${1:-help}" in
  diag)    cmd_diag ;;
  quick)   shift; cmd_quick "$@" ;;
  ui)      cmd_ui ;;
  mem)     shift; cmd_mem "$@" ;;
  zram)    cmd_zram ;;
  freq)    cmd_freq ;;
  iomix)   cmd_iomix ;;
  tune)    cmd_tune ;;
  untune)  cmd_untune ;;
  wbt)     cmd_wbt ;;
  memlog)  shift; cmd_memlog "$@" ;;
  ddr)     shift; cmd_ddr "$@" ;;
  freqtable) cmd_freqtable ;;
  upmig)   cmd_upmig ;;
  sustain) shift; cmd_sustain "$@" ;;
  all)     shift; cmd_diag; cmd_quick "$@"; cmd_freq; cmd_zram; cmd_iomix; cmd_ui; cmd_mem; cmd_sustain 4 ;;
  *)       sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
