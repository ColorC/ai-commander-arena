#!/bin/sh
# Build the Lua runtime FPC loads (lua54_64.dll), the arena C module (lua/arena.dll) and a standalone lua.exe (the
# arena script sandbox host, arena/sandbox/runner.py; copy it to arena/sandbox/bin/) with MinGW-w64.
#   sh fpc/native/build.sh <lua-5.4.3 source dir> <out dir>
# Source: https://www.lua.org/ftp/lua-5.4.3.tar.gz
#   sha256 f8612276169e3bfcbcfb8f226195bfc6e466fe13042f1076cbde92b7ec96bbfb
# Verified with gcc 14.2.0 (MinGW-W64 x86_64-ucrt-posix-seh).
# FPC looks for exactly "lua54_64.dll" in its root; arena.dll must import that same name so it binds
# to the Lua state the game already loaded.
set -e
SRC="$1/src"
OUT="$2"
HERE="$(cd "$(dirname "$0")" && pwd)"
OBJ="$OUT/obj"
mkdir -p "$OBJ" "$OUT/lua"
for f in lapi lcode lctype ldebug ldo ldump lfunc lgc llex lmem lobject lopcodes lparser lstate lstring \
         ltable ltm lundump lvm lzio lauxlib lbaselib lcorolib ldblib liolib lmathlib loadlib loslib \
         lstrlib ltablib lutf8lib linit; do
  gcc -O2 -Wall -DLUA_COMPAT_5_3 -DLUA_BUILD_AS_DLL -c "$SRC/$f.c" -o "$OBJ/$f.o"
done
gcc -shared -s -static-libgcc -o "$OUT/lua54_64.dll" "$OBJ"/*.o -Wl,--out-implib,"$OBJ/liblua54_64.a"
gcc -O2 -Wall -shared -s -static-libgcc -I"$SRC" -o "$OUT/lua/arena.dll" "$HERE/arena.c" "$OBJ/liblua54_64.a" -lgdi32
objdump -p "$OUT/lua/arena.dll" | grep -q 'DLL Name: lua54_64.dll'
# lua.exe links the same objects statically, so it runs without any DLL next to it
mkdir -p "$OUT/obj-exe"
gcc -O2 -Wall -DLUA_COMPAT_5_3 -c "$SRC/lua.c" -o "$OUT/obj-exe/lua.o"
gcc -s -static -o "$OUT/lua.exe" "$OUT/obj-exe/lua.o" "$OBJ"/*.o
echo "built $OUT/lua54_64.dll, $OUT/lua/arena.dll and $OUT/lua.exe"
