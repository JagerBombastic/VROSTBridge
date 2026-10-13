"""Generate the checked-in SteamVR action manifest and Index bindings.

This is a developer utility; running the packaged application needs no Python.
"""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "assets"
actions = []
bindings = {"poses": [], "haptics": [], "skeleton": [], "sources": []}
for side in ("left", "right"):
    def action(name, kind, direction="in"):
        path = f"/actions/test/{direction}/{side}_{name}"
        entry = {"name": path, "type": kind, "requirement": "optional"}
        if kind == "skeleton":
            entry["skeleton"] = f"/skeleton/hand/{side}"
        actions.append(entry)
        return path

    bindings["poses"].append({"path": f"/user/hand/{side}/pose/raw", "output": action("pose", "pose")})
    bindings["haptics"].append({"path": f"/user/hand/{side}/output/haptic", "output": action("haptic", "vibration", "out")})
    bindings["skeleton"].append({"path": f"/user/hand/{side}/input/skeleton/{side}", "output": action("skeleton", "skeleton")})

    def source(component, mode, inputs):
        bindings["sources"].append({"path": f"/user/hand/{side}/input/{component}", "mode": mode,
            "inputs": {port: {"output": action(name, kind)} for port, name, kind in inputs}})

    source("trigger", "trigger", [("pull", "trigger", "vector1"), ("touch", "trigger_touch", "boolean"), ("click", "trigger_click", "boolean")])
    source("grip", "force_sensor", [("force", "grip", "vector1"), ("value", "grip_value", "vector1")])
    # Grip touch remains optional/unbound: do not invent a force-sensor touch port.
    action("grip_touch", "boolean")
    source("thumbstick", "joystick", [("position", "stick", "vector2"), ("click", "stick_click", "boolean"), ("touch", "stick_touch", "boolean")])
    source("trackpad", "trackpad", [("position", "pad", "vector2"), ("touch", "pad_touch", "boolean"), ("force", "pad_force", "vector1")])
    # Intentionally unbound: users assign a chord for the remote dashboard.
    action("remote_menu", "boolean")
    for button in ("a", "b"):
        source(button, "button", [("click", button, "boolean"), ("touch", button + "_touch", "boolean")])

manifest = {"action_sets": [{"name": "/actions/test", "usage": "single"}], "actions": actions,
    "default_bindings": [{"controller_type": "knuckles", "binding_url": "bindings_index.json"}],
    "localization": [{"language_tag": "en_US", "/actions/test": "VROSTBridge qualification"}]}
binding = {"controller_type": "knuckles", "name": "VROSTBridge Index controls",
    "description": "Local qualification controls. The system button remains reserved for SteamVR.",
    "bindings": {"/actions/test": bindings}}
root.mkdir(exist_ok=True)
for name, data in [("actions.json", manifest), ("bindings_index.json", binding)]:
    (root / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
