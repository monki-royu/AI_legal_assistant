# -*- coding: utf-8 -*-
"""Fix the cypher template separator - join with empty string instead of \\n."""
path = "__003__create_neo4j_database/cypher_generator.py"
with open(path, encoding="utf-8") as f:
    content = f.read()

# Current line: return "\\n".join(statements)
# The \\n in the source file produces \ + \ + n (3 chars) at runtime
# This causes Neo4j syntax errors when sent via JSON transport
# Fix: join with empty string (each statement already ends with ";")
content = content.replace(
    'return "\\\\n".join(statements)',
    'return "".join(statements)'
)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Done")