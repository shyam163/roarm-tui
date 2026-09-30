"""Control tab: joint sliders, jog settings, arm view, load bars, action buttons."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Label, Select

from roarm import protocol as P
from roarm.ui.widgets import ArmView, JointSlider, LoadBars

STEPS = [(f"{s}°", float(s)) for s in (1, 5, 15)]
SPEEDS = [("slow", 300), ("medium", 1000), ("max", 0)]

BUTTON_ACTIONS = {
    "btn-home": "home",
    "btn-torque": "toggle_torque",
    "btn-open": "grip_open",
    "btn-close": "grip_close",
    "btn-led": "toggle_led",
    "btn-estop": "estop",
}


class ControlTab(Container):
    def compose(self) -> ComposeResult:
        with Horizontal(id="control-main"):
            with Vertical(id="joints-panel", classes="panel"):
                for joint in P.JOINTS:
                    yield JointSlider(joint, P.JOINT_LABELS[joint], id=f"slider-{joint}")
                with Horizontal(classes="row"):
                    yield Label("step", classes="field-label")
                    yield Select(STEPS, value=5.0, allow_blank=False, id="step")
                    yield Label("speed", classes="field-label")
                    yield Select(SPEEDS, value=1000, allow_blank=False, id="speed")
                yield LoadBars(id="loads")
            yield ArmView(id="arm-view", classes="panel")
        with Horizontal(id="control-buttons"):
            yield Button("⌂ Home", id="btn-home")
            yield Button("⚡ Torque", id="btn-torque")
            yield Button("◁▷ Open", id="btn-open")
            yield Button("▷◁ Close", id="btn-close")
            yield Button("💡 LED", id="btn-led")
            yield Button("■ E-STOP", id="btn-estop", variant="error")

    def on_mount(self) -> None:
        self.query_one("#joints-panel").border_title = "JOINTS"
        self.query_one("#arm-view").border_title = "ARM"
        self.set_selected(0)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        action = BUTTON_ACTIONS.get(event.button.id or "")
        if action:
            event.stop()
            await self.app.run_action(action)

    def on_joint_slider_changed(self, msg: JointSlider.Changed) -> None:
        msg.stop()
        self.app.jog(msg.joint, msg.value)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "step":
            self.app.set_step(float(event.value))
            for s in self.query(JointSlider):
                s.step_deg = float(event.value)
        elif event.select.id == "speed":
            self.app.set_jog_speed(int(event.value))

    def set_selected(self, index: int) -> None:
        for i, joint in enumerate(P.JOINTS):
            self.query_one(f"#slider-{joint}", JointSlider).selected = i == index

    def sync_targets(self, target: P.Pose) -> None:
        for joint in P.JOINTS:
            self.query_one(f"#slider-{joint}", JointSlider).set_target(target.get(joint), emit=False)

    def update_state(self, state: P.ArmState, target: P.Pose, selected: int) -> None:
        for joint in P.JOINTS:
            self.query_one(f"#slider-{joint}", JointSlider).actual = state.pose.get(joint)
        self.sync_targets(target)
        view = self.query_one(ArmView)
        view.pose = state.pose
        view.xyz = (state.x, state.y, state.z)
        self.query_one(LoadBars).loads = dict(state.loads)
