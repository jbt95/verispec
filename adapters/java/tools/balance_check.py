#!/usr/bin/env python3
"""Structural sanity check for Java/Kotlin sources: balance of braces, parens
and brackets, ignoring comments, strings and char literals."""
import sys


def scan(path: str, kotlin: bool = False) -> int:
    with open(path, encoding="utf-8") as handle:
        src = handle.read()
    i = 0
    n = len(src)
    stack = []
    line = 1
    problems = []
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        # line comment
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        # block comment
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            if j < 0:
                problems.append(f"{path}:{line}: unterminated block comment")
                break
            line += src.count("\n", i, j)
            i = j + 2
            continue
        # string literal
        if c == '"':
            if src[i : i + 3] == '"""':
                j = src.find('"""', i + 3)
                if j < 0:
                    problems.append(f"{path}:{line}: unterminated text block")
                    break
                line += src.count("\n", i, j)
                i = j + 3
                continue
            i += 1
            while i < n:
                if src[i] == "\\":
                    if src[i + 1 : i + 2] == "\n":
                        line += 1
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                if src[i] == "\n":
                    problems.append(f"{path}:{line}: newline inside a string literal")
                    break
                i += 1
            continue
        # char literal
        if c == "'":
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "'":
                    i += 1
                    break
                if src[i] == "\n":
                    problems.append(f"{path}:{line}: newline inside a char literal")
                    break
                i += 1
            continue
        if c in "([{":
            stack.append((c, line))
            i += 1
            continue
        if c in ")]}":
            if not stack:
                problems.append(f"{path}:{line}: unmatched '{c}'")
                i += 1
                continue
            opener, oline = stack.pop()
            if "([{".index(opener) != ")]}".index(c):
                problems.append(
                    f"{path}:{line}: '{c}' closes '{opener}' opened at line {oline}"
                )
            i += 1
            continue
        i += 1
    for opener, oline in stack:
        problems.append(f"{path}:{oline}: '{opener}' is never closed")
    for p in problems:
        print(p)
    return len(problems)


if __name__ == "__main__":
    bad = 0
    for arg in sys.argv[1:]:
        bad += scan(arg)
    print("OK" if bad == 0 else f"{bad} problem(s)")
    sys.exit(1 if bad else 0)
