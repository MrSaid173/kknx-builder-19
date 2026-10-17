#!/bin/bash
# Круговой тест lzo-rle / lzo в пользовательском режиме: исходники берутся из дерева ядра ПОСЛЕ наложения бэкпорта.
# Нужны gcc; для проверки путей arm64 ещё aarch64-linux-gnu-gcc и qemu-aarch64 (apt install gcc-aarch64-linux-gnu qemu-user).
# Запуск: KERNEL=/путь/к/ядру ./run.sh
set -e
K="${KERNEL:?задайте KERNEL=/путь/к/ядру}"
HERE="$(cd "$(dirname "$0")" && pwd)"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
cp "$K"/lib/lzo/lzo1x_compress.c "$K"/lib/lzo/lzo1x_decompress_safe.c "$K"/lib/lzo/lzodefs.h "$W"/
mkdir -p "$W/shim"; cp -r "$HERE/shim/." "$W/shim/"; mkdir -p "$W/shim/linux"; cp "$K/include/linux/lzo.h" "$W/shim/linux/lzo.h"
cp "$HERE/test.c" "$W/"
cd "$W"
grep -q lzorle1x_1_compress shim/linux/lzo.h || { echo "в дереве нет lzorle1x_1_compress: бэкпорт lzorle не наложен"; exit 1; }
echo "== x86-64"; gcc -O2 -Ishim -I. -o t_x86 test.c lzo1x_compress.c lzo1x_decompress_safe.c && ./t_x86
if command -v aarch64-linux-gnu-gcc >/dev/null && command -v qemu-aarch64 >/dev/null; then
  echo "== aarch64 (qemu)"; aarch64-linux-gnu-gcc -O2 -static -DCONFIG_ARM64 -DCONFIG_HAVE_EFFICIENT_UNALIGNED_ACCESS -Ishim -I. -o t_a64 test.c lzo1x_compress.c lzo1x_decompress_safe.c && qemu-aarch64 ./t_a64
else
  echo "(aarch64-gcc или qemu-aarch64 нет: проверка arm64 пропущена)"
fi
