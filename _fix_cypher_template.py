# -*- coding: utf-8 -*-
"""Fix the cypher template to use real newlines."""
path = "__003__create_neo4j_database/cypher_generator.py"

with open(path, encoding="utf-8") as f:
    content = f.read()

# The template returns "\\n".join(statements) which produces literal backslash-n (2 chars)
# These get JSON-encoded and Neo4j interprets them as invalid escape sequences
# Fix: join without separator (each statement already ends with ";")
old = 'return "\\n".join(statements)'
new = 'return "".join(statements)'

if old in content:
    content = content.replace(old, new)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] Changed to \"\".join(statements) - no separator needed")
else:
    print(f"[WARN] Pattern not found - checking alternatives")
    for i, line in enumerate(open(path, encoding="utf-8").readlines()):
        if "join" in line and "statements" in line:
            print(f"  Line {i+1}: {repr(line)}")