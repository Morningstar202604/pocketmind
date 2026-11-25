/*
 * libpty：给原生引擎一个真正的 PTY。
 *
 * 为什么不用 Runtime.exec：Android 应用的 exec 没有控制终端，vim/top/less
 * 会退化成 dumb 输出。这里用 posix_openpt + fork + exec，子进程接管 slave，
 * 父进程把 master fd 交给 Kotlin 侧读写。
 *
 * 编译（NDK 27）：
 *   aarch64-linux-android26-clang -D_GNU_SOURCE -shared -fPIC -O2 -o libpty.so pty.c
 *   x86_64-linux-android26-clang  -D_GNU_SOURCE -shared -fPIC -O2 -o libpty.so pty.c
 */
#include <jni.h>
#include <errno.h>
#include <fcntl.h>
#include <pty.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

static void put_env(char **envp, int *n, const char *key, const char *value) {
    size_t klen = strlen(key);
    // 同名环境变量以最后一次设置为准：NativeProot.env 要把 guest PATH/HOME
    // 覆盖掉这里的 Android 默认值，否则终端里找不到 /usr/bin/qwen。
    for (int i = 0; i < *n; i++) {
        if (envp[i] && strncmp(envp[i], key, klen) == 0 && envp[i][klen] == '=') {
            char *item = (char *) malloc(strlen(key) + strlen(value) + 2);
            if (!item) return;
            sprintf(item, "%s=%s", key, value);
            free(envp[i]);
            envp[i] = item;
            return;
        }
    }
    if (*n >= 30) return;
    char *item = (char *) malloc(strlen(key) + strlen(value) + 2);
    if (!item) return;
    sprintf(item, "%s=%s", key, value);
    envp[(*n)++] = item;
}

JNIEXPORT jintArray JNICALL
Java_com_pocket_agent_runtime_NativePty_start(
    JNIEnv *env, jclass clazz, jstring cwd_, jobjectArray argv_, jobjectArray extra_env_,
    jstring home_, jstring tmp_, jint cols, jint rows) {

    const char *cwd = (*env)->GetStringUTFChars(env, cwd_, NULL);
    const char *home = (*env)->GetStringUTFChars(env, home_, NULL);
    const char *tmp = (*env)->GetStringUTFChars(env, tmp_, NULL);
    int argc = argv_ ? (*env)->GetArrayLength(env, argv_) : 0;
    if (argc < 1 || argc > 32) goto fail;
    char *argv[33];
    jstring argv_refs[33];
    for (int i = 0; i < argc; i++) {
        argv_refs[i] = (jstring) (*env)->GetObjectArrayElement(env, argv_, i);
        argv[i] = (char *) (*env)->GetStringUTFChars(env, argv_refs[i], NULL);
    }
    argv[argc] = NULL;

    char *envp[40];
    int envc = 0;
    put_env(envp, &envc, "TERM", "xterm-256color");
    put_env(envp, &envc, "HOME", home);
    put_env(envp, &envc, "TMPDIR", tmp);
    put_env(envp, &envc, "PATH", "/system/bin:/system/xbin:/vendor/bin:/usr/bin:/bin");
    put_env(envp, &envc, "LANG", "en_US.UTF-8");
    if (extra_env_) {
        int n = (*env)->GetArrayLength(env, extra_env_);
        for (int i = 0; i < n && envc < 35; i++) {
            jstring js = (jstring) (*env)->GetObjectArrayElement(env, extra_env_, i);
            const char *s = (*env)->GetStringUTFChars(env, js, NULL);
            char *copy = strdup(s);
            char *eq = copy ? strchr(copy, '=') : NULL;
            if (eq) {
                *eq = '\0';
                put_env(envp, &envc, copy, eq + 1);
            }
            free(copy);
            (*env)->ReleaseStringUTFChars(env, js, s);
            (*env)->DeleteLocalRef(env, js);
        }
    }
    envp[envc] = NULL;

    int master = posix_openpt(O_RDWR | O_NOCTTY);
    if (master < 0) goto fail_env;
    if (grantpt(master) != 0 || unlockpt(master) != 0) {
        close(master);
        master = -1;
        goto fail_env;
    }
    char slave_name[128];
    const char *name = ptsname(master);
    if (!name || strlen(name) >= sizeof(slave_name)) {
        close(master);
        master = -1;
        goto fail_env;
    }
    strcpy(slave_name, name);

    struct winsize ws;
    memset(&ws, 0, sizeof(ws));
    ws.ws_col = (unsigned short) (cols > 0 ? cols : 80);
    ws.ws_row = (unsigned short) (rows > 0 ? rows : 24);
    ioctl(master, TIOCSWINSZ, &ws);

    pid_t pid = fork();
    if (pid < 0) {
        close(master);
        master = -1;
        goto fail_env;
    }
    if (pid == 0) {
        close(master);
        setsid();
        int slave = open(slave_name, O_RDWR | O_NOCTTY);
        if (slave < 0) _exit(127);
        ioctl(slave, TIOCSCTTY, 0);
        dup2(slave, STDIN_FILENO);
        dup2(slave, STDOUT_FILENO);
        dup2(slave, STDERR_FILENO);
        if (slave > 2) close(slave);
        chdir(cwd);
        execve(argv[0], argv, envp);
        _exit(127);
    }

    jint values[2];
    values[0] = master;
    values[1] = (jint) pid;
    jintArray result = (*env)->NewIntArray(env, 2);
    if (result) (*env)->SetIntArrayRegion(env, result, 0, 2, values);
    for (int i = 0; i < argc; i++) {
        (*env)->ReleaseStringUTFChars(env, argv_refs[i], argv[i]);
        (*env)->DeleteLocalRef(env, argv_refs[i]);
    }
    for (int i = 0; i < envc; i++) free(envp[i]);
    (*env)->ReleaseStringUTFChars(env, cwd_, cwd);
    (*env)->ReleaseStringUTFChars(env, home_, home);
    (*env)->ReleaseStringUTFChars(env, tmp_, tmp);
    return result;

fail_env:
    for (int i = 0; i < envc; i++) free(envp[i]);
fail:
    if (cwd) (*env)->ReleaseStringUTFChars(env, cwd_, cwd);
    if (home) (*env)->ReleaseStringUTFChars(env, home_, home);
    if (tmp) (*env)->ReleaseStringUTFChars(env, tmp_, tmp);
    return NULL;
}

JNIEXPORT void JNICALL
Java_com_pocket_agent_runtime_NativePty_resize(JNIEnv *env, jclass clazz, jint fd, jint cols, jint rows) {
    (void) env;
    (void) clazz;
    struct winsize ws;
    memset(&ws, 0, sizeof(ws));
    ws.ws_col = (unsigned short) (cols > 0 ? cols : 80);
    ws.ws_row = (unsigned short) (rows > 0 ? rows : 24);
    ioctl(fd, TIOCSWINSZ, &ws);
}

JNIEXPORT void JNICALL
Java_com_pocket_agent_runtime_NativePty_close(JNIEnv *env, jclass clazz, jint fd, jint pid) {
    (void) env;
    (void) clazz;
    if (fd >= 0) close(fd);
    if (pid > 0) {
        kill((pid_t) pid, SIGHUP);
        for (int i = 0; i < 10; i++) {
            pid_t r = waitpid((pid_t) pid, NULL, WNOHANG);
            if (r == (pid_t) pid || r < 0) return;
            usleep(20000);
        }
        kill((pid_t) pid, SIGKILL);
        waitpid((pid_t) pid, NULL, 0);
    }
}
