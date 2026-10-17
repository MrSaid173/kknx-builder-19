# blk-wbt для 4.9 (writeback throttling, бэкпорт из 4.10)

Что делает: ограничивает фоновую буферную запись (writeback), когда задержка чтения превышает цель
(`/sys/block/mmcblk0/queue/wbt_lat_usec`, по умолчанию 2000 мкс для неротационных дисков, `0` = выключено).
Нужен, чтобы чтение (запуск приложений, UI) не падало, пока система сбрасывает на eMMC большие порции грязных страниц.
На ваших замерах чтение под фоновой записью падало на ~78%.

Что внутри (`wbt-backport.patch`, 15 файлов):
- `block/blk-wbt.c/.h` и `include/trace/events/wbt.h`: из v4.10 как есть, три правки под 4.9
  (нет `REQ_IDLE`: sync-записи не троттлятся; нет `q->queue_depth`: глубина по умолчанию; `backing_dev_info` здесь указатель).
- `block/blk-stat.c/.h`: упрощённая версия 4.10 только для обычных (legacy) очередей, без blk-mq.
- крючки в `blk-core.c` (wait/track/issue/done/requeue), `blk-sysfs.c` (`wbt_lat_usec`, подключение при регистрации очереди),
  `blk-settings.c`, `blkdev.h`, `blk_types.h` (флаг `REQ_BACKGROUND`, структуры статистики).
- `mm/page-writeback.c` + `backing-dev-defs.h`: метка `dirty_sleep` (писатель спит в balance_dirty_pages: троттлинг ослабляется).
- `fs/f2fs/f2fs.h`: фоновая запись (kupdate/background) помечается `REQ_BACKGROUND`.

Сознательно НЕ перенесено:
- крючок CFQ «отключать wbt для не-root cgroup»: на Android фоновые приложения лежат в не-root blkio-группе,
  wbt выключился бы при первом же их вводе-выводе;
- blk-mq (eMMC здесь на старой очереди), `REQ_NOIDLE -> REQ_IDLE` и прочая переделка флагов 4.10.

Включение: `experiments=wbt` (шаг workflow применяет патч, `apply_tweaks.py` включает `CONFIG_BLK_WBT=y`, `CONFIG_BLK_WBT_SQ=y`).
Своя цель задержки: `experiments=wbt4000` (число слитно, мкс, допустимо 100-1000000): после наложения патча `apply_tweaks.py` подставляет его
в `wbt_default_latency_nsec()` в `block/blk-wbt.c` вместо 2000 мкс.
Проверено: патч чисто накладывается на файлы вашего коммита; затронутые объекты (`block/`, `mm/page-writeback.o`) компилируются
кросс-gcc 13 без ошибок и предупреждений в новых файлах. На железе НЕ проверено.
