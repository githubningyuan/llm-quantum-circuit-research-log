#!/usr/bin/env python3
"""Fixed-path hhl-agent socket relay used only by the isolated P1 acceptance.
The service itself checks SO_PEERCRED. This helper has no host-execution API.
"""
import json, os, socket, stat, sys, time
SOCKETS={
 'broker': ('/run/hhl-task2/p1-review-research.sock',12*1024*1024,180),
 'evaluator': ('/run/hhl-task2/p1-review-evaluator.sock',16*1024,115),
}
def main():
    if len(sys.argv)!=2 or sys.argv[1] not in SOCKETS: raise SystemExit('MODE_REFUSED')
    if (os.geteuid(),os.getegid())!=(997,988): raise SystemExit('WRONG_PEER_IDENTITY')
    path,max_reply,timeout=SOCKETS[sys.argv[1]]
    parent=os.lstat('/run/hhl-task2'); st=os.lstat(path)
    if not stat.S_ISSOCK(st.st_mode) or (st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode))!=(0,988,0o660):
        raise SystemExit('SOCKET_METADATA_REFUSED')
    if (parent.st_uid,stat.S_IMODE(parent.st_mode))!=(0,0o755): raise SystemExit('SOCKET_PARENT_REFUSED')
    wire=sys.stdin.buffer.read(4*1024*1024+4096)
    if not wire.endswith(b'\n') or wire.count(b'\n')!=1 or len(wire)>4*1024*1024+2048:
        raise SystemExit('WIRE_REFUSED')
    end=time.monotonic()+timeout
    data=bytearray()
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout);sock.connect(path);sock.sendall(wire)
        while len(data)<=max_reply:
            sock.settimeout(max(0.1,end-time.monotonic()))
            part=sock.recv(min(65536,max_reply+1-len(data)))
            if not part:break
            data.extend(part)
            if b'\n' in part:break
    if not data.endswith(b'\n') or data.count(b'\n')!=1 or len(data)>max_reply:
        raise SystemExit('REPLY_REFUSED')
    obj=json.loads(data)
    if type(obj) is not dict:raise SystemExit('REPLY_SHAPE_REFUSED')
    sys.stdout.buffer.write(data)
if __name__=='__main__':main()
