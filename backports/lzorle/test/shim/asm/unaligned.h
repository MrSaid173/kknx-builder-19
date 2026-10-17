#pragma once
#include <string.h>
#include <linux/types.h>
#include <stdint.h>
static inline uint16_t get_unaligned_le16(const void *p){uint16_t v;memcpy(&v,p,2);return v;}
static inline uint32_t get_unaligned_le32(const void *p){uint32_t v;memcpy(&v,p,4);return v;}
static inline uint64_t get_unaligned_le64(const void *p){uint64_t v;memcpy(&v,p,8);return v;}
#define get_unaligned(p) ({ __typeof__(*(p)) _v; memcpy(&_v,(p),sizeof(_v)); _v; })
static inline void put_unaligned_le16(uint16_t v, void *p){memcpy(p,&v,2);}
static inline void put_unaligned_le32(uint32_t v, void *p){memcpy(p,&v,4);}
static inline void put_unaligned_le64(uint64_t v, void *p){memcpy(p,&v,8);}
#define put_unaligned(v,p) ({ __typeof__(*(p)) _t=(v); memcpy((p),&_t,sizeof(_t)); })
