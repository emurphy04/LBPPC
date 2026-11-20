/* Game files shown edited (LBPPC_FILE_EDITS, scripts/quit_menu.py): cut, tail and patches through reads, seeks and stats. */
#include "../src/runtime_file.c"
#include <assert.h>
static int32_t guest_errno;
int32_t *runtime_errno(void) { return &guest_errno; }
int32_t runtime_guest_errno(int e) { return e; }
uintptr_t runtime_lookup(const RuntimeExport *table,size_t n,const char *name) {
    for (size_t i=0;i<n;++i) if (!strcmp(table[i].name,name)) return (uintptr_t)table[i].function;
    return 0;
}
static void put(FILE *f,const void *data,size_t n) { assert(fwrite(data,1,n,f)==n); }
static void put_entry(FILE *f,const char *path,uint64_t original,uint64_t cut,const char *tail,uint32_t patches) {
    uint16_t length=(uint16_t)strlen(path); uint64_t tail_size=strlen(tail);
    put(f,&length,2); put(f,path,length); put(f,&original,8); put(f,&cut,8); put(f,&tail_size,8); put(f,tail,tail_size);
    put(f,&patches,4);
}
static void put_patch(FILE *f,uint64_t at,const char *bytes) {
    uint32_t size=(uint32_t)strlen(bytes);
    put(f,&at,8); put(f,&size,4); put(f,bytes,size);
}
int main(void) {
    char root[]="out/file-edits-XXXXXX";
    assert(mkdtemp(root));
    char game[512],user[512],path[600],list[600];
    snprintf(game,sizeof(game),"%s/game",root);
    snprintf(user,sizeof(user),"%s/user",root);
    assert(!mkdir(game,0755));
    snprintf(path,sizeof(path),"%s/data.farc",game);
    FILE *f=fopen(path,"wb"); assert(f); put(f,"0123456789abcdef",16); assert(!fclose(f));
    snprintf(path,sizeof(path),"%s/other.farc",game);
    f=fopen(path,"wb"); assert(f); put(f,"0123456789abcde",15); assert(!fclose(f));
    snprintf(list,sizeof(list),"%s/file_edits.bin",root);
    f=fopen(list,"wb"); assert(f);
    uint32_t count=2;
    put(f,"LBPEDIT1",8); put(f,&count,4);
    /* data.farc: its first 10 bytes, then XYZ; pp over bytes 2-3 and Q over the tail's Y */
    put_entry(f,"data.farc",16,10,"XYZ",2); put_patch(f,2,"pp"); put_patch(f,11,"Q");
    /* other.farc: made for a 16-byte file, so this 15-byte one is left alone */
    put_entry(f,"other.farc",16,10,"XYZ",0);
    assert(!fclose(f));
#ifdef _WIN32
    _putenv_s("LBPPC_FILE_EDITS",list);
#else
    setenv("LBPPC_FILE_EDITS",list,1);
#endif
    runtime_file_configure(game,user);
    assert(edit_count==2);

    const char *shown="01pp456789XQZ";
    int fd=(int)do_open("/app0/data.farc",0,0); assert(fd>=3);
    char buffer[32]={0};
    assert(do_read(fd,buffer,4)==4 && !memcmp(buffer,"01pp",4));
    assert(do_read(fd,buffer,32)==9 && !memcmp(buffer,shown+4,9));
    assert(do_read(fd,buffer,32)==0);
    assert(do_pread(fd,buffer,6,8)==5 && !memcmp(buffer,shown+8,5));
    assert(do_pread(fd,buffer,2,11)==2 && !memcmp(buffer,"QZ",2));
    assert(do_pread(fd,buffer,4,13)==0);
    assert(do_lseek(fd,-3,2)==10 && do_read(fd,buffer,3)==3 && !memcmp(buffer,"XQZ",3));
    assert(do_lseek(fd,-1,1)==12 && do_lseek(fd,-20,1)==-EINVAL);
    GuestStat info;
    assert(!do_fstat(fd,&info) && info.size==13);
    assert(!do_stat("data.farc",&info) && info.size==13);
    assert(!do_close(fd));

    fd=(int)do_open("/app0/other.farc",0,0); assert(fd>=3);
    assert(do_read(fd,buffer,32)==15 && !memcmp(buffer,"0123456789abcde",15));
    assert(!do_fstat(fd,&info) && info.size==15);
    assert(!do_close(fd));

    /* A dump spelling that differs only by case still resolves (LBP3 asks for Chunk1_001.farc
     * while the extracted file is chunk1_001.farc), in the file name and in a folder name. */
    snprintf(path,sizeof(path),"%s/chunk1_001.farc",game);
    f=fopen(path,"wb"); assert(f); put(f,"casefold",8); assert(!fclose(f));
    fd=(int)do_open("/app0/Chunk1_001.farc",0,0); assert(fd>=3);
    assert(do_read(fd,buffer,32)==8 && !memcmp(buffer,"casefold",8));
    assert(!do_stat("/app0/chunk1_001.farc",&info) && info.size==8);
    assert(!do_close(fd));
    assert(do_stat("/app0/no_such_archive.farc",&info)==-ENOENT);
    snprintf(path,sizeof(path),"%s/Inner",game); assert(!mkdir(path,0755));
    snprintf(path,sizeof(path),"%s/Inner/file.farc",game);
    f=fopen(path,"wb"); assert(f); put(f,"folder",6); assert(!fclose(f));
    fd=(int)do_open("/app0/inner/file.farc",0,0); assert(fd>=3);
    assert(do_read(fd,buffer,32)==6 && !memcmp(buffer,"folder",6));
    assert(!do_close(fd));

    remove(list);
    snprintf(path,sizeof(path),"%s/data.farc",game); remove(path);
    snprintf(path,sizeof(path),"%s/other.farc",game); remove(path);
    snprintf(path,sizeof(path),"%s/chunk1_001.farc",game); remove(path);
    snprintf(path,sizeof(path),"%s/Inner/file.farc",game); remove(path);
    snprintf(path,sizeof(path),"%s/Inner",game); rmdir(path);
    rmdir(game);
    const char *dirs[]={"temp0","download0","data"};
    for (int i=0;i<3;++i) { snprintf(path,sizeof(path),"%s/%s",user,dirs[i]); rmdir(path); }
    rmdir(user); rmdir(root);
    puts("Game file edits: cut, tail, patches, seeks and stats PASS");
}
