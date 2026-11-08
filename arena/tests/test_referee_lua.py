"""lua_literal: only tables, strings, integers, booleans; strings can never break out of their quotes."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.referee import lua_literal  # noqa: E402


def parse(text):
    """Minimal reader for the subset lua_literal writes: proves the text is pure data and round-trips."""
    pos = [0]

    def ws():
        while pos[0] < len(text) and text[pos[0]] in " \t\n":
            pos[0] += 1

    def string():
        assert text[pos[0]] == '"'
        pos[0] += 1
        out = bytearray()
        while text[pos[0]] != '"':
            c = text[pos[0]]
            if c == "\\":
                n = text[pos[0] + 1]
                if n.isdigit():
                    out.append(int(text[pos[0] + 1:pos[0] + 4]))
                    pos[0] += 4
                else:
                    out += n.encode()
                    pos[0] += 2
            else:
                assert c not in "\n\r" and 0x20 <= ord(c) < 0x7F, f"raw char {c!r} in string"
                out += c.encode()
                pos[0] += 1
        pos[0] += 1
        return out.decode("utf-8")

    def value():
        ws()
        c = text[pos[0]]
        if c == '"':
            return string()
        if c == "{":
            pos[0] += 1
            items, keyed = [], {}
            while True:
                ws()
                if text[pos[0]] == "}":
                    pos[0] += 1
                    return keyed if keyed else items
                bare = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=").match(text, pos[0])
                if bare:
                    pos[0] = bare.end()
                    keyed[bare[0][:-1]] = value()
                elif text[pos[0]] == "[":
                    pos[0] += 1
                    k = string()
                    assert text[pos[0]:pos[0] + 2] == "]="
                    pos[0] += 2
                    keyed[k] = value()
                else:
                    items.append(value())
                ws()
                if text[pos[0]] == ",":
                    pos[0] += 1
        m = re.compile(r"-?\d+|true|false").match(text, pos[0])
        assert m, text[pos[0]:pos[0] + 20]
        pos[0] = m.end()
        if m[0] in ("true", "false"):
            return m[0] == "true"
        return int(m[0])

    v = value()
    ws()
    assert pos[0] == len(text), "trailing text"
    return v


class LuaLiteral(unittest.TestCase):
    def test_round_trip(self):
        v = {"batch_id": "s0-0001", "cmds": [{"cmd_id": "s0-c0001", "kind": "orders", "unit": "1/A/TF Alamo",
                                              "orders": [{"orders": "Move Hasty", "destination_location": "2416"}]}],
             "ok": True, "off": False, "n": -12}
        self.assertEqual(parse(lua_literal.lua(v)), v)

    def test_chunk_is_a_return_statement(self):
        self.assertTrue(lua_literal.chunk({"a": 1}).startswith("return {"))

    def test_hostile_strings_stay_inside_quotes(self):
        for s in ['"]] os.execute("calc") --', 'a\\', 'x"y', "line\nbreak", "cr\rlf", "nul\x00byte", "tab\t", "]]", "--[[",
                  "中文 unit", "emoji \U0001F600", "\x7f", "\\\"\\", "%s%d", "\\u{41}", "\\065"]:
            text = lua_literal.lua(s)
            self.assertEqual(parse(text), s, repr(s))
            self.assertNotIn("\n", text)
            self.assertNotIn("\r", text)
            self.assertTrue(text.startswith('"') and text.endswith('"'))

    def test_hostile_keys_are_quoted(self):
        v = {'a"] = os.exit() --': 1, "x y": 2}
        self.assertEqual(parse(lua_literal.lua(v)), v)

    def test_plain_keys_are_bare_and_keywords_are_quoted(self):
        text = lua_literal.lua({"batch_id": 1, "end": 2, "not": 3, "a b": 4, "9x": 5, "ok_1": 6})
        self.assertEqual(text, '{["9x"]=5,["a b"]=4,batch_id=1,["end"]=2,["not"]=3,ok_1=6}')
        self.assertEqual(lua_literal.chunk({"batch_id": "s0-0007", "side": 0, "cmds": []}),
                         'return {batch_id="s0-0007",cmds={},side=0}')

    def test_refuses_what_is_not_data(self):
        for bad in (1.5, None, float("nan"), object(), b"bytes", {1, 2}, {1: "int key"}, 2 ** 60, "lone\ud800surrogate"):
            with self.assertRaises((ValueError, UnicodeEncodeError), msg=repr(bad)):
                lua_literal.lua(bad)

    def test_depth_is_bounded(self):
        v = []
        for _ in range(40):
            v = [v]
        with self.assertRaises(ValueError):
            lua_literal.lua(v)

    def test_bool_is_not_an_integer(self):
        self.assertEqual(lua_literal.lua([True, False, 1, 0]), "{true,false,1,0}")


if __name__ == "__main__":
    unittest.main()
