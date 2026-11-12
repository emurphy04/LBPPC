/* Offline AppContent provider for the configured base-game profile.
 * No package mounting, downloads or license emulation. The player's own DLC, set up by the launcher into
 * <user>/addcont/<title id>/<entitlement label>/ (scripts/game_setup.py), is reported as installed (see addon_list). */
#include "runtime.h"
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#define INVALID_ID ((int32_t)0x805a1000)
#define NOT_LOADED ((int32_t)0x805a1001)
#define PARAMETER ((int32_t)0x80d90002)
#define BUSY ((int32_t)0x80d90003)
static unsigned refs, initialized, configured;
static uint32_t parameters[5];
static unsigned queries, lists;
void runtime_content_configure(const uint32_t values[5]) {
    if (refs || initialized || (values[0]!=1 && values[0]!=3)) {
        fputs("ERROR: invalid or late content profile\n",stderr); exit(1);
    }
    memcpy(parameters,values,sizeof(parameters)); configured=1;
}
static void require_provider(void) {
    if (!configured) { fputs("STOP: AppContent needs --content-profile\n",stderr); exit(21); }
}
/* System modules: every library this eboot imports is provided by the host
 * runtime, so loading one only tracks a reference count. AppContent (0xb4)
 * additionally requires the configured content profile. */
static unsigned module_refs[0x10000];
static ABI int32_t module_load(uint16_t id) {
    if (!id) return INVALID_ID;
    if (id==0xb4) require_provider();
    if (module_refs[id]==UINT32_MAX) { fputs("STOP: module reference overflow\n",stderr); exit(21); }
    ++module_refs[id];
    if (id==0xb4) { refs=module_refs[id]; printf("Runtime: AppContent host provider loaded; references=%u\n",refs); }
    else printf("Runtime: system module 0x%x loaded (host implementation)\n",id);
    return 0;
}
static ABI int32_t module_loaded(uint16_t id) {
    if (!id) return INVALID_ID;
    return module_refs[id] ? 0 : NOT_LOADED;
}
static ABI int32_t module_unload(uint16_t id) {
    int32_t result=module_loaded(id);
    if (result) return result;
    --module_refs[id];
    if (id==0xb4) { refs=module_refs[id]; if (!refs) initialized=0; }
    return 0;
}
static void require_loaded(void) {
    require_provider();
    if (!refs) { fputs("STOP: AppContent called before module load\n",stderr); exit(21); }
}
static ABI int32_t content_init(const unsigned char *init,unsigned char *boot) {
    require_loaded();
    if (!init || !boot) return PARAMETER;
    if (initialized) return BUSY;
    /* Only the reserved-zero init profile is implemented. */
    for (unsigned i=0;i<32;++i) if (init[i]) return PARAMETER;
    memset(boot,0,40); initialized=1;
    puts("Runtime: AppContent initialized; offline base-game profile, no mounted add-ons");
    return 0;
}
static void require_initialized(void) {
    require_loaded();
    if (!initialized) { fputs("STOP: AppContent query before initialization\n",stderr); exit(21); }
}
static ABI int32_t param_int(uint32_t id,int32_t *out) {
    require_initialized();
    if (id>4 || !out) return PARAMETER;
    memcpy(out,&parameters[id],4); ++queries;
    printf("Runtime: AppContent parameter %u = %d\n",id,*out);
    return 0;
}
/* SceAppContentAddcontInfo: entitlement label (16 chars + NUL), padding, download status. */
#define LABEL_SIZE 17
#define ADDON_NO_EXTRA_DATA 0u  /* an add-on that is only an entitlement (LittleBigPlanet 3's DLC) */
#define ADDON_INSTALLED 4u      /* an add-on with data, installed */
#define MAX_ADDONS 64
typedef struct { char label[LABEL_SIZE]; char padding[3]; uint32_t status; } AddonInfo;
/* A DLC counts only as the player's own dump: <label>/sce_sys/param.sfo is additional content ("ac") whose
 * CONTENT_ID (UP9000-CUSA00473_00-LBPDLCSONYCO0001) ends in the folder's label. A bare folder named after a label
 * is not DLC, and is said so once. */
static int real_addon(const char *folder,const char *label) {
    char sfo[4200], content[64]="", category[8]="";
    if (snprintf(sfo,sizeof(sfo),"%s/sce_sys/param.sfo",folder)>=(int)sizeof(sfo)) return 0;
    runtime_sfo_value(sfo,"CONTENT_ID",content,sizeof(content),NULL);
    runtime_sfo_value(sfo,"CATEGORY",category,sizeof(category),NULL);
    size_t n=strlen(content), l=strlen(label);
    int ok=!strncmp(category,"ac",2) && n>l && content[n-l-1]=='-' && !strcmp(content+n-l,label);
    static char warned[MAX_ADDONS][LABEL_SIZE];
    static unsigned warnings;
    if (!ok) {
        int seen=0;
        for (unsigned i=0;i<warnings;++i) seen|=!strcmp(warned[i],label);
        if (!seen && warnings<MAX_ADDONS) {
            memcpy(warned[warnings++],label,l+1);
            printf("Runtime: DLC %s left out: not a DLC dump (no matching sce_sys/param.sfo)\n",label);
        }
    }
    return ok;
}
/* The add-on folders of one folder: each subfolder is named by its entitlement label (up to 16
 * characters), as the launcher sets them up. Labels seen before are skipped. */
static void scan_labels(const char *folder,char labels[][LABEL_SIZE],uint32_t *count) {
    DIR *dir=opendir(folder);
    for (struct dirent *e; dir && (e=readdir(dir));) {
        char path[4096];
        struct stat entry;
        size_t n=strlen(e->d_name);
        if (e->d_name[0]=='.' || n>=LABEL_SIZE || *count>=MAX_ADDONS) continue;
        if (snprintf(path,sizeof(path),"%s/%s",folder,e->d_name)>=(int)sizeof(path) ||
            stat(path,&entry) || !S_ISDIR(entry.st_mode) || !real_addon(path,e->d_name)) continue;
        int seen=0;
        for (uint32_t i=0;i<*count;++i) seen|=!strcmp(labels[i],e->d_name);
        if (!seen) memcpy(labels[(*count)++],e->d_name,n+1);
    }
    if (dir) closedir(dir);
}
/* This game's add-on folder: <user>/addcont/<title id>. */
static int addon_root(char *out,size_t size) {
    return (size_t)snprintf(out,size,"%s/addcont/CUSA00473",runtime_file_user_dir())<size;
}
int runtime_content_addon(const char *label,char *folder,size_t size) {
    char root[4096];
    if (!label || !*label || strlen(label)>=LABEL_SIZE || strchr(label,'/') || strchr(label,'\\') ||
        !addon_root(root,sizeof(root))) return 0;
    char path[4096];
    struct stat entry;
    if ((size_t)snprintf(path,sizeof(path),"%s/%s",root,label)>=sizeof(path) || stat(path,&entry) ||
        !S_ISDIR(entry.st_mode) || !real_addon(path,label)) return 0;
    if (folder && size) snprintf(folder,size,"%s",path);
    return 1;
}
/* Does the add-on have data besides its sce_sys (a DLC that is more than an entitlement)? */
static int has_data(const char *folder) {
    DIR *dir=opendir(folder);
    int data=0;
    for (struct dirent *e; dir && !data && (e=readdir(dir));)
        data=e->d_name[0]!='.' && strcmp(e->d_name,"sce_sys");
    if (dir) closedir(dir);
    return data;
}
/* Add-ons are the folders <user>/addcont/CUSA00473/<entitlement label>. No add-on data is mounted: LittleBigPlanet
 * 3 imports no sceAppContentAddcontMount; its DLC is an entitlement. Its check (eboot 0x2463d0) lists the add-ons
 * for each store service label, asks each one's entitlement key (runtime_extra.c), and grants only those whose
 * download status is 0, "no extra data": an add-on of sce_sys alone reports that, one with data "installed" (4).
 * Without a list the count is returned, with one at most capacity entries are written. Never touch unused list
 * slots. */
static ABI int32_t addon_list(uint32_t service,void *list,uint32_t capacity,uint32_t *hits) {
    require_initialized();
    if ((!capacity || !list) && !hits) return PARAMETER;
    static char labels[MAX_ADDONS][LABEL_SIZE];
    uint32_t found=0;
    char root[4096];
    if (addon_root(root,sizeof(root))) scan_labels(root,labels,&found);
    uint32_t count=list && capacity && found>capacity ? capacity : found;
    static unsigned logged;
    if (logged<8) {  /* what the game asks, for chasing DLC problems (each call after these is the same) */
        ++logged;
        printf("Runtime: AppContent add-on list (service %u, room for %u): %u installed",service,capacity,(unsigned)found);
        for (uint32_t i=0;i<found;++i) printf("%s%s",i ? ", " : ": ",labels[i]);
        putchar('\n');
    }
    if (list && capacity) for (uint32_t i=0;i<count;++i) {
        AddonInfo info;
        memset(&info,0,sizeof(info)); memcpy(info.label,labels[i],strlen(labels[i]));
        char folder[4200];
        info.status=runtime_content_addon(labels[i],folder,sizeof(folder)) && has_data(folder) ? ADDON_INSTALLED
                                                                                              : ADDON_NO_EXTRA_DATA;
        memcpy((unsigned char *)list+(size_t)i*sizeof(info),&info,sizeof(info));
    }
    if (hits) *hits=count;
    ++lists; return 0;
}
uintptr_t runtime_content_resolve(const char *name) {
    if (!strcmp(name,"g8cM39EUZ6o#M#N")) return (uintptr_t)module_load;
    if (!strcmp(name,"fMP5NHUOaMk#M#N")) return (uintptr_t)module_loaded;
    if (!strcmp(name,"eR2bZFAAU0Q#M#N")) return (uintptr_t)module_unload;
    if (!strcmp(name,"R9lA82OraNs#c#d")) return (uintptr_t)content_init;
    if (!strcmp(name,"99b82IKXpH4#c#d")) return (uintptr_t)param_int;
    if (!strcmp(name,"xnd8BJzAxmk#c#d")) return (uintptr_t)addon_list;
    return 0;
}
void runtime_content_report(void) {
    printf("Runtime: AppContent references=%u, initialized=%u, parameter queries=%u, list queries=%u\n",refs,initialized,queries,lists);
}
