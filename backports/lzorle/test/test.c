#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <linux/lzo.h>
static uint64_t st=88172645463325252ULL;
static uint32_t rnd(void){st^=st<<13;st^=st>>7;st^=st<<17;return (uint32_t)(st>>11);}
static void gen(unsigned char *b,size_t n,int kind){
  size_t i=0;
  switch(kind){
  case 0: for(i=0;i<n;i++) b[i]=rnd(); break;                    /* случайные */
  case 1: memset(b,0,n); break;                                  /* нули */
  case 2: memset(b,rnd(),n); break;                              /* один байт */
  case 3: while(i<n){size_t r=1+rnd()%700; unsigned char v=(rnd()%3==0)?0:rnd(); while(r--&&i<n)b[i++]=v;} break; /* длинные серии */
  case 4: while(i<n){ if(rnd()%2){size_t r=1+rnd()%40; unsigned char v=rnd(); while(r--&&i<n)b[i++]=v;} else {size_t r=1+rnd()%40; while(r--&&i<n)b[i++]=rnd();} } break; /* серии и мусор */
  case 5: for(i=0;i<n;i++) b[i]=(i%97<60)?'a'+(i%7):rnd()%4; break; /* текстоподобные */
  case 6: for(i=0;i<n;i++) b[i]=(rnd()%16==0)?rnd():0; break;    /* редкие ненули */
  default: for(i=0;i<n;i++) b[i]=(i/16)&0xff; break;
  }
}
int main(void){
  static unsigned char wrk[LZO1X_MEM_COMPRESS];
  size_t sizes[]={1,2,3,4,5,7,15,16,17,31,64,100,255,256,1000,4095,4096,4097,8192,16384,65536};
  long ok=0,bad=0; size_t tot_in=0,tot_rle=0,tot_lzo=0;
  for(int it=0;it<6000;it++){
    size_t n=sizes[rnd()%(sizeof(sizes)/sizeof(sizes[0]))]; int kind=rnd()%8;
    unsigned char *in=malloc(n+64),*out=malloc(n+n/16+4096),*back=malloc(n+64),*out2=malloc(n+n/16+4096);
    gen(in,n,kind);
    size_t ol=0,ol2=0,bl;
    int r1=lzorle1x_1_compress(in,n,out,&ol,wrk);
    int r2=lzo1x_1_compress(in,n,out2,&ol2,wrk);
    if(r1||r2){printf("compress fail it=%d n=%zu kind=%d r=%d/%d\n",it,n,kind,r1,r2);bad++;goto next;}
    bl=n+64; memset(back,0xA5,n+64);
    int d1=lzo1x_decompress_safe(out,ol,back,&bl);
    if(d1||bl!=n||memcmp(back,in,n)){printf("RLE roundtrip FAIL it=%d n=%zu kind=%d d=%d bl=%zu\n",it,n,kind,d1,bl);bad++;goto next;}
    bl=n+64; memset(back,0x5A,n+64);
    d1=lzo1x_decompress_safe(out2,ol2,back,&bl);
    if(d1||bl!=n||memcmp(back,in,n)){printf("LZO roundtrip FAIL it=%d n=%zu kind=%d\n",it,n,kind);bad++;goto next;}
    /* усечённый вход не должен приводить к записи за пределы выхода */
    if(ol>4){ bl=n; d1=lzo1x_decompress_safe(out,ol-1,back,&bl); (void)d1; }
    ok++; tot_in+=n; tot_rle+=ol; tot_lzo+=ol2;
next: free(in);free(out);free(back);free(out2);
  }
  printf("ok=%ld bad=%ld | вход %zu Б: lzo-rle %zu Б, lzo %zu Б\n",ok,bad,tot_in,tot_rle,tot_lzo);
  return bad!=0;
}
