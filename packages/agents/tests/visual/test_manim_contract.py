from brainbench_agents.visual import check_script, template_for

GOOD = '''\
from manim import *


class Lesson(Scene):
    def construct(self) -> None:
        """Two scenes."""
        self.next_section(name="intro")
        self.play(Create(Circle()))
        self.next_section(name="vote")
        self.wait(1)
'''


def test_valid_script_passes() -> None:
    assert check_script(GOOD, ["intro", "vote"]) == []


def test_template_follows_the_contract() -> None:
    template = template_for(["intro", "vote"]).replace("...", "pass")
    assert check_script(template, ["intro", "vote"]) == []


def test_syntax_error_is_reported_with_its_line() -> None:
    assert check_script("class Lesson(Scene:\n", ["intro"])[0].startswith("SyntaxError at line 1")


def test_sections_must_match_the_storyboard_in_order() -> None:
    problems = check_script(GOOD, ["vote", "intro"])
    assert "expected ['vote', 'intro'], found ['intro', 'vote']" in problems[0]
    assert check_script(GOOD, ["intro", "vote", "outro"])


def test_exactly_one_lesson_scene() -> None:
    two = GOOD + "\n\nclass Extra(MovingCameraScene):\n    pass\n"
    assert any("only Lesson may subclass Scene" in p for p in check_script(two, ["intro", "vote"]))
    renamed = GOOD.replace("class Lesson", "class Lecture")
    assert check_script(renamed, ["intro", "vote"]) == [
        "define exactly one class Lesson(Scene); found 0",
        "only Lesson may subclass Scene; also found Lecture",
    ]


def test_section_names_must_be_literals_and_come_first() -> None:
    dynamic = GOOD.replace('name="vote"', "name=ids[1]")
    assert any("literal" in p for p in check_script(dynamic, ["intro", "vote"]))
    late = GOOD.replace(
        '        self.next_section(name="intro")\n',
        '        x = 1\n        self.next_section(name="intro")\n',
    )
    assert check_script(late, ["intro", "vote"]) == [
        'the first statement of construct() must be self.next_section(name="intro")'
    ]
