"""Same as run_dev_sqlite.py, on port 8001. Kept for backwards compatibility —
prefer: python run_dev_sqlite.py --port 8001"""
from run_dev_sqlite import main

if __name__ == "__main__":
    main(default_port=8001)
