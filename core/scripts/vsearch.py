r"""One-line-per-hit vault search for the assistant: python vsearch.py "words" [-n 5]
Refreshes the incremental SQLite FTS5 index first (about half a second), then prints at most n hits as: path | title | short snippet.
Index file: J:\Jarvis2\logs\vault_fts.db (rebuildable from the vault at any time). Built on vault_fts.py (written by a free worker, tests in test_vault_fts.py)."""
import re, subprocess, sys, pathlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = pathlib.Path(__file__).parent
VAULT, DB = r"J:\Jarvis-Brain", r"J:\Jarvis2\logs\vault_fts.db"
n = "5"
args = sys.argv[1:]
if "-n" in args:
    i = args.index("-n"); n = args[i + 1]; del args[i:i + 2]
q = " ".join(args)
if not q:
    sys.exit('usage: vsearch.py "words" [-n 5]')
subprocess.run([sys.executable, str(HERE / "vault_fts.py"), "index", VAULT, DB], capture_output=True)
out = subprocess.run([sys.executable, str(HERE / "vault_fts.py"), "search", DB, q, "-n", n], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
for block in re.split(r"\n(?=[^\t\n]+\.md\t)", out.strip()):
    parts = block.split("\t", 2)
    if len(parts) < 3: 
        continue
    path, title, snip = parts
    snip = re.sub(r"</?b>|\s+", lambda m: "" if m.group(0).startswith("<") else " ", snip).strip()
    print(f"{path} | {title.strip()[:70]} | {snip[:140]}")
