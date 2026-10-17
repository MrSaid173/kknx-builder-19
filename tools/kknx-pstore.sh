#!/system/bin/sh
# Забирает журнал ядра, сохранённый после зависания/паники (pstore/ramoops), и кладёт в одну папку.
# Запуск от root, СРАЗУ после перезагрузки (до следующего сбоя):
#   su -c "sh /sdcard/Download/kknx-pstore.sh"
# Нужна сборка с experiments=ramoops (или preset balanced/lab). Результат: /sdcard/Download/kknx-pstore-<время>/
P=/sys/fs/pstore
OUT=/sdcard/Download/kknx-pstore-$(date +%Y%m%d-%H%M%S 2>/dev/null || echo now)

if [ "$(id -u)" != "0" ]; then echo "Нужен root: su -c \"sh $0\""; exit 1; fi
if [ ! -d "$P" ]; then echo "Нет $P: ядро собрано без pstore (включите experiments=ramoops)"; exit 1; fi
if ! grep -q pstore /proc/mounts; then mount -t pstore pstore "$P" 2>/dev/null; fi
if [ -z "$(ls -A "$P" 2>/dev/null)" ]; then
  echo "pstore пуст. Это нормально, если перезагрузка была штатной."
  echo "Если было зависание: после него журнал сохраняется только при перезагрузке по panic/watchdog,"
  echo "при долгом удержании кнопки питания (аппаратный сброс) память может обнулиться."
  dmesg 2>/dev/null | grep -i -E "ramoops|pstore" | head -n 5
  exit 0
fi
mkdir -p "$OUT" && cp -a "$P"/. "$OUT"/ 2>/dev/null
echo "Сохранено в $OUT:"
ls -l "$OUT"
for f in "$OUT"/console-ramoops*; do
  [ -f "$f" ] && { echo "--- конец $f ---"; tail -n 40 "$f"; }
done
for f in "$OUT"/dmesg-ramoops*; do
  [ -f "$f" ] && { echo "--- начало $f ---"; head -n 25 "$f"; break; }
done
echo "Файлы из $P исчезнут после удаления: удалять их не нужно, пока не отправили разработчику."
