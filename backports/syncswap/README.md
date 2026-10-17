# swap-in из zram без swap cache (бэкпорт Linux 4.15)

Коммиты `mm, swap: skip swapcache for swapin of synchronous device` и `bdi: introduce BDI_CAP_SYNCHRONOUS_IO`. Если слот swap принадлежит устройству с флагом `SWP_SYNCHRONOUS_IO` (zram) и на него ссылается один PTE (`__swap_count == 1`), `do_swap_page()` выделяет страницу сама, читает её через `swap_readpage()` и не кладёт в swap cache и не делает readahead. Слот освобождается обычным `swap_free()` (zram при этом выбрасывает сжатую копию).

Файлы: `include/linux/backing-dev.h`, `include/linux/swap.h` (`SWP_SCANNING` сдвинут на бит 13), `mm/memory.c`, `mm/page_io.c`, `mm/swapfile.c`, `drivers/block/zram/zram_drv.c`.

Отличия от апстрима 4.15:
- `swap_readpage()` без параметра `do_poll`.
- `__swap_count()` читает `swap_map` под `si->lock` (swapoff обнуляет `max` и `swap_map` под тем же замком); при гонке вернёт 0, и пойдёт старый путь через `swapin_readahead()`.
- Добавлено исправление гонки из Linux 6.8 (`13ddaf26be32`, mm/swap: fix race when skipping swapcache): перед чтением слот закрепляется `swapcache_prepare()` (SWAP_HAS_CACHE), после `swap_free()` снимается `swapcache_free()`. Без этого два потока одного процесса могли параллельно прочитать один слот, первый заканчивал и освобождал слот, страницу выселяли в тот же слот, и `pte_same()` у второго срабатывал на чужих данных. Проигравший теперь ждёт один тик (`schedule_timeout_uninterruptible(1)`) и заходит в fault заново. Закреплённый слот заодно не даёт swapoff закончиться посреди чтения.

Оставшиеся риски: затронут горячий путь `do_swap_page()` вместе с SPF; на устройстве не проверялся и не собирался.

Включение: `experiments=syncswap`. Проверено только чтением кода и наложением патча. Не собиралось и не запускалось. Первым делом проверьте загрузку, затем `kknx-test.sh mem`, `dmesg` на `Bad page`, `BUG`, `Bad rss-counter`.
