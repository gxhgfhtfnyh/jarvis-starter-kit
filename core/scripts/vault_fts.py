#!/usr/bin/env python3
"""
Fast local search index for a folder of markdown notes using SQLite FTS5.
"""

import argparse
import os
import re
import sqlite3
from pathlib import Path
from typing import List, Tuple, Optional


def extract_frontmatter_and_body(content: str) -> Tuple[str, str]:
    """Extract YAML frontmatter and body from markdown content.
    Returns (frontmatter, body_without_frontmatter)."""
    if content.startswith('---'):
        # Find the closing ---
        end_match = re.search(r'\n---\n', content[3:])
        if end_match:
            frontmatter_end = 3 + end_match.end()  # position after \n---\n
            frontmatter = content[3:frontmatter_end - 4]  # exclude the closing \n---
            # Strip leading/trailing newlines
            frontmatter = frontmatter.strip('\n')
            body = content[frontmatter_end:]
            return frontmatter, body
    return '', content


def extract_title_and_headings(content: str) -> Tuple[str, List[str]]:
    """Extract title (first # heading) and all headings from markdown content."""
    headings = []
    title = ''

    for line in content.split('\n'):
        match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if match:
            heading_text = match.group(2).strip()
            headings.append(heading_text)
            if not title and match.group(1) == '#':
                title = heading_text

    return title, headings


def get_mtime(path: Path) -> float:
    """Get file modification time."""
    return path.stat().st_mtime


def walk_md_files(folder: Path) -> List[Path]:
    """Walk folder for .md files, skipping directories starting with dot."""
    md_files = []
    for root, dirs, files in os.walk(folder):
        # Skip directories starting with dot
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for file in files:
            if file.endswith('.md'):
                md_files.append(Path(root) / file)
    return md_files


def build_fts_query(query: str) -> str:
    """Build a safe FTS5 query from user input.
    Quote each word and join with AND, fallback to OR."""
    # Split on whitespace, keep words with punctuation
    words = query.strip().split()
    if not words:
        return '""'

    # Quote each word to escape special characters
    quoted_words = [f'"{word}"' for word in words]

    # Try AND first
    return ' AND '.join(quoted_words)


def build_fts_query_or(query: str) -> str:
    """Build a safe FTS5 query using OR."""
    words = query.strip().split()
    if not words:
        return '""'
    quoted_words = [f'"{word}"' for word in words]
    return ' OR '.join(quoted_words)



class VaultFTS:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self._init_db()

    def _init_db(self):
        """Initialize the database schema."""
        cursor = self.conn.cursor()

        # Regular table for tracking file mtimes
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS file_mtimes (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL
            )
        ''')

        # FTS5 virtual table for full-text search
        cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                title,
                headings,
                body,
                path UNINDEXED
            )
        ''')

        self.conn.commit()

    def _get_existing_mtime(self, path: str) -> Optional[float]:
        """Get stored mtime for a path."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT mtime FROM file_mtimes WHERE path = ?', (path,))
        row = cursor.fetchone()
        return row[0] if row else None

    def _update_mtime(self, path: str, mtime: float):
        """Update or insert mtime for a path."""
        cursor = self.conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO file_mtimes (path, mtime) VALUES (?, ?)',
            (path, mtime)
        )

    def _delete_mtime(self, path: str):
        """Delete mtime entry for a path."""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM file_mtimes WHERE path = ?', (path,))

    def _delete_from_fts(self, path: str):
        """Delete note from FTS table."""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM notes_fts WHERE path = ?', (path,))

    def _insert_into_fts(self, path: str, title: str, headings: str, body: str):
        """Insert note into FTS table."""
        cursor = self.conn.cursor()
        cursor.execute(
            'INSERT INTO notes_fts (title, headings, body, path) VALUES (?, ?, ?, ?)',
            (title, headings, body, path)
        )

    def _update_fts(self, path: str, title: str, headings: str, body: str):
        """Update note in FTS table."""
        cursor = self.conn.cursor()
        cursor.execute(
            'UPDATE notes_fts SET title = ?, headings = ?, body = ? WHERE path = ?',
            (title, headings, body, path)
        )

    def index_folder(self, folder: str) -> Tuple[int, int, int, int]:
        """Index all .md files in folder.
        Returns (new_count, updated_count, removed_count, total_count)."""
        folder_path = Path(folder).resolve()
        md_files = walk_md_files(folder_path)

        # Get relative paths
        rel_paths = set()
        for md_file in md_files:
            rel_path = str(md_file.relative_to(folder_path))
            rel_paths.add(rel_path)

        # Get existing paths from database
        cursor = self.conn.cursor()
        cursor.execute('SELECT path FROM file_mtimes')
        existing_paths = {row[0] for row in cursor.fetchall()}

        new_count = 0
        updated_count = 0
        removed_count = 0

        # Remove files that no longer exist
        for path in existing_paths - rel_paths:
            self._delete_from_fts(path)
            self._delete_mtime(path)
            removed_count += 1

        # Process each file
        for rel_path in sorted(rel_paths):
            full_path = folder_path / rel_path
            current_mtime = get_mtime(full_path)
            stored_mtime = self._get_existing_mtime(rel_path)

            if stored_mtime is None:
                # New file
                new_count += 1
            elif abs(current_mtime - stored_mtime) > 1e-9:
                # Updated file
                updated_count += 1
            else:
                # Unchanged
                continue

            # Read and process file
            content = full_path.read_text(encoding='utf-8')

            # Remove frontmatter
            _, body = extract_frontmatter_and_body(content)

            # Extract title and headings
            title, headings_list = extract_title_and_headings(body)
            if not title:
                title = full_path.stem

            headings_str = ' '.join(headings_list)

            # Insert or update in FTS
            if stored_mtime is None:
                self._insert_into_fts(rel_path, title, headings_str, body)
            else:
                self._update_fts(rel_path, title, headings_str, body)

            # Update mtime
            self._update_mtime(rel_path, current_mtime)

        self.conn.commit()

        # Get total count
        cursor.execute('SELECT COUNT(*) FROM file_mtimes')
        total_count = cursor.fetchone()[0]

        return new_count, updated_count, removed_count, total_count

    def search(self, query: str, limit: int = 5) -> List[Tuple[str, str, str]]:
        """Search the index.
        Returns list of (path, title, snippet)."""
        cursor = self.conn.cursor()

        # Build AND query first
        fts_query_and = build_fts_query(query)

        # Try AND query
        sql = '''
            SELECT path, title,
                   snippet(notes_fts, 2, '<b>', '</b>', '...', 32) as snippet
            FROM notes_fts
            WHERE notes_fts MATCH ?
            ORDER BY bm25(notes_fts, 10.0, 4.0, 1.0)
            LIMIT ?
        '''

        cursor.execute(sql, (fts_query_and, limit))
        results = cursor.fetchall()

        # If no results, try OR query
        if not results:
            fts_query_or = build_fts_query_or(query)
            cursor.execute(sql, (fts_query_or, limit))
            results = cursor.fetchall()

        return results

    def close(self):
        self.conn.close()


def main():
    parser = argparse.ArgumentParser(description='Fast local search for markdown notes')
    subparsers = parser.add_subparsers(dest='command', required=True)

    # Index command
    index_parser = subparsers.add_parser('index', help='Index a folder of markdown files')
    index_parser.add_argument('folder', help='Folder containing markdown files')
    index_parser.add_argument('db_path', help='Path to SQLite database')

    # Search command
    search_parser = subparsers.add_parser('search', help='Search the index')
    search_parser.add_argument('db_path', help='Path to SQLite database')
    search_parser.add_argument('query', help='Search query')
    search_parser.add_argument('-n', '--limit', type=int, default=5, help='Max results (default: 5)')

    args = parser.parse_args()

    if args.command == 'index':
        vfts = VaultFTS(args.db_path)
        try:
            new_count, updated_count, removed_count, total_count = vfts.index_folder(args.folder)
            print(f'indexed {new_count} new, {updated_count} updated, {removed_count} removed, {total_count} total')
        finally:
            vfts.close()

    elif args.command == 'search':
        vfts = VaultFTS(args.db_path)
        try:
            results = vfts.search(args.query, args.limit)
            for path, title, snippet in results:
                print(f'{path}\t{title}\t{snippet}')
        finally:
            vfts.close()


if __name__ == '__main__':
    main()