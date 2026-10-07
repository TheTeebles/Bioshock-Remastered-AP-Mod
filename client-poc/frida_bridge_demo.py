"""
Send one console command to BioShock through the agent, the way the Archipelago client does. UNTESTED against the game.

    pip install frida
    python frida_bridge_demo.py "GiveItem 10 ShockGame.ADAM"

Needs bioshock_ap_agent.js next to this file. That is the same script the client ships as client/agent.js.
The BioShock Client in the Archipelago Launcher does all of this by itself; this is for trying one command by hand.
"""
import sys
from pathlib import Path

import frida


def on_message(message: dict, data: bytes | None) -> None:
    if message["type"] == "send":
        print("agent:", message["payload"])
    else:
        print("agent error:", message)


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "GiveItem 10 ShockGame.ADAM"
    agent_source = (Path(__file__).parent / "bioshock_ap_agent.js").read_text(encoding="utf-8")

    session = frida.attach("BioshockHD.exe")
    script = session.create_script(agent_source)
    script.on("message", on_message)
    script.load()

    print("state:", script.exports_sync.state())
    print("queued, commands waiting:", script.exports_sync.exec(1, command))
    input("Watch for the agent's exec_result, then press Enter to detach.\n")
    print("state:", script.exports_sync.state())
    session.detach()


if __name__ == "__main__":
    main()
