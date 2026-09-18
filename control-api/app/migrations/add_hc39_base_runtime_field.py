"""
HC3.9 Control-plane persistence migration.

Adds base_runtime_json TEXT column to proxmox_provisioning_jobs table.
- Additive only (no drops, no resets)
- Preserves all existing data
- Idempotent
"""

import sqlite3


def migrate_sqlite_add_base_runtime_json(db_path: str) -> dict:
    """
    Add base_runtime_json column to proxmox_provisioning_jobs table.
    
    Args:
        db_path: Path to SQLite database file
        
    Returns:
        dict: Migration result with keys:
            - success: bool
            - rows_affected: int
            - message: str
            - error: str (if any)
    """
    result = {
        "success": False,
        "rows_affected": 0,
        "message": "",
        "error": None,
    }
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if column already exists
        cursor.execute(
            "PRAGMA table_info(proxmox_provisioning_jobs)"
        )
        columns = {row[1]: row for row in cursor.fetchall()}
        
        if "base_runtime_json" in columns:
            result["success"] = True
            result["message"] = "Column base_runtime_json already exists"
            conn.close()
            return result
        
        # Add the column
        cursor.execute(
            "ALTER TABLE proxmox_provisioning_jobs "
            "ADD COLUMN base_runtime_json TEXT"
        )
        
        conn.commit()
        
        # Verify
        cursor.execute(
            "PRAGMA table_info(proxmox_provisioning_jobs)"
        )
        columns = {row[1]: row for row in cursor.fetchall()}
        
        if "base_runtime_json" not in columns:
            raise RuntimeError("Column not created despite successful ALTER")
        
        # Count total rows (unchanged)
        cursor.execute("SELECT COUNT(*) FROM proxmox_provisioning_jobs")
        total = cursor.fetchone()[0]
        
        result["success"] = True
        result["rows_affected"] = total
        result["message"] = (
            f"Successfully added base_runtime_json column. "
            f"Total rows preserved: {total}"
        )
        
        conn.close()
        
    except Exception as e:
        result["error"] = str(e)
        result["message"] = f"Migration failed: {e}"
    
    return result


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python add_hc39_base_runtime_field.py <db_path>")
        sys.exit(1)
    
    db_path = sys.argv[1]
    result = migrate_sqlite_add_base_runtime_json(db_path)
    
    print(f"Migration Result:")
    print(f"  Success: {result['success']}")
    print(f"  Message: {result['message']}")
    if result['error']:
        print(f"  Error: {result['error']}")
        sys.exit(1)
    
    sys.exit(0)
