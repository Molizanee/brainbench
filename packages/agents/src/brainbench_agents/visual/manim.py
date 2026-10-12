"""The Manim script contract (design D7).

A script defines exactly one class ``Lesson(Scene)`` whose ``construct()`` calls
``self.next_section(name="<scene-id>")`` at the start of every storyboard scene, in storyboard
order. The worker renders it with ``--save_sections``, so each section becomes one clip whose
real duration times that scene's caption cue. :func:`check_script` enforces the contract before
a render is attempted, and its findings are fed to the repair agent like a render error.
"""

import ast
from collections.abc import Sequence

MANIM_VERSION = "0.22.0"

TEMPLATE = """\
from manim import *


class Lesson(Scene):
    def construct(self) -> None:
        self.next_section(name="{first}")
        # scene "{first}": build the mobjects, then self.play(...) / self.wait(...)
        ...
        self.next_section(name="{second}")
        # scene "{second}"
        ...
"""


def template_for(scene_ids: Sequence[str]) -> str:
    """The template filled with the first two scene ids, as an example for the prompt."""
    first = scene_ids[0] if scene_ids else "intro"
    second = scene_ids[1] if len(scene_ids) > 1 else "next_scene"
    return TEMPLATE.format(first=first, second=second)


def _base_names(node: ast.ClassDef) -> list[str]:
    names = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _section_names(construct: ast.FunctionDef) -> tuple[list[str], list[str]]:
    """``self.next_section`` names in source order, plus problems with non-literal names."""
    calls = [
        node
        for node in ast.walk(construct)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "next_section"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    ]
    calls.sort(key=lambda node: (node.lineno, node.col_offset))
    names: list[str] = []
    problems: list[str] = []
    for call in calls:
        value: ast.expr | None = next((k.value for k in call.keywords if k.arg == "name"), None)
        if value is None and call.args:
            value = call.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            names.append(value.value)
        else:
            problems.append(
                f"line {call.lineno}: self.next_section must be called with a literal "
                'name="<scene-id>"'
            )
    return names, problems


def check_script(script: str, scene_ids: Sequence[str]) -> list[str]:
    """Problems that break the script contract; an empty list means it may be rendered."""
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        return [f"SyntaxError at line {exc.lineno}: {exc.msg}"]
    problems: list[str] = []
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    scenes = [c for c in classes if any(name.endswith("Scene") for name in _base_names(c))]
    lessons = [c for c in classes if c.name == "Lesson"]
    if len(lessons) != 1:
        problems.append(f"define exactly one class Lesson(Scene); found {len(lessons)}")
    others = [c.name for c in scenes if c.name != "Lesson"]
    if others:
        problems.append(f"only Lesson may subclass Scene; also found {', '.join(others)}")
    if len(lessons) != 1:
        return problems
    lesson = lessons[0]
    if "Scene" not in _base_names(lesson):
        problems.append("Lesson must subclass Scene")
    construct = next(
        (n for n in lesson.body if isinstance(n, ast.FunctionDef) and n.name == "construct"), None
    )
    if construct is None:
        problems.append("Lesson must define construct(self)")
        return problems
    names, name_problems = _section_names(construct)
    problems.extend(name_problems)
    expected = list(scene_ids)
    if names != expected:
        problems.append(
            "construct() must call self.next_section(name=...) once per storyboard scene, "
            f"in order: expected {expected}, found {names}"
        )
    elif body := [s for s in construct.body if not _is_docstring(s)]:
        first = body[0]
        is_section = (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Call)
            and isinstance(first.value.func, ast.Attribute)
            and first.value.func.attr == "next_section"
        )
        if not is_section:
            problems.append(
                "the first statement of construct() must be "
                f'self.next_section(name="{expected[0]}")'
            )
    return problems
