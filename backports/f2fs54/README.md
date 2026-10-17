# fs/f2fs: обновление с уровня Linux 5.3 до 5.4

Сверка `fs/f2fs` закреплённого коммита с mainline показала, что он на уровне **5.3** (от v5.3 отличается на 956 строк, от v4.19 на 5971). Патч переводит его на 5.4 трёхсторонним слиянием: ваше дерево + разница v5.3→v5.4. Затронуто 22 файла.

## Что слито, и как разобраны конфликты
Конфликтов 5, все разобраны вручную:
- `data.c`: структура bio из 5.4 (`__bio_alloc(fio, ...)`, `page_is_mergeable`), а вставки inline-шифрования дерева (`fscrypt_set_ice_dun`, `fscrypt_set_ice_skip`, `fscrypt_mergeable_bio`, `PG_DUN`, `REQ_NOENCRYPT`) сохранены; число их вхождений до и после одинаково.
- `file.c`: вызовы времени 4.9 (`timespec_trunc`) вместо `timestamp_truncate` из 5.4.
- `dir.c`: `<linux/sched.h>` (`sched/signal.h` в 4.9 нет).
- `super.c`: проверка `io_bits` из 5.4 + `MS_RDONLY` как в этом дереве.

## Что выключено заглушками (`fs/f2fs/compat54.h`)
В 4.9 нет инфраструктуры, переносить её нельзя без правок VFS:
- **fs-verity**: `fsverity_active()` всегда false, ioctl'ы возвращают `-EOPNOTSUPP`, `verity.c` не добавляется.
- **casefold**: `CONFIG_UNICODE` не определён, `IS_CASEFOLDED()` равен 0. Раздел, созданный с флагом casefold, такое ядро не смонтирует (как и 5.4 без `CONFIG_UNICODE`). Обычный `/data` ядра 5.3 этого флага не имеет.
- **fscrypt v2** (`FS_IOC_ADD_ENCRYPTION_KEY` и др.): ioctl'ы возвращают `-EOPNOTSUPP`. Шифрование `/data` (FBE) работает как раньше через keyring v1.
- `FS_IOC_GETFSLABEL` / `SETFSLABEL` включены (номера как в mainline).

## Изменения вне fs/f2fs
- `include/linux/f2fs_fs.h`: версия из v5.4 (в дереве совпадала с v5.3). Добавляет `F2FS_IO_ALIGNED` и два поля `s_encoding*` в зарезервированной области суперблока, размер структуры прежний.
- `fs/quota/dquot.c`, `include/linux/quotaops.h`: `dquot_initialize_needed()` из mainline (нужна `evict_inode` 5.4).
- `fs/f2fs/Kconfig`: `select NLS` (преобразование UTF-16 для метки раздела).

## Проверено
- накладывается без отклонений на чистые файлы закреплённого коммита, в том числе после бэкпорта `wbt` (он правит `f2fs.h`); обратное наложение определяется как «уже наложен»;
- `fs/f2fs` и `fs/quota` компилируются clang-16 (ARM64, `kuro439_defconfig`) с `-Werror=implicit-function-declaration`. Дерево по умолчанию собирается с `-Wno-implicit-function-declaration`, поэтому недостающие функции молча проходят компиляцию и падают при линковке; проверку делали отдельно;
- все внешние символы `fs/f2fs` определены в ядре (`find_inode_nowait`, `dquot_initialize_needed`, `strndup_user`, `utf16s_to_utf8s`, `utf8s_to_utf16s`).

## Не проверено
Полная сборка и линковка образа, загрузка, работа на устройстве. Это файловая система `/data`: формат на диске не меняется, но перед первым запуском сделайте резервную копию (TWRP/ADB). Если что-то не так, уберите `f2fs54` и соберите ядро заново.

## Включение
`experiments=f2fs54` (входит в пресет `lab`). В готовом образе проверяется строка `switch io_bits option is not allowed`: в версии 5.3 её нет.
