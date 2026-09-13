import unittest
import sys
import os

# Isolate unit tests into dedicated test database to avoid lock contention
TEST_DB = "test_medrx_run.db"
os.environ["MEDRX_DB_FILE"] = TEST_DB

if __name__ == '__main__':
    loader = unittest.TestLoader()
    start_dir = os.path.dirname(os.path.abspath(__file__))
    suite = loader.discover(start_dir, pattern='test_*.py')
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    print("\n==========================================")
    print(f"TOTAL TESTS RUN   : {result.testsRun}")
    print(f"TOTAL PASSED      : {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"TOTAL FAILURES    : {len(result.failures)}")
    print(f"TOTAL ERRORS      : {len(result.errors)}")
    print("==========================================")

    # Clean up test database
    try:
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
    except Exception:
        pass
    
    if not result.wasSuccessful():
        sys.exit(1)
