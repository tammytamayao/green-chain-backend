import os
from config import DB_PATH
from db import init_db

# Remove the db file if it exists
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
    print(f"Removed existing DB file: {DB_PATH}")
else:
    print(f"No existing DB file found at: {DB_PATH}")

# Initialize new schema
init_db()
print("Initialized new database schema.")
