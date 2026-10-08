#!/usr/bin/env python3
"""Metadata-only CLI. Acquisition permanently refuses in this offline increment."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from procure.evaluation.source_transport import acquire_sources, validate_proposal


class MetadataParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default would echo unapproved inputs; expose a fixed code only.
        print(json.dumps({'status':'HOLD','error':'INVALID_ARGUMENTS',
                          'new_requests_authorized':0,'execution_authorized':False}))
        raise SystemExit(2)


def main():
    parser=MetadataParser(description=__doc__)
    parser.add_argument('--validate-proposal',type=Path,help='Inspect local proposal metadata; never authorize or acquire')
    args=parser.parse_args()
    if args.validate_proposal is not None:
        try:
            with args.validate_proposal.open('rb') as f: raw=f.read(4097)
            if len(raw)>4096: raise ValueError()
            def pairs(rows):
                out={}
                for key,value in rows:
                    if key in out: raise ValueError()
                    out[key]=value
                return out
            proposal=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            result=validate_proposal(proposal)
        except (OSError,ValueError,TypeError,UnicodeError):
            result={'status':'HOLD','error':'INVALID_PROPOSAL','new_requests_authorized':0,'execution_authorized':False}
    else:
        result=acquire_sources(None,None)
    print(json.dumps(result,sort_keys=True))
    return 2

if __name__=='__main__': raise SystemExit(main())
