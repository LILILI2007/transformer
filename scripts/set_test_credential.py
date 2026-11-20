"""Interactive, non-echoed setup; never pass a secret as an argument."""
import getpass
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from modlingo.test_credentials import store

if __name__=='__main__':
    secret=getpass.getpass('Test API key (hidden): ')
    store(secret)
    del secret
    print('Dedicated Windows test credential saved. No plaintext file created.')
