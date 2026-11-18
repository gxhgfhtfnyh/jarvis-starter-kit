#!/usr/bin/env python3
"""code_doctor.py - static code checker and safe auto-repair tool for AI agents.

Design rules
  * Never imports or executes the code it inspects. Everything is static.
  * Deterministic. Same input, same output. No model calls.
  * `fix` is a dry run unless --write is given. A fix is kept only if the
    file still parses afterwards, and a .bak copy is made before writing.
  * Anything it cannot repair safely is reported with a hint, so a model
    can repair it and then re-run `check` to verify.

Commands
  check PATH...    report problems             (exit 0 clean, 1 problems, 2 usage)
  fix PATH...      show repairs as a diff; add --write to apply them
                   (--aggressive also applies behaviour-changing fixes)
  explain [FILE]   turn a Python traceback (file or stdin) into structured frames

Add --json to any command for machine-readable output.
Standard library only. Python 3.9+.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import difflib
import json
import os
import re
import shutil
import subprocess
import symtable
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

VERSION = "1.0"
MAX_BYTES = 2_000_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".tox",
             ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist", "build",
             "site-packages", "dist-packages"}
SEVERITIES = ("error", "warning", "info")
# Fixes that change runtime behaviour. `x == None` is a real idiom in SQLAlchemy
# and NumPy, and narrowing a bare except changes what is caught, so these are
# applied only with --aggressive.
AGGRESSIVE = {"W-NONE-COMPARE", "W-BARE-EXCEPT"}


@dataclass
class Finding:
    file: str
    line: int
    col: int
    code: str
    severity: str
    message: str
    hint: str = ""
    fixable: bool = False
    edit: tuple | None = None  # (line, col_start, col_end, replacement); internal

    @property
    def fix_level(self):
        if not self.fixable:
            return None
        return "aggressive" if self.code in AGGRESSIVE else "safe"

    def to_dict(self, lines=None, context=0):
        d = {k: getattr(self, k) for k in
             ("file", "line", "col", "code", "severity", "message", "hint", "fixable")}
        d["fix_level"] = self.fix_level
        if lines and context and 1 <= self.line <= len(lines):
            lo, hi = max(1, self.line - context), min(len(lines), self.line + context)
            d["source"] = [f"{n}: {lines[n - 1]}" for n in range(lo, hi + 1)]
        return d


# --------------------------------------------------------------------- file io

def read_source(path):
    """Return (text, meta). text uses '\\n' only; meta restores the original form."""
    data = Path(path).read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError(f"file larger than {MAX_BYTES} bytes, skipped")
    bom = data.startswith(b"\xef\xbb\xbf")
    text = data[3:].decode("utf-8") if bom else data.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n").replace("\r", "\n"), {"bom": bom, "newline": newline}


def write_source(path, text, meta, backup=True):
    """Atomic write that keeps the original newline style and BOM."""
    path = Path(path)
    data = text.replace("\n", meta["newline"]).encode("utf-8")
    if meta["bom"]:
        data = b"\xef\xbb\xbf" + data
    if backup:
        shutil.copy2(path, str(path) + ".bak")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# -------------------------------------------------------------- python: parse

def parse(text, fname):
    """Return (tree, error, warnings). Exactly one of tree/error is None."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            return ast.parse(text, filename=fname), None, caught
        except SyntaxError as exc:
            return None, exc, caught
        except (ValueError, RecursionError, MemoryError) as exc:
            err = SyntaxError(str(exc) or type(exc).__name__)
            err.lineno, err.offset = 1, 1
            return None, err, caught


SYNTAX_HINTS = [
    ("was never closed", "An opening bracket has no partner. Find the bracket on the "
                         "reported line and add the closing one where the expression ends."),
    ("unterminated string", "A string literal is missing its closing quote on this line."),
    ("unterminated triple-quoted", "A triple-quoted string is never closed. The real "
                                   "mistake is usually far above the reported line."),
    ("unexpected indent", "This line is indented more than the block it belongs to."),
    ("unindent does not match", "This line's indentation matches no enclosing block. "
                                "Align it with the statement that opened the block."),
    ("expected an indented block", "The statement above needs a body. Indent the next "
                                   "line or add `pass`."),
    ("expected ':'", "A block statement (def/if/for/while/class/try/with) is missing "
                     "its trailing colon."),
    ("does not match opening", "Bracket types are mismatched, e.g. `(` closed by `]`."),
    ("unmatched", "There is a closing bracket with no matching opening bracket."),
    ("invalid character", "A non-ASCII look-alike character (smart quote, long dash, "
                          "non-breaking space) is used as code."),
    ("perhaps you forgot a comma", "Two expressions are adjacent with no comma or "
                                   "operator between them."),
    ("cannot assign to", "The left side of `=` is not assignable. Did you mean `==`?"),
    ("Missing parentheses", "Python 2 print statement. Use print(...)."),
    ("invalid syntax", "Check the reported token and the line just above it; a missing "
                       "bracket, comma or quote on the previous line is the usual cause."),
]

FENCE = re.compile(r"^\s*```[\w.+-]*\s*$")
CHAR_CLASSES = [
    ("“”„‟«»＂", '"'),
    ("‘’‚‛＇", "'"),
    ("   　   ", " "),
    ("​‌‍⁠﻿", ""),
    ("–—−", "-"),
    ("，", ","), ("：", ":"), ("；", ";"),
    ("（", "("), ("）", ")"),
]


def _detab(line, width):
    lead = re.match(r"[ \t]*", line).group()
    return lead.expandtabs(width) + line[len(lead):] if "\t" in lead else line


def _syntax_candidates(text, err):
    """Yield (name, repaired_text) guesses for one syntax error, best first."""
    lines = text.split("\n")
    msg = err.msg or ""
    first = next((i for i, l in enumerate(lines) if l.strip()), None)
    if first is not None and FENCE.match(lines[first]):
        rest = lines[first + 1:]
        close = next((i for i, l in enumerate(rest) if FENCE.match(l)), None)
        body = rest[:close] if close is not None else rest
        yield "markdown-fence", "\n".join(body).rstrip("\n") + "\n"
    if isinstance(err, IndentationError) and any(re.match(r" *\t", l) for l in lines):
        for width in (4, 8):
            yield "tabs-to-spaces", "\n".join(_detab(l, width) for l in lines)
    ln = err.lineno or 0
    if not 1 <= ln <= len(lines):
        return
    line = lines[ln - 1]

    def with_line(new):
        return "\n".join(lines[:ln - 1] + [new] + lines[ln:])

    if "expected ':'" in msg:
        yield "missing-colon", with_line(line.rstrip() + ":")
        for i in [m.start() for m in re.finditer("#", line)][::-1]:
            yield "missing-colon", with_line(line[:i].rstrip() + ":  " + line[i:])
    m = re.search(r"\(U\+([0-9A-Fa-f]{4,6})\)", msg)
    if m and "invalid" in msg:
        ch = chr(int(m.group(1), 16))
        for chars, repl in CHAR_CLASSES:
            if ch in chars:
                yield "unicode-lookalike", with_line(
                    line.translate({ord(c): repl for c in chars}))
    if "Missing parentheses" in msg and "print" in msg:
        m = re.match(r"^(\s*)print\s+(.*?)\s*$", line)
        if m and ">>" not in m.group(2):
            yield "py2-print", with_line(f"{m.group(1)}print({m.group(2).rstrip(',')})")


def repair_syntax(text, fname):
    """Apply syntax repairs while each one makes measurable progress."""
    # Progress means the file parses, or the error we targeted is gone and the
    # new one has not been seen before (the tokenizer reports some errors ahead
    # of earlier ones, so "moved later in the file" is not a usable test).
    def sig(e):
        return (e.lineno, e.offset, e.msg)

    applied = []
    _, err, _ = parse(text, fname)
    seen = set()
    for _ in range(60):
        if err is None:
            break
        seen.add(sig(err))
        for name, cand in _syntax_candidates(text, err):
            if cand == text:
                continue
            _, err2, _ = parse(cand, fname)
            if err2 is None:
                ok = True
            elif name == "markdown-fence":
                ok = True  # a fence line is never valid Python
            elif name == "tabs-to-spaces":
                ok = not isinstance(err2, IndentationError)
            else:
                ok = sig(err2) not in seen
            if ok:
                text, err = cand, err2
                applied.append(name)
                break
        else:
            break
    return text, applied


# ------------------------------------------------------------- python: checks

def _col(line, byte_offset):
    """ast column offsets are UTF-8 bytes; convert to a str index."""
    return len(line.encode("utf-8")[:byte_offset].decode("utf-8", "ignore"))


class PyChecker(ast.NodeVisitor):
    def __init__(self, fname, lines):
        self.fname, self.lines, self.out = fname, lines, []

    def add(self, node, code, severity, message, hint="", edit=None):
        self.out.append(Finding(self.fname, node.lineno, node.col_offset + 1, code,
                                severity, message, hint, edit is not None, edit))

    def generic_visit(self, node):
        for name in ("body", "orelse", "finalbody"):
            block = getattr(node, name, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                self._block(block)
        super().generic_visit(node)

    def _block(self, stmts):
        seen = {}
        for i, stmt in enumerate(stmts):
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and not stmt.decorator_list:
                if stmt.name in seen:
                    self.add(stmt, "W-DUPLICATE-DEF", "warning",
                             f"`{stmt.name}` is defined again; the definition on line "
                             f"{seen[stmt.name]} is silently replaced",
                             "Delete or rename one of the two definitions.")
                seen[stmt.name] = stmt.lineno
            if isinstance(stmt, (ast.Return, ast.Raise, ast.Continue, ast.Break)) \
                    and i + 1 < len(stmts):
                self.add(stmts[i + 1], "W-UNREACHABLE", "warning",
                         "code after return/raise/break/continue never runs",
                         "Remove it, or move it above the statement that exits.")
                break

    def _between(self, left, right, pattern, repl):
        """Edit that rewrites the operator text sitting between two operands."""
        if left.end_lineno != right.lineno:
            return None
        line = self.lines[right.lineno - 1]
        a, b = _col(line, left.end_col_offset), _col(line, right.col_offset)
        seg = line[a:b]
        if len(re.findall(pattern, seg)) != 1:
            return None
        return (right.lineno, a, b, re.sub(pattern, repl, seg, count=1))

    def visit_Compare(self, node):
        operands = [node.left] + node.comparators
        for left, op, right in zip(operands, node.ops, operands[1:]):
            consts = [n for n in (left, right) if isinstance(n, ast.Constant)]
            if isinstance(op, (ast.Eq, ast.NotEq)) and any(c.value is None for c in consts):
                good = "is" if isinstance(op, ast.Eq) else "is not"
                bad = "==" if isinstance(op, ast.Eq) else "!="
                self.add(right, "W-NONE-COMPARE", "warning",
                         f"comparison to None with `{bad}`", f"Use `{good} None`.",
                         self._between(left, right, r"\s*" + re.escape(bad) + r"\s*",
                                       f" {good} "))
            elif isinstance(op, (ast.Is, ast.IsNot)) and any(
                    type(c.value) in (str, bytes, int, float) for c in consts):
                good = "==" if isinstance(op, ast.Is) else "!="
                pat = r"\s*\bis\b\s*" if isinstance(op, ast.Is) else r"\s*\bis\s+not\b\s*"
                self.add(right, "W-IS-LITERAL", "warning",
                         "identity test against a literal; the result depends on interning",
                         f"Use `{good}`.", self._between(left, right, pat, f" {good} "))
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        reraises = any(isinstance(n, ast.Raise) and n.exc is None for n in ast.walk(node))
        if node.type is None and not reraises:
            line = self.lines[node.lineno - 1]
            c = _col(line, node.col_offset)
            m = re.match(r"except\s*:", line[c:])
            self.add(node, "W-BARE-EXCEPT", "warning",
                     "bare `except:` also catches KeyboardInterrupt and SystemExit",
                     "Catch `Exception`, or the specific error you expect.",
                     (node.lineno, c, c + m.end(), "except Exception:") if m else None)
        self.generic_visit(node)

    def _defaults(self, node):
        for d in node.args.defaults + [k for k in node.args.kw_defaults if k is not None]:
            empty_call = (isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                          and d.func.id in ("list", "dict", "set")
                          and not d.args and not d.keywords)
            if isinstance(d, (ast.List, ast.Dict, ast.Set)) or empty_call:
                self.add(d, "W-MUTABLE-DEFAULT", "warning",
                         f"mutable default argument in `{node.name}` is shared between calls",
                         "Default to None and create the object inside the function.")
        self.generic_visit(node)

    visit_FunctionDef = visit_AsyncFunctionDef = _defaults

    def visit_JoinedStr(self, node):
        if any(not isinstance(v, ast.Constant) for v in node.values):
            self.generic_visit(node)
            return
        edit = None
        if node.lineno == node.end_lineno:
            line = self.lines[node.lineno - 1]
            a, b = _col(line, node.col_offset), _col(line, node.end_col_offset)
            m = re.match(r"([rR]?)[fF]([rR]?)(?=['\"])", line[a:b])
            if m and "{" not in line[a:b] and "}" not in line[a:b]:
                edit = (node.lineno, a, a + m.end(), m.group(1) + m.group(2))
        self.add(node, "W-FSTRING-NO-FIELDS", "info", "f-string has no placeholders",
                 "Remove the `f` prefix, or add the {value} that was intended.", edit)

    def visit_FormattedValue(self, node):
        self.visit(node.value)  # skip format_spec: it is a JoinedStr with no fields

    def visit_Raise(self, node):
        exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        if isinstance(exc, ast.Name) and exc.id == "NotImplemented" \
                and exc.lineno == exc.end_lineno:
            line = self.lines[exc.lineno - 1]
            self.add(exc, "W-RAISE-NOTIMPLEMENTED", "warning",
                     "`NotImplemented` is not an exception", "Raise NotImplementedError.",
                     (exc.lineno, _col(line, exc.col_offset), _col(line, exc.end_col_offset),
                      "NotImplementedError"))
        self.generic_visit(node)

    def visit_Assert(self, node):
        if isinstance(node.test, ast.Tuple) and node.test.elts:
            self.add(node, "W-ASSERT-TUPLE", "warning",
                     "assert on a non-empty tuple is always true",
                     "Write `assert condition, message` without parentheses.")
        self.generic_visit(node)

    def visit_Dict(self, node):
        seen = set()
        for key in node.keys:
            if isinstance(key, ast.Constant):
                ident = (type(key.value).__name__, repr(key.value))
                if ident in seen:
                    self.add(key, "W-DUPLICATE-KEY", "warning",
                             f"dictionary key {key.value!r} appears twice; the later "
                             "value wins", "Remove or rename one of the keys.")
                seen.add(ident)
        self.generic_visit(node)


MODULE_NAMES = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
    "__path__", "__builtins__", "__annotations__", "__dict__", "__class__",
    "__module__", "__qualname__", "__cached__", "__all__", "WindowsError",
    "reveal_type", "__conditional_annotations__", "__classdict__", "__firstlineno__",
    "__static_attributes__", "__type_params__", "__warningregistry__",
    "get_ipython", "__IPYTHON__"}


def _undefined_names(text, fname, tree):
    """Names read as globals that nothing in the module ever binds."""
    names = [n for n in ast.walk(tree) if isinstance(n, ast.Name)]
    if any(n.id in ("globals", "exec", "__builtins__") for n in names):
        return []  # namespace is manipulated dynamically; analysis would guess
    if any(n.id == "global_enum" for n in names):
        return []  # enum members are exported into the module at import time
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return []
        if isinstance(node, ast.Attribute) and node.attr in ("_convert_", "global_enum"):
            return []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # already reported by parse()
            top = symtable.symtable(text, fname, "exec")
    except Exception:
        return []
    defined, wanted = set(), {}  # wanted: name -> first line of the scope reading it
    stack = [top]
    while stack:
        table = stack.pop()
        stack.extend(table.get_children())
        start = 0 if table is top else table.get_lineno()
        for sym in table.get_symbols():
            name = sym.get_name()
            bound = (sym.is_assigned() or sym.is_imported() or sym.is_namespace()
                     or sym.is_parameter())
            if bound and (table is top or sym.is_declared_global()):
                defined.add(name)
            # `not is_local()` matters: symtable treats a function named "top"
            # as the module scope and reports its parameters as global.
            if sym.is_referenced() and sym.is_global() and \
                    not (sym.is_local() and table is not top) and not bound:
                wanted[name] = min(start, wanted.get(name, start))
    out = []
    loads = sorted((n for n in names if isinstance(n.ctx, ast.Load)),
                   key=lambda n: (n.lineno, n.col_offset))
    for name in sorted(set(wanted) - defined - MODULE_NAMES):
        node = next((n for n in loads if n.id == name and n.lineno >= wanted[name]), None)
        if node is not None:
            out.append(Finding(fname, node.lineno, node.col_offset + 1, "W-UNDEFINED-NAME",
                               "warning", f"name `{name}` is used but never defined",
                               "Fix the spelling, add the missing import, or define it."))
    return out


def _unused_imports(tree, fname):
    if Path(fname).name == "__init__.py":
        return []
    imported = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for a in stmt.names:
                imported[a.asname or a.name.split(".")[0]] = (stmt, a.name)
        elif isinstance(stmt, ast.ImportFrom) and stmt.module != "__future__":
            for a in stmt.names:
                if a.name != "*" and a.asname != a.name:
                    imported[a.asname or a.name] = (stmt, a.name)
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            used.update(re.findall(r"[A-Za-z_]\w*", node.value))  # __all__, annotations
    return [Finding(fname, stmt.lineno, stmt.col_offset + 1, "W-UNUSED-IMPORT", "info",
                    f"`{shown}` is imported but never used",
                    "Remove it unless the import is needed for its side effects.")
            for name, (stmt, shown) in imported.items() if name not in used]


def _ast_findings(tree, text, fname):
    checker = PyChecker(fname, text.split("\n"))
    checker.visit(tree)
    return checker.out


def check_python(path, text):
    fname = str(path)
    tree, err, caught = parse(text, fname)
    if err is not None:
        msg = err.msg or "syntax error"
        hint = next((h for key, h in SYNTAX_HINTS if key in msg), "")
        _, err_after, _ = parse(repair_syntax(text, fname)[0], fname)
        return [Finding(fname, err.lineno or 1, err.offset or 1, "E-SYNTAX", "error",
                        msg, hint, err_after is None)]
    out = _ast_findings(tree, text, fname)
    out += _undefined_names(text, fname, tree)
    out += _unused_imports(tree, fname)
    for w in caught:
        out.append(Finding(fname, w.lineno or 1, 1, "W-SYNTAX-WARNING", "warning",
                           str(w.message), "Use a raw string (r'...') or escape the "
                           "backslash." if "escape" in str(w.message) else ""))
    return out


def fix_python(path, text, aggressive=False):
    fname = str(path)
    text, applied = repair_syntax(text, fname)
    tree, _, _ = parse(text, fname)
    if tree is None:
        return text, applied
    findings = [f for f in _ast_findings(tree, text, fname)
                if f.edit and (aggressive or f.code not in AGGRESSIVE)]
    for f in sorted(findings, key=lambda f: (f.edit[0], f.edit[1]), reverse=True):
        ln, a, b, repl = f.edit
        lines = text.split("\n")
        lines[ln - 1] = lines[ln - 1][:a] + repl + lines[ln - 1][b:]
        candidate = "\n".join(lines)
        if parse(candidate, fname)[0] is not None:  # keep only edits that still parse
            text = candidate
            applied.append(f.code)
    return text, applied


# ------------------------------------------------------------ other file types

def _json_error(fname, exc, code="E-JSON", line=None):
    return Finding(fname, line or exc.lineno, exc.colno, code, "error", exc.msg,
                   "JSON needs double-quoted keys and strings, no trailing commas, "
                   "no comments.")


def _json_candidates(text):
    lines = text.split("\n")
    first = next((i for i, l in enumerate(lines) if l.strip()), None)
    bodies = [text]
    if first is not None and FENCE.match(lines[first]):
        rest = lines[first + 1:]
        close = next((i for i, l in enumerate(rest) if FENCE.match(l)), None)
        bodies.insert(0, "\n".join(rest[:close] if close is not None else rest) + "\n")
    for body in bodies:
        if body != text:
            yield "markdown-fence", body
        yield "trailing-comma", re.sub(r",(\s*[}\]])", r"\1", body)


def fix_json(path, text, aggressive=False):
    try:
        json.loads(text)
        return text, []
    except ValueError:
        pass
    for name, cand in _json_candidates(text):
        try:
            json.loads(cand)
            return cand, [name]
        except ValueError:
            continue
    return text, []


def check_json(path, text):
    try:
        json.loads(text)
        return []
    except json.JSONDecodeError as exc:
        f = _json_error(str(path), exc)
        f.fixable = bool(fix_json(path, text)[1])
        return [f]


def check_jsonl(path, text):
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        if line.strip():
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                out.append(_json_error(str(path), exc, "E-JSONL", n))
                if len(out) >= 20:
                    break
    return out


def check_toml(path, text):
    try:
        import tomllib
    except ImportError:
        return []
    try:
        tomllib.loads(text)
        return []
    except tomllib.TOMLDecodeError as exc:
        m = re.search(r"line (\d+), column (\d+)", str(exc))
        ln, col = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
        return [Finding(str(path), ln, col, "E-TOML", "error", str(exc))]


def check_yaml(path, text):
    try:
        import yaml
    except ImportError:
        return []
    try:
        list(yaml.safe_load_all(text))
        return []
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        return [Finding(str(path), mark.line + 1 if mark else 1,
                        mark.column + 1 if mark else 1, "E-YAML", "error",
                        getattr(exc, "problem", None) or str(exc))]


def _external_syntax(tool_args, code):
    """Syntax-only check through an installed interpreter (parses, never runs)."""
    def check(path, text):
        exe = shutil.which(tool_args[0])
        if not exe:
            return []
        try:
            proc = subprocess.run([exe, *tool_args[1:], str(path)], capture_output=True,
                                  text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode == 0:
            return []
        err = [l for l in proc.stderr.splitlines() if l.strip()]
        m = re.search(r":(?: line)? ?(\d+)", proc.stderr)
        msg = next((l for l in err if "Error" in l or "error" in l), err[-1] if err else "")
        return [Finding(str(path), int(m.group(1)) if m else 1, 1, code, "error",
                        msg.strip() or "syntax error")]
    return check


def check_merge_markers(path, text):
    if "<<<<<<< " not in text or ">>>>>>> " not in text:
        return []
    for n, line in enumerate(text.split("\n"), 1):
        if line.startswith("<<<<<<< "):
            return [Finding(str(path), n, 1, "E-MERGE-MARKER", "error",
                            "unresolved merge conflict markers",
                            "Keep one side of the conflict and delete the marker lines.")]
    return []


def lint_python(path):
    """Optional extra pass through ruff or pyflakes when one is installed."""
    out = []
    if shutil.which("ruff"):
        proc = subprocess.run(["ruff", "check", "--no-fix", "--output-format", "json",
                               str(path)], capture_output=True, text=True, timeout=60)
        try:
            for item in json.loads(proc.stdout or "[]"):
                loc = item.get("location") or {}
                out.append(Finding(str(path), loc.get("row", 1), loc.get("column", 1),
                                   "L-" + str(item.get("code")), "warning",
                                   item.get("message", "")))
        except ValueError:
            pass
        return out
    proc = subprocess.run([sys.executable, "-m", "pyflakes", str(path)],
                          capture_output=True, text=True, timeout=60)
    for line in proc.stdout.splitlines():
        m = re.match(r"^.*?:(\d+):(?:(\d+):?)?\s*(.*)$", line)
        if m:
            out.append(Finding(str(path), int(m.group(1)), int(m.group(2) or 1),
                               "L-PYFLAKES", "warning", m.group(3)))
    return out


CHECKERS = {
    ".py": check_python, ".pyw": check_python,
    ".json": check_json, ".jsonl": check_jsonl, ".ndjson": check_jsonl,
    ".toml": check_toml, ".yaml": check_yaml, ".yml": check_yaml,
    ".js": _external_syntax(["node", "--check"], "E-JS-SYNTAX"),
    ".mjs": _external_syntax(["node", "--check"], "E-JS-SYNTAX"),
    ".cjs": _external_syntax(["node", "--check"], "E-JS-SYNTAX"),
    ".sh": _external_syntax(["bash", "-n"], "E-SH-SYNTAX"),
}
FIXERS = {".py": fix_python, ".pyw": fix_python, ".json": fix_json}


# -------------------------------------------------------------------- driver

def iter_files(paths):
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for name in sorted(files):
                    if Path(name).suffix.lower() in CHECKERS:
                        yield Path(root) / name
        else:
            yield p  # a missing path is reported as unreadable by the caller


def check_text(path, text, lint=False):
    checker = CHECKERS.get(Path(path).suffix.lower())
    out = check_merge_markers(path, text)
    if checker and not out:
        out += checker(path, text)
    if lint and Path(path).suffix.lower() in (".py", ".pyw"):
        try:
            out += lint_python(path)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return sorted(out, key=lambda f: (f.line, f.col, f.code))


def run(args):
    results, changes, sources = [], [], {}
    for path in iter_files(args.paths):
        try:
            text, meta = read_source(path)
        except (OSError, ValueError) as exc:  # UnicodeDecodeError is a ValueError
            results.append(Finding(str(path), 1, 1, "E-UNREADABLE", "error",
                                   f"{type(exc).__name__}: {exc}"))
            continue
        if args.command == "fix":
            fixer = FIXERS.get(path.suffix.lower())
            new, applied = fixer(path, text, args.aggressive) if fixer else (text, [])
            if new != text:
                diff = "".join(difflib.unified_diff(
                    text.splitlines(True), new.splitlines(True),
                    f"a/{path}", f"b/{path}"))
                if args.write:
                    write_source(path, new, meta, backup=not args.no_backup)
                changes.append({"file": str(path), "applied": applied, "diff": diff,
                                "written": bool(args.write)})
                text = new
            found = check_text(path, text)
        else:
            found = check_text(path, text, lint=args.lint)
        sources[str(path)] = text.split("\n")
        results += found

    if getattr(args, "baseline", None):
        try:
            old = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
            known = {(f["file"], f["code"], f["message"]) for f in old.get("findings", [])}
            results = [f for f in results if (f.file, f.code, f.message) not in known]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"code_doctor: cannot read baseline: {exc}", file=sys.stderr)
            return 2

    counts = {s: sum(f.severity == s for f in results) for s in SEVERITIES}
    summary = {"files": len(sources), **counts,
               "fixable": sum(f.fixable for f in results)}
    if args.json:
        payload = {"tool": "code_doctor", "version": VERSION, "command": args.command,
                   "summary": summary,
                   "findings": [f.to_dict(sources.get(f.file), args.context)
                                for f in results]}
        if args.command == "fix":
            payload["changes"] = changes
        print(json.dumps(payload, indent=2))
    else:
        for change in changes:
            verb = "repaired" if change["written"] else "would repair"
            print(f"# {verb} {change['file']}: {', '.join(change['applied'])}")
            print(change["diff"], end="" if change["diff"].endswith("\n") else "\n")
        if changes and not args.write:
            print("# dry run - nothing written. Re-run with --write to apply.\n")
        for f in results:
            tag = {"safe": " [auto-fixable]",
                   "aggressive": " [fixable with --aggressive]"}.get(f.fix_level, "")
            print(f"{f.file}:{f.line}:{f.col}: {f.severity} {f.code} {f.message}{tag}")
            if f.hint:
                print(f"    hint: {f.hint}")
        totals = (f"\n{summary['files']} file(s): {counts['error']} error(s), "
                  f"{counts['warning']} warning(s), {counts['info']} note(s)")
        if args.command == "fix":
            print(f"{totals} remaining; {len(changes)} file(s) repaired.")
        else:
            print(f"{totals}; {summary['fixable']} auto-fixable.")
    return 1 if counts["error"] or counts["warning"] else 0


# ------------------------------------------------------------------- explain

FRAME = re.compile(r'^\s*File "(.+?)", line (\d+)(?:, in (.*?))?\s*$')
EXC_LINE = re.compile(r"^([A-Za-z_][\w.]*)(?::\s?(.*))?$")
LIBRARY = re.compile(r"site-packages|dist-packages|[\\/]lib[\\/]python\d|^<", re.I)
EXC_HINTS = {
    "NameError": "A name is used before it exists: typo, missing import, or a variable "
                 "only assigned inside a branch that did not run.",
    "AttributeError": "The object is not the type the code assumes. If it mentions "
                      "'NoneType', a call above returned None; check that call.",
    "TypeError": "A value has the wrong type or a call has the wrong arguments. Compare "
                 "the call on the failing line with the function's signature.",
    "KeyError": "The key is missing. Print the available keys, or use .get() with a default.",
    "IndexError": "The sequence is shorter than assumed; check for the empty case.",
    "ValueError": "Right type, unacceptable value. Validate the input before this line.",
    "ModuleNotFoundError": "The package is not installed in this interpreter, or the "
                           "module name is misspelled.",
    "ImportError": "The module exists but the name does not: wrong version or a "
                   "circular import.",
    "FileNotFoundError": "The path does not exist relative to the current working "
                         "directory. Build paths from Path(__file__).parent.",
    "UnicodeDecodeError": "The file is not in the assumed encoding. Pass encoding= explicitly.",
    "UnicodeEncodeError": "The output stream cannot represent a character. Set "
                          "encoding='utf-8' on the file or stream.",
    "RecursionError": "A recursive function has no reachable base case.",
    "ZeroDivisionError": "A divisor is zero; guard the empty or zero case.",
    "JSONDecodeError": "The text is not valid JSON; it is often empty or an error page.",
    "PermissionError": "The file is locked, read-only, or owned by another user.",
    "AssertionError": "An assumption stated by the code is false; inspect the values in "
                      "the assert.",
    "IndentationError": "Run `code_doctor.py check` on the file named in the last frame.",
    "SyntaxError": "Run `code_doctor.py check` on the file named in the last frame.",
    "UnboundLocalError": "The variable is assigned somewhere in this function, so it is "
                         "local, but this line runs before the assignment.",
    "StopIteration": "next() was called on an exhausted iterator; pass a default.",
    "TimeoutError": "The operation took too long; retry with backoff or raise the limit.",
}


def explain(text, context):
    lines = text.replace("\r\n", "\n").split("\n")
    frames = []
    for line in lines:
        m = FRAME.match(line)
        if m:
            frames.append({"file": m.group(1), "line": int(m.group(2)),
                           "function": m.group(3) or "",
                           "library": bool(LIBRARY.search(m.group(1)))})
    exc_type = exc_msg = ""
    for line in reversed(lines):
        m = EXC_LINE.match(line.strip()) if line and not line[0].isspace() else None
        if m and re.search(r"(Error|Exception|Warning|Exit|Interrupt|Iteration)$",
                           m.group(1)):
            exc_type, exc_msg = m.group(1), m.group(2) or ""
            break
    for frame in frames:
        p = Path(frame["file"])
        if context and p.is_file():
            try:
                src = read_source(p)[0].split("\n")
            except (OSError, ValueError):
                continue
            lo, hi = max(1, frame["line"] - context), min(len(src), frame["line"] + context)
            frame["source"] = [f"{'>' if n == frame['line'] else ' '} {n}: {src[n - 1]}"
                               for n in range(lo, hi + 1)]
    own = [f for f in frames if not f["library"]]
    culprit = (own or frames or [None])[-1]
    return {"tool": "code_doctor", "version": VERSION, "command": "explain",
            "exception": exc_type, "message": exc_msg,
            "hint": EXC_HINTS.get(exc_type.rsplit(".", 1)[-1], ""),
            "culprit": culprit, "frames": frames}


def run_explain(args):
    if args.traceback in (None, "-"):
        text = sys.stdin.read()
    else:
        text = Path(args.traceback).read_text(encoding="utf-8", errors="replace")
    report = explain(text, args.context)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        if not report["frames"] and not report["exception"]:
            print("No Python traceback found in the input.")
            return 2
        print(f"{report['exception'] or 'Unknown error'}: {report['message']}")
        if report["hint"]:
            print(f"hint: {report['hint']}")
        c = report["culprit"]
        if c:
            print(f"\nlook here first: {c['file']}:{c['line']} in {c['function'] or '?'}")
            print("\n".join(c.get("source", [])))
        print(f"\n{len(report['frames'])} frame(s), innermost last:")
        for f in report["frames"]:
            print(f"  {'lib ' if f['library'] else 'yours'} {f['file']}:{f['line']} "
                  f"{f['function']}")
    return 0 if report["frames"] or report["exception"] else 2


# ------------------------------------------------------------------------ cli

def main(argv=None):
    ap = argparse.ArgumentParser(prog="code_doctor.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p, context_default):
        p.add_argument("--json", action="store_true", help="machine-readable output")
        p.add_argument("--context", type=int, default=context_default, metavar="N",
                       help="source lines to include around each location")

    p = sub.add_parser("check", help="report problems without changing anything")
    p.add_argument("paths", nargs="+")
    p.add_argument("--lint", action="store_true",
                   help="also run ruff or pyflakes if installed")
    p.add_argument("--baseline", metavar="FILE",
                   help="earlier `check --json` output; report only new findings")
    common(p, 2)

    p = sub.add_parser("fix", help="repair what is safe to repair (dry run by default)")
    p.add_argument("paths", nargs="+")
    p.add_argument("--write", action="store_true", help="apply the repairs")
    p.add_argument("--no-backup", action="store_true", help="do not keep a .bak copy")
    p.add_argument("--aggressive", action="store_true",
                   help="also apply fixes that change behaviour "
                        "(== None -> is None, bare except -> except Exception)")
    common(p, 2)

    p = sub.add_parser("explain", help="structure a Python traceback")
    p.add_argument("traceback", nargs="?", help="file holding the traceback; default stdin")
    common(p, 3)

    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return run_explain(args) if args.command == "explain" else run(args)


if __name__ == "__main__":
    sys.exit(main())
