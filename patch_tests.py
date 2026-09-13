import glob
import os

for f in glob.glob('test_*.py'):
    with open(f, 'r', encoding='utf-8') as fp:
        content = fp.read()
    if 'MEDRX_DB_FILE' not in content:
        db_name = f"test_env_{f}.db"
        content = f"import os\nos.environ['MEDRX_DB_FILE'] = '{db_name}'\n" + content
        with open(f, 'w', encoding='utf-8') as fp:
            fp.write(content)
        print(f"Isolated {f} -> {db_name}")
