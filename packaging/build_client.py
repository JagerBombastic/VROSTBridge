"""Assemble a client-only update (the VROSTBridge Bazzite installer ZIP) without modifying or requiring host binaries."""
import argparse
from pathlib import Path
from build_distributions import CLIENT_FOLDER, build_client

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--frozen',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    build_client(args.work.resolve()/CLIENT_FOLDER,args.frozen.resolve(),args.output.resolve())
