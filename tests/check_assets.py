"""Check binding references, directions and expected controller sides."""
import json
from pathlib import Path
root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "client/assets/actions.json").read_text())
binding = json.loads((root / "client/assets/bindings_index.json").read_text())
actions = {a["name"]: a for a in manifest["actions"]}
assert len(actions) == len(manifest["actions"])
assert binding["controller_type"] == "knuckles"
section = binding["bindings"]["/actions/test"]
outputs = []
for group in ("poses", "haptics", "skeleton"):
    for entry in section[group]:
        assert entry["output"] in actions
        outputs.append(entry["output"])
for entry in section["sources"]:
    side = entry["path"].split("/")[3]
    assert side in ("left", "right")
    channels = {
        "trigger": {"pull", "click", "touch"},
        "force_sensor": {"force"},
        "joystick": {"position", "click", "touch"},
        "trackpad": {"position", "click", "touch"},
        "button": {"click", "touch"},
    }
    assert set(entry["inputs"]) <= channels[entry["mode"]], "Invalid SteamVR binding mode/channel"
    for port in entry["inputs"].values():
        assert port["output"] in actions
        assert f"/in/{side}_" in port["output"], "Controller bound to the other hand"
        outputs.append(port["output"])
assert len(outputs) == len(set(outputs))
for side in ("left", "right"):
    for name in ("pose", "trigger", "trigger_click", "grip", "a", "b", "stick", "pad", "skeleton", "remote_menu"):
        assert f"/actions/test/in/{side}_{name}" in outputs
    assert f"/actions/test/out/{side}_haptic" in outputs
    system = [s for s in section["sources"] if s["path"] == f"/user/hand/{side}/input/system"]
    assert len(system) == 1 and system[0]["mode"] == "button"
    assert system[0]["inputs"]["click"]["output"] == f"/actions/test/in/{side}_remote_menu"
    for name, component, mode, channel in (
        ("grip_value", "grip", "trigger", "pull"),
        ("grip", "grip", "force_sensor", "force"),
        ("pad_force", "trackpad", "force_sensor", "force"),
    ):
        assert any(s["path"] == f"/user/hand/{side}/input/{component}" and s["mode"] == mode
                   and s["inputs"].get(channel, {}).get("output") == f"/actions/test/in/{side}_{name}"
                   for s in section["sources"])
print(f"Validated {len(actions)} actions and {len(outputs)} binding outputs; both system buttons mapped")
