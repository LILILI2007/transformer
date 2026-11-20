"""Run the suite from a real, spawn-safe entry point and save local evidence."""
import json
import gc
from pathlib import Path
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from modlingo import __version__


def main():
    started=time.monotonic()
    stem=ROOT/'artifacts'/('regression-'+__version__)
    with Path(str(stem)+'.log').open('w',encoding='utf-8') as output:
        class Result(unittest.TextTestResult):
            def stopTest(self,test):
                super().stopTest(test)
                # GUI fixtures own Tcl objects on this thread. Collect their
                # cycles here, before a later worker triggers garbage collection.
                gc.collect()
        result=unittest.TextTestRunner(stream=output,verbosity=2,resultclass=Result).run(unittest.defaultTestLoader.discover(str(ROOT/'tests')))
    evidence=dict(version=__version__,tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),seconds=round(time.monotonic()-started,3),passed=result.wasSuccessful())
    Path(str(stem)+'.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps(evidence))
    return not result.wasSuccessful()


if __name__=='__main__':raise SystemExit(main())
