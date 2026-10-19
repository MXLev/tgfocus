"""Exercise the actual packaged worker over its public IPC protocol."""
import json
import subprocess
import sys
import threading
from queue import Queue

process = subprocess.Popen([sys.argv[1]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True)
responses = Queue()
def read():
    for line in process.stdout:
        responses.put(json.loads(line))
threading.Thread(target=read, daemon=True).start()
def receive(): return responses.get(timeout=15)
def send(value):
    process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
    process.stdin.flush()
try:
    assert receive()["type"] == "ready"
    config = dict(enabled=True, allPrivate=False, keywords=["срочно"], people=[], chats=[], allowMentions=True, allowReplies=True)
    send(dict(command="config", config=config))
    assert receive()["type"] == "configSaved"
    send(dict(command="simulate", message=dict(kind="private", text="СРОЧНО!")))
    assert receive()["allowed"] is True
    send(dict(command="simulate", message=dict(kind="group", text="СРОЧНО!")))
    assert receive()["allowed"] is False
    send(dict(command="config", config=dict(config, allowMentions=False, allowReplies=True)))
    assert receive()["type"] == "configSaved"
    send(dict(command="simulate", message=dict(kind="group", text="Привет", replyToMe=True)))
    assert receive()["allowed"] is True
    send(dict(command="simulate", message=dict(kind="group", text="Привет", mentioned=True)))
    assert receive()["allowed"] is False
    send(dict(command="config", config=dict(config, allowMentions=True, allowReplies=False)))
    assert receive()["type"] == "configSaved"
    send(dict(command="simulate", message=dict(kind="group", text="Привет", replyToMe=True)))
    assert receive()["allowed"] is False
    send(dict(command="simulate", message=dict(kind="group", text="Привет", mentioned=True)))
    assert receive()["allowed"] is True
    send(dict(command="config", config=dict(config, allowMentions=False)))
    assert receive()["type"] == "configSaved"
    send(dict(command="simulate", message=dict(kind="group", text="Привет", mentioned=True)))
    assert receive()["allowed"] is False
    send(dict(command="connect", apiId="0", apiHash=""))
    assert receive()["type"] == "error"
    send(dict(command="quit"))
    process.wait(timeout=10)
    assert process.returncode == 0, process.stderr.read()
    print("Packaged worker: startup, configuration, private/group filtering, independent mention/reply switches, invalid credentials, clean shutdown OK")
finally:
    if process.poll() is None:
        process.kill()
        process.wait(timeout=10)
