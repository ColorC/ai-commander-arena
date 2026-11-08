/* ai-commander-arena: tiny Lua C module loaded by FPC's own Lua (lua\arena.dll via package.cpath).
 * Runs inside the game process: lists this process's windows and controls, posts key presses and button clicks
 * to them, captures a window to .bmp, reads clocks and sleeps (real-time pacing).
 * No other process is touched. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lua.h"
#include "lauxlib.h"

typedef struct { lua_State *L; int n; } enum_ctx;

static BOOL CALLBACK collect(HWND h, LPARAM lp) {
  enum_ctx *c = (enum_ctx *)lp;
  DWORD pid = 0;
  GetWindowThreadProcessId(h, &pid);
  if (pid != GetCurrentProcessId()) return TRUE;
  char cls[128] = {0}, title[256] = {0};
  GetClassNameA(h, cls, sizeof cls);
  GetWindowTextA(h, title, sizeof title);
  lua_createtable(c->L, 0, 4);
  lua_pushinteger(c->L, (lua_Integer)(INT_PTR)h); lua_setfield(c->L, -2, "hwnd");
  lua_pushstring(c->L, cls); lua_setfield(c->L, -2, "class");
  lua_pushstring(c->L, title); lua_setfield(c->L, -2, "title");
  lua_pushboolean(c->L, IsWindowVisible(h)); lua_setfield(c->L, -2, "visible");
  lua_rawseti(c->L, -2, ++c->n);
  return TRUE;
}

/* arena.windows() -> { {hwnd=, class=, title=, visible=}, ... } (top-level windows of this process) */
static int l_windows(lua_State *L) {
  enum_ctx c = { L, 0 };
  lua_newtable(L);
  EnumWindows(collect, (LPARAM)&c);
  return 1;
}

typedef struct { const char *cls; HWND found; } find_ctx;

static BOOL CALLBACK find_cls(HWND h, LPARAM lp) {
  find_ctx *f = (find_ctx *)lp;
  DWORD pid = 0;
  GetWindowThreadProcessId(h, &pid);
  if (pid != GetCurrentProcessId()) return TRUE;
  char cls[128] = {0};
  GetClassNameA(h, cls, sizeof cls);
  if (strcmp(cls, f->cls) == 0) { f->found = h; return FALSE; }
  return TRUE;
}

/* arena.key(vk [, class = "TfMain"]) -> hwnd or nil, err : posts WM_KEYDOWN + WM_KEYUP to that window */
static int l_key(lua_State *L) {
  UINT vk = (UINT)luaL_checkinteger(L, 1);
  find_ctx f = { luaL_optstring(L, 2, "TfMain"), NULL };
  EnumWindows(find_cls, (LPARAM)&f);
  if (!f.found) { lua_pushnil(L); lua_pushfstring(L, "no window of class %s in this process", f.cls); return 2; }
  UINT scan = MapVirtualKeyA(vk, MAPVK_VK_TO_VSC);
  LPARAM down = 1 | ((LPARAM)scan << 16);
  LPARAM up = down | (1u << 30) | (1u << 31);
  BOOL ok = PostMessageA(f.found, WM_KEYDOWN, vk, down) && PostMessageA(f.found, WM_KEYUP, vk, up);
  if (!ok) { lua_pushnil(L); lua_pushfstring(L, "PostMessage failed: %d", (int)GetLastError()); return 2; }
  lua_pushinteger(L, (lua_Integer)(INT_PTR)f.found);
  return 1;
}

typedef struct { const char *cls; const char *text; HWND top; HWND button; } click_ctx;

static BOOL CALLBACK find_button(HWND h, LPARAM lp) {
  click_ctx *c = (click_ctx *)lp;
  char t[256] = {0};
  GetWindowTextA(h, t, sizeof t);
  if (strcmp(t, c->text) == 0) { c->button = h; return FALSE; }
  return TRUE;
}

static BOOL CALLBACK find_dialog(HWND h, LPARAM lp) {
  click_ctx *c = (click_ctx *)lp;
  DWORD pid = 0;
  GetWindowThreadProcessId(h, &pid);
  if (pid != GetCurrentProcessId() || !IsWindowVisible(h)) return TRUE;
  char cls[128] = {0};
  GetClassNameA(h, cls, sizeof cls);
  if (strcmp(cls, c->cls) != 0) return TRUE;
  c->top = h;
  EnumChildWindows(h, find_button, lp);
  return c->button == NULL;
}

/* arena.click(class, text) -> button hwnd, dialog title | nil, err : in a visible top-level window of this
 * process with that class, posts BM_CLICK to the child control whose text is exactly `text` */
static int l_click(lua_State *L) {
  click_ctx c = { luaL_checkstring(L, 1), luaL_checkstring(L, 2), NULL, NULL };
  EnumWindows(find_dialog, (LPARAM)&c);
  if (!c.button) { lua_pushnil(L); lua_pushfstring(L, c.top ? "no control \"%s\"" : "no visible %s", c.top ? c.text : c.cls); return 2; }
  char title[512] = {0};
  GetWindowTextA(c.top, title, sizeof title);
  if (!PostMessageA(c.button, BM_CLICK, 0, 0)) { lua_pushnil(L); lua_pushfstring(L, "PostMessage failed: %d", (int)GetLastError()); return 2; }
  lua_pushinteger(L, (lua_Integer)(INT_PTR)c.button);
  lua_pushstring(L, title);
  return 2;
}

/* arena.close(class) -> hwnd, title | nil, err : posts WM_CLOSE to the first visible top-level window of this
 * process with that class (the hot-seat turn replay window has no text buttons) */
static int l_close(lua_State *L) {
  click_ctx c = { luaL_checkstring(L, 1), "\x01", NULL, NULL }; /* text that no control has: stops at the first match */
  EnumWindows(find_dialog, (LPARAM)&c);
  if (!c.top) { lua_pushnil(L); lua_pushfstring(L, "no visible %s", c.cls); return 2; }
  char title[512] = {0};
  GetWindowTextA(c.top, title, sizeof title);
  if (!PostMessageA(c.top, WM_CLOSE, 0, 0)) { lua_pushnil(L); lua_pushfstring(L, "PostMessage failed: %d", (int)GetLastError()); return 2; }
  lua_pushinteger(L, (lua_Integer)(INT_PTR)c.top);
  lua_pushstring(L, title);
  return 2;
}

/* top-level window of this process with that class: a visible one if any, else a hidden one (the main form is
 * hidden while the game runs minimized) */
static HWND top_of(const char *cls) {
  click_ctx c = { cls, "\x01", NULL, NULL };
  EnumWindows(find_dialog, (LPARAM)&c);
  if (!c.top) {
    find_ctx f = { cls, NULL };
    EnumWindows(find_cls, (LPARAM)&f);
    c.top = f.found;
  }
  return c.top;
}

static BOOL CALLBACK collect_child(HWND h, LPARAM lp) {
  enum_ctx *c = (enum_ctx *)lp;
  char cls[128] = {0}, text[256] = {0};
  RECT r;
  GetClassNameA(h, cls, sizeof cls);
  GetWindowTextA(h, text, sizeof text);
  GetWindowRect(h, &r);
  lua_createtable(c->L, 0, 7);
  lua_pushinteger(c->L, (lua_Integer)(INT_PTR)h); lua_setfield(c->L, -2, "hwnd");
  lua_pushinteger(c->L, (lua_Integer)(INT_PTR)GetParent(h)); lua_setfield(c->L, -2, "parent");
  lua_pushstring(c->L, cls); lua_setfield(c->L, -2, "class");
  lua_pushstring(c->L, text); lua_setfield(c->L, -2, "text");
  lua_pushboolean(c->L, IsWindowVisible(h)); lua_setfield(c->L, -2, "visible");
  lua_pushinteger(c->L, r.right - r.left); lua_setfield(c->L, -2, "w");
  lua_pushinteger(c->L, r.bottom - r.top); lua_setfield(c->L, -2, "h");
  if (strstr(cls, "Check")) { /* BM_GETCHECK: 0 / 1 for button-based check boxes */
    lua_pushinteger(c->L, (lua_Integer)SendMessageA(h, BM_GETCHECK, 0, 0)); lua_setfield(c->L, -2, "checked");
  }
  lua_rawseti(c->L, -2, ++c->n);
  return TRUE;
}

/* arena.children(class) -> { {hwnd=, parent=, class=, text=, visible=, w=, h=, checked=}, ... } | nil, err : every child
 * control (all depths) of the top-level window of this process with that class (see top_of) */
static int l_children(lua_State *L) {
  HWND top = top_of(luaL_checkstring(L, 1));
  if (!top) { lua_pushnil(L); lua_pushfstring(L, "no visible %s", lua_tostring(L, 1)); return 2; }
  enum_ctx c = { L, 0 };
  lua_newtable(L);
  EnumChildWindows(top, collect_child, (LPARAM)&c);
  return 1;
}

/* arena.shot(class, path) -> width, height | nil, err : PrintWindow of the top-level window of this process with
 * that class (see top_of), written as a 24-bit .bmp (the end-of-game screen, for checking parsed results) */
static int l_shot(lua_State *L) {
  HWND top = top_of(luaL_checkstring(L, 1));
  const char *path = luaL_checkstring(L, 2);
  if (!top) { lua_pushnil(L); lua_pushfstring(L, "no visible %s", lua_tostring(L, 1)); return 2; }
  RECT r;
  GetWindowRect(top, &r);
  int w = r.right - r.left, h = r.bottom - r.top;
  if (w <= 0 || h <= 0) { lua_pushnil(L); lua_pushstring(L, "empty window"); return 2; }
  HDC screen = GetDC(NULL), mem = CreateCompatibleDC(screen);
  HBITMAP bmp = CreateCompatibleBitmap(screen, w, h);
  HGDIOBJ old = SelectObject(mem, bmp);
  BOOL ok = PrintWindow(top, mem, 2 /* PW_RENDERFULLCONTENT */);
  SelectObject(mem, old);
  int stride = ((w * 3 + 3) / 4) * 4;
  BITMAPINFOHEADER bi = { sizeof bi, w, h, 1, 24, BI_RGB, (DWORD)(stride * h), 0, 0, 0, 0 };
  char *bits = (char *)malloc((size_t)stride * h);
  int lines = bits ? GetDIBits(mem, bmp, 0, (UINT)h, bits, (BITMAPINFO *)&bi, DIB_RGB_COLORS) : 0;
  DeleteObject(bmp); DeleteDC(mem); ReleaseDC(NULL, screen);
  if (!ok || lines != h) { free(bits); lua_pushnil(L); lua_pushstring(L, "PrintWindow failed"); return 2; }
  BITMAPFILEHEADER bf = { 0x4D42, (DWORD)(sizeof bf + sizeof bi + stride * h), 0, 0, (DWORD)(sizeof bf + sizeof bi) };
  FILE *f = fopen(path, "wb");
  if (!f) { free(bits); lua_pushnil(L); lua_pushfstring(L, "cannot write %s", path); return 2; }
  fwrite(&bf, sizeof bf, 1, f); fwrite(&bi, sizeof bi, 1, f); fwrite(bits, 1, (size_t)stride * h, f);
  fclose(f);
  free(bits);
  lua_pushinteger(L, w);
  lua_pushinteger(L, h);
  return 2;
}

/* arena.ms() -> milliseconds since boot (for real-time pacing) */
static int l_ms(lua_State *L) {
  lua_pushinteger(L, (lua_Integer)GetTickCount64());
  return 1;
}

/* arena.now() -> wall clock, Unix milliseconds (shared clock with the job script) */
static int l_now(lua_State *L) {
  FILETIME ft;
  GetSystemTimeAsFileTime(&ft);
  ULONGLONG t = ((ULONGLONG)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
  lua_pushinteger(L, (lua_Integer)((t - 116444736000000000ULL) / 10000));
  return 1;
}

/* arena.sleep(ms): blocks the calling (game) thread. Peeks the message queue every 100 ms without removing
 * anything, so Windows does not mark the window as not responding; no message is dispatched. */
static int l_sleep(lua_State *L) {
  lua_Integer ms = luaL_checkinteger(L, 1);
  ULONGLONG end = GetTickCount64() + (ULONGLONG)(ms > 0 ? ms : 0);
  MSG msg;
  for (;;) {
    ULONGLONG now = GetTickCount64();
    if (now >= end) break;
    ULONGLONG left = end - now;
    Sleep((DWORD)(left < 100 ? left : 100));
    PeekMessageA(&msg, NULL, 0, 0, PM_NOREMOVE);
  }
  return 0;
}

static const luaL_Reg fns[] = { {"windows", l_windows}, {"key", l_key}, {"click", l_click}, {"close", l_close}, {"ms", l_ms}, {"now", l_now},
                                {"sleep", l_sleep}, {"children", l_children}, {"shot", l_shot},
                                {NULL, NULL} };

__declspec(dllexport) int luaopen_arena(lua_State *L) {
  luaL_newlib(L, fns);
  return 1;
}
