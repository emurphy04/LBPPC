#include "runtime.h"
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#ifdef _WIN32
#include <direct.h>
#define MAKE_DIR(path) _mkdir(path)
#define REMOVE_DIR(path) _rmdir(path)
#else
#include <unistd.h>
#define MAKE_DIR(path) mkdir(path,0755)
#define REMOVE_DIR(path) rmdir(path)
#endif
/* The user folder add-ons are listed from (runtime_file.c in the game): a temporary one here. */
static char user_dir[512];
const char *runtime_file_user_dir(void) { return user_dir; }

/* param.sfo as the game's (probe.c runtime_sfo_value): text values only, which is all the add-on check reads. */
int runtime_sfo_value(const char *path,const char *key,char *text,size_t text_size,uint32_t *number) {
    (void)number;
    FILE *f=fopen(path,"rb");
    if (!f) return 0;
    unsigned char data[4096];
    size_t n=fread(data,1,sizeof(data),f); fclose(f);
    if (n<20 || memcmp(data,"\0PSF",4)) return 0;
    uint32_t keys,values,count;
    memcpy(&keys,data+8,4); memcpy(&values,data+12,4); memcpy(&count,data+16,4);
    for (uint32_t i=0;i<count;++i) {
        const unsigned char *e=data+20+i*16;
        uint16_t key_offset; uint32_t length,offset;
        memcpy(&key_offset,e,2); memcpy(&length,e+4,4); memcpy(&offset,e+12,4);
        if (strcmp((const char *)data+keys+key_offset,key)) continue;
        size_t copy=length<text_size-1 ? length : text_size-1;
        memcpy(text,data+values+offset,copy); text[copy]=0;
        return 1;
    }
    return 0;
}
/* A param.sfo with CATEGORY and CONTENT_ID, as an extracted DLC has. */
static void write_sfo(const char *path,const char *category,const char *content) {
    unsigned char out[512]={0};
    const char *names[2]={"CATEGORY","CONTENT_ID"}, *vals[2]={category,content};
    uint32_t keys=20+2*16, key_at=0, val_at=0;
    unsigned char key_table[64]={0}, value_table[128]={0};
    for (int i=0;i<2;++i) {
        uint16_t ko=(uint16_t)key_at, fmt=0x204;
        uint32_t len=(uint32_t)strlen(vals[i])+1, cap=(len+3)&~3u, vo=val_at;
        unsigned char *e=out+20+i*16;
        memcpy(e,&ko,2); memcpy(e+2,&fmt,2); memcpy(e+4,&len,4); memcpy(e+8,&cap,4); memcpy(e+12,&vo,4);
        memcpy(key_table+key_at,names[i],strlen(names[i])+1); key_at+=(uint32_t)strlen(names[i])+1;
        memcpy(value_table+val_at,vals[i],len); val_at+=cap;
    }
    key_at=(key_at+3)&~3u;
    uint32_t values=keys+key_at, count=2, version=0x101;
    memcpy(out,"\0PSF",4); memcpy(out+4,&version,4); memcpy(out+8,&keys,4); memcpy(out+12,&values,4);
    memcpy(out+16,&count,4); memcpy(out+keys,key_table,key_at); memcpy(out+values,value_table,val_at);
    FILE *f=fopen(path,"wb"); assert(f); fwrite(out,1,values+val_at,f); fclose(f);
}

/* Add-on folders: two real DLC dumps, a bare folder named like a label, and a dump whose CONTENT_ID is another
 * label's. */
static const char *const labels[]={"LBPDLCSONYCO0001","LBPDLCORIGLK0001","LBPDLCFAKEFAKE01","LBPDLCSONYMP0001"};
static void remove_test_dirs(void) {
    char path[700];
    for (unsigned i=0;i<4;++i) {
        snprintf(path,sizeof(path),"%s/addcont/CUSA00473/%s/sce_sys/param.sfo",user_dir,labels[i]); remove(path);
        snprintf(path,sizeof(path),"%s/addcont/CUSA00473/%s/data.bin",user_dir,labels[i]); remove(path);
        snprintf(path,sizeof(path),"%s/addcont/CUSA00473/%s/sce_sys",user_dir,labels[i]); REMOVE_DIR(path);
        snprintf(path,sizeof(path),"%s/addcont/CUSA00473/%s",user_dir,labels[i]); REMOVE_DIR(path);
    }
    const char *dirs[]={"addcont/CUSA00473","addcont",""};
    for (unsigned i=0;i<3;++i) { snprintf(path,sizeof(path),"%s/%s",user_dir,dirs[i]); REMOVE_DIR(path); }
}
static void make_addons(void) {
    char path[700];
    const char *dirs[]={"","addcont","addcont/CUSA00473"};
    for (unsigned i=0;i<3;++i) { snprintf(path,sizeof(path),"%s/%s",user_dir,dirs[i]); MAKE_DIR(path); }
    for (unsigned i=0;i<4;++i) {
        snprintf(path,sizeof(path),"%s/addcont/CUSA00473/%s",user_dir,labels[i]); assert(!MAKE_DIR(path));
        if (i==2) continue;  /* the bare folder */
        snprintf(path,sizeof(path),"%s/addcont/CUSA00473/%s/sce_sys",user_dir,labels[i]); assert(!MAKE_DIR(path));
        char content[64];
        snprintf(content,sizeof(content),"UP9000-CUSA00473_00-%s",i==3 ? "LBPDLCSONYMP0002" : labels[i]);
        snprintf(path,sizeof(path),"%s/addcont/CUSA00473/%s/sce_sys/param.sfo",user_dir,labels[i]);
        write_sfo(path,"ac",content);
        if (i==1) {  /* a DLC with data besides sce_sys */
            snprintf(path,sizeof(path),"%s/addcont/CUSA00473/%s/data.bin",user_dir,labels[i]);
            FILE *f=fopen(path,"wb"); assert(f); fclose(f);
        }
    }
}
typedef int32_t (ABI *Module)(uint16_t);
typedef int32_t (ABI *Init)(const void *,void *);
typedef int32_t (ABI *Param)(uint32_t,int32_t *);
typedef int32_t (ABI *List)(uint32_t,void *,uint32_t,uint32_t *);
#define GET(t,n) ((t)runtime_content_resolve(n))
static int listed(const unsigned char *entries,uint32_t hits,const char *label,uint32_t want) {
    for (uint32_t i=0;i<hits;++i) {
        uint32_t status;
        memcpy(&status,entries+i*24+20,4);
        if (!strcmp((const char *)entries+i*24,label) && status==want) return 1;
    }
    return 0;
}
int main(int argc,char **argv) {
    const char *temp=getenv("TEMP");
    snprintf(user_dir,sizeof(user_dir),"%s/lbppc-content-test",temp && *temp ? temp : "/tmp");
    remove_test_dirs();
    Module load=GET(Module,"g8cM39EUZ6o#M#N");
    Module loaded=GET(Module,"fMP5NHUOaMk#M#N");
    Module unload=GET(Module,"eR2bZFAAU0Q#M#N");
    Init init=GET(Init,"R9lA82OraNs#c#d");
    Param param=GET(Param,"99b82IKXpH4#c#d");
    List list=GET(List,"xnd8BJzAxmk#c#d");
    assert(load && loaded && unload && init && param && list);
    assert(!runtime_content_resolve("g8cM39EUZ6o#I#J"));
    if (argc>1 && !strcmp(argv[1],"--missing")) { load(0xb4); return 99; }
    const uint32_t profile[]={1,13,0x80000000,0,7};
    runtime_content_configure(profile);
    if (argc>1 && !strcmp(argv[1],"--unknown")) {
        /* Other system modules are host-provided: reference counted, independent. */
        assert((uint32_t)loaded(0xb5)==0x805a1001);
        assert(load(0xb5)==0 && loaded(0xb5)==0 && (uint32_t)loaded(0xb4)==0x805a1001);
        assert(unload(0xb5)==0 && (uint32_t)loaded(0xb5)==0x805a1001);
        puts("PASS: independent module references"); return 0;
    }
    assert((uint32_t)loaded(0)==0x805a1000);
    assert((uint32_t)loaded(0xb4)==0x805a1001);
    assert((uint32_t)unload(0xb4)==0x805a1001);
    assert(load(0xb4)==0 && load(0xb4)==0 && loaded(0xb4)==0);
    unsigned char initial[32]={0};
    struct { unsigned char boot[40]; uint32_t canary; } b;
    memset(&b,0xa5,sizeof(b));
    assert((uint32_t)init(NULL,b.boot)==0x80d90002);
    initial[31]=1; assert((uint32_t)init(initial,b.boot)==0x80d90002); initial[31]=0;
    assert(b.boot[0]==0xa5 && init(initial,b.boot)==0);
    for (unsigned i=0;i<40;++i) assert(b.boot[i]==0);
    assert(b.canary==0xa5a5a5a5);
    b.boot[0]=0xcc; assert((uint32_t)init(initial,b.boot)==0x80d90003 && b.boot[0]==0xcc);
    struct { int32_t value; uint32_t canary; } result={-1,0x1234};
    for (unsigned i=0;i<5;++i) {
        assert(param(i,&result.value)==0 && (uint32_t)result.value==profile[i]);
        assert(result.canary==0x1234);
    }
    result.value=77;
    assert((uint32_t)param(5,&result.value)==0x80d90002 && result.value==77);
    assert((uint32_t)param(0,NULL)==0x80d90002);
    unsigned char entries[96]; memset(entries,0xaa,sizeof(entries));
    struct { uint32_t hits,canary; } h={99,0xabcdef};
    assert(list(0,NULL,0,&h.hits)==0 && h.hits==0 && h.canary==0xabcdef);
    assert(list(0,entries,2,&h.hits)==0 && h.hits==0);
    for (unsigned i=0;i<sizeof(entries);++i) assert(entries[i]==0xaa);
    assert((uint32_t)list(0,NULL,0,NULL)==0x80d90002);
    /* Add-ons: only real DLC dumps in <user>/addcont/CUSA00473/<label> count. */
    make_addons();
    assert(list(0,NULL,0,&h.hits)==0 && h.hits==2);
    memset(entries,0xaa,sizeof(entries));
    assert(list(0,entries,1,&h.hits)==0 && h.hits==1);
    for (unsigned i=24;i<sizeof(entries);++i) assert(entries[i]==0xaa);  /* unused slots untouched */
    assert(list(0,entries,4,&h.hits)==0 && h.hits==2);
    /* Status 0 (no extra data) for a DLC of sce_sys alone, as LittleBigPlanet 3 grants; 4 for one with data. */
    assert(listed(entries,2,"LBPDLCSONYCO0001",0) && listed(entries,2,"LBPDLCORIGLK0001",4));
    assert(!listed(entries,2,"LBPDLCFAKEFAKE01",0) && !listed(entries,2,"LBPDLCSONYMP0001",0));
    char folder[700];
    assert(runtime_content_addon("LBPDLCSONYCO0001",folder,sizeof(folder)) && strstr(folder,"LBPDLCSONYCO0001"));
    assert(!runtime_content_addon("LBPDLCFAKEFAKE01",NULL,0) && !runtime_content_addon("LBPDLCSONYMP0001",NULL,0));
    assert(!runtime_content_addon("../../x",NULL,0) && !runtime_content_addon("",NULL,0));
    remove_test_dirs();
    assert(list(0,NULL,0,&h.hits)==0 && h.hits==0);
    assert(unload(0xb4)==0 && loaded(0xb4)==0);
    assert((uint32_t)init(initial,b.boot)==0x80d90003);
    assert(unload(0xb4)==0 && (uint32_t)loaded(0xb4)==0x805a1001);
    assert(load(0xb4)==0 && init(initial,b.boot)==0 && unload(0xb4)==0);
    puts("PASS: AppContent lifecycle, profile, output boundaries and DLC dumps");
    return 0;
}
