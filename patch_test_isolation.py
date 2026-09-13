import glob
import os
import re

setup_code = """    def setUp(self):
        db_file = f"test_db_{self._testMethodName}_{secrets.token_hex(4)}.db"
        os.environ["MEDRX_DB_FILE"] = db_file
        db_init()
        seed_demo_data(force_reset=True)
        self.conn = get_db_connection()
        self.cursor = self.conn.cursor()

    def tearDown(self):
        if hasattr(self, 'conn') and self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
"""

for f in glob.glob('test_*.py'):
    with open(f, 'r', encoding='utf-8') as fp:
        content = fp.read()
    
    # Strip any top-level MEDRX_DB_FILE header
    clean_lines = [l for l in content.splitlines(keepends=True) if 'MEDRX_DB_FILE' not in l]
    content = "".join(clean_lines)

    # Ensure secrets is imported
    if 'import secrets' not in content:
        content = "import secrets\n" + content

    # Replace setUp and tearDown
    class_match = re.search(r'class \w+\(unittest\.TestCase\):', content)
    if class_match:
        content = re.sub(r'    def setUp\b.*?(?=    def test_|\Z)', setup_code + '\n', content, flags=re.DOTALL)
        content = re.sub(r'    @classmethod\s+def setUpClass\b.*?(?=    def test_|\Z)', setup_code + '\n', content, flags=re.DOTALL)

    with open(f, 'w', encoding='utf-8') as fp:
        fp.write(content)
    print(f"Patched {f} with fast unique DB isolation.")
