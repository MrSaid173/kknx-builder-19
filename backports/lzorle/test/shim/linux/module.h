#pragma once
#include <linux/types.h>
#include <string.h>
#undef __BIG_ENDIAN
#undef __LITTLE_ENDIAN
#define __LITTLE_ENDIAN 1234
#include <string.h>
#define likely(x) __builtin_expect(!!(x),1)
#define unlikely(x) __builtin_expect(!!(x),0)
#define EXPORT_SYMBOL_GPL(x)
#define MODULE_LICENSE(x)
#define MODULE_DESCRIPTION(x)
#define __ffs(x) __builtin_ctzl(x)
#define noinline __attribute__((noinline))
#define __ALIGN_MASK(x,mask) (((x)+(mask))&~(mask))
#define ALIGN(x,a) __ALIGN_MASK((x),(__typeof__(x))(a)-1)
#define IS_ALIGNED(x,a) (((x)&((__typeof__(x))(a)-1))==0)
#define BUILD_BUG_ON(c) ((void)sizeof(char[1-2*!!(c)]))
