# TCP internal pacing (бэкпорт из Linux 4.13)

Проблема: BBR строится на pacing, а в 4.9 pacing умеет делать только планировщик `fq`. Сборщик ставит `fq` строкой в `init.kknx.rc` (`default_qdisc`),
но это работает лишь для интерфейсов, у которых qdisc выбирается по умолчанию. У `rmnet` (мобильная сеть) и VPN-интерфейсов обычно `noqueue`/`pfifo`,
и там BBR без pacing по документации «работает неправильно и даёт лишние потери».

Что делает: если на интерфейсе нет `fq`, TCP сам разносит пакеты таймером высокого разрешения (один hrtimer на сокет). Касается только сокетов,
которые просят pacing (BBR или `SO_MAX_PACING_RATE`); сокеты cubic/reno и прочие не меняются.

Источник: коммит `218af599fa63` «tcp: internal implementation for pacing» (Eric Dumazet, Linux 4.13), 8 файлов:
`include/linux/tcp.h`, `include/net/sock.h`, `include/net/tcp.h`, `net/core/sock.c`, `net/ipv4/tcp_bbr.c`, `net/ipv4/tcp_output.c`, `net/ipv4/tcp_timer.c`, `net/sched/sch_fq.c`.

Отличия от апстрима (адаптация под 4.9):
- в 4.9 старая модель TSQ (`tp->tsq_flags`, битовые операции `TSQ_QUEUED`/`TSQ_THROTTLED`), а не `sk_tsq_flags` и `TSQF_*` из 4.13. `tcp_pace_kick()` переписана под неё
  (ставит сокет в очередь тасклета так же, как `tcp_wfree()`; вызывается из hardirq, поэтому без `local_irq_save`);
- поле `sk_pacing_status` в `struct sock` поставлено после `sk_max_pacing_rate` (хунк апстрима не лёг, в 4.9 раскладка другая).

Что уже было в дереве и не требовало бэкпорта: `tcp_bbr.c` здесь соответствует 4.9.y stable с исправлениями (`has_seen_rtt`, `full_bw_reached`), не нужно ничего тянуть из 4.14 или 4.19.
Не включено: модель Early Departure Time из Linux 4.20 (она требует переделки временных меток TCP).

Включение: `experiments=tcppace` (патч накладывает `scripts/apply_backports.py`). Имеет смысл вместе с `net_cc=bbr`.

Проверено: патч чисто ложится на файлы закреплённого коммита; компилируются целиком `net/ipv4`, `net/ipv6`, `net/sched`, `net/core/sock.o`.
Не проверено: на железе (сетевая подсистема: сначала проверьте на запасном телефоне; признаки проблем: зависающие соединения BBR, рост нагрузки softirq).
