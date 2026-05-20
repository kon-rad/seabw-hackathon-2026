# =========== Copyright 2023 @ CAMEL-AI.org. All Rights Reserved. ===========
# Licensed under the Apache License, Version 2.0 (the “License”);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =========== Copyright 2023 @ CAMEL-AI.org. All Rights Reserved. ===========
from __future__ import annotations

import os
import os.path as osp
import sqlite3
from typing import Any, Dict, List

SCHEMA_DIR = "social_platform/schema"
DB_DIR = "data"
DB_NAME = "social_media.db"

USER_SCHEMA_SQL = "user.sql"
POST_SCHEMA_SQL = "post.sql"
FOLLOW_SCHEMA_SQL = "follow.sql"
MUTE_SCHEMA_SQL = "mute.sql"
LIKE_SCHEMA_SQL = "like.sql"
DISLIKE_SCHEMA_SQL = "dislike.sql"
REPORT_SCHEAM_SQL = "report.sql"
TRACE_SCHEMA_SQL = "trace.sql"
REC_SCHEMA_SQL = "rec.sql"
COMMENT_SCHEMA_SQL = "comment.sql"
COMMENT_LIKE_SCHEMA_SQL = "comment_like.sql"
COMMENT_DISLIKE_SCHEMA_SQL = "comment_dislike.sql"
PRODUCT_SCHEMA_SQL = "product.sql"
GROUP_SCHEMA_SQL = "chat_group.sql"
GROUP_MEMBER_SCHEMA_SQL = "group_member.sql"
GROUP_MESSAGE_SCHEMA_SQL = "group_message.sql"

TABLE_NAMES = {
    "user",
    "post",
    "follow",
    "mute",
    "like",
    "dislike",
    "report",
    "trace",
    "rec",
    "comment.sql",
    "comment_like.sql",
    "comment_dislike.sql",
    "product.sql",
    "group",
    "group_member",
    "group_message",
}


def get_db_path() -> str:
    # First check if the database path is set in environment variables
    env_db_path = os.environ.get("WONDERWALL_DB_PATH")
    if env_db_path:
        return env_db_path

    # If no environment variable is set, use the original default path
    curr_file_path = osp.abspath(__file__)
    parent_dir = osp.dirname(osp.dirname(curr_file_path))
    db_dir = osp.join(parent_dir, DB_DIR)
    os.makedirs(db_dir, exist_ok=True)
    db_path = osp.join(db_dir, DB_NAME)
    return db_path


def get_schema_dir_path() -> str:
    curr_file_path = osp.abspath(__file__)
    parent_dir = osp.dirname(osp.dirname(curr_file_path))
    schema_dir = osp.join(parent_dir, SCHEMA_DIR)
    return schema_dir


ALL_SOCIAL_MEDIA_SCHEMAS = [
    USER_SCHEMA_SQL, POST_SCHEMA_SQL, FOLLOW_SCHEMA_SQL, MUTE_SCHEMA_SQL,
    LIKE_SCHEMA_SQL, DISLIKE_SCHEMA_SQL, REPORT_SCHEAM_SQL, TRACE_SCHEMA_SQL,
    REC_SCHEMA_SQL, COMMENT_SCHEMA_SQL, COMMENT_LIKE_SCHEMA_SQL,
    COMMENT_DISLIKE_SCHEMA_SQL, PRODUCT_SCHEMA_SQL, GROUP_SCHEMA_SQL,
    GROUP_MEMBER_SCHEMA_SQL, GROUP_MESSAGE_SCHEMA_SQL,
]


def create_db(
    db_path: str | None = None,
    schemas: list[str] | None = None,
    schema_dir_override: str | None = None,
):
    r"""Create the database and execute schema SQL files.

    Args:
        db_path: Path to the SQLite database file. If ``None``, uses the
            default path from ``get_db_path()``.
        schemas: List of SQL filenames to execute.  If ``None``, loads all
            social-media schemas (backwards-compatible default).
        schema_dir_override: Directory containing the SQL files.  If ``None``,
            uses ``get_schema_dir_path()`` (the original social_platform/schema
            directory).
    """
    schema_dir = schema_dir_override or get_schema_dir_path()
    if db_path is None:
        db_path = get_db_path()
    if schemas is None:
        schemas = ALL_SOCIAL_MEDIA_SCHEMAS

    # Connect to the database:
    print("db_path", db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        for schema_file in schemas:
            sql_path = osp.join(schema_dir, schema_file)
            with open(sql_path, "r") as sql_file:
                sql_script = sql_file.read()
            cursor.executescript(sql_script)

        # Commit the changes:
        conn.commit()

    except sqlite3.Error as e:
        print(f"An error occurred while creating tables: {e}")

    return conn, cursor


def print_db_tables_summary():
    # Connect to the SQLite database
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Retrieve a list of all tables in the database
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()

    # Print a summary of each table
    for table in tables:
        table_name = table[0]
        if table_name not in TABLE_NAMES:
            continue
        print(f"Table: {table_name}")

        # Retrieve the table schema
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        column_names = [column[1] for column in columns]
        print("- Columns:", column_names)

        # Retrieve and print foreign key information
        cursor.execute(f"PRAGMA foreign_key_list({table_name})")
        foreign_keys = cursor.fetchall()
        if foreign_keys:
            print("- Foreign Keys:")
            for fk in foreign_keys:
                print(f"    {fk[2]} references {fk[3]}({fk[4]}) on update "
                      f"{fk[5]} on delete {fk[6]}")
        else:
            print("  No foreign keys.")

        # Print the first few rows of the table
        cursor.execute(f"SELECT * FROM {table_name} LIMIT 5;")
        rows = cursor.fetchall()
        for row in rows:
            print(row)
        print()  # Adds a newline for better readability between tables

    # Close the database connection
    conn.close()


def fetch_table_from_db(cursor: sqlite3.Cursor,
                        table_name: str) -> List[Dict[str, Any]]:
    cursor.execute(f"SELECT * FROM {table_name}")
    columns = [description[0] for description in cursor.description]
    data_dicts = [dict(zip(columns, row)) for row in cursor.fetchall()]
    return data_dicts


def fetch_rec_table_as_matrix(cursor: sqlite3.Cursor) -> List[List[int]]:
    # First, query all user_ids from the user table, assuming they start from
    # 1 and are consecutive
    cursor.execute("SELECT user_id FROM user ORDER BY user_id")
    user_ids = [row[0] for row in cursor.fetchall()]

    # Then, query all records from the rec table
    cursor.execute(
        "SELECT user_id, post_id FROM rec ORDER BY user_id, post_id")
    rec_rows = cursor.fetchall()
    # Initialize a dictionary, assigning an empty list to each user_id
    user_posts = {user_id: [] for user_id in user_ids}
    # Fill the dictionary with the records queried from the rec table
    for user_id, post_id in rec_rows:
        if user_id in user_posts:
            user_posts[user_id].append(post_id)
    # Convert the dictionary into matrix form
    matrix = [user_posts[user_id] for user_id in user_ids]
    return matrix


def insert_matrix_into_rec_table(cursor: sqlite3.Cursor,
                                 matrix: List[List[int]]) -> None:
    # Iterate through the matrix, skipping the placeholder at index 0
    for user_id, post_ids in enumerate(matrix, start=1):
        # Adjusted to start counting from 1
        for post_id in post_ids:
            # Insert each combination of user_id and post_id into the rec table
            cursor.execute("INSERT INTO rec (user_id, post_id) VALUES (?, ?)",
                           (user_id, post_id))


if __name__ == "__main__":
    create_db()
    print_db_tables_summary()
