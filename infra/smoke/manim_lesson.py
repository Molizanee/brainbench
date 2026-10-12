"""Two-section fixture for the Manim sandbox check (task 1.4)."""

from manim import Circle, Create, Scene, Square, Transform


class Lesson(Scene):
    def construct(self) -> None:
        self.next_section(name="intro")
        circle = Circle()
        self.play(Create(circle), run_time=0.5)

        self.next_section(name="transform")
        self.play(Transform(circle, Square()), run_time=0.5)
